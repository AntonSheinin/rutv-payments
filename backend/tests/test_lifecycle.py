"""Single-process lifecycle without background payment tasks or startup replay."""

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.db import migrate_database
from app.lifecycle import lifespan
from app.models import Base
from fastapi import FastAPI
from sqlalchemy import MetaData, inspect, text


def test_migration_cli_rejects_application_database_test_override() -> None:
    """A CLI test override must fail before connecting to an application database."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "TEST_DATABASE_URL": "postgresql+psycopg://unused@localhost/payments"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "TEST_DATABASE_URL requires a dedicated payments_test database" in result.stderr


async def test_startup_no_worker_and_loss_of_ownership_blocks_mutations(client, headers, provider):
    assert provider["requests"] == []
    await client.app.state.ownership.invalidate()
    response = await client.post(
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
    assert (await client.get("/api/v1/payments", headers=headers)).status_code == 200


async def test_singleton_and_shutdown_release_ownership(application):
    async with application.router.lifespan_context(application):
        owner = application.state.ownership
        with pytest.raises(RuntimeError, match="Another service"):
            other = FastAPI(lifespan=lifespan)
            async with other.router.lifespan_context(other):
                pass
        assert not owner.closed
    assert owner.closed


def test_clean_migration_matches_target_schema(migration_sessions):
    engine = migration_sessions.kw["bind"]
    metadata = MetaData()
    metadata.reflect(engine)
    metadata.drop_all(engine)
    with engine.begin() as migration:
        migrate_database(migration)
    with engine.begin() as migration:
        migrate_database(migration)
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_populated_payment_migration_preserves_ids_and_splits_installments(migration_sessions):
    """Upgrade actual previous-schema records without losing checkout or ledger identities."""
    engine = migration_sessions.kw["bind"]
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
                text(
                    "SELECT checkout_status FROM payments WHERE provider_correlation='open-attempt'"
                )
            )
            == "failed"
        )
        assert (
            connection.scalar(
                text("SELECT browser_state FROM payments WHERE provider_correlation='open-attempt'")
            )
            == "closed"
        )
        assert not {"payment_requests", "payment_attempts"} & set(
            inspect(connection).get_table_names()
        )
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
            == "failed"
        )
        assert (
            connection.scalar(
                text("SELECT status FROM payments WHERE provider_correlation='sub-attempt'")
            )
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
    with engine.begin() as migration:
        migrate_database(migration)


def test_result_status_migration_preserves_financial_reservations(migration_sessions):
    """Retired labels collapse without releasing unresolved refund/cancel claims."""
    engine = migration_sessions.kw["bind"]
    metadata = MetaData()
    metadata.reflect(engine)
    metadata.drop_all(engine)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    timestamp = datetime.now(UTC)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "f72b38a015d9")
        metadata = MetaData()
        metadata.reflect(connection)
        common = {"created_at": timestamp, "updated_at": timestamp}
        for identifier, status in [("open", "awaiting_approval"), ("lost", "unknown")]:
            connection.execute(
                metadata.tables["payments"].insert(),
                {
                    **common,
                    "id": identifier,
                    "status": status,
                    "price_per_period": 100,
                    "currency": "ILS",
                    "is_subscription": False,
                    "comments": "",
                    "origin": "created_here",
                },
            )
            connection.execute(
                metadata.tables["payment_attempts"].insert(),
                {
                    **common,
                    "id": identifier,
                    "payment_id": identifier,
                    "status": status,
                    "actor": "migration",
                    "submission_key": identifier,
                    "input_hash": "hash",
                    "terms": {"first_name": "Migration", "last_name": "Client"},
                    "provider_id": "ORDER-" + identifier,
                    "phase": "created" if identifier == "open" else "create_dispatched",
                    "browser_state": "open" if identifier == "open" else "closed",
                },
            )
        for status in ("unknown", "pending", "failed", "completed"):
            connection.execute(
                metadata.tables["transactions"].insert(),
                {
                    **common,
                    "id": status,
                    "provider_id": status,
                    "api_family": "capture",
                    "kind": "refund",
                    "status": status,
                    "provider_status": "UNCONFIRMED" if status == "unknown" else status.upper(),
                    "amount": 10,
                    "currency": "ILS",
                    "origin": "created_here",
                    "effective_at": timestamp,
                    "last_synced_at": timestamp,
                },
            )
        for status in ("unknown", "pending", "failed", "completed", "reserved", "dispatched"):
            connection.execute(
                metadata.tables["financial_actions"].insert(),
                {
                    **common,
                    "id": status,
                    "actor": "migration",
                    "scope": "cancel:subscription",
                    "submission_key": status,
                    "input_hash": "hash",
                    "resource_id": "subscription",
                    "status": status,
                },
            )
        # Older schemas allowed several checkout children; every historical
        # checkout must survive independently without moving existing ledger facts.
        connection.execute(
            metadata.tables["payment_attempts"].insert(),
            {
                **common,
                "id": "earlier",
                "payment_id": "open",
                "status": "failed",
                "actor": "migration",
                "submission_key": "earlier",
                "input_hash": "earlier-hash",
                "provider_id": "EARLIER-ORDER",
                "terms": {"first_name": "Earlier", "last_name": "Client"},
                "phase": "finished",
                "browser_state": "closed",
                "checkout_status": "failed",
            },
        )
        connection.execute(
            metadata.tables["audit_events"].insert(),
            {
                **common,
                "id": "checkout-audit",
                "actor": "migration",
                "action": "checkout.submitted",
                "resource_id": "earlier",
                "details": {},
            },
        )
        command.upgrade(config, "head")
        assert "payment_attempts" not in inspect(connection).get_table_names()
        assert dict(
            connection.execute(
                text("SELECT id, status FROM payments WHERE id IN ('open', 'lost')")
            ).all()
        ) == {
            "open": "pending",
            "lost": "failed",
        }
        earlier = connection.execute(
            text(
                "SELECT id, first_name, provider_id, submission_key FROM payments "
                "WHERE provider_correlation='earlier'"
            )
        ).one()
        assert earlier.first_name == "Earlier" and earlier.provider_id == "EARLIER-ORDER"
        assert earlier.submission_key == "earlier"
        assert connection.scalar(text("SELECT count(*) FROM payments")) == 3
        assert (
            connection.scalar(
                text("SELECT resource_id FROM audit_events WHERE id='checkout-audit'")
            )
            == earlier.id
        )
        assert (
            connection.scalar(text("SELECT provider_id FROM payments WHERE id='open'"))
            == "ORDER-open"
        )
        refunds = connection.execute(
            text("SELECT id, status, reservation_active FROM transactions")
        ).all()
        assert {identifier: (status, reserved) for identifier, status, reserved in refunds} == {
            "unknown": ("failed", True),
            "pending": ("pending", True),
            "failed": ("failed", False),
            "completed": ("completed", False),
        }
        active = connection.scalars(
            text("SELECT id FROM financial_actions WHERE reservation_active")
        ).all()
        assert set(active) == {"unknown", "pending", "reserved", "dispatched"}
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
