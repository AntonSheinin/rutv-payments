import { useRef, useState } from "react";
import { api, CheckoutResult } from "./api";

export function CancelAttempt({
  requestId,
  attemptId,
  updated,
  disabled = false,
}: {
  requestId: string;
  attemptId: string;
  updated: (result: CheckoutResult) => void;
  disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const sent = useRef(false);
  return (
    <div
      className="attempt-cancellation"
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
              `/payments/${requestId}/attempts/${attemptId}/cancel`,
              "POST",
            );
            updated(result);
          } catch (error) {
            setError(
              (error as Error).message +
                " No automatic retry was made. Refresh the saved payment before taking another action.",
            );
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Cancelling attempt…" : "Cancel payment attempt"}
      </button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
