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
  const currencies = preferredCurrency
    ? [preferredCurrency]
    : Array.from(
        new Set([
          ...stats.currencies.map((c) => c.currency),
          ...(stats.balance?.balances.map((b) => b.currency) ?? []),
        ]),
      ).sort();
  const totals = (currency: string) =>
    stats.currencies.find((c) => c.currency === currency);
  const timestamp = (value: string) =>
    new Intl.DateTimeFormat("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  return (
    <>
      <div className="metrics">
        <Metric
          title="Collected / Net collected"
          amount={
            !currencies.length
              ? "No matching records"
              : currencies.map((currency) => {
                  const selected = totals(currency);
                  return (
                    <div key={currency}>
                      {currency}: {money(selected?.collected ?? "0", currency)}{" "}
                      /{" "}
                      {selected && !selected.fees_complete
                        ? "Net unavailable"
                        : money(selected?.net_after_fees ?? "0", currency)}
                    </div>
                  );
                })
          }
          note="Net after refunds, reversals and fees"
          icon={<ArrowDownLeft size={19} />}
          main
        />
        <Metric
          title="PayPal balance"
          amount={
            !stats.balance
              ? "Not synchronized"
              : !currencies.length
                ? "Unavailable for this currency"
                : currencies.map((currency) => {
                    const balance = stats.balance?.balances.find(
                      (b) => b.currency === currency,
                    );
                    return (
                      <div key={currency}>
                        {currency}:{" "}
                        {balance
                          ? money(balance.total, currency)
                          : "Unavailable for this currency"}
                      </div>
                    );
                  })
          }
          note={
            <>
              Account balance · currency only
              {stats.balance && (
                <>
                  <span>
                    Last synced: {timestamp(stats.balance.last_synced_at)}
                  </span>
                  <span>Balance as of: {timestamp(stats.balance.as_of)}</span>
                </>
              )}
            </>
          }
          icon={<Wallet size={19} />}
        />
        <Metric
          title="Refunds & reversals"
          amount={
            !currencies.length
              ? "No matching records"
              : currencies.map((currency) => {
                  const selected = totals(currency);
                  return (
                    <div key={currency}>
                      {currency}: {money(selected?.refunds ?? "0", currency)} /{" "}
                      {money(selected?.reversals ?? "0", currency)}
                    </div>
                  );
                })
          }
          note="Refunds / Reversals"
          icon={<ArrowUpRight size={19} />}
        />
        <article className="metric subscription-health">
          <div>
            <span>Subscription health</span>
            <Repeat size={19} />
          </div>
          {["ACTIVE", "SUSPENDED"].map((status) => (
            <div className="status-row" key={status}>
              <Badge status={status} />
              <strong>{stats.current_subscriptions[status] ?? 0}</strong>
            </div>
          ))}
        </article>
      </div>
      <section className="panel">
        <div className="panel-title">
          <div>
            <h2>Collection activity</h2>
            <p>Recorded one-time and subscription collections</p>
          </div>
          <button className="text-button" onClick={onViewPayments}>
            View payments
            <ArrowUpRight size={15} />
          </button>
        </div>
        {currencies.map((currency) => {
          const points = stats.trends
            .filter((t) => t.currency === currency && t.kind === "collection")
            .slice(-30);
          const max = Math.max(...points.map((p) => Number(p.amount)), 1);
          return (
            <React.Fragment key={currency}>
              <p className="chart-currency">{currency}</p>
              {points.length ? (
                <div className="chart-scroll">
                  <div
                    className="bar-chart"
                    aria-label={`Daily collections · ${currency}`}
                  >
                    {points.map((p) => (
                      <div
                        key={p.date}
                        title={`${p.date}: ${money(p.amount, currency)}`}
                      >
                        <span
                          style={{
                            height: `${Math.max(3, (Number(p.amount) / max) * 160)}px`,
                          }}
                        />
                        <small>
                          {new Intl.DateTimeFormat("en-GB", {
                            day: "numeric",
                            month: "short",
                            timeZone: "UTC",
                          }).format(
                            new Date(p.date.slice(0, 10) + "T00:00:00Z"),
                          )}
                        </small>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="chart-empty">
                  <div className="empty-bars">
                    {[
                      30, 55, 40, 75, 50, 90, 65, 105, 70, 85, 60, 110, 80, 95,
                      65, 100,
                    ].map((n, i) => (
                      <i key={i} style={{ height: n }} />
                    ))}
                  </div>
                  <span>Your collection activity will appear here</span>
                </div>
              )}
            </React.Fragment>
          );
        })}
        {!currencies.length && (
          <div className="chart-empty">No collections match these filters</div>
        )}
      </section>
      <section className="panel">
        <div className="panel-title">
          <div>
            <h2>Financial summary</h2>
            <p>Every currency stands on its own</p>
          </div>
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
  amount: React.ReactNode;
  note: React.ReactNode;
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
