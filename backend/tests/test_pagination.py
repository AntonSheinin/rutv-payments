"""Every financial and checkout-history row remains reachable through bounded pages."""

from decimal import Decimal

import pytest
from app.models import Attempt, Payment, Subscription, Transaction, now


def test_large_detail_histories_are_bounded_complete_and_stably_sorted(sessions, client, headers):
    """More than 100 rows with tied timestamps must paginate without gaps or duplicates."""
    instant = now()
    with sessions() as db:
        payment = Payment(
            status="partially_refunded", price_per_period=Decimal("100.00"), currency="ILS"
        )
        sub = Subscription(
            provider_id="I-PAGINATION", status="ACTIVE", amount=Decimal("10.00"), currency="ILS"
        )
        db.add_all([payment, sub])
        db.flush()
        original = Transaction(
            provider_id="PAGINATION-CAPTURE",
            api_family="capture",
            kind="collection",
            status="completed",
            provider_status="COMPLETED",
            amount=Decimal("100.00"),
            currency="ILS",
            payment_id=payment.id,
            effective_at=instant,
        )
        db.add(original)
        db.flush()
        attempts = []
        payment_rows = [original]
        sub_rows = []
        for index in range(101):
            refund = Transaction(
                provider_id=f"PAGINATION-REFUND-{index}",
                api_family="capture",
                kind="refund",
                status="completed",
                provider_status="COMPLETED",
                amount=Decimal("0.01"),
                currency="ILS",
                payment_id=payment.id,
                original_id=original.id,
                effective_at=instant,
            )
            installment = Transaction(
                provider_id=f"PAGINATION-SALE-{index}",
                api_family="sale",
                kind="collection",
                status="completed",
                provider_status="COMPLETED",
                amount=Decimal("10.00"),
                currency="ILS",
                subscription_id=sub.id,
                effective_at=instant,
            )
            attempt = Attempt(
                payment_id=payment.id,
                actor="pagination",
                submission_key=str(index),
                input_hash="pagination",
                terms={"is_subscription": False},
                status="failed",
                phase="closed",
                browser_state="closed",
                created_at=instant,
            )
            db.add_all([refund, installment, attempt])
            attempts.append(attempt)
            payment_rows.append(refund)
            sub_rows.append(installment)
        db.commit()
        payment_id, sub_id = payment.id, sub.id
        expected_payment = sorted(row.id for row in payment_rows)
        expected_sub = sorted(row.id for row in sub_rows)
        expected_attempts = sorted((row.id for row in attempts), reverse=True)
    for resource, identifier, expected in [
        ("payments", payment_id, expected_payment),
        ("subscriptions", sub_id, expected_sub),
    ]:
        seen = []
        for page in range(1, 7):
            response = client.get(
                f"/api/v1/{resource}/{identifier}",
                params={"history_page": page, "history_page_size": 20},
                headers=headers,
            )
            assert response.status_code == 200
            data = response.json()["transactions"]
            assert data["total"] == len(expected) and len(data["items"]) <= 20
            assert (data["page"], data["page_size"]) == (page, 20)
            seen.extend(row["id"] for row in data["items"])
        assert seen == expected
    seen = []
    for page in range(1, 7):
        data = client.get(
            f"/api/v1/payments/{payment_id}",
            params={"attempt_page": page, "attempt_page_size": 20},
            headers=headers,
        ).json()
        assert Decimal(data["refundable_amount"]) == Decimal("98.99")
        result = data["attempts"]
        assert result["total"] == 101 and len(result["items"]) <= 20
        seen.extend(row["id"] for row in result["items"])
    assert seen == expected_attempts
    empty = client.get(
        f"/api/v1/payments/{payment_id}",
        params={"attempt_page": 100, "history_page": 100},
        headers=headers,
    ).json()
    assert empty["attempts"]["items"] == empty["transactions"]["items"] == []
    assert empty["attempts"]["total"] == 101 and empty["transactions"]["total"] == 102


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("history_page", 0),
        ("history_page_size", 101),
        ("attempt_page", -1),
        ("attempt_page_size", 0),
    ],
)
def test_invalid_detail_pagination_is_rejected(client, headers, parameter, value):
    """Reject invalid pagination before reading records or contacting PayPal."""
    assert (
        client.get(
            "/api/v1/payments/not-a-record", params={parameter: value}, headers=headers
        ).status_code
        == 422
    )


def test_older_attempt_pages_cannot_offer_cancellation(sessions, client, headers):
    """Only the latest open attempt is cancellable, regardless of the requested page."""
    from datetime import timedelta

    instant = now()
    with sessions() as db:
        payment = Payment(
            status="awaiting_approval", price_per_period=Decimal("10.00"), currency="ILS"
        )
        db.add(payment)
        db.flush()
        for index in range(21):
            db.add(
                Attempt(
                    payment_id=payment.id,
                    actor="pagination",
                    submission_key=str(index),
                    input_hash="pagination",
                    terms={"is_subscription": False},
                    status="awaiting_approval" if index == 20 else "cancelled",
                    phase="created" if index == 20 else "closed",
                    browser_state="open" if index == 20 else "closed",
                    created_at=instant + timedelta(seconds=index),
                )
            )
        db.commit()
        identifier = payment.id
    first = client.get(
        f"/api/v1/payments/{identifier}", params={"attempt_page_size": 10}, headers=headers
    ).json()["attempts"]
    last = client.get(
        f"/api/v1/payments/{identifier}",
        params={"attempt_page": 3, "attempt_page_size": 10},
        headers=headers,
    ).json()["attempts"]
    assert first["items"][0]["can_cancel"]
    assert not any(row["can_cancel"] for row in first["items"][1:] + last["items"])
