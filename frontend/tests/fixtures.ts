import { test as base, expect, Page, BrowserContext } from "@playwright/test";
const credentials = {
  username: process.env.E2E_USERNAME,
  password: process.env.E2E_PASSWORD,
};
if (!credentials.username || !credentials.password)
  throw new Error(
    "Set E2E_USERNAME and E2E_PASSWORD to a disposable test login.",
  );
type StaffCookies = Awaited<ReturnType<BrowserContext["cookies"]>>;
/** Worker-owned staff login, with a fresh authenticated page for every scenario. */
export const test = base.extend<{}, { staffCookies: StaffCookies }>({
  staffCookies: [
    async ({ browser }, use) => {
      const context = await browser.newContext();
      try {
        const baseURL = process.env.E2E_BASE_URL || "http://localhost:8100";
        const response = await context.request.post(
          baseURL + "/api/v1/auth/login",
          {
            headers: { Origin: baseURL },
            data: credentials,
          },
        );
        expect(response.ok()).toBeTruthy();
        await use(await context.cookies());
      } finally {
        await context.close();
      }
    },
    { scope: "worker" },
  ],
  page: async ({ page, context, staffCookies }, use) => {
    await context.addCookies(staffCookies);
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "Dashboard", exact: true }),
    ).toBeVisible();
    await use(page);
  },
});
export { expect };

/** Mock provider/browser contracts only: no test sends an actual checkout/refund to PayPal. */
export async function checkout(
  page: Page,
  mode: "approved" | "cancelled" | "error" | "waiting" | "blocked" = "approved",
) {
  const calls: {
    creates: { key: string | null; body: Record<string, unknown> }[];
    outcomes: string[];
    sdkStarts: number;
    subscriptionStarts: { presentationMode: string }[];
    eligibilityFlows: string[];
    closures: number;
    sdkDestroyed: number;
  } = {
    creates: [],
    outcomes: [],
    sdkStarts: 0,
    subscriptionStarts: [],
    eligibilityFlows: [],
    closures: 0,
    sdkDestroyed: 0,
  };
  let current: Record<string, unknown>;
  await page.exposeFunction("recordSdkStart", () => {
    calls.sdkStarts++;
  });
  await page.exposeFunction(
    "recordSubscriptionStart",
    (options: { presentationMode: string }) => {
      calls.subscriptionStarts.push(options);
    },
  );
  await page.exposeFunction(
    "recordSdkEligibility",
    (options: { paymentFlow: string }) => {
      calls.eligibilityFlows.push(options.paymentFlow);
    },
  );
  await page.exposeFunction("recordSdkDestroy", () => {
    calls.sdkDestroyed++;
  });
  await page.route("https://www.sandbox.paypal.com/web-sdk/v6/core", (r) =>
    r.fulfill({
      contentType: "text/javascript",
      body: `
    customElements.define('paypal-basic-card-container',class extends HTMLElement {});
    customElements.define('paypal-basic-card-button',class extends HTMLElement {connectedCallback(){if(!this.shadowRoot){const root=this.attachShadow({mode:'open'});const b=document.createElement('button');b.textContent='Submit payment';b.type='button';root.append(b);}}});
    window.paypal={createInstance:async()=>({findEligibleMethods:async(options)=>{window.recordSdkEligibility(options);return {isEligible:()=>true}},createPayPalSubscriptionPaymentSession:callbacks=>({destroy:()=>window.recordSdkDestroy(),start:async(options,subscription)=>{window.recordSubscriptionStart(options);${mode === "blocked" ? "throw new Error('Popup blocked');" : ""}const {subscriptionId}=await subscription;${mode === "approved" ? "await callbacks.onApprove({subscriptionId});" : mode === "cancelled" ? "callbacks.onCancel();" : mode === "error" ? "callbacks.onError();" : ""}}}),createPayPalGuestOneTimePaymentSession:callbacks=>({destroy:()=>window.recordSdkDestroy(),start:async(options,order)=>{window.recordSdkStart();const {orderId}=await order;${mode === "approved" ? "await callbacks.onApprove({orderId});" : mode === "cancelled" ? "callbacks.onCancel();" : mode === "error" ? "callbacks.onWarn();" : ""}}})})};
  `,
    }),
  );
  await page.route("**/api/v1/payments", async (r) => {
    if (r.request().method() !== "POST") return r.continue();
    const body = r.request().postDataJSON();
    calls.creates.push({ key: r.request().headers()["idempotency-key"], body });
    current = {
      id: "REQUEST1",
      provider_id:
        (body.is_subscription ? "I-SUB" : "ORDER") + calls.creates.length,
      checkout_status: null,
      status: "pending",
      browser_state: "open",
      proof: "signed-proof",
      can_close: true,
      terms: { ...body, client_name: `${body.first_name} ${body.last_name}` },
    };
    await r.fulfill({ json: current });
  });
  await page.route("**/api/v1/payments/*/checkout-outcome", async (r) => {
    const body = r.request().postDataJSON();
    calls.outcomes.push(body.outcome);
    current = {
      ...current,
      status:
        body.outcome === "approved"
          ? (current.terms as { is_subscription: boolean }).is_subscription
            ? "active"
            : "completed"
          : body.outcome === "cancelled"
            ? "cancelled"
            : "failed",
      browser_state: "closed",
      checkout_status: body.outcome === "approved" ? "success" : "failed",
      can_close: false,
      proof: null,
    };
    await r.fulfill({ json: current });
  });
  await page.route("**/api/v1/checkout-submissions/*", (r) =>
    r.fulfill({ json: current }),
  );
  await page.route("**/api/v1/checkout-return?*", (r) =>
    r.fulfill({ json: current }),
  );
  await page.route("**/api/v1/payments/*/close", (route) => {
    calls.closures++;
    current = {
      ...current,
      checkout_status: "failed",
      can_close: false,
      browser_state: "closed",
      proof: null,
    };
    return route.fulfill({ json: current });
  });
  return calls;
}

export async function fill(page: Page) {
  await page
    .getByRole("button", { name: "Create payment", exact: true })
    .click();
  await page.getByLabel("First name", { exact: true }).fill("Test");
  await page.getByLabel("Last name", { exact: true }).fill("client");
  await page.getByLabel("Agreement number", { exact: true }).fill("000123-A");
  await page.getByLabel("Payment amount", { exact: true }).fill("50.00");
}
