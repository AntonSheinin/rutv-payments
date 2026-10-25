import { useWorkspace } from "./useWorkspace";
import { errorMessage } from "./api";
import type { Payment } from "./types";
import { Menu, RefreshCw, ShieldCheck, X } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";
import {
  api,
  ApiError,
  setCsrf,
  requestsPending,
  subscribeRequests,
} from "./api";
import type { User } from "./types";
import { CheckoutResult as ResultPage } from "./CheckoutResult";
import type { CheckoutResult } from "./types";
import { submissionStorage } from "./checkout";
import { Refund } from "./Refund";
import { Dashboard } from "./Dashboard";
import { Pagination } from "./Pagination";
import { Details } from "./Details";
import { FiltersBar } from "./FiltersBar";
import { Login } from "./Login";
import { CreatePayment } from "./PaymentForm";
import { Empty, Records } from "./Records";
import { Navigation } from "./Navigation";
import { dashboardFilters, emptyFilters, labels, View } from "./ui";
export function App() {
  const requestPending = useSyncExternalStore(
    subscribeRequests,
    requestsPending,
  );
  const [user, setUser] = useState<User | null>(null),
    [loading, setLoading] = useState(true),
    [view, setView] = useState<View>("dashboard");
  const [refundItem, setRefundItem] = useState<Payment | null>(null);
  const {
    filters,
    setFilters,
    setPage,
    setPageSize,
    listing,
    setListing,
    stats,
    detail,
    setDetail,
    detailType,
    setDetailType,
    error,
    setError,
    notice,
    setNotice,
    busy,
    setRevision,
    setDetailPaging,
    detailLoading,
    setDetailLoading,
    detailError,
    synchronize,
    cancelSubscription,
    balanceSyncing,
    synchronizeBalance,
  } = useWorkspace(user, view);
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
  useEffect(() => {
    api<User>("/auth/me")
      .then((u) => {
        setCsrf(u.csrf);
        setUser(u);
      })
      .catch((e: unknown) => {
        if (!(e instanceof ApiError && e.status === 401)) {
          setError(errorMessage(e));
        }
      })
      .finally(() => setLoading(false));
  }, []);
  async function logout() {
    try {
      await api("/auth/logout", "POST");
    } catch (error) {
      setError(errorMessage(error));
      return;
    }
    setCsrf("");
    setUser(null);
    setMobileMenuOpen(false);
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
    history.replaceState(null, "", "/");
    setShowResult(false);
    setView(next);
    setPage(1);
    setFilters(next === "dashboard" ? dashboardFilters() : emptyFilters);
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
  const options = user.options;
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
      <Navigation
        userName={user.name}
        view={view}
        mobileMenuOpen={mobileMenuOpen}
        requestPending={requestPending}
        navigate={navigate}
        closeMenu={() => setMobileMenuOpen(false)}
        logout={logout}
      />
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
              <span className={"environment " + options.environment}>
                {options.environment}
              </span>
              {view === "dashboard" && !showResult && (
                <button
                  className="secondary"
                  disabled={
                    requestPending ||
                    balanceSyncing ||
                    !options.paypal_configured
                  }
                  onClick={() => void synchronizeBalance()}
                >
                  <RefreshCw
                    size={17}
                    className={balanceSyncing ? "spin" : ""}
                  />
                  {balanceSyncing ? "Syncing balance…" : "Sync PayPal balance"}
                </button>
              )}
              {view === "subscriptions" && !showResult && (
                <button
                  className="secondary"
                  disabled={
                    busy || requestPending || !options.paypal_configured
                  }
                  onClick={() => void synchronize({ scope: "subscriptions" })}
                >
                  <RefreshCw size={17} className={busy ? "spin" : ""} />
                  Import subscriptions
                </button>
              )}
              <button
                className="primary"
                disabled={requestPending}
                onClick={() => navigate("create")}
              >
                Create payment
              </button>
            </div>
          </div>
          {!options.paypal_configured && (
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
              onAbandonError={setError}
              onSaved={(item) => {
                setCheckout(item);
                setShowResult(true);
              }}
            />
          ) : (
            <>
              <FiltersBar
                view={view}
                currencies={options.currencies}
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
                  </div>
                  {listing ? (
                    <>
                      <Records
                        items={listing.items}
                        view={view}
                        open={(row) => {
                          setDetailLoading(true);
                          setDetailPaging({
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
          onPageChange={(nextPage) =>
            setDetailPaging((current) => ({
              ...current,
              history_page: nextPage,
            }))
          }
          onPageSizeChange={(size) =>
            setDetailPaging((current) => ({
              ...current,
              history_page: 1,
              history_page_size: size,
            }))
          }
          type={detailType}
          busy={busy}
          requestError={error}
          close={() => setDetail(null)}
          synchronize={synchronize}
          cancelSubscription={cancelSubscription}
          updated={() => setRevision((r) => r + 1)}
        />
      )}
    </div>
  );
}
