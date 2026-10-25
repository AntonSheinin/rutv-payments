import { useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import type {
  User,
  Payment,
  Subscription,
  PaymentDetail,
  SubscriptionDetail,
  ResourceView,
  Page,
  WorkspaceRecord,
  Stats,
  SyncInput,
  SyncResult,
  CancellationResult,
} from "./types";
import { emptyFilters, query } from "./ui";
import type { SearchState, View } from "./ui";

/** Local read/action state with bounded pagination and stale-response suppression. */
export function useWorkspace(user: User | null, view: View) {
  const [filters, setFilters] = useState<SearchState>(emptyFilters),
    [page, setPage] = useState(1),
    [pageSize, setPageSize] = useState(20),
    [listing, setListing] = useState<Page<Payment | Subscription> | null>(null),
    [stats, setStats] = useState<Stats | null>(null),
    [detail, setDetail] = useState<WorkspaceRecord | null>(null),
    [detailType, setDetailType] = useState<ResourceView>("payments");
  const [subscriptionImportPage, setSubscriptionImportPage] = useState(1);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [revision, setRevision] = useState(0);
  const [detailPaging, setDetailPaging] = useState({
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
      Object.entries(detailPaging).map(([key, value]) => [key, String(value)]),
    );
    api<PaymentDetail | SubscriptionDetail>(
      "/" + detailType + "/" + detailId + "?" + params,
    )
      .then((item) => {
        if (!active) return;
        const next = { ...detailPaging };
        if ("transactions" in item)
          next.history_page = Math.min(
            next.history_page,
            Math.max(
              1,
              Math.ceil(item.transactions.total / item.transactions.page_size),
            ),
          );
        if (next.history_page !== detailPaging.history_page) {
          setDetailPaging(next);
          return;
        }
        setDetail(item);
      })
      .catch((e) => {
        if (active) setDetailError(errorMessage(e));
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
        : api<Page<Payment | Subscription>>("/" + view + suffix).then((s) => {
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
        if (active) setError(errorMessage(e));
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [user, view, filters, page, pageSize, revision]);
  async function synchronize(data: SyncInput) {
    if (busy) return false;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<SyncResult>("/synchronizations", "POST", data);
      if (
        !Number.isInteger(result.processed) ||
        result.processed < 0 ||
        !Number.isInteger(result.excluded) ||
        result.excluded < 0 ||
        typeof result.coverage_note !== "string" ||
        !(
          result.next_page === null ||
          (Number.isInteger(result.next_page) && result.next_page > 0)
        )
      ) {
        throw new Error(
          "Invalid synchronization response; result is unconfirmed.",
        );
      }
      setRevision((r) => r + 1);
      return true;
    } catch (error) {
      setError(errorMessage(error));
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function cancelSubscription(id: string, reason: string) {
    if (busy) return false;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<CancellationResult>(
        `/subscriptions/${id}/cancel`,
        "POST",
        { reason },
        crypto.randomUUID(),
      );
      if (!["completed", "failed"].includes(result.status))
        throw new Error("Invalid action response; result is unconfirmed.");
      setNotice(`Result: ${result.status}`);
      if (result.error) setError(result.error);
      setRevision((r) => r + 1);
      return result.status === "completed";
    } catch (error) {
      setError(errorMessage(error));
      return false;
    } finally {
      setBusy(false);
    }
  }

  return {
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
    subscriptionImportPage,
    setSubscriptionImportPage,
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
  };
}
