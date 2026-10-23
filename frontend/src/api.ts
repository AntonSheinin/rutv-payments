export interface User {
  name: string;
  csrf: string;
}
export interface Options {
  currencies: string[];
  period_limits: Record<string, number>;
  environment: string;
  paypal_configured: boolean;
  paypal_client_id: string;
  maximum_amount: string;
}
export interface Entity {
  checkout_status?: "success" | "failed" | null;
  id: string;
  provider_id?: string;
  client_name?: string;
  agreement_number?: string;
  can_refund?: boolean;
  collection_id?: string | null;
  refundable_amount?: string | null;
  refunded_amount?: string;
  refund_deadline?: string;
  status?: string;
  can_cancel?: boolean;
  cancellable_attempt_id?: string | null;
  provider_status?: string;
  amount?: string;
  price_per_period?: string;
  currency?: string;
  period_unit?: string;
  period_count?: number;
  is_subscription?: boolean;
  payment_cycles?: number | null;
  comments?: string;
  effective_at?: string;
  created_at?: string;
  last_synced_at?: string;
  next_billing_at?: string;
  outstanding?: string;
  payer_name?: string;
  payer_email?: string;
  kind?: string;
  fee?: string | null;
  net?: string | null;
  origin?: string;
  url?: string;
  payment_id?: string;
  subscription_id?: string;
  charged_payments?: number;
  remaining_payments?: number | null;
  payments_unlimited?: boolean;
  api_family?: string;
  transactions?: Page;
  adjustments?: Entity[];
  attempts?: Page;
  terms?: PaymentTerms;
  error?: string;
  metadata_json?: {
    frequency?: { interval_count: number; interval_unit: string };
    total_cycles?: number | null;
    failed_payments_count?: number;
  };
  lifetime_totals?: { currency: string; kind: string; amount: string }[];
}
export interface PaymentTerms {
  first_name: string;
  last_name: string;
  agreement_number: string;
  price_per_period: string;
  currency: string;
  period_unit: string;
  period_count: number;
  is_subscription: boolean;
  payment_cycles: number | null;
  comments: string;
}
export interface CheckoutResult {
  checkout_status: "success" | "failed" | null;
  id: string;
  attempt_id: string;
  provider_id: string | null;
  status: string;
  browser_state: string;
  terms: PaymentTerms & { client_name: string };
  proof: string | null;
  can_cancel: boolean;
  error?: string;
}
export interface SyncResult {
  processed: number;
  excluded: number;
  next_page: number | null;
  coverage_note: string;
}
export interface Page {
  items: Entity[];
  total: number;
  page: number;
  page_size: number;
}
export interface Stats {
  currencies: {
    currency: string;
    collected: string;
    refunds: string;
    reversals: string;
    net_before_fees: string;
    net_after_fees: string | null;
    known_fees: string;
    fees_complete: boolean;
    completed_count: number;
    pending_count: number;
    failed_count: number;
  }[];
  trends: { date: string; currency: string; kind: string; amount: string }[];
  current_subscriptions: Record<string, number>;
  scope: string;
  timezone: string;
  environment: string;
  coverage: {
    scope: string;
    last_success?: string;
  }[];
}

let csrf = "";
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export function setCsrf(value: string) {
  csrf = value;
}
let pendingRequests = 0;
const requestListeners = new Set<() => void>();
/** Keep new-attempt controls locked until all open API responses are consumed. */
export function requestsPending() {
  return pendingRequests > 0;
}
export function subscribeRequests(listener: () => void) {
  requestListeners.add(listener);
  return () => {
    requestListeners.delete(listener);
  };
}
function requestActivity(change: number) {
  pendingRequests += change;
  requestListeners.forEach((listener) => listener());
}
/** Call the private API with session/CSRF credentials; preserve HTTP status for callers. */
export async function api<T>(
  path: string,
  method = "GET",
  data?: unknown,
  key?: string,
): Promise<T> {
  requestActivity(1);
  try {
    const response = await fetch("/api/v1" + path, {
      method,
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        ...(csrf ? { "X-CSRF-Token": csrf } : {}),
        ...(key ? { "Idempotency-Key": key } : {}),
      },
      ...(data !== undefined ? { body: JSON.stringify(data) } : {}),
    });
    const result = await response.json().catch((error: unknown) => {
      if (error instanceof SyntaxError) {
        throw new ApiError(
          response.status,
          `Invalid server response (${response.status})`,
        );
      }
      throw error;
    });
    if (!response.ok) {
      const message = Array.isArray(result.detail)
        ? result.detail.map((e: { msg: string }) => e.msg).join("; ")
        : result.detail;
      throw new ApiError(
        response.status,
        message || `Request failed (${response.status})`,
      );
    }
    return result as T;
  } finally {
    requestActivity(-1);
  }
}
export function money(value?: string | null, currency = "ILS") {
  // Number conversion is display-only; authoritative financial arithmetic uses Decimal.
  return value == null
    ? "Not available"
    : new Intl.NumberFormat("en", { style: "currency", currency }).format(
        Number(value),
      );
}
export function localTimezone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}
export function dateLabel(value?: string | null) {
  return value
    ? new Intl.DateTimeFormat("en-GB", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "\u2014";
}
