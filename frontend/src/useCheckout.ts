import { useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, requestsPending } from "./api";
import type { CheckoutResult, Options, PaymentTerms } from "./types";
import {
  closeCheckout,
  checkoutSdk,
  storedSubmission,
  submissionStorage,
} from "./checkout";
import type { CheckoutSdk } from "./checkout";

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
/** Own one checkout lifecycle; no effect starts or replays provider financial work. */
export function useCheckout(
  options: Options,
  onSaved: (item: CheckoutResult) => void,
  onAbandonError: (message: string) => void,
) {
  const [form, setForm] = useState<PaymentTerms>(empty),
    [unlimited, setUnlimited] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [ready, setReady] = useState(false),
    [checkoutPayment, setCheckoutPayment] = useState<CheckoutResult | null>(
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
  useEffect(() => {
    abandoned.current = false;
    return () => {
      abandoned.current = true;
      endSession.current?.();
      const item = resultRef.current;
      if (item?.checkout_status === null)
        void closeCheckout(item).catch((error) =>
          onAbandonError(errorMessage(error)),
        );
    };
  }, []);
  const values = useRef(form);
  values.current = form;
  const unlimitedRef = useRef(unlimited);
  unlimitedRef.current = unlimited;
  useEffect(() => {
    if (!options.paypal_configured) return;
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
        if (current) setError(errorMessage(e));
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
    const intent = crypto.randomUUID();
    sessionStorage.setItem(submissionStorage, JSON.stringify({ intent }));
    const item = await api<CheckoutResult>(
      "/payments",
      "POST",
      { ...payload(), intent_key: intent },
      crypto.randomUUID(),
    );
    resultRef.current = item;
    if (abandoned.current) {
      await closeCheckout(item);
      throw new Error("Checkout closed before completion");
    }
    setCheckoutPayment(item);
    if (!item.proof || !item.provider_id || item.checkout_status !== null) {
      const closed = await closeCheckout(item);
      if (!abandoned.current) onSaved(closed);
      throw new Error(item.error || "Checkout creation did not succeed");
    }
    return item;
  }
  async function creationFailed(error: unknown) {
    if (abandoned.current) return;
    setError(errorMessage(error));
    if (resultRef.current) {
      const closed = await closeCheckout(resultRef.current);
      if (!abandoned.current) onSaved(closed);
      return;
    }
    const saved = storedSubmission();
    // Validation/auth rejection occurs before a payment exists. Keep the form editable.
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
      setCheckoutPayment(null);
      let destination = "/paypal/fail";
      const state = resultRef.current?.proof;
      try {
        const item = resultRef.current;
        if (!item?.proof) throw new Error("Checkout correlation is missing");
        const result = await api<CheckoutResult>(
          `/payments/${item.id}/checkout-outcome`,
          "POST",
          {
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
        } catch (error: unknown) {
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
    } catch (error: unknown) {
      if (!terminal && !abandoned.current) setError(errorMessage(error));
      await fail("error");
    }
  }
  function closed(result: CheckoutResult) {
    resultRef.current = result;
    try {
      endSession.current?.();
    } finally {
      if (!abandoned.current) onSaved(result);
    }
  }
  return {
    form,
    setForm,
    unlimited,
    setUnlimited,
    busy,
    error,
    ready,
    checkoutPayment,
    formRef,
    cardContainer,
    cardRef,
    startCheckout,
    closed,
  };
}
