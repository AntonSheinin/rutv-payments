"""Separate terminal checkout outcomes from verified financial state."""

import sqlalchemy as sa
from alembic import op

revision = "f72b38a015d9"
down_revision = "e61f28c904a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Freeze completed historical checkouts without rewriting financial facts."""
    op.add_column("payment_attempts", sa.Column("checkout_status", sa.String(10), nullable=True))
    op.create_check_constraint(
        "ck_attempt_checkout_status", "payment_attempts", "checkout_status IN ('success', 'failed')"
    )
    op.execute("""UPDATE payment_attempts SET checkout_status = CASE
        WHEN status IN ('completed','active','suspended') THEN 'success' ELSE 'failed' END,
        browser_state='closed'""")


def downgrade() -> None:
    """Remove the checkout outcome column without changing ledger facts."""
    op.drop_constraint("ck_attempt_checkout_status", "payment_attempts", type_="check")
    op.drop_column("payment_attempts", "checkout_status")
