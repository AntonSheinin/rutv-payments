"""Repair payment summaries using current checkout and cancelled-agreement evidence."""

from alembic import op

revision = "e61f28c904a7"
down_revision = "d7e91a0326b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Restore attempt-derived summaries without altering accepted financial collections."""
    op.execute("""
        UPDATE payments p SET status=a.status FROM (
          SELECT DISTINCT ON (payment_id) payment_id,status
          FROM payment_attempts ORDER BY payment_id,created_at DESC
        ) a WHERE a.payment_id=p.id AND NOT EXISTS (
          SELECT 1 FROM transactions t WHERE t.payment_id=p.id
            AND t.kind='collection' AND t.status<>'failed'
        )
    """)
    # Agreement activation has no collected installment on its own. Keep an old
    # cancelled agreement from changing a newer checkout under the same payment.
    op.execute("""
        UPDATE payment_attempts a SET status='cancelled',browser_state='closed'
        FROM subscriptions s WHERE s.payment_id=a.payment_id
          AND s.provider_id=a.provider_id AND s.status IN ('CANCELLED','EXPIRED')
          AND a.id=(SELECT latest.id FROM payment_attempts latest
                    WHERE latest.payment_id=a.payment_id
                    ORDER BY latest.created_at DESC LIMIT 1)
          AND NOT EXISTS (SELECT 1 FROM transactions t WHERE t.payment_id=a.payment_id
                          AND t.kind='collection' AND t.status<>'failed')
    """)
    op.execute("""
        UPDATE payments p SET status='cancelled' FROM subscriptions s
        WHERE s.payment_id=p.id AND s.status IN ('CANCELLED','EXPIRED')
          AND EXISTS (SELECT 1 FROM payment_attempts a WHERE a.payment_id=p.id
                      AND a.provider_id=s.provider_id AND a.status='cancelled'
                      AND a.id=(SELECT latest.id FROM payment_attempts latest
                                WHERE latest.payment_id=p.id
                                ORDER BY latest.created_at DESC LIMIT 1))
          AND NOT EXISTS (SELECT 1 FROM transactions t WHERE t.payment_id=p.id
                          AND t.kind='collection' AND t.status<>'failed')
    """)


def downgrade() -> None:
    """Retain repaired data; this revision adds no schema objects to remove."""
