import type { SyncInput, ResourceView } from "./types";
import type { WorkspaceRecord } from "./types";
import { useRef, useState } from "react";
import { dateLabel, money } from "./api";
import { Pagination } from "./Pagination";
import { Records } from "./Records";
import { Badge, labels, SubscriptionCancellationNotice } from "./ui";
import { CloseCheckout } from "./CloseCheckout";

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
  synchronize,
  cancelSubscription,
  updated,
}: {
  item: WorkspaceRecord;
  loading: boolean;
  loadError: string;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  type: ResourceView;
  busy: boolean;
  requestError: string;
  close: () => void;
  synchronize: (data: SyncInput) => Promise<boolean>;
  cancelSubscription: (id: string, reason: string) => Promise<boolean>;
  updated: () => void;
}) {
  const [reason, setReason] = useState(""),
    [cancel, setCancel] = useState(false),
    [from, setFrom] = useState(""),
    [through, setThrough] = useState("");
  const sent = useRef(false);
  const payment = "is_subscription" in item ? item : null;
  const subscription = "charged_payments" in item ? item : null;
  const history = "transactions" in item ? item.transactions : null;
  const scope = type === "payments" ? "payment" : "subscription";
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
              {money(item.amount, item.currency)}{" "}
              <Badge status={item.checkout_status ?? item.status} />
            </div>
            <dl>
              <dt>PayPal / financial status</dt>
              <dd>{item.status.replaceAll("_", " ")}</dd>
              <dt>Agreement number</dt>
              <dd>{item.agreement_number || "Unassociated"}</dd>
              <dt>Local ID</dt>
              <dd className="mono">{item.id}</dd>
              <dt>Provider ID</dt>
              <dd>{item.provider_id || "Not available"}</dd>
              <dt>Last verified</dt>
              <dd>{dateLabel(item.last_synced_at)}</dd>
              {type === "subscriptions" && (
                <>
                  <dt>Charged payments (recorded)</dt>
                  <dd>{subscription?.charged_payments ?? "Not available"}</dd>
                  <dt>Remaining payments</dt>
                  <dd>
                    {subscription?.payments_unlimited
                      ? "Until cancelled"
                      : (subscription?.remaining_payments ?? "Not available")}
                  </dd>
                </>
              )}
              {subscription?.next_billing_at && (
                <>
                  <dt>Next billing</dt>
                  <dd>{dateLabel(subscription.next_billing_at)}</dd>
                </>
              )}
              {payment?.fee !== undefined && (
                <>
                  <dt>Provider fee</dt>
                  <dd>{money(payment.fee, item.currency)}</dd>
                </>
              )}
              {subscription?.outstanding != null && (
                <>
                  <dt>Outstanding</dt>
                  <dd>{money(subscription.outstanding, item.currency)}</dd>
                </>
              )}
            </dl>
            {payment?.comments && (
              <div className="internal-note">
                <strong>Internal comments</strong>
                <p>{payment.comments}</p>
              </div>
            )}
            {type === "payments" && payment?.can_close && (
              <CloseCheckout
                paymentId={item.id}
                updated={updated}
                disabled={busy}
              />
            )}
            {((type === "subscriptions" &&
              ["APPROVAL_PENDING", "APPROVED"].includes(item.status)) ||
              (payment?.is_subscription &&
                ["pending", "failed"].includes(item.status))) && (
              <SubscriptionCancellationNotice />
            )}
            <div className="detail-actions">
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  void synchronize({ scope, resource_id: item.id })
                }
              >
                Refresh from PayPal (read-only)
              </button>
              {type === "subscriptions" &&
                ["ACTIVE", "SUSPENDED"].includes(item.status) && (
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
                  void synchronize({
                    scope: "subscription",
                    resource_id: item.id,
                    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
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
                  void cancelSubscription(item.id, reason);
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
            {requestError && (
              <p className="error" role="alert">
                {requestError}
              </p>
            )}
            {history && history.total > 0 && (
              <>
                <h3>Financial history</h3>
                <Records view="ledger" busy={false} items={history.items} />
                <Pagination
                  page={history.page}
                  pageSize={history.page_size}
                  total={history.total}
                  label="Financial history"
                  disabled={busy}
                  onPageChange={onPageChange}
                  onPageSizeChange={onPageSizeChange}
                />
              </>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
