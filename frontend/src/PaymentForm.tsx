import { SubscriptionCancellationNotice } from "./ui";
import { CreditCard, ShieldCheck, Wallet } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  CheckoutResult,
  money,
  Options,
  PaymentTerms,
  requestsPending,
} from "./api";
import {
  closeCheckout,
  checkoutSdk,
  CheckoutSdk,
  storedSubmission,
  submissionStorage,
} from "./checkout";
import { CancelAttempt } from "./CancelAttempt";

const empty: PaymentTerms = {
  first_name: "",
  last_name: "",
  agreement_number: "",
  price_per_period: "",
  currency: "ILS",
  period_unit: "MONTH",
  period_count: 1,
  is_subscription: false,
  payment_cycles: 1,
  comments: "",
};
export function CreatePayment({
  options,
  requestPending,
  onAbandonError,
  onSaved,
}: {
  options: Options | null;
  requestPending: boolean;
  onAbandonError: (message: string) => void;
  onSaved: (item: CheckoutResult) => void;
}) {
  const [form, setForm] = useState<PaymentTerms>(empty),
    [unlimited, setUnlimited] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [ready, setReady] = useState(false),
    [checkoutAttempt, setCheckoutAttempt] = useState<CheckoutResult | null>(
      null,
    );
  const formRef = useRef<HTMLFormElement>(null),
    cardContainer = useRef<HTMLDivElement>(null),
    cardRef = useRef<HTMLElement | null>(null),
    sdkRef = useRef<CheckoutSdk | null>(null),
    active = useRef(false),
    resultRef = useRef<CheckoutResult | null>(null);
  const endSession = useRef<(() => void) | null>(null);
  const abandoned = useRef(false);
  useEffect(
    () => () => {
      abandoned.current = true;
      endSession.current?.();
      const item = resultRef.current;
      if (item?.checkout_status === null)
        void closeCheckout(item).catch((error) =>
          onAbandonError(error.message),
        );
    },
    [],
  );
  const values = useRef(form);
  values.current = form;
  const unlimitedRef = useRef(unlimited);
  unlimitedRef.current = unlimited;
  useEffect(() => {
    if (!options?.paypal_configured) return;
    let current = true;
    setReady(false);
    sdkRef.current = null;
    checkoutSdk(options.paypal_client_id, options.environment)
      .then(async (sdk) => {
        const methods = await sdk.findEligibleMethods({
          currencyCode: form.currency,
          paymentFlow: form.is_subscription
            ? "RECURRING_PAYMENT"
            : "ONE_TIME_PAYMENT",
        });
        if (!current) return;
        if (!methods.isEligible(form.is_subscription ? "paypal" : "card"))
          throw new Error(
            "PayPal checkout is unavailable for this payment type, currency or account.",
          );
        sdkRef.current = sdk;
        if (form.is_subscription) {
          setReady(true);
          return;
        }
        const container = document.createElement("paypal-basic-card-container");
        const button = document.createElement("paypal-basic-card-button");
        button.setAttribute("buyer-country", "IL");
        button.setAttribute("aria-label", "Submit payment");
        button.addEventListener("click", () => {
          void startCheckout(button);
        });
        container.append(button);
        cardContainer.current?.replaceChildren(container);
        cardRef.current = button;
        setReady(true);
      })
      .catch((e) => {
        if (current) setError(e.message);
      });
    return () => {
      current = false;
      cardRef.current = null;
      cardContainer.current?.replaceChildren();
    };
  }, [options, form.currency, form.is_subscription]);
  function payload() {
    return {
      first_name: values.current.first_name,
      last_name: values.current.last_name,
      agreement_number: values.current.agreement_number,
      price_per_period: values.current.price_per_period,
      currency: values.current.currency,
      period_unit: values.current.period_unit,
      period_count: values.current.period_count,
      is_subscription: values.current.is_subscription,
      comments: values.current.comments,
      payment_cycles: values.current.is_subscription
        ? unlimitedRef.current
          ? null
          : values.current.payment_cycles
        : null,
    };
  }
  function identity() {
    const value = {
      intent: crypto.randomUUID(),
      key: crypto.randomUUID(),
    };
    sessionStorage.setItem(submissionStorage, JSON.stringify(value));
    return value;
  }
  function leave(outcome: "cancelled" | "error") {
    if (abandoned.current) return;
    const item = resultRef.current;
    const params = new URLSearchParams();
    if (item?.proof) {
      params.set("state", item.proof);
      params.set("outcome", outcome);
    }
    window.location.assign("/paypal/fail" + (params.size ? "?" + params : ""));
  }
  async function create() {
    const id = identity();
    const item = await api<CheckoutResult>(
      "/payments",
      "POST",
      { ...payload(), intent_key: id.intent },
      id.key,
    );
    resultRef.current = item;
    if (abandoned.current) {
      await closeCheckout(item);
      throw new Error("Checkout closed before completion");
    }
    setCheckoutAttempt(item);
    if (item.status !== "awaiting_approval" || !item.provider_id) {
      const closed = await closeCheckout(item);
      if (!abandoned.current) onSaved(closed);
      throw new Error(item.error || "Checkout creation did not succeed");
    }
    return item;
  }
  async function creationFailed(error: unknown) {
    if (abandoned.current) return;
    setError((error as Error).message);
    if (resultRef.current) {
      const closed = await closeCheckout(resultRef.current);
      if (!abandoned.current) onSaved(closed);
      return;
    }
    const saved = storedSubmission();
    // Validation/auth rejection occurs before an attempt exists. Keep the form editable.
    if (
      error instanceof ApiError &&
      [400, 401, 403, 422].includes(error.status)
    ) {
      sessionStorage.removeItem(submissionStorage);
    } else if (saved) {
      try {
        const closed = await closeCheckout(
          await api<CheckoutResult>(
            "/checkout-submissions/" + encodeURIComponent(saved.intent),
          ),
        );
        if (!abandoned.current) onSaved(closed);
        return;
      } catch (lookupError) {
        if (abandoned.current) return;
        // A completed error response plus an absent durable submission is safe to correct.
        if (
          error instanceof ApiError &&
          error.status >= 400 &&
          lookupError instanceof ApiError &&
          lookupError.status === 404
        ) {
          sessionStorage.removeItem(submissionStorage);
        } else {
          leave("error");
          return;
        }
      }
    }
    active.current = false;
    setBusy(false);
  }
  async function startCheckout(button?: HTMLElement) {
    if (
      active.current ||
      requestsPending() ||
      !formRef.current?.reportValidity() ||
      !sdkRef.current
    )
      return;
    const recurring = values.current.is_subscription;
    active.current = true;
    setBusy(true);
    setError("");
    let terminal = false;
    let creation: Promise<unknown> | undefined;
    const approve = async (providerId: string) => {
      if (terminal) return;
      terminal = true;
      setCheckoutAttempt(null);
      let destination = "/paypal/fail";
      const state = resultRef.current?.proof;
      try {
        const item = resultRef.current;
        if (!item?.proof) throw new Error("Checkout correlation is missing");
        const result = await api<CheckoutResult>(
          `/payments/${item.id}/checkout-outcome`,
          "POST",
          {
            attempt_id: item.attempt_id,
            proof: item.proof,
            provider_id: providerId,
            outcome: "approved",
          },
        );
        resultRef.current = result;
        if (result.checkout_status === "success")
          destination = "/paypal/success";
      } finally {
        if (!abandoned.current)
          window.location.assign(
            destination + (state ? "?state=" + encodeURIComponent(state) : ""),
          );
      }
    };
    const fail = async (outcome: "cancelled" | "error") => {
      if (!terminal) {
        terminal = true;
        endSession.current?.();
        try {
          // A closed/blocked popup must not abort an outstanding creation request.
          await creation;
        } catch {
          // createForCheckout already displays the recorded creation failure.
          return;
        }
        leave(outcome);
      }
    };
    const callbacks = {
      onComplete: () => {},
      onCancel: () => fail("cancelled"),
      onError: () => fail("error"),
    };
    function rememberSession(session: { destroy(): void }) {
      endSession.current = () => {
        endSession.current = null;
        terminal = true;
        session.destroy();
      };
    }
    async function createForCheckout() {
      try {
        return await create();
      } catch (error) {
        terminal = true;
        endSession.current?.();
        await creationFailed(error);
        throw error;
      }
    }
    try {
      if (recurring) {
        const session = sdkRef.current.createPayPalSubscriptionPaymentSession({
          ...callbacks,
          onApprove: (data) => approve(data.subscriptionId),
        });
        rememberSession(session);
        const subscription = createForCheckout().then((item) => ({
          subscriptionId: item.provider_id!,
        }));
        creation = subscription;
        // Start inside the Submit gesture; await creation inside PayPal's popup.
        // Select popup explicitly: no full-page redirect or application fallback.
        await session.start({ presentationMode: "popup" }, subscription);
      } else {
        if (!button) throw new Error("PayPal card button is missing");
        const session = sdkRef.current.createPayPalGuestOneTimePaymentSession({
          ...callbacks,
          onApprove: (data) => approve(data.orderId),
          onWarn: () => fail("error"),
        });
        rememberSession(session);
        const order = createForCheckout().then((item) => ({
          orderId: item.provider_id!,
        }));
        creation = order;
        await session.start(
          { presentationMode: "auto", targetElement: button },
          order,
        );
      }
    } catch {
      await fail("error");
    }
  }
  return (
    <div className="create-grid">
      <form
        className="panel create-form"
        ref={formRef}
        onSubmit={(e) => {
          e.preventDefault();
          if (form.is_subscription) void startCheckout();
          else cardRef.current?.click();
        }}
      >
        <div className="panel-title">
          <h2>Payment details</h2>
          <CreditCard size={18} />
        </div>
        <fieldset className="form-body" disabled={busy || requestPending}>
          <div className="form-row">
            <label>
              First name
              <input
                value={form.first_name}
                onChange={(e) =>
                  setForm({ ...form, first_name: e.target.value })
                }
                autoComplete="given-name"
                maxLength={140}
                required
              />
            </label>
            <label>
              Last name
              <input
                value={form.last_name}
                onChange={(e) =>
                  setForm({ ...form, last_name: e.target.value })
                }
                autoComplete="family-name"
                maxLength={140}
                required
              />
            </label>
          </div>
          <label>
            Agreement number
            <input
              value={form.agreement_number}
              onChange={(e) =>
                setForm({ ...form, agreement_number: e.target.value })
              }
              maxLength={255}
              required
            />
          </label>
          <div className="form-row">
            <label>
              {form.is_subscription
                ? "Amount per billing interval"
                : "Payment amount"}
              <input
                inputMode="decimal"
                type="number"
                min="0.01"
                step="0.01"
                max={options?.maximum_amount}
                value={form.price_per_period}
                onChange={(e) =>
                  setForm({ ...form, price_per_period: e.target.value })
                }
                placeholder="0.00"
                required
              />
            </label>
            <label>
              Currency
              <select
                value={form.currency}
                onChange={(e) => setForm({ ...form, currency: e.target.value })}
              >
                {(options?.currencies || []).map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="form-row">
            <label>
              Every
              <input
                type="number"
                min="1"
                max={
                  form.is_subscription
                    ? options?.period_limits[form.period_unit]
                    : 36500
                }
                value={form.period_count}
                onChange={(e) =>
                  setForm({ ...form, period_count: Number(e.target.value) })
                }
                required
              />
            </label>
            <label>
              Payment period
              <select
                value={form.period_unit}
                onChange={(e) =>
                  setForm({
                    ...form,
                    period_unit: e.target.value,
                    period_count: 1,
                  })
                }
              >
                {["DAY", "WEEK", "MONTH", "YEAR"].map((u) => (
                  <option key={u} value={u}>
                    {u.toLowerCase()}(s)
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="subscription-toggle">
            <div>
              <strong>Subscription</strong>
              <small>Automatically bill at the selected interval</small>
            </div>
            <input
              aria-label="Is subscription"
              type="checkbox"
              role="switch"
              checked={form.is_subscription}
              onChange={(e) => {
                setForm({
                  ...form,
                  is_subscription: e.target.checked,
                  payment_cycles: 1,
                });
                setUnlimited(false);
              }}
            />
          </label>
          {form.is_subscription && (
            <div className="cycles">
              <label>
                Payment cycles
                <input
                  type="number"
                  min="1"
                  max="999"
                  disabled={unlimited}
                  value={form.payment_cycles ?? 1}
                  onChange={(e) =>
                    setForm({ ...form, payment_cycles: Number(e.target.value) })
                  }
                  required={!unlimited}
                />
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={unlimited}
                  onChange={(e) => setUnlimited(e.target.checked)}
                />
                Continue until canceled
              </label>
              <SubscriptionCancellationNotice />
            </div>
          )}
          <label>
            Comments <span className="muted">(internal only)</span>
            <textarea
              rows={4}
              value={form.comments}
              onChange={(e) => setForm({ ...form, comments: e.target.value })}
              maxLength={4000}
              placeholder="Add context for your teamâ€¦"
            />
          </label>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {form.is_subscription ? (
            <button
              className="primary"
              disabled={busy || requestPending || !ready}
            >
              Submit
            </button>
          ) : (
            <div className="checkout-submit">
              <strong>Submit payment</strong>
              <div ref={cardContainer} aria-busy={busy} />
              <small>
                {ready
                  ? "Submit opens PayPal's secure card form."
                  : "Preparing PayPal checkout?"}
              </small>
            </div>
          )}
        </fieldset>
        {checkoutAttempt?.can_cancel && (
          <div className="attempt-controls">
            <CancelAttempt
              requestId={checkoutAttempt.id}
              attemptId={checkoutAttempt.attempt_id}
              updated={(result) => {
                resultRef.current = result;
                try {
                  endSession.current?.();
                } finally {
                  onSaved(result);
                }
              }}
            />
          </div>
        )}
      </form>
      <aside className="panel payment-preview">
        <span className="eyebrow">PAYMENT SUMMARY</span>
        <div className="preview-icon">
          <Wallet size={27} />
        </div>
        <h2>{money(form.price_per_period || "0", form.currency)}</h2>
        <p>
          {form.is_subscription
            ? `Every ${form.period_count} ${form.period_unit.toLowerCase()}(s)`
            : `One-time payment for ${form.period_count} ${form.period_unit.toLowerCase()}(s)`}
        </p>
        <hr />
        <dl>
          <dt>Payment type</dt>
          <dd>{form.is_subscription ? "Subscription" : "One-time"}</dd>
          <dt>Duration</dt>
          <dd>
            {form.is_subscription
              ? unlimited
                ? "Until canceled"
                : `${form.payment_cycles} billing cycle(s)`
              : "One charge"}
          </dd>
          <dt>Client name</dt>
          <dd>{`${form.first_name} ${form.last_name}`.trim() || "â€”"}</dd>
        </dl>
        <div className="safe-note">
          <ShieldCheck size={19} />
          <p>
            Checkout happens on PayPal. Card details never pass through this
            service.
          </p>
        </div>
        <small>Review the terms above. Submit opens PayPal directly.</small>
      </aside>
    </div>
  );
}
