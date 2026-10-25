import { ListFilter, RefreshCw, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { dashboardFilters, emptyFilters, SearchState, View } from "./ui";
export function FiltersBar({
  filters,
  setFilters,
  onRefresh,
  busy,
  currencies,
  view,
}: {
  filters: SearchState;
  setFilters: (f: SearchState) => void;
  onRefresh: () => void;
  busy: boolean;
  currencies: string[];
  view: View;
}) {
  const [expanded, setExpanded] = useState(false),
    [draft, setDraft] = useState(filters.q);
  useEffect(() => setDraft(filters.q), [filters.q]);
  return (
    <section className="filters">
      <form
        className="search"
        onSubmit={(e) => {
          e.preventDefault();
          setFilters({ ...filters, q: draft });
        }}
      >
        <Search size={17} />
        <input
          aria-label="Search payments and agreements"
          placeholder="Search client, agreement or transaction ID…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button>Search</button>
      </form>
      <select
        aria-label="Currency filter"
        value={filters.currency}
        onChange={(e) => setFilters({ ...filters, currency: e.target.value })}
      >
        <option value="">All currencies</option>
        {currencies.map((c) => (
          <option key={c}>{c}</option>
        ))}
      </select>
      <button className="secondary" onClick={() => setExpanded(!expanded)}>
        <ListFilter size={15} />
        Filters
      </button>
      <button
        aria-label="Refresh records"
        className="icon-button"
        onClick={onRefresh}
        disabled={busy}
      >
        <RefreshCw size={17} className={busy ? "spin" : ""} />
      </button>
      {expanded && (
        <div className="advanced">
          <label>
            From
            <input
              type="date"
              value={filters.start_date}
              onChange={(e) =>
                setFilters({ ...filters, start_date: e.target.value })
              }
            />
          </label>
          <label>
            Through
            <input
              type="date"
              value={filters.end_date}
              onChange={(e) =>
                setFilters({ ...filters, end_date: e.target.value })
              }
            />
          </label>
          <label>
            Checkout result
            <select
              aria-label="Checkout result"
              value={filters.checkout_status}
              onChange={(e) =>
                setFilters({ ...filters, checkout_status: e.target.value })
              }
            >
              <option value="">All results</option>
              <option value="success">Success</option>
              <option value="failed">Failed</option>
            </select>
          </label>
          <label>
            {view === "payments" ? "Collected / Refunded" : "Origin"}
            <select
              aria-label={
                view === "payments" ? "Collected / Refunded" : "Origin"
              }
              value={view === "payments" ? filters.kind : filters.origin}
              onChange={(e) =>
                setFilters({
                  ...filters,
                  [view === "payments" ? "kind" : "origin"]: e.target.value,
                })
              }
            >
              <option value="">All records</option>
              {view === "payments" ? (
                <>
                  <option value="collection">Collected</option>
                  <option value="refund">Refunded</option>
                </>
              ) : (
                <>
                  <option value="created_here">Created here</option>
                  <option value="imported">Imported</option>
                </>
              )}
            </select>
          </label>
          <label>
            Minimum amount
            <input
              type="number"
              min="0"
              step="0.01"
              value={filters.min_amount}
              onChange={(e) =>
                setFilters({ ...filters, min_amount: e.target.value })
              }
            />
          </label>
          <label>
            Maximum amount
            <input
              type="number"
              min="0"
              step="0.01"
              value={filters.max_amount}
              onChange={(e) =>
                setFilters({ ...filters, max_amount: e.target.value })
              }
            />
          </label>
          <label>
            Payment type
            <select
              value={filters.is_subscription}
              onChange={(e) =>
                setFilters({ ...filters, is_subscription: e.target.value })
              }
            >
              <option value="">All types</option>
              <option value="true">Subscription</option>
              <option value="false">One-time</option>
            </select>
          </label>
          <button
            className="text-button"
            onClick={() =>
              setFilters(
                view === "dashboard" ? dashboardFilters() : emptyFilters,
              )
            }
          >
            {view === "dashboard" ? "Reset filters" : "Clear filters"}
          </button>
        </div>
      )}
    </section>
  );
}
