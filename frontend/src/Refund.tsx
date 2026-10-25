import { errorMessage } from "./api";
import type { RefundResult } from "./types";
import type { Payment } from "./types";
import { useRef, useState } from "react";
import { api, dateLabel, money } from "./api";
/** One explicit confirmed amount; an uncertain response never enables a second send. */
export function Refund({
  item,
  close,
  updated,
}: {
  item: Payment;
  close: () => void;
  updated: () => void;
}) {
  const [amount, setAmount] = useState(""),
    [reason, setReason] = useState(""),
    [review, setReview] = useState(false),
    [busy, setBusy] = useState(false),
    [result, setResult] = useState<RefundResult | null>(null),
    [error, setError] = useState("");
  const submitted = useRef(false);
  const expired =
    !item.refund_deadline || Date.now() >= Date.parse(item.refund_deadline);
  async function confirm() {
    if (submitted.current || expired) return;
    submitted.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api<RefundResult>(
        `/transactions/${item.collection_id}/refunds`,
        "POST",
        { amount, reason },
        crypto.randomUUID(),
      );
      setResult(result);
      updated();
    } catch (e) {
      setError(errorMessage(e));
      updated();
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <section
        className="detail-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Refund payment"
      >
        <div className="panel-title">
          <h2>Refund {item.client_name || "unassociated payment"}</h2>
          <button className="secondary" onClick={close} disabled={busy}>
            Close
          </button>
        </div>
        <div className="detail-body">
          <dl>
            <dt>Agreement number</dt>
            <dd>{item.agreement_number || "Unassociated"}</dd>
            <dt>Payment date</dt>
            <dd>{dateLabel(item.effective_at)}</dd>
            <dt>Original payment</dt>
            <dd>{money(item.amount, item.currency)}</dd>
            <dt>Already refunded</dt>
            <dd>{money(item.refunded_amount, item.currency)}</dd>
            <dt>Maximum refundable</dt>
            <dd>{money(item.refundable_amount, item.currency)}</dd>
            <dt>Refund deadline</dt>
            <dd>{dateLabel(item.refund_deadline)}</dd>
            <dt>Last verified</dt>
            <dd>{dateLabel(item.last_synced_at)}</dd>
          </dl>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setReview(true);
            }}
          >
            <fieldset disabled={busy || submitted.current || expired}>
              <label>
                Refund amount ({item.currency})
                <input
                  type="number"
                  min="0.01"
                  step="0.01"
                  max={item.refundable_amount || "0"}
                  value={amount}
                  required
                  onChange={(e) => {
                    setAmount(e.target.value);
                    setReview(false);
                  }}
                />
              </label>
              <button
                className="secondary"
                type="button"
                onClick={() => {
                  setAmount(item.refundable_amount || "");
                  setReview(false);
                }}
              >
                Full remaining amount
              </button>
              <label>
                Reason
                <input
                  value={reason}
                  maxLength={127}
                  required
                  onChange={(e) => {
                    setReason(e.target.value);
                    setReview(false);
                  }}
                />
              </label>
              {review ? (
                <>
                  <p>
                    Confirm refund of{" "}
                    <strong>{money(amount, item.currency)}</strong> through the
                    original payment. A refund does not cancel a subscription.
                  </p>
                  <button
                    className="danger"
                    type="button"
                    onClick={() => void confirm()}
                  >
                    Confirm refund
                  </button>
                </>
              ) : (
                <button className="secondary">Review refund</button>
              )}
            </fieldset>
          </form>
          {expired && <p className="error">The refund window has expired.</p>}
          {result && (
            <p role="status">
              Refund status: {result.status}
              {result.error ? ` · ${result.error}` : ""}
            </p>
          )}
          {error && (
            <p className="error" role="alert">
              {error} No automatic retry was made. Inspect the saved payment
              before starting another refund.
            </p>
          )}
        </div>
      </section>
    </div>
  );
}
