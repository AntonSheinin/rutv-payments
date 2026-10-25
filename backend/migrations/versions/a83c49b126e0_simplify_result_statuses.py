"""Retire redundant result states without releasing financial reservations."""

import sqlalchemy as sa
from alembic import op

revision = "a83c49b126e0"
down_revision = "f72b38a015d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve outstanding reservations before collapsing historical result labels."""
    op.add_column(
        "transactions",
        sa.Column("reservation_active", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "financial_actions",
        sa.Column("reservation_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.execute("""UPDATE transactions SET reservation_active =
        (kind = 'refund' AND status IN ('pending', 'unknown'))""")
    op.execute("""UPDATE financial_actions SET reservation_active =
        (status IN ('reserved', 'dispatched', 'pending', 'unknown'))""")
    # An open hosted session uses the ordinary pending result; eligibility comes
    # from its existing phase/browser fields, never a dedicated result label.
    op.execute("""UPDATE payment_attempts SET status = CASE
        WHEN checkout_status IS NULL AND browser_state = 'open' AND phase = 'created'
        THEN 'pending' ELSE 'failed' END WHERE status = 'awaiting_approval'""")
    op.execute("""UPDATE payments SET status = CASE WHEN EXISTS (
        SELECT 1 FROM payment_attempts a WHERE a.payment_id = payments.id
        AND a.checkout_status IS NULL AND a.browser_state = 'open' AND a.phase = 'created'
        ) THEN 'pending' ELSE 'failed' END WHERE status = 'awaiting_approval'""")
    for table in ("payments", "payment_attempts", "transactions", "financial_actions"):
        op.execute(sa.text(f"UPDATE {table} SET status = 'failed' WHERE status = 'unknown'"))


def downgrade() -> None:
    """Keep outstanding money protected when returning to the previous schema."""
    op.execute("""UPDATE transactions SET status = 'unknown'
        WHERE kind = 'refund' AND status = 'failed' AND reservation_active""")
    op.execute("""UPDATE financial_actions SET status = 'unknown'
        WHERE status = 'failed' AND reservation_active""")
    op.drop_column("financial_actions", "reservation_active")
    op.drop_column("transactions", "reservation_active")
