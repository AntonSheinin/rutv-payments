export type View = "dashboard" | "payments" | "subscriptions" | "create";
export type SearchState = {
  q: string;
  currency: string;
  status: string;
  checkout_status: string;
  start_date: string;
  end_date: string;
  origin: string;
  kind: string;
  min_amount: string;
  max_amount: string;
  is_subscription: string;
};
export const emptyFilters: SearchState = {
  q: "",
  currency: "",
  status: "",
  checkout_status: "",
  start_date: "",
  end_date: "",
  origin: "",
  kind: "",
  min_amount: "",
  max_amount: "",
  is_subscription: "",
};
export const labels: Record<View, string> = {
  dashboard: "Dashboard",
  payments: "Payments",
  subscriptions: "Subscriptions",
  create: "Create payment",
};
export function query(filters: SearchState, page = 1, pageSize = 20) {
  const q = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  });
  Object.entries(filters).forEach(([k, v]) => {
    if (v) q.set(k, v);
  });
  return "?" + q.toString();
}
export function Badge({ status }: { status: string }) {
  return (
    <span className={"badge " + status.toLowerCase()}>
      {status.replaceAll("_", " ")}
    </span>
  );
}

export function SubscriptionCancellationNotice() {
  return (
    <p className="muted">
      PayPal allows subscription cancellation only while Active or Suspended.
      Closing checkout does not confirm cancellation.
    </p>
  );
}
