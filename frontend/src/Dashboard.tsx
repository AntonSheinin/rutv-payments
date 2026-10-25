import { ArrowDownLeft, ArrowUpRight, Repeat, Wallet } from "lucide-react";
import React from "react";
import { money } from "./api";
import type { Stats } from "./types";
import { Empty } from "./Records";
import { Badge } from "./ui";
export function Dashboard({
  stats,
  onViewPayments,
  preferredCurrency,
}: {
  stats: Stats | null;
  onViewPayments: () => void;
  preferredCurrency: string;
}) {
  if (!stats)
    return (
      <section className="panel result-panel">
        <h2>Statistics unavailable</h2>
        <p>
          Financial totals cannot be displayed until statistics load
          successfully.
        </p>
      </section>
    );
  const selected = preferredCurrency
      ? stats.currencies.find((c) => c.currency === preferredCurrency)
      : stats.currencies[0],
    currency = preferredCurrency || selected?.currency || "ILS";
  const active = stats.current_subscriptions.ACTIVE ?? 0;
  const points = stats.trends.filter(
    (t) => t.currency === currency && t.kind === "collection",
  );
  const max = Math.max(...points.map((p) => Number(p.amount)), 1);
  return (
    <>
      <div className="metrics">
        <Metric
          title="Collected payments"
          amount={money(selected?.collected || "0", currency)}
          note={`${selected?.completed_count || 0} completed payments`}
          icon={<ArrowDownLeft size={19} />}
          main
        />
        <Metric
          title="Net collected"
          amount={money(selected?.net_before_fees || "0", currency)}
          note="After refunds & reversals · before fees"
          icon={<Wallet size={19} />}
        />
        <Metric
          title="Active subscriptions"
          amount={String(active)}
          note="Current synchronized agreements"
          icon={<Repeat size={19} />}
        />
        <Metric
          title="Refunds & reversals"
          amount={money(
            String(
              Number(selected?.refunds || 0) + Number(selected?.reversals || 0),
            ),
            currency,
          )}
          note={`${selected?.pending_count || 0} pending · ${selected?.failed_count || 0} failed`}
          icon={<ArrowUpRight size={19} />}
        />
      </div>
      <div className="dashboard-grid">
        <section className="panel">
          <div className="panel-title">
            <div>
              <h2>Collection activity</h2>
              <p>Completed collections · {currency}</p>
            </div>
            <button className="text-button" onClick={onViewPayments}>
              View payments
              <ArrowUpRight size={15} />
            </button>
          </div>
          {points.length ? (
            <div className="bar-chart" aria-label="Daily collections">
              {points.slice(-30).map((p, i) => (
                <div
                  key={p.date + i}
                  title={`${p.date}: ${money(p.amount, currency)}`}
                >
                  <span
                    style={{
                      height: `${Math.max(3, (Number(p.amount) / max) * 160)}px`,
                    }}
                  />
                  <small>{new Date(p.date).getDate()}</small>
                </div>
              ))}
            </div>
          ) : (
            <div className="chart-empty">
              <div className="empty-bars">
                {[
                  30, 55, 40, 75, 50, 90, 65, 105, 70, 85, 60, 110, 80, 95, 65,
                  100,
                ].map((n, i) => (
                  <i key={i} style={{ height: n }} />
                ))}
              </div>
              <span>Your collection activity will appear here</span>
            </div>
          )}
          <div className="chart-caption">
            Payment dates in your local timezone
          </div>
        </section>
        <section className="panel subscription-summary">
          <div className="panel-title">
            <h2>Subscription health</h2>
            <Repeat size={17} />
          </div>
          <div className="subscription-number">
            {Object.values(stats.current_subscriptions).reduce(
              (a, b) => a + b,
              0,
            )}
            <span>total agreements</span>
          </div>
          {["ACTIVE", "SUSPENDED", "CANCELLED", "EXPIRED"].map((s) => (
            <div className="status-row" key={s}>
              <Badge status={s} />
              <strong>{stats.current_subscriptions[s] ?? 0}</strong>
            </div>
          ))}
        </section>
      </div>
      <section className="panel">
        <div className="panel-title">
          <div>
            <h2>Financial summary</h2>
            <p>Every currency stands on its own</p>
          </div>
          <span className="badge">Verified records only</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Currency</th>
                <th>Collected</th>
                <th>Refunds</th>
                <th>Reversals</th>
                <th>Net before fees</th>
                <th>Known fees</th>
                <th>Net after fees</th>
              </tr>
            </thead>
            <tbody>
              {stats.currencies.length ? (
                stats.currencies.map((c) => (
                  <tr key={c.currency}>
                    <td>
                      <strong>{c.currency}</strong>
                    </td>
                    <td>{money(c.collected, c.currency)}</td>
                    <td>{money(c.refunds, c.currency)}</td>
                    <td>{money(c.reversals, c.currency)}</td>
                    <td>
                      <strong>{money(c.net_before_fees, c.currency)}</strong>
                    </td>
                    <td>
                      {money(c.known_fees, c.currency)}
                      {!c.fees_complete && <small>Partial coverage</small>}
                    </td>
                    <td>{money(c.net_after_fees, c.currency)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={7}>
                    <Empty
                      title="No financial records yet"
                      description="Completed payments will appear here."
                    />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function Metric({
  title,
  amount,
  note,
  icon,
  main = false,
}: {
  title: string;
  amount: string;
  note: string;
  icon: React.ReactNode;
  main?: boolean;
}) {
  return (
    <article className={"metric " + (main ? "metric-main" : "")}>
      <div>
        <span>{title}</span>
        {icon}
      </div>
      <strong>{amount}</strong>
      <small>{note}</small>
    </article>
  );
}
