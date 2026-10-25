import { errorMessage } from "./api";
import { useEffect, useState } from "react";
import { api } from "./api";
import type { CheckoutResult as Result } from "./types";
import { closeCheckout, storedSubmission } from "./checkout";
import { Badge } from "./ui";

/** Initial redirect callbacks may complete once; recorded-page reloads are read-only. */
export function CheckoutResult({
  initial,
  requestPending,
  fresh,
  updated,
}: {
  initial?: Result;
  requestPending: boolean;
  fresh: () => void;
  updated: (result: Result) => void;
}) {
  const [result, setResult] = useState<Result | null>(initial || null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(!initial);
  async function load() {
    setBusy(true);
    setError("");
    try {
      const state = new URLSearchParams(location.search).get("state");
      const identity = state ? null : storedSubmission();
      if (!state && !identity)
        throw new Error("No checkout submission is selected.");
      let item = await api<Result>(
        state
          ? "/checkout-return?state=" + encodeURIComponent(state)
          : "/checkout-submissions/" + encodeURIComponent(identity!.intent),
      );
      if (
        state &&
        item.checkout_status === null &&
        item.proof &&
        item.browser_state === "open"
      ) {
        const params = new URLSearchParams(location.search);
        const outcome =
          location.pathname === "/paypal/success"
            ? "approved"
            : params.get("outcome") === "error"
              ? "error"
              : "cancelled";
        item = await api<Result>(
          `/payments/${item.id}/checkout-outcome`,
          "POST",
          {
            proof: state,
            outcome,
            provider_id:
              (item.terms.is_subscription
                ? params.get("subscription_id")
                : params.get("token")) || item.provider_id,
          },
        );
      }
      if (item.checkout_status === null) item = await closeCheckout(item);
      setResult(item);
      updated(item);
      // Remove callback secrets from the visible URL after the initial dispatch.
      history.replaceState(null, "", location.pathname);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (!initial) void load();
  }, []);
  return (
    <section className="panel result-panel">
      <h2>Payment outcome</h2>
      {busy ? (
        <p>Verifying the saved payment…</p>
      ) : (
        result && (
          <>
            {result.checkout_status !== null && (
              <Badge status={result.checkout_status} />
            )}
            <p>
              {result.terms.client_name} · Agreement{" "}
              {result.terms.agreement_number}
            </p>
            <p>{result.error}</p>
            <p>
              {result.checkout_status === "success"
                ? "PayPal approval was received."
                : "Checkout was unsuccessful. This checkout is closed; start a new payment to try again."}
            </p>
          </>
        )
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button
        className="primary"
        disabled={busy || requestPending}
        onClick={fresh}
      >
        Create a new payment
      </button>
    </section>
  );
}
