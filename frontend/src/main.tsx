import {
  CreditCard,
  LayoutDashboard,
  LogOut,
  Menu,
  Plus,
  RefreshCw,
  Repeat,
  ShieldCheck,
  X,
} from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import {
  api,
  ApiError,
  Entity,
  Options,
  Page,
  setCsrf,
  requestsPending,
  subscribeRequests,
  Stats,
  User,
} from "./api";
import { CheckoutResult as ResultPage } from "./CheckoutResult";
import { CheckoutResult, SyncResult } from "./api";
import { submissionStorage } from "./checkout";
import { Refund } from "./Refund";
import { Dashboard } from "./Dashboard";
import { Pagination } from "./Pagination";
import { Details } from "./Details";
import { FiltersBar } from "./FiltersBar";
import { Login } from "./Login";
import { CreatePayment } from "./PaymentForm";
import { Empty, Records } from "./Records";
import "./styles.css";
import { ThemeToggle } from "./ThemeToggle";
import { emptyFilters, labels, query, SearchState, View } from "./ui";
function App() {
  const requestPending = useSyncExternalStore(
    subscribeRequests,
    requestsPending,
  );
  const [user, setUser] = useState<User | null>(null),
    [loading, setLoading] = useState(true),
    [view, setView] = useState<View>("dashboard"),
    [options, setOptions] = useState<Options | null>(null);
  const [filters, setFilters] = useState<SearchState>(emptyFilters),
    [page, setPage] = useState(1),
    [pageSize, setPageSize] = useState(20),
    [listing, setListing] = useState<Page | null>(null),
    [stats, setStats] = useState<Stats | null>(null),
    [detail, setDetail] = useState<Entity | null>(null),
    [detailType, setDetailType] = useState<View>("payments");
  const [subscriptionImportPage, setSubscriptionImportPage] = useState(1);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [revision, setRevision] = useState(0),
    [refundItem, setRefundItem] = useState<Entity | null>(null);
  const [showResult, setShowResult] = useState(() =>
    ["/paypal/success", "/paypal/fail"].includes(location.pathname),
  );
  const [checkout, setCheckout] = useState<CheckoutResult | undefined>();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [creationKey, setCreationKey] = useState(0);
  useEffect(() => {
    if (!mobileMenuOpen) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setMobileMenuOpen(false);
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [mobileMenuOpen]);
  const [detailPaging, setDetailPaging] = useState({
    attempt_page: 1,
    attempt_page_size: 20,
    history_page: 1,
    history_page_size: 20,
  });
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const detailId = detail?.id;
  useEffect(() => {
    if (!user || !detailId) return;
    let active = true;
    setDetailLoading(true);
    setDetailError("");
    const params = new URLSearchParams(
      Object.entries(detailPaging)
        .filter(
          ([key]) => detailType === "payments" || key.startsWith("history_"),
        )
        .map(([key, value]) => [key, String(value)]),
    );
    api<Entity>("/" + detailType + "/" + detailId + "?" + params)
      .then((item) => {
        if (!active) return;
        const next = { ...detailPaging };
        if (item.attempts)
          next.attempt_page = Math.min(
            next.attempt_page,
            Math.max(
              1,
              Math.ceil(item.attempts.total / item.attempts.page_size),
            ),
          );
        if (item.transactions)
          next.history_page = Math.min(
            next.history_page,
            Math.max(
              1,
              Math.ceil(item.transactions.total / item.transactions.page_size),
            ),
          );
        if (
          next.attempt_page !== detailPaging.attempt_page ||
          next.history_page !== detailPaging.history_page
        ) {
          setDetailPaging(next);
          return;
        }
        setDetail(item);
      })
      .catch((e) => {
        if (active) setDetailError(e.message);
      })
      .finally(() => {
        if (active) setDetailLoading(false);
      });
    return () => {
      // A slower response for an old selection must not overwrite the current detail panel.
      active = false;
    };
  }, [user, detailId, detailType, revision, detailPaging]);
  useEffect(() => {
    api<User>("/auth/me")
      .then((u) => {
        setCsrf(u.csrf);
        setUser(u);
      })
      .catch((e: unknown) => {
        if (!(e instanceof ApiError && e.status === 401)) {
          setError((e as Error).message);
        }
      })
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    if (user)
      api<Options>("/options")
        .then(setOptions)
        .catch((e) => setError(e.message));
  }, [user]);
  useEffect(() => {
    if (!user || view === "create") return;
    let active = true;
    setBusy(true);
    setError("");
    if (view === "dashboard") setStats(null);
    else setListing(null);
    const suffix = query(filters, page, pageSize);
    const task =
      view === "dashboard"
        ? api<Stats>("/statistics" + suffix).then((s) => {
            if (active) setStats(s);
          })
        : api<Page>("/" + view + suffix).then((s) => {
            if (!active) return;
            const lastPage = Math.max(1, Math.ceil(s.total / s.page_size));
            if (page > lastPage) {
              setPage(lastPage);
              return;
            }
            setListing(s);
          });
    task
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [user, view, filters, page, pageSize, revision]);
  async function run(path: string, data: unknown) {
    if (busy) return false;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<Entity & Partial<SyncResult>>(
        path,
        "POST",
        data,
        crypto.randomUUID(),
      );
      if (path === "/synchronizations") {
        if (
          !Number.isInteger(result.processed) ||
          result.processed! < 0 ||
          !Number.isInteger(result.excluded) ||
          result.excluded! < 0 ||
          typeof result.coverage_note !== "string" ||
          !(
            result.next_page === null ||
            (Number.isInteger(result.next_page) && result.next_page! > 0)
          )
        )
          throw new Error(
            "Invalid synchronization response; result is unconfirmed.",
          );
      } else {
        if (typeof result.status !== "string" || !result.status)
          throw new Error("Invalid action response; result is unconfirmed.");
        setNotice(`Result: ${result.status}`);
      }
      if (result.error) setError(result.error);
      setRevision((r) => r + 1);
      return !result.error;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  function freshPayment() {
    if (requestsPending()) return;
    setError("");
    setDetail(null);
    setCreationKey((key) => key + 1);
    sessionStorage.removeItem(submissionStorage);
    history.replaceState(null, "", "/");
    setCheckout(undefined);

    setShowResult(false);
    setView("create");
    setMobileMenuOpen(false);
    setNotice("");
  }
  function navigate(next: View) {
    if (requestsPending()) return;
    setMobileMenuOpen(false);
    setNotice("");
    if (next === "create") {
      freshPayment();
      return;
    }
    setShowResult(false);
    setView(next);
    setPage(1);
    setFilters(emptyFilters);
    setListing(null);
    setError("");
    setDetail(null);
  }
  if (loading) return <div className="loading">Opening payments…</div>;
  if (!user)
    return (
      <Login
        sessionError={error}
        onLogin={(u) => {
          setError("");
          setUser(u);
        }}
      />
    );
  return (
    <div className="shell">
      <header className="mobile-toolbar">
        <button
          className="icon-button"
          aria-label="Open menu"
          aria-expanded={mobileMenuOpen}
          aria-controls="main-navigation"
          onClick={() => setMobileMenuOpen(true)}
        >
          <Menu size={20} />
        </button>
        <span>RuTV</span>
      </header>
      {mobileMenuOpen && (
        <button
          className="menu-backdrop"
          aria-label="Close menu"
          onClick={() => setMobileMenuOpen(false)}
        />
      )}
      <aside
        id="main-navigation"
        className={"sidebar" + (mobileMenuOpen ? " open" : "")}
      >
        <div className="sidebar-brand">
          <a
            className="wordmark"
            href="#"
            onClick={() => navigate("dashboard")}
          >
            <img src="/assets/rutv-logo.png" alt="RuTV logo" />
            <span className="brand-name">RuTV</span>
          </a>
          <ThemeToggle />
          <button
            className="icon-button sidebar-close"
            aria-label="Close menu"
            onClick={() => setMobileMenuOpen(false)}
          >
            <X size={18} />
          </button>
        </div>
        <nav aria-label="Main navigation">
          {(
            [
              ["dashboard", LayoutDashboard],
              ["payments", CreditCard],
              ["subscriptions", Repeat],
            ] as const
          ).map(([key, Icon]) => (
            <button
              key={key}
              disabled={requestPending}
              aria-label={labels[key]}
              aria-current={view === key ? "page" : undefined}
              title={labels[key]}
              className={view === key ? "active" : ""}
              onClick={() => navigate(key)}
            >
              <Icon size={20} />
              {labels[key]}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span className="profile-name" title={user.name}>
            {user.name}
          </span>
          <button
            className="logout-button"
            aria-label="Logout"
            title="Logout"
            disabled={requestPending}
            onClick={async () => {
              try {
                await api("/auth/logout", "POST");
              } catch (e) {
                setError((e as Error).message);
                return;
              }
              setCsrf("");
              setUser(null);
              setMobileMenuOpen(false);
            }}
          >
            <LogOut size={20} />
          </button>
        </div>
      </aside>
      <div className="main">
        <main className="content">
          <div className="page-heading">
            <div>
              <h1>
                {showResult
                  ? "Payment result"
                  : view === "dashboard"
                    ? "Dashboard"
                    : labels[view]}
              </h1>
              <p>
                {showResult
                  ? "The saved outcome of your PayPal checkout."
                  : view === "dashboard"
                    ? "A closer look at your collections and recurring payments."
                    : view === "subscriptions"
                      ? "Current agreements, upcoming billings, and payment history."
                      : view === "create"
                        ? "Set the terms. Payment details are collected by PayPal."
                        : "Find, review, and manage every payment record."}
              </p>
            </div>
            <div className="page-actions">
              <span className={"environment " + options?.environment}>
                {options?.environment || "loading"}
              </span>
              <button
                className="primary"
                disabled={requestPending}
                onClick={() => navigate("create")}
              >
                <Plus size={17} />
                Create payment
              </button>
            </div>
          </div>
          {options && !options.paypal_configured && (
            <div className="banner">
              <ShieldCheck size={18} />
              <span>
                Sandbox · Add PayPal credentials to enable checkout and
                synchronization.
              </span>
            </div>
          )}
          {error && (
            <div className="error" role="alert">
              {error}
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {notice && (
            <div className="notice">
              {notice}
              <button aria-label="Dismiss notice" onClick={() => setNotice("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {showResult ? (
            <ResultPage
              initial={checkout}
              requestPending={requestPending}
              updated={setCheckout}
              fresh={freshPayment}
            />
          ) : view === "create" ? (
            <CreatePayment
              options={options}
              requestPending={requestPending}
              key={creationKey}
              onAbandonError={(message) => setError(message)}
              onSaved={(item) => {
                setCheckout(item);
                setShowResult(true);
              }}
            />
          ) : (
            <>
              <FiltersBar
                view={view}
                currencies={options?.currencies || []}
                filters={filters}
                setFilters={(f) => {
                  setFilters(f);
                  setPage(1);
                }}
                onRefresh={() => setRevision((r) => r + 1)}
                busy={busy}
              />
              {view === "dashboard" ? (
                <Dashboard
                  preferredCurrency={filters.currency}
                  stats={stats}
                  onViewPayments={() => {
                    setView("payments");
                    setPage(1);
                  }}
                />
              ) : (
                <section className="panel">
                  <div className="panel-title">
                    <h2>{labels[view]}</h2>
                    <span>
                      {listing
                        ? `${listing.total} records`
                        : busy
                          ? "Loading records..."
                          : "Records unavailable"}
                    </span>
                    {view === "subscriptions" && (
                      <form
                        onSubmit={(event) => {
                          event.preventDefault();
                          void run("/synchronizations", {
                            scope: "subscriptions",
                            page: subscriptionImportPage,
                          });
                        }}
                      >
                        <label>
                          Import page
                          <input
                            type="number"
                            min="1"
                            step="1"
                            required
                            value={subscriptionImportPage}
                            onChange={(event) =>
                              setSubscriptionImportPage(
                                Number(event.target.value),
                              )
                            }
                          />
                        </label>
                        <button className="secondary" disabled={busy}>
                          <RefreshCw size={14} />
                          Import subscriptions
                        </button>
                      </form>
                    )}
                  </div>
                  {listing ? (
                    <>
                      <Records
                        items={listing.items}
                        view={view}
                        open={(row) => {
                          setDetailLoading(true);
                          setDetailPaging({
                            attempt_page: 1,
                            attempt_page_size: 20,
                            history_page: 1,
                            history_page_size: 20,
                          });
                          setDetailType(view);
                          setDetail(row);
                        }}
                        busy={busy}
                        refund={setRefundItem}
                        updated={() => setRevision((revision) => revision + 1)}
                      />
                      <Pagination
                        page={listing.page}
                        pageSize={listing.page_size}
                        total={listing.total}
                        label={labels[view]}
                        disabled={busy}
                        onPageChange={setPage}
                        onPageSizeChange={(size) => {
                          setPageSize(size);
                          setPage(1);
                        }}
                      />
                    </>
                  ) : (
                    <Empty
                      title={
                        busy ? "Loading records..." : "Records unavailable"
                      }
                      description={
                        busy
                          ? "Reading saved records."
                          : "Use Refresh records to try again."
                      }
                    />
                  )}
                </section>
              )}
            </>
          )}
        </main>
      </div>
      {refundItem && (
        <Refund
          item={refundItem}
          close={() => setRefundItem(null)}
          updated={() => setRevision((r) => r + 1)}
        />
      )}
      {detail && (
        <Details
          key={detail.id}
          item={detail}
          loading={detailLoading}
          loadError={detailError}
          onPageChange={(kind, nextPage) =>
            setDetailPaging((current) => ({
              ...current,
              [kind === "attempts" ? "attempt_page" : "history_page"]: nextPage,
            }))
          }
          onPageSizeChange={(kind, size) =>
            setDetailPaging((current) => ({
              ...current,
              [kind === "attempts" ? "attempt_page" : "history_page"]: 1,
              [kind === "attempts" ? "attempt_page_size" : "history_page_size"]:
                size,
            }))
          }
          type={detailType}
          busy={busy}
          requestError={error}
          close={() => setDetail(null)}
          run={run}
          updated={() => setRevision((r) => r + 1)}
        />
      )}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
