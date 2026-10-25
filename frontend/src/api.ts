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
/** Keep new-payment controls locked until all open API responses are consumed. */
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
/** Narrow unknown fetch/SDK errors without treating arbitrary payloads as messages. */
export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return "Unexpected error. Inspect the saved result before taking another action.";
}
export function money(
  value: string | null | undefined,
  currency: string | null,
) {
  // Number conversion is display-only; authoritative financial arithmetic uses Decimal.
  return value == null
    ? "Not available"
    : currency == null
      ? `${value} (currency unavailable)`
      : new Intl.NumberFormat("en", { style: "currency", currency }).format(
          Number(value),
        );
}
export function dateLabel(value?: string | null) {
  return value
    ? new Intl.DateTimeFormat("en-GB", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "\u2014";
}
