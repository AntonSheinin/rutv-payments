import { ArrowUpRight, CreditCard } from "lucide-react";
import { dateLabel, Entity, money } from "./api";
import { Badge, View } from "./ui";
import { CancelAttempt } from "./CancelAttempt";
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
  items: Entity[];
  view: View | "ledger";
  open?: (e: Entity) => void;
  busy: boolean;
  refund?: (item: Entity) => void;
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
            items.map((row) => (
              <tr
                key={row.id}
                onClick={open ? () => open(row) : undefined}
                tabIndex={open ? 0 : undefined}
                onKeyDown={(e) => {
                  if (e.key === "Enter") open?.(row);
                }}
              >
                <td>
                  <strong>{row.client_name || "Unassociated record"}</strong>
                  <small>
                    Agreement: {row.agreement_number || "Unassociated"}
                  </small>
                  <small>
                    {row.payer_email ||
                      row.kind ||
                      row.origin ||
                      row.id.slice(0, 8)}
                  </small>
                </td>
                <td>
                  <strong>
                    {money(row.amount || row.price_per_period, row.currency)}
                  </strong>
                  {refund && view === "payments" && (
                    <button
                      type="button"
                      className="refund-button"
                      disabled={
                        busy ||
                        !(
                          row.can_refund &&
                          row.collection_id &&
                          row.refund_deadline &&
                          Date.now() < Date.parse(row.refund_deadline)
                        )
                      }
                      onClick={(e) => {
                        e.stopPropagation();
                        refund(row);
                      }}
                      onKeyDown={(e) => e.stopPropagation()}
                    >
                      Refund
                    </button>
                  )}
                </td>
                {view === "subscriptions" && (
                  <>
                    <td>{row.charged_payments ?? "Not available"}</td>
                    <td>
                      {row.payments_unlimited
                        ? "Until cancelled"
                        : (row.remaining_payments ?? "Not available")}
                    </td>
                  </>
                )}
                <td>
                  <Badge
                    status={
                      row.checkout_status ??
                      (row.origin === "created_here"
                        ? "in progress"
                        : row.status)
                    }
                  />
                  {row.collection_id && row.checkout_status && (
                    <small>
                      Financial status: {row.status?.replaceAll("_", " ")}
                    </small>
                  )}
                  {view === "payments" &&
                    row.cancellable_attempt_id &&
                    updated && (
                      <CancelAttempt
                        requestId={row.id}
                        attemptId={row.cancellable_attempt_id}
                        updated={updated}
                        disabled={busy}
                      />
                    )}
                </td>
                <td>
                  {dateLabel(
                    view === "subscriptions"
                      ? row.next_billing_at
                      : row.effective_at || row.created_at,
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
            ))
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
