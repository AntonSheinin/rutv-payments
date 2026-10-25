import { errorMessage } from "./api";
import { useRef, useState } from "react";
import { api } from "./api";
import type { CheckoutResult } from "./types";
export function CloseCheckout({
  paymentId,
  updated,
  disabled = false,
}: {
  paymentId: string;
  updated: (result: CheckoutResult) => void;
  disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const sent = useRef(false);
  return (
    <div
      className="checkout-cancellation"
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
    >
      <button
        type="button"
        className="secondary"
        disabled={disabled || busy || sent.current}
        onClick={async () => {
          if (sent.current) return;
          sent.current = true;
          setBusy(true);
          setError("");
          try {
            const result = await api<CheckoutResult>(
              `/payments/${paymentId}/close`,
              "POST",
            );
            updated(result);
          } catch (error) {
            setError(
              errorMessage(error) +
                " No automatic retry was made. Refresh the saved payment before taking another action.",
            );
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Closing checkout…" : "Close checkout"}
      </button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
