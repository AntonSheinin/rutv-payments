"""Move checkout execution onto payments and remove the redundant child table."""

from decimal import Decimal
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "b94d50c237f1"
down_revision = "a83c49b126e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve provider claims and existing payment/ledger identities before removing children."""
    for name, length in (
        ("first_name", 140),
        ("last_name", 140),
        ("submission_key", 128),
        ("input_hash", 64),
        ("provider_correlation", 36),
        ("provider_id", 128),
        ("plan_id", 128),
        ("checkout_status", 10),
        ("browser_state", 20),
        ("phase", 20),
        ("error", 100),
        ("debug_id", 128),
    ):
        op.add_column("payments", sa.Column(name, sa.String(length), nullable=True))
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE payments SET provider_correlation = id"))
    payments = sa.Table("payments", sa.MetaData(), autoload_with=connection)
    originals = {row["id"]: dict(row) for row in connection.execute(payments.select()).mappings()}
    checkouts = (
        connection.execute(
            sa.text("SELECT * FROM payment_attempts ORDER BY payment_id, created_at, id")
        )
        .mappings()
        .all()
    )
    latest = {row["payment_id"]: row["id"] for row in checkouts}
    for checkout in checkouts:
        parent_id = checkout["payment_id"]
        terms = checkout["terms"]
        values = {
            name: checkout[name]
            for name in (
                "submission_key",
                "input_hash",
                "provider_id",
                "plan_id",
                "checkout_status",
                "browser_state",
                "phase",
                "error",
                "debug_id",
                "actor",
            )
        }
        values.update(
            first_name=terms.get("first_name"),
            last_name=terms.get("last_name"),
            provider_correlation=checkout["id"],
        )
        if latest[parent_id] == checkout["id"]:
            # Keep the existing register identity, financial summary and ledger links.
            target_id = parent_id
            connection.execute(payments.update().where(payments.c.id == parent_id).values(**values))
        else:
            # Old schemas could contain several children. Preserve every earlier
            # checkout as its own independent payment, without guessing ledger reassignment.
            target_id = str(uuid4())
            previous = originals[parent_id].copy()
            previous.update(values)
            previous.update(
                id=target_id,
                intent_key=None,
                status=checkout["status"],
                created_at=checkout["created_at"],
                updated_at=checkout["updated_at"],
            )
            for field in (
                "client_name",
                "agreement_number",
                "currency",
                "period_unit",
                "period_count",
                "payment_cycles",
                "is_subscription",
                "comments",
            ):
                if field in terms:
                    previous[field] = terms[field]
            if "price_per_period" in terms:
                previous["price_per_period"] = Decimal(terms["price_per_period"])
            connection.execute(payments.insert().values(**previous))
        connection.execute(
            sa.text(
                "UPDATE audit_events SET resource_id=:payment "
                "WHERE resource_id=:checkout AND action LIKE 'checkout.%'"
            ),
            {"payment": target_id, "checkout": checkout["id"]},
        )
    op.drop_table("payment_attempts")
    op.alter_column("payments", "provider_correlation", nullable=False)
    op.create_unique_constraint(None, "payments", ["provider_correlation"])
    op.create_unique_constraint(None, "payments", ["provider_id"])
    op.create_unique_constraint(None, "payments", ["actor", "submission_key"])
    op.create_check_constraint(
        "ck_payment_checkout_status", "payments", "checkout_status IN ('success', 'failed')"
    )


def downgrade() -> None:
    """Refuse reconstruction of a removed multi-checkout model from unified payments."""
    raise RuntimeError(
        "Payment-owned checkout cannot be downgraded without splitting payment records"
    )
