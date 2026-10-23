"""Remove unused reporting coverage fields without changing financial records."""

import sqlalchemy as sa
from alembic import op

revision = "c8a2d4f61790"
down_revision = "b5f3694e7475"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop fields that have no producers in the direct synchronization flow."""
    op.drop_column("sync_state", "coverage_start")
    op.drop_column("sync_state", "coverage_end")
    op.drop_column("sync_state", "error")


def downgrade() -> None:
    """Restore the previous schema shape for an explicit migration rollback."""
    op.add_column("sync_state", sa.Column("error", sa.String(300), nullable=True))
    op.add_column(
        "sync_state", sa.Column("coverage_end", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "sync_state", sa.Column("coverage_start", sa.DateTime(timezone=True), nullable=True)
    )
