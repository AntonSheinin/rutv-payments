"""Use one payment register for checkout states and imported collections."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "d7e91a0326b4"
down_revision = "c8a2d4f61790"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Rename the payment identity and attach existing collections without losing facts."""
    op.rename_table("payment_requests", "payments")
    for table in ("payment_attempts", "subscriptions", "transactions"):
        op.alter_column(table, "request_id", new_column_name="payment_id")
    op.execute("ALTER INDEX ix_payment_requests_client_name RENAME TO ix_payments_client_name")
    op.execute(
        "ALTER INDEX ix_payment_requests_agreement_number RENAME TO ix_payments_agreement_number"
    )
    op.execute(
        "ALTER INDEX ix_payment_attempts_request_id RENAME TO ix_payment_attempts_payment_id"
    )
    for column in (
        "intent_key",
        "actor",
        "environment",
        "period_unit",
        "period_count",
        "client_name",
        "agreement_number",
    ):
        op.alter_column("payments", column, nullable=True)
    op.add_column(
        "payments",
        sa.Column("origin", sa.String(20), nullable=False, server_default="created_here"),
    )
    op.alter_column("payments", "origin", server_default=None)
    connection = op.get_bind()
    for constraint in sa.inspect(connection).get_check_constraints("payments"):
        if "price_per_period" in constraint["sqltext"]:
            op.drop_constraint(constraint["name"], "payments", type_="check")
    op.create_check_constraint(None, "payments", "price_per_period >= 0")
    rows = (
        connection.execute(
            sa.text("""
        SELECT t.*, p.intent_key, p.is_subscription AS parent_subscription
        FROM transactions t LEFT JOIN payments p ON p.id = t.payment_id
        WHERE t.kind = 'collection' ORDER BY t.effective_at, t.id
    """)
        )
        .mappings()
        .all()
    )
    seen: set[str] = set()
    for row in rows:
        payment_id = row["payment_id"]
        # Subscription activation is not an installment collection. Each installment
        # gets its own payment; its refund remains attached to that collection.
        if (
            payment_id is None
            or row["parent_subscription"]
            or (payment_id in seen and row["status"] != "failed")
        ):
            payment_id = str(uuid4())
            connection.execute(
                sa.text("""
                INSERT INTO payments
                  (id, status, price_per_period, currency, is_subscription, client_name,
                   agreement_number, comments, origin, created_at, updated_at)
                VALUES (:id, :status, :amount, :currency, :recurring, :client_name,
                        :agreement_number, '', :origin, :created_at, :updated_at)
            """),
                {
                    "id": payment_id,
                    "status": row["status"],
                    "amount": row["amount"],
                    "currency": row["currency"],
                    "recurring": row["subscription_id"] is not None,
                    "client_name": row["client_name"],
                    "agreement_number": row["agreement_number"],
                    "origin": row["origin"],
                    "created_at": row["effective_at"],
                    "updated_at": row["updated_at"],
                },
            )
        if row["status"] != "failed":
            seen.add(payment_id)
        connection.execute(
            sa.text("UPDATE transactions SET payment_id=:payment WHERE id=:id OR original_id=:id"),
            {"payment": payment_id, "id": row["id"]},
        )
    connection.execute(
        sa.text("""
        UPDATE payments p SET status = CASE
          WHEN c.status = 'failed' THEN p.status
          WHEN c.status <> 'completed' THEN c.status
          WHEN c.provider_status = 'REFUNDED' OR COALESCE(a.refunded, 0) >= c.amount THEN 'refunded'
          WHEN c.provider_status = 'PARTIALLY_REFUNDED' OR COALESCE(a.refunded, 0) > 0
            THEN 'partially_refunded'
          WHEN COALESCE(a.reversed, 0) > COALESCE(a.restored, 0) THEN 'reversed'
          ELSE 'completed' END
        FROM (
          SELECT DISTINCT ON (payment_id) * FROM transactions WHERE kind='collection'
          ORDER BY payment_id, (status='failed'), effective_at DESC
        ) c LEFT JOIN (
          SELECT original_id,
            SUM(amount) FILTER (WHERE kind='refund') AS refunded,
            SUM(amount) FILTER (WHERE kind='reversal') AS reversed,
            SUM(amount) FILTER (WHERE kind='adjustment') AS restored
          FROM transactions WHERE status='completed' GROUP BY original_id
        ) a ON a.original_id = c.id
        WHERE c.kind='collection' AND c.payment_id=p.id
    """)
    )
    op.create_index(
        "uq_payment_collection",
        "transactions",
        ["payment_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'collection' AND status <> 'failed'"),
    )


def downgrade() -> None:
    """Refuse a rollback that would discard imported payments and their identities."""
    raise RuntimeError("Unified payments cannot be downgraded without discarding payment records")
