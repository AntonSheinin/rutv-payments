import { useRef, useState } from "react";
import { api, dateLabel, Entity, localTimezone, money } from "./api";
import { Pagination } from "./Pagination";
import { Records } from "./Records";
import { Badge, labels, SubscriptionCancellationNotice, View } from "./ui";
import { CancelAttempt } from "./CancelAttempt";

export function Details({
  item,
  loading,
  loadError,
  onPageChange,
  onPageSizeChange,
  type,
  busy,
  requestError,
  close,
  run,
  updated,
}: {
  item: Entity;
  loading: boolean;
  loadError: string;
  onPageChange: (kind: "attempts" | "history", page: number) => void;
  onPageSizeChange: (kind: "attempts" | "history", size: number) => void;
  type: View;
  busy: boolean;
  requestError: string;
  close: () => void;
  run: (path: string, data: unknown) => Promise<boolean>;
  updated: () => void;
}) {
  const [client, setClient] = useState(item.client_name || ""),
    [agreement, setAgreement] = useState(item.agreement_number || ""),
    [reason, setReason] = useState(""),
    [cancel, setCancel] = useState(false),
    [error, setError] = useState(""),
    [from, setFrom] = useState(""),
    [through, setThrough] = useState("");
  const sent = useRef(false);
  async function associate() {
    try {
      await api(`/${type}/${item.id}/association`, "PATCH", {
        client_name: client,
        agreement_number: agreement,
      });
      updated();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const scope =
    type === "payments"
      ? "payment"
      : type === "subscriptions"
        ? "subscription"
        : "transaction";
  return (
    <div className="modal-backdrop">
      <section
        className="detail-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Record details"
      >
        <div className="panel-title">
          <div>
            <span className="eyebrow">{labels[type]}</span>
            <h2>{item.client_name || "Unassociated record"}</h2>
          </div>
          <button className="secondary" onClick={close}>
            Close details
          </button>
        </div>
        {loading || loadError ? (
          <div className="detail-body">
            {loadError ? (
              <p className="error" role="alert">
                {loadError}
              </p>
            ) : (
              <p>Loading record details...</p>
            )}
          </div>
        ) : (
          <div className="detail-body">
            <div className="detail-amount">
              {money(item.amount || item.price_per_period, item.currency)}{" "}
              <Badge status={item.checkout_status ?? item.status} />
            </div>
            <dl>
              <dt>PayPal / financial status</dt>
              <dd>{item.status?.replaceAll("_", " ")}</dd>
              <dt>Agreement number</dt>
              <dd>{item.agreement_number || "Unassociated"}</dd>
              <dt>Local ID</dt>
              <dd className="mono">{item.id}</dd>
              <dt>Provider ID</dt>
              <dd>{item.provider_id || "See attempts below"}</dd>
              <dt>Last verified</dt>
              <dd>{dateLabel(item.last_synced_at)}</dd>
              {type === "subscriptions" && (
                <>
                  <dt>Charged payments (recorded)</dt>
                  <dd>{item.charged_payments ?? "Not available"}</dd>
                  <dt>Remaining payments</dt>
                  <dd>
                    {item.payments_unlimited
                      ? "Until cancelled"
                      : (item.remaining_payments ?? "Not available")}
                  </dd>
                </>
              )}
              {item.next_billing_at && (
                <>
                  <dt>Next billing</dt>
                  <dd>{dateLabel(item.next_billing_at)}</dd>
                </>
              )}
              {item.fee !== undefined && (
                <>
                  <dt>Provider fee</dt>
                  <dd>{money(item.fee, item.currency)}</dd>
                </>
              )}
              {item.outstanding != null && (
                <>
                  <dt>Outstanding</dt>
                  <dd>{money(item.outstanding, item.currency)}</dd>
                </>
              )}
            </dl>
            {item.comments && (
              <div className="internal-note">
                <strong>Internal comments</strong>
                <p>{item.comments}</p>
              </div>
            )}
            {!!item.attempts?.total && (
              <section aria-label="Payment attempts">
                <h3>Payment attempts</h3>
                {item.attempts.items.map((a) => (
                  <div className="checkout-item" key={a.id}>
                    <Badge status={a.checkout_status ?? "in progress"} />
                    <span>{dateLabel(a.created_at)}</span>
                    <span>
                      {a.provider_id || "Provider identity unconfirmed"}
                    </span>
                    <small>{a.error}</small>
                    {a.can_cancel && (
                      <CancelAttempt
                        requestId={item.id}
                        attemptId={a.id}
                        updated={updated}
                        disabled={busy}
                      />
                    )}
                  </div>
                ))}
                <Pagination
                  page={item.attempts.page}
                  pageSize={item.attempts.page_size}
                  total={item.attempts.total}
                  label="Payment attempts"
                  disabled={busy}
                  onPageChange={(page) => onPageChange("attempts", page)}
                  onPageSizeChange={(size) =>
                    onPageSizeChange("attempts", size)
                  }
                />
              </section>
            )}
            {((type === "subscriptions" &&
              ["APPROVAL_PENDING", "APPROVED"].includes(item.status || "")) ||
              (item.is_subscription &&
                ["awaiting_approval", "pending", "unknown"].includes(
                  item.status || "",
                ))) && <SubscriptionCancellationNotice />}
            <div className="detail-actions">
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  void run("/synchronizations", { scope, resource_id: item.id })
                }
              >
                Refresh from PayPal (read-only)
              </button>
              {type === "subscriptions" &&
                ["ACTIVE", "SUSPENDED"].includes(item.status || "") && (
                  <button className="danger" onClick={() => setCancel(true)}>
                    Cancel subscription
                  </button>
                )}
            </div>
            {type === "subscriptions" && (
              <form
                className="advanced"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run("/synchronizations", {
                    scope: "subscription",
                    resource_id: item.id,
                    timezone: localTimezone(),
                    start_date: from,
                    end_date: through,
                  });
                }}
              >
                <label>
                  Installments from
                  <input
                    type="date"
                    required
                    value={from}
                    onChange={(e) => setFrom(e.target.value)}
                  />
                </label>
                <label>
                  Installments through
                  <input
                    type="date"
                    required
                    min={from}
                    value={through}
                    onChange={(e) => setThrough(e.target.value)}
                  />
                </label>
                <button className="secondary" disabled={busy}>
                  Read installment window
                </button>
                <small>
                  Choose at most 31 days. PayPal controls recurring charges,
                  retries and emails.
                </small>
              </form>
            )}
            {cancel && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (sent.current) return;
                  sent.current = true;
                  void run(`/subscriptions/${item.id}/cancel`, { reason });
                }}
              >
                <label>
                  Cancellation reason
                  <input
                    required
                    maxLength={127}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    disabled={sent.current}
                  />
                </label>
                <p>
                  Stops future renewals. Previous payments are not refunded.
                </p>
                <button className="danger" disabled={busy || sent.current}>
                  Confirm cancellation
                </button>
              </form>
            )}
            {["payments", "subscriptions"].includes(type) && (
              <div className="reference-form">
                <label>
                  Client name
                  <input
                    value={client}
                    maxLength={255}
                    onChange={(e) => setClient(e.target.value)}
                  />
                </label>
                <label>
                  Agreement number
                  <input
                    value={agreement}
                    maxLength={255}
                    onChange={(e) => setAgreement(e.target.value)}
                  />
                </label>
                <button
                  className="secondary"
                  disabled={!client.trim() || !agreement.trim()}
                  onClick={() => void associate()}
                >
                  Save association
                </button>
              </div>
            )}
            {(error || requestError) && (
              <p className="error" role="alert">
                {error || requestError}
              </p>
            )}
            {!!item.transactions?.total && (
              <>
                <h3>Financial history</h3>
                <Records
                  view="ledger"
                  busy={false}
                  items={item.transactions.items}
                />
                <Pagination
                  page={item.transactions.page}
                  pageSize={item.transactions.page_size}
                  total={item.transactions.total}
                  label="Financial history"
                  disabled={busy}
                  onPageChange={(page) => onPageChange("history", page)}
                  onPageSizeChange={(size) => onPageSizeChange("history", size)}
                />
              </>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
