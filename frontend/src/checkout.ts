import { api, CheckoutResult } from "./api";
/** PayPal controls both checkout presentations; neither flow retries automatically. */
export interface GuestSession {
  destroy(): void;
  start(
    options: { presentationMode: "auto"; targetElement: HTMLElement },
    order: Promise<{ orderId: string }>,
  ): Promise<void>;
}
interface Methods {
  isEligible(method: "card" | "paypal"): boolean;
}
interface SessionCallbacks {
  onComplete: () => void;
  onCancel: () => void;
  onError: () => void;
}
interface SubscriptionSession {
  destroy(): void;
  start(
    options: { presentationMode: "popup" },
    subscription: Promise<{ subscriptionId: string }>,
  ): Promise<void | { redirectURL?: string }>;
}
export interface CheckoutSdk {
  findEligibleMethods(options: {
    currencyCode: string;
    paymentFlow: "ONE_TIME_PAYMENT" | "RECURRING_PAYMENT";
  }): Promise<Methods>;
  createPayPalGuestOneTimePaymentSession(
    callbacks: {
      onApprove: (data: { orderId: string }) => Promise<void>;
      onWarn: () => void;
    } & SessionCallbacks,
  ): GuestSession;
  createPayPalSubscriptionPaymentSession(
    callbacks: {
      onApprove: (data: { subscriptionId: string }) => Promise<void>;
    } & SessionCallbacks,
  ): SubscriptionSession;
}
declare global {
  interface Window {
    paypal?: {
      createInstance(options: {
        clientId: string;
        components: string[];
        pageType: string;
        locale: string;
      }): Promise<CheckoutSdk>;
    };
  }
}
let initialized: Promise<CheckoutSdk> | undefined;
export function checkoutSdk(
  clientId: string,
  environment: string,
): Promise<CheckoutSdk> {
  if (!initialized)
    initialized = new Promise<void>((resolve, reject) => {
      const script = document.createElement("script");
      script.src =
        environment === "sandbox"
          ? "https://www.sandbox.paypal.com/web-sdk/v6/core"
          : "https://www.paypal.com/web-sdk/v6/core";
      script.onload = () => resolve();
      script.onerror = () =>
        reject(new Error("PayPal could not load. No payment was submitted."));
      document.head.append(script);
    }).then(() => {
      if (!window.paypal) throw new Error("PayPal SDK is unavailable");
      return window.paypal.createInstance({
        clientId,
        components: ["paypal-guest-payments", "paypal-subscriptions"],
        pageType: "checkout",
        locale: "en-IL",
      });
    });
  return initialized;
}

export const submissionStorage = "payments.current-submission";
export interface SubmissionIdentity {
  intent: string;
  key: string;
}
export function storedSubmission(): SubmissionIdentity | null {
  const value = sessionStorage.getItem(submissionStorage);
  if (!value) return null;
  const parsed: unknown = JSON.parse(value);
  if (
    typeof parsed !== "object" ||
    parsed === null ||
    !("intent" in parsed) ||
    !("key" in parsed) ||
    typeof parsed.intent !== "string" ||
    typeof parsed.key !== "string"
  )
    throw new Error("Invalid saved submission identity");
  return { intent: parsed.intent, key: parsed.key };
}

export async function closeCheckout(
  item: CheckoutResult,
): Promise<CheckoutResult> {
  return item.checkout_status === null
    ? api<CheckoutResult>(
        `/payments/${item.id}/attempts/${item.attempt_id}/close`,
        "POST",
      )
    : item;
}
