import type { Payment, Subscription, RecordRow, ResourceView } from "./types";
import { ArrowUpRight, CreditCard } from "lucide-react";
import { dateLabel, money } from "./api";
import { Badge } from "./ui";
import { CloseCheckout } from "./CloseCheckout";
export function Empty({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="empty">
      <CreditCard size={29} strokeWidth={1.3} />
      <h3>{title}</h3>
      <p>{description}</p>
    </div>
  );
}

export function Records({
  items,
  view,
  open,
  busy,
  refund,
  updated,
}: {
  items: RecordRow[];
  view: ResourceView | "ledger";
  open?: (e: Payment | Subscription) => void;
  busy: boolean;
  refund?: (item: Payment) => void;
  updated?: () => void;
}) {
  return (
    <div className="table-scroll">
      {view === "subscriptions" && (
        <p className="muted">
          Charged counts include recorded successful payments; imported history
          may be incomplete. Remaining payments reflect the last PayPal refresh.
        </p>
      )}
      <table>
        <thead>
          <tr>
            <th>Client name / agreement</th>
            <th>{view === "subscriptions" ? "Billing amount" : "Amount"}</th>
            {view === "subscriptions" && (
              <>
                <th>Charged payments (recorded)</th>
                <th>Remaining payments</th>
              </>
            )}
            <th>Status</th>
            <th>{view === "subscriptions" ? "Next billing" : "Date"}</th>
            <th>Provider reference</th>
            {open && <th />}
          </tr>
        </thead>
        <tbody>
          {items.length ? (
            items.map((row) => {
              const payment = "is_subscription" in row ? row : null;
              const subscription = "charged_payments" in row ? row : null;
              const movement = "kind" in row ? row : null;
              const checkout = payment || subscription;
              return (
                <tr
                  key={row.id}
                  onClick={
                    open && !("kind" in row) ? () => open(row) : undefined
                  }
                  tabIndex={open ? 0 : undefined}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !("kind" in row)) open?.(row);
                  }}
                >
                  <td>
                    <strong>{row.client_name || "Unassociated record"}</strong>
                    <small>
                      Agreement: {row.agreement_number || "Unassociated"}
                    </small>
                    <small>
                      {row.payer_email ||
                        movement?.kind ||
                        row.origin ||
                        row.id.slice(0, 8)}
                    </small>
                  </td>
                  <td>
                    <strong>{money(row.amount, row.currency)}</strong>
                    {refund && payment && view === "payments" && (
                      <button
                        type="button"
                        className="refund-button"
                        disabled={
                          busy ||
                          !(
                            payment.can_refund &&
                            payment.collection_id &&
                            payment.refund_deadline &&
                            Date.now() < Date.parse(payment.refund_deadline)
                          )
                        }
                        onClick={(e) => {
                          e.stopPropagation();
                          refund(payment);
                        }}
                        onKeyDown={(e) => e.stopPropagation()}
                      >
                        Refund
                      </button>
                    )}
                  </td>
                  {view === "subscriptions" && (
                    <>
                      <td>
                        {subscription?.charged_payments ?? "Not available"}
                      </td>
                      <td>
                        {subscription?.payments_unlimited
                          ? "Until cancelled"
                          : (subscription?.remaining_payments ??
                            "Not available")}
                      </td>
                    </>
                  )}
                  <td>
                    <Badge
                      status={
                        checkout?.checkout_status ??
                        (checkout && row.origin === "created_here"
                          ? "in progress"
                          : row.status)
                      }
                    />
                    {payment?.collection_id && checkout?.checkout_status && (
                      <small>
                        Financial status: {row.status.replaceAll("_", " ")}
                      </small>
                    )}
                    {view === "payments" && payment?.can_close && updated && (
                      <CloseCheckout
                        paymentId={row.id}
                        updated={updated}
                        disabled={busy}
                      />
                    )}
                  </td>
                  <td>
                    {dateLabel(
                      view === "subscriptions"
                        ? subscription?.next_billing_at
                        : "effective_at" in row
                          ? row.effective_at
                          : row.created_at,
                    )}
                  </td>
                  <td className="mono">
                    {row.provider_id || row.id.slice(0, 13)}
                  </td>
                  {open && (
                    <td>
                      <ArrowUpRight size={16} />
                    </td>
                  )}
                </tr>
              );
            })
          ) : (
            <tr>
              <td colSpan={(open ? 6 : 5) + (view === "subscriptions" ? 2 : 0)}>
                <Empty
                  title={busy ? "Loading recordsâ€¦" : "No matching records"}
                  description="Try a different search or create a payment."
                />
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
