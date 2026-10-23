"""Single-process lifecycle without background payment tasks or startup replay."""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.db import migrate_database
from app.main import lifespan
from app.models import Base
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, inspect, text


def test_startup_no_worker_and_loss_of_ownership_blocks_mutations(client, headers, provider):
    assert not hasattr(client.app.state, "processing_task")
    assert provider["requests"] == []
    client.app.state.ownership.invalidate()
    response = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "first_name": "Test",
            "last_name": "Client",
            "agreement_number": "1",
            "price_per_period": "5.00",
            "intent_key": "intent",
        },
    )
    assert response.status_code == 503
    assert not provider["requests"]
    assert client.get("/api/v1/payments", headers=headers).status_code == 200


def test_singleton_and_shutdown_release_ownership(application):
    with TestClient(application) as first:
        with pytest.raises(RuntimeError, match="Another service"):
            with TestClient(FastAPI(lifespan=lifespan)):
                pass
        assert not first.app.state.ownership.closed
    assert first.app.state.ownership.closed


def test_clean_migration_matches_target_schema(sessions):
    engine = sessions.kw["bind"]
    metadata = MetaData()
    metadata.reflect(engine)
    metadata.drop_all(engine)
    migrate_database(engine)
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    migrate_database(engine)


def test_populated_payment_migration_preserves_ids_and_splits_installments(sessions):
    """Upgrade actual previous-schema records without losing checkout or ledger identities."""
    engine = sessions.kw["bind"]
    metadata = MetaData()
    metadata.reflect(engine)
    metadata.drop_all(engine)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "c8a2d4f61790")
        for identifier, recurring, status in [
            ("saved-payment", False, "completed"),
            ("saved-subscription", True, "active"),
            ("open-payment", False, "awaiting_approval"),
        ]:
            connection.execute(
                text("""
                INSERT INTO payment_requests
                (id,intent_key,status,price_per_period,currency,period_unit,period_count,
                 is_subscription,client_name,agreement_number,comments,actor,environment,created_at,updated_at)
                VALUES (:id,:id,:status,50,'ILS','MONTH',1,:recurring,
                        'Test Client','001','','test','sandbox',now(),now())
            """),
                {"id": identifier, "recurring": recurring, "status": status},
            )
        connection.execute(
            text("""
            INSERT INTO subscriptions
            (id,provider_id,request_id,status,metadata_json,origin,created_at,updated_at)
            VALUES ('sub','I-SUB','saved-subscription','CANCELLED','{}','created_here',now(),now())
        """)
        )
        for identifier, payment_id, provider_id, status in [
            ("open-attempt", "open-payment", "ORDER-OPEN", "awaiting_approval"),
            ("sub-attempt", "saved-subscription", "I-SUB", "active"),
        ]:
            connection.execute(
                text("""
                INSERT INTO payment_attempts
                (id,request_id,provider_id,actor,submission_key,input_hash,terms,status,
                 browser_state,phase,created_at,updated_at)
                VALUES (:id,:payment,:provider,'test',:id,'hash','{}',:status,
                        'open','created',now(),now())
            """),
                {
                    "id": identifier,
                    "payment": payment_id,
                    "provider": provider_id,
                    "status": status,
                },
            )
        for identifier, parent, subscription, kind, original, amount in [
            ("capture", "saved-payment", None, "collection", None, 50),
            ("failed-capture-1", "saved-payment", None, "collection", None, 50),
            ("failed-capture-2", "saved-payment", None, "collection", None, 50),
            ("failed-open", "open-payment", None, "collection", None, 50),
            ("refund", "saved-payment", None, "refund", "capture", 20),
            ("import", None, None, "collection", None, 10),
            ("installment-1", "saved-subscription", "sub", "collection", None, 50),
            ("installment-2", "saved-subscription", "sub", "collection", None, 50),
        ]:
            connection.execute(
                text("""
                INSERT INTO transactions
                (id,provider_id,api_family,kind,status,provider_status,request_id,subscription_id,
                 original_id,amount,currency,origin,effective_at,last_synced_at,created_at,updated_at)
                VALUES (:id,:id,'capture',:kind,
                        :status,:provider_status,
                        :parent,:subscription,
                        :original,:amount,'ILS','imported',now(),now(),now(),now())
            """),
                {
                    "id": identifier,
                    "kind": kind,
                    "parent": parent,
                    "subscription": subscription,
                    "original": original,
                    "amount": amount,
                    "status": "failed" if identifier.startswith("failed-") else "completed",
                    "provider_status": "DECLINED"
                    if identifier.startswith("failed-")
                    else "COMPLETED",
                },
            )
        command.upgrade(config, "d7e91a0326b4")
        assert (
            connection.scalar(text("SELECT status FROM payments WHERE id='open-payment'"))
            == "awaiting_approval"
        )
        # Also exercise a database where the earlier summary bug was already applied.
        connection.execute(text("UPDATE payments SET status='failed' WHERE id='open-payment'"))
        command.upgrade(config, "head")
        assert (
            connection.scalar(
                text("SELECT checkout_status FROM payment_attempts WHERE id='open-attempt'")
            )
            == "failed"
        )
        assert (
            connection.scalar(
                text("SELECT browser_state FROM payment_attempts WHERE id='open-attempt'")
            )
            == "closed"
        )
        assert "payment_requests" not in inspect(connection).get_table_names()
        assert connection.scalar(text("SELECT count(*) FROM payments")) == 6
        assert connection.scalar(text("SELECT count(*) FROM transactions")) == 8
        assert (
            connection.scalar(text("SELECT status FROM payments WHERE id='saved-payment'"))
            == "partially_refunded"
        )
        assert (
            connection.scalar(text("SELECT status FROM payments WHERE id='saved-subscription'"))
            == "cancelled"
        )
        assert (
            connection.scalar(text("SELECT status FROM payments WHERE id='open-payment'"))
            == "awaiting_approval"
        )
        assert (
            connection.scalar(text("SELECT status FROM payment_attempts WHERE id='sub-attempt'"))
            == "cancelled"
        )
        assert (
            connection.scalar(text("SELECT payment_id FROM transactions WHERE id='refund'"))
            == "saved-payment"
        )
        assert (
            len(
                set(
                    connection.scalars(
                        text("SELECT payment_id FROM transactions WHERE subscription_id='sub'")
                    )
                )
            )
            == 2
        )
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    migrate_database(engine)
