import { test, expect, Page, BrowserContext } from "@playwright/test";
import fs from "node:fs";
const credentials =
  process.env.E2E_USERNAME && process.env.E2E_PASSWORD
    ? { username: process.env.E2E_USERNAME, password: process.env.E2E_PASSWORD }
    : JSON.parse(fs.readFileSync("../.local-access.json", "utf8"));

let sessionCookies: Awaited<ReturnType<BrowserContext["cookies"]>>;
test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext();
  const response = await context.request.post(
    "http://localhost:8100/api/v1/auth/login",
    {
      headers: { Origin: "http://localhost:8100" },
      data: credentials,
    },
  );
  expect(response.ok()).toBeTruthy();
  sessionCookies = await context.cookies();
  await context.close();
});

test.beforeEach(async ({ page, context }) => {
  await context.addCookies(sessionCookies);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toBeVisible();
});

/** Mock provider/browser contracts only: no test sends an actual checkout/refund to PayPal. */
async function checkout(
  page: Page,
  mode: "approved" | "cancelled" | "error" | "waiting" | "blocked" = "approved",
) {
  const calls: {
    creates: { key: string | null; body: Record<string, unknown> }[];
    outcomes: string[];
    sdkStarts: number;
    subscriptionStarts: { presentationMode: string }[];
    eligibilityFlows: string[];
    cancellations: number;
    closures: number;
    sdkDestroyed: number;
  } = {
    creates: [],
    outcomes: [],
    sdkStarts: 0,
    subscriptionStarts: [],
    eligibilityFlows: [],
    cancellations: 0,
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
      attempt_id: "ATTEMPT" + calls.creates.length,
      provider_id:
        (body.is_subscription ? "I-SUB" : "ORDER") + calls.creates.length,
      checkout_status: null,
      status: "awaiting_approval",
      browser_state: "open",
      proof: "signed-proof",
      can_cancel: true,
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
      can_cancel: false,
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
  await page.route("**/api/v1/payments/*/attempts/*/cancel", (route) => {
    calls.cancellations++;
    current = {
      ...current,
      status: "cancelled",
      browser_state: "closed",
      can_cancel: false,
      checkout_status: "failed",
      proof: null,
    };
    return route.fulfill({ json: current });
  });
  await page.route("**/api/v1/payments/*/attempts/*/close", (route) => {
    calls.closures++;
    current = {
      ...current,
      checkout_status: "failed",
      can_cancel: false,
      browser_state: "closed",
      proof: null,
    };
    return route.fulfill({ json: current });
  });
  return calls;
}

async function fill(page: Page) {
  await page
    .getByRole("button", { name: "Create payment", exact: true })
    .click();
  await page.getByLabel("First name", { exact: true }).fill("Test");
  await page.getByLabel("Last name", { exact: true }).fill("client");
  await page.getByLabel("Agreement number", { exact: true }).fill("000123-A");
  await page.getByLabel("Payment amount", { exact: true }).fill("50.00");
}

for (const status of [401, 403, 422, 503])
  test(`rejected Submit (${status}) preserves editable form without an automatic retry`, async ({
    page,
  }) => {
    const calls = await checkout(page);
    let reject = true,
      rejected = 0;
    await page.route("**/api/v1/payments", (route) => {
      if (!reject || route.request().method() !== "POST")
        return route.fallback();
      rejected++;
      return route.fulfill({ status, json: { detail: "Submission rejected" } });
    });
    await page.route("**/api/v1/checkout-submissions/*", (route) =>
      reject
        ? route.fulfill({ status: 404, json: { detail: "Not found" } })
        : route.fallback(),
    );
    await fill(page);
    await page
      .getByRole("button", { name: "Submit payment", exact: true })
      .click();
    await expect(page.getByRole("alert")).toContainText("Submission rejected");
    await expect(page.getByLabel("First name", { exact: true })).toHaveValue(
      "Test",
    );
    expect(
      await page.evaluate(() =>
        sessionStorage.getItem("payments.current-submission"),
      ),
    ).toBeNull();
    expect(rejected).toBe(1);
    expect(calls.creates).toHaveLength(0);
    reject = false;
    await page
      .getByLabel("First name", { exact: true })
      .fill("Corrected client");
    await page
      .getByRole("button", { name: "Submit payment", exact: true })
      .click();
    await expect(page.locator(".result-panel .badge")).toHaveText("success");
    expect(calls.creates).toHaveLength(1);
    expect(calls.outcomes).toEqual(["approved"]);
  });

test("lost Submit response retains its identity and never starts another attempt", async ({
  page,
}) => {
  await checkout(page);
  let submissions = 0;
  await page.route("**/api/v1/payments", (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    submissions++;
    return route.abort("failed");
  });
  await page.route("**/api/v1/checkout-submissions/*", (route) =>
    route.fulfill({ status: 404, json: { detail: "Not found" } }),
  );
  await fill(page);
  await page
    .getByRole("button", { name: "Submit payment", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Create a new payment" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("payments.current-submission"),
    ),
  ).not.toBeNull();
  await page
    .getByRole("button", { name: "Create payment", exact: true })
    .click();
  await expect(page.getByLabel("First name", { exact: true })).toHaveValue("");
  expect(submissions).toBe(1);
});

test("subscription validation rejection leaves the form editable", async ({
  page,
}) => {
  await checkout(page);
  let submissions = 0;
  await page.route("**/api/v1/payments", (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    submissions++;
    return route.fulfill({
      status: 422,
      json: { detail: "Correct the subscription terms" },
    });
  });
  await page.reload();
  await fill(page);
  await page.getByRole("switch", { name: "Is subscription" }).check();
  await page.getByRole("button", { name: "Submit", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Correct the subscription terms",
  );
  await expect(page.getByLabel("First name", { exact: true })).toHaveValue(
    "Test",
  );
  await expect(
    page.getByRole("button", { name: "Submit", exact: true }),
  ).toBeEnabled();
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("payments.current-submission"),
    ),
  ).toBeNull();
  expect(submissions).toBe(1);
});

test("subscription imports request only the operator-selected page", async ({
  page,
}) => {
  const pages: number[] = [];
  await page.route("**/api/v1/synchronizations", (route) => {
    pages.push(route.request().postDataJSON().page);
    return route.fulfill({
      json: {
        processed: 5,
        excluded: 0,
        next_page: pages.at(-1)! + 1,
        coverage_note: "One page read.",
      },
    });
  });
  await page
    .getByRole("button", { name: "Subscriptions", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Import subscriptions", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Import subscriptions", exact: true })
    .click();
  await expect.poll(() => pages.length).toBe(1);
  await expect(page.locator(".notice")).toHaveCount(0);
  await page.getByLabel("Import page", { exact: true }).fill("2");
  await expect(
    page.getByRole("button", { name: "Import subscriptions", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Import subscriptions", exact: true })
    .click();
  await expect.poll(() => pages.length).toBe(2);
  await expect(page.locator(".notice")).toHaveCount(0);
  expect(pages).toEqual([1, 2]);
});

test("cancel on the create page closes checkout and permits a new payment", async ({
  page,
}) => {
  const calls = await checkout(page, "waiting");
  await fill(page);
  await page
    .getByRole("button", { name: "Submit payment", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Cancel payment attempt", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Cancel payment attempt", exact: true })
    .click();
  await expect(page.locator(".result-panel .badge")).toHaveText("failed");
  expect(calls.cancellations).toBe(1);
  expect(calls.sdkDestroyed).toBe(1);
  expect(calls.outcomes).toHaveLength(0);
  expect(calls.creates).toHaveLength(1);
  await page.getByRole("button", { name: "Dashboard", exact: true }).click();
  await page
    .getByRole("button", { name: "Create payment", exact: true })
    .click();
  await expect(page.getByLabel("First name", { exact: true })).toHaveValue("");
  expect(calls.creates).toHaveLength(1);
});

test("abandoned checkout never blocks a fresh form or resumes the previous attempt", async ({
  page,
}) => {
  const calls = await checkout(page, "waiting");
  await fill(page);
  await page
    .getByRole("button", { name: "Submit payment", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Cancel payment attempt", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Create payment", exact: true })
    .click();
  await expect.poll(() => calls.closures).toBe(1);
  await expect(page.getByLabel("First name", { exact: true })).toHaveValue("");
  expect(calls.creates).toHaveLength(1);
  await page.getByLabel("First name", { exact: true }).fill("Second");
  await page.getByLabel("Last name", { exact: true }).fill("Client");
  await page.getByLabel("Agreement number", { exact: true }).fill("NEW");
  await page.getByLabel("Payment amount", { exact: true }).fill("60");
  await page
    .getByRole("button", { name: "Submit payment", exact: true })
    .click();
  await expect.poll(() => calls.creates.length).toBe(2);
  expect(calls.creates[0].body.intent_key).not.toBe(
    calls.creates[1].body.intent_key,
  );
  expect(calls.outcomes).toHaveLength(0);
});

test("unsuccessful subscription creation permits a fresh explicit submission", async ({
  page,
}) => {
  await checkout(page, "waiting");
  const submissions: Record<string, unknown>[] = [];
  await page.route("**/api/v1/payments", (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    const body = route.request().postDataJSON();
    submissions.push(body);
    return route.fulfill({
      json: {
        id: `PAYMENT${submissions.length}`,
        attempt_id: `ATTEMPT${submissions.length}`,
        provider_id: null,
        status: "unknown",
        checkout_status: "failed",
        browser_state: "closed",
        proof: null,
        error: "Provider creation failed",
        can_cancel: false,
        terms: { ...body, client_name: `${body.first_name} ${body.last_name}` },
      },
    });
  });
  for (let attempt = 0; attempt < 2; attempt++) {
    await fill(page);
    await page.getByRole("switch", { name: "Is subscription" }).check();
    await page.getByRole("button", { name: "Submit", exact: true }).click();
    await expect(page.locator(".result-panel .badge")).toHaveText("failed");
    await expect(
      page.getByRole("button", { name: "Create a new payment" }),
    ).toBeEnabled();
    expect(submissions).toHaveLength(attempt + 1);
  }
  expect(submissions[0].intent_key).not.toBe(submissions[1].intent_key);
  await page.getByRole("button", { name: "Create a new payment" }).click();
  await expect(page.getByLabel("First name", { exact: true })).toHaveValue("");
  expect(submissions).toHaveLength(2);
});

test("an open creation request blocks new attempts until its response", async ({
  page,
}) => {
  const calls = await checkout(page, "waiting");
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started = false;
  await page.route("**/api/v1/payments", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    started = true;
    await held;
    return route.fallback();
  });
  await fill(page);
  await page
    .getByRole("button", { name: "Submit payment", exact: true })
    .click();
  await expect.poll(() => started).toBeTruthy();
  await expect(
    page.getByRole("button", { name: "Create payment", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Dashboard", exact: true }),
  ).toBeDisabled();
  expect(calls.creates).toHaveLength(0);
  release();
  await expect(
    page.getByRole("button", { name: "Create payment", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Create payment", exact: true })
    .click();
  await expect(page.getByLabel("First name", { exact: true })).toHaveValue("");
  await expect.poll(() => calls.closures).toBe(1);
  expect(calls.creates).toHaveLength(1);
  expect(calls.outcomes).toHaveLength(0);
});

test("approval response finishes before a new attempt can start and selects its own result", async ({
  page,
}) => {
  const calls = await checkout(page, "approved");
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started = false;
  await page.route("**/api/v1/payments/*/checkout-outcome", async (route) => {
    started = true;
    await held;
    return route.fallback();
  });
  await fill(page);
  await page
    .getByRole("button", { name: "Submit payment", exact: true })
    .click();
  await expect.poll(() => started).toBe(true);
  await expect(
    page.getByRole("button", { name: "Create payment", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Dashboard", exact: true }),
  ).toBeDisabled();
  expect(calls.creates).toHaveLength(1);
  release();
  await expect(page.locator(".result-panel .badge")).toHaveText("success");
  await page
    .getByRole("button", { name: "Create payment", exact: true })
    .click();
  await expect(page.getByLabel("First name", { exact: true })).toHaveValue("");
  expect(calls.outcomes).toEqual(["approved"]);
  expect(calls.creates).toHaveLength(1);
});

test("saved-submission lookup must finish before a fresh form can replace it", async ({
  page,
}) => {
  const calls = await checkout(page, "waiting");
  await page.route("**/api/v1/payments", (route) =>
    route.request().method() === "POST"
      ? route.abort("failed")
      : route.fallback(),
  );
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started = false,
    hold = true;
  const saved = {
    id: "OLD",
    attempt_id: "OLD-ATTEMPT",
    provider_id: null,
    status: "unknown",
    checkout_status: "failed",
    browser_state: "closed",
    proof: null,
    can_cancel: false,
    terms: {
      first_name: "Old",
      last_name: "Attempt",
      client_name: "Old Attempt",
      agreement_number: "1",
      price_per_period: "50.00",
      currency: "ILS",
      period_unit: "MONTH",
      period_count: 1,
      is_subscription: false,
      payment_cycles: null,
      comments: "",
    },
  };
  await page.route("**/api/v1/checkout-submissions/*", async (route) => {
    if (hold) {
      started = true;
      await held;
    }
    return route.fulfill({ json: saved });
  });
  await fill(page);
  await page
    .getByRole("button", { name: "Submit payment", exact: true })
    .click();
  await expect.poll(() => started).toBe(true);
  await expect(
    page.getByRole("button", { name: "Create payment", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Dashboard", exact: true }),
  ).toBeDisabled();
  hold = false;
  release();
  await expect(page.locator(".result-panel .badge")).toHaveText("failed");
  await page
    .getByRole("button", { name: "Create a new payment", exact: true })
    .click();
  await expect(page.getByLabel("First name", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("First name", { exact: true })).toBeEnabled();
  expect(calls.creates).toHaveLength(0);
  expect(calls.outcomes).toHaveLength(0);
});

test("statistics outages hide totals, including previously loaded values", async ({
  page,
}) => {
  let unavailable = false;
  await page.route("**/api/v1/statistics?*", (route) =>
    unavailable
      ? route.fulfill({
          status: 503,
          json: { detail: "Statistics temporarily unavailable" },
        })
      : route.continue(),
  );
  await page.reload();
  await expect(page.locator(".metrics")).toBeVisible();
  unavailable = true;
  await page.getByLabel("Currency filter").selectOption("USD");
  await expect(page.getByRole("alert")).toContainText(
    "Statistics temporarily unavailable",
  );
  await expect(
    page.getByRole("heading", { name: "Statistics unavailable", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".metrics")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "No financial records yet" }),
  ).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Statistics unavailable", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".metrics")).toHaveCount(0);
});

for (const payload of [
  {},
  { processed: 0, excluded: 0, coverage_note: "Missing pagination" },
])
  test(`malformed synchronization response is unconfirmed: ${JSON.stringify(payload)}`, async ({
    page,
  }) => {
    let sends = 0;
    await page.route("**/api/v1/synchronizations", (route) => {
      sends++;
      return route.fulfill({ json: payload });
    });
    await page
      .getByRole("button", { name: "Subscriptions", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Import subscriptions", exact: true }),
    ).toBeEnabled();
    await page
      .getByRole("button", { name: "Import subscriptions", exact: true })
      .click();
    await expect(page.getByRole("alert")).toContainText(
      "Invalid synchronization response; result is unconfirmed.",
    );
    await expect(
      page.getByText("Result: completed", { exact: true }),
    ).toHaveCount(0);
    expect(sends).toBe(1);
  });

test("cancellation without a returned status is unconfirmed and cannot be repeated", async ({
  page,
}) => {
  const item = {
    id: "SUB-INVALID",
    provider_id: "I-INVALID",
    status: "ACTIVE",
    amount: "50.00",
    currency: "ILS",
    client_name: "Response audit",
    agreement_number: "AUDIT",
  };
  await page.route("**/api/v1/subscriptions?*", (route) =>
    route.fulfill({
      json: { items: [item], total: 1, page: 1, page_size: 20 },
    }),
  );
  await page.route("**/api/v1/subscriptions/SUB-INVALID?*", (route) =>
    route.fulfill({
      json: {
        ...item,
        transactions: { items: [], total: 0, page: 1, page_size: 20 },
      },
    }),
  );
  let sends = 0;
  await page.route("**/api/v1/subscriptions/SUB-INVALID/cancel", (route) => {
    sends++;
    return route.fulfill({ json: {} });
  });
  await page
    .getByRole("button", { name: "Subscriptions", exact: true })
    .click();
  await page.getByRole("row").filter({ hasText: "Response audit" }).click();
  await page
    .getByRole("button", { name: "Cancel subscription", exact: true })
    .click();
  await page
    .getByLabel("Cancellation reason", { exact: true })
    .fill("Audit test");
  await page
    .getByRole("button", { name: "Confirm cancellation", exact: true })
    .click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "Invalid action response; result is unconfirmed.",
  );
  await expect(
    page.getByText("Result: completed", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Confirm cancellation", exact: true }),
  ).toBeDisabled();
  expect(sends).toBe(1);
});

test("one Submit opens checkout and one callback completes; reload sends no mutation", async ({
  page,
}) => {
  const calls = await checkout(page);
  await fill(page);
  await page
    .getByRole("button", { name: "Submit payment", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Payment result" }),
  ).toBeVisible();
  await expect(page.locator(".result-panel .badge")).toHaveText("success");
  expect(calls.creates).toHaveLength(1);
  expect(calls.sdkStarts).toBe(1);
  expect(calls.outcomes).toEqual(["approved"]);
  await page.reload();
  await expect(page.locator(".result-panel .badge")).toHaveText("success");
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Dashboard", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await expect(
    page.getByRole("heading", { name: "Payment result" }),
  ).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Create payment", exact: true })
    .click();
  await expect(page.getByLabel("First name", { exact: true })).toHaveValue("");
  expect(calls.creates).toHaveLength(1);
  expect(calls.outcomes).toHaveLength(1);
  expect(calls.creates[0].body.agreement_number).toBe("000123-A");
});

for (const mode of ["cancelled", "error"] as const)
  test(`${mode} stops; only a new operator Submit creates another attempt`, async ({
    page,
  }) => {
    const calls = await checkout(page, mode);
    await fill(page);
    await page
      .getByRole("button", { name: "Submit payment", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Create a new payment" }),
    ).toBeVisible();
    await page.reload();
    await expect(
      page.getByRole("button", { name: "Create a new payment" }),
    ).toBeVisible();
    expect(calls.creates).toHaveLength(1);
    expect(calls.outcomes).toEqual([mode]);
    await page.getByRole("button", { name: "Create a new payment" }).click();
    await expect(
      page.getByLabel("Agreement number", { exact: true }),
    ).toHaveValue("");
    await page.getByLabel("First name", { exact: true }).fill("Test");
    await page.getByLabel("Last name", { exact: true }).fill("client");
    await page.getByLabel("Agreement number", { exact: true }).fill("NEW");
    await page.getByLabel("Payment amount", { exact: true }).fill("50");
    expect(calls.creates).toHaveLength(1);
    await page
      .getByRole("button", { name: "Submit payment", exact: true })
      .click();
    await expect.poll(() => calls.creates.length).toBe(2);
    expect(calls.creates[0].body.intent_key).not.toBe(
      calls.creates[1].body.intent_key,
    );
    expect(calls.creates[0].key).not.toBe(calls.creates[1].key);
  });

test("refund button beside amount opens only its form and preserves exact reviewed amount", async ({
  page,
}) => {
  const row = {
    id: "REFUNDABLE",
    collection_id: "REFUNDABLE",
    client_name: "Refund client",
    agreement_number: "0009",
    amount: "50.00",
    currency: "ILS",
    kind: "collection",
    status: "completed",
    can_refund: true,
    refundable_amount: "35.00",
    refunded_amount: "15.00",
    effective_at: new Date().toISOString(),
    refund_deadline: new Date(Date.now() + 86400000).toISOString(),
  };
  await page.route("**/api/v1/payments?*", (r) =>
    r.fulfill({
      json: {
        items: [
          row,
          {
            ...row,
            id: "EXPIRED",
            client_name: "Expired client",
            refund_deadline: "2020-01-01T00:00:00Z",
          },
        ],
        total: 2,
        page: 1,
        page_size: 20,
      },
    }),
  );
  let amount = "",
    requests = 0;
  await page.route("**/api/v1/transactions/REFUNDABLE/refunds", (r) => {
    requests++;
    amount = r.request().postDataJSON().amount;
    return r.fulfill({ json: { id: "REFUND1", status: "completed" } });
  });
  await page.getByRole("button", { name: "Payments", exact: true }).click();
  const eligible = page.getByRole("row").filter({ hasText: "Refund client" });
  await expect(
    eligible
      .getByRole("cell")
      .nth(1)
      .getByRole("button", { name: "Refund", exact: true }),
  ).toBeVisible();
  await expect(
    eligible.getByRole("button", { name: "Refund", exact: true }),
  ).toBeVisible();
  const inactive = page
    .getByRole("row")
    .filter({ hasText: "Expired client" })
    .getByRole("button", { name: "Refund", exact: true });
  await expect(inactive).toBeDisabled();
  const active = eligible.getByRole("button", { name: "Refund", exact: true });
  await expect(active).toBeEnabled();
  for (const theme of ["light", "dark"]) {
    await page.evaluate(
      (theme) => (document.documentElement.dataset.theme = theme),
      theme,
    );
    await expect(active).toHaveCSS(
      "background-color",
      theme === "light" ? "rgb(2, 132, 199)" : "rgb(224, 153, 0)",
    );
    await expect(active).toHaveCSS("margin-inline-start", "16px");
    await expect(active).toHaveCSS("border-style", "solid");
    await expect(inactive).toHaveCSS(
      "background-color",
      theme === "light" ? "rgb(226, 232, 240)" : "rgb(55, 65, 81)",
    );
  }
  await eligible.getByRole("button", { name: "Refund", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await expect(
    page.getByRole("dialog", { name: "Record details" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Full remaining amount" }).click();
  await page
    .getByLabel("Reason", { exact: true })
    .fill("Requested partial correction");
  await page
    .getByRole("button", { name: "Review refund", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Confirm refund", exact: true })
    .click();
  await expect(page.getByRole("status")).toHaveText("Refund status: completed");
  expect(amount).toBe("35.00");
  expect(requests).toBe(1);
});

test("refund failure never repeats and retains its entered amount", async ({
  page,
}) => {
  const row = {
    id: "REJECTED",
    collection_id: "REJECTED",
    client_name: "Refund client",
    agreement_number: "1",
    amount: "50.00",
    currency: "ILS",
    kind: "collection",
    status: "completed",
    can_refund: true,
    refundable_amount: "50.00",
    refunded_amount: "0.00",
    effective_at: new Date().toISOString(),
    refund_deadline: new Date(Date.now() + 86400000).toISOString(),
  };
  await page.route("**/api/v1/payments?*", (r) =>
    r.fulfill({ json: { items: [row], total: 1, page: 1, page_size: 20 } }),
  );
  let requests = 0;
  await page.route("**/api/v1/transactions/REJECTED/refunds", (r) => {
    requests++;
    return r.fulfill({ status: 409, json: { detail: "Refund unavailable" } });
  });
  await page.getByRole("button", { name: "Payments", exact: true }).click();
  await page.getByRole("button", { name: "Refund", exact: true }).click();
  await page.getByLabel("Refund amount (ILS)").fill("10.00");
  await page.getByLabel("Reason", { exact: true }).fill("Requested");
  await page
    .getByRole("button", { name: "Review refund", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Confirm refund", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("Refund unavailable");
  await expect(page.getByLabel("Refund amount (ILS)")).toHaveValue("10.00");
  await expect(
    page.getByRole("button", { name: "Confirm refund", exact: true }),
  ).toBeDisabled();
  expect(requests).toBe(1);
});

test("only one auth lookup, no role display, and only supported currencies", async ({
  page,
}) => {
  let lookups = 0;
  page.on("request", (r) => {
    if (r.url().endsWith("/api/v1/auth/me")) lookups++;
  });
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toBeVisible();
  expect(lookups).toBe(1);
  await expect(page.getByLabel("Currency filter").locator("option")).toHaveText(
    ["All currencies", "ILS", "USD", "EUR"],
  );
});

test("session lookup outage remains an availability error", async ({
  page,
}) => {
  await page.route("**/api/v1/auth/me", (r) =>
    r.fulfill({
      status: 503,
      json: { detail: "Database temporarily unavailable" },
    }),
  );
  await page.reload();
  await expect(page.getByRole("alert")).toContainText(
    "Database temporarily unavailable",
  );
});

for (const mode of ["approved", "cancelled", "error", "blocked"] as const)
  test(`subscription popup ${mode} uses the shared terminal outcome flow`, async ({
    page,
  }) => {
    const calls = await checkout(page, mode);
    await fill(page);
    await page.getByRole("switch", { name: "Is subscription" }).check();
    await expect(
      page.getByRole("button", { name: "Submit", exact: true }),
    ).toBeEnabled();
    await page.getByRole("button", { name: "Submit", exact: true }).click();
    await expect(page.locator(".result-panel .badge")).toHaveText(
      mode === "approved" ? "success" : "failed",
    );
    expect(calls.subscriptionStarts).toEqual([{ presentationMode: "popup" }]);
    expect(calls.eligibilityFlows).toContain("RECURRING_PAYMENT");
    expect(calls.sdkStarts).toBe(0);
    expect(calls.creates).toHaveLength(1);
    expect(calls.creates[0].body.is_subscription).toBe(true);
    expect(calls.outcomes).toEqual([mode === "blocked" ? "error" : mode]);
    expect(page.url()).toContain(
      mode === "approved" ? "/paypal/success" : "/paypal/fail",
    );
    await page.reload();
    await expect(page.locator(".result-panel .badge")).toHaveText(
      mode === "approved" ? "success" : "failed",
    );
    expect(calls.subscriptionStarts).toHaveLength(1);
    expect(calls.creates).toHaveLength(1);
    expect(calls.outcomes).toHaveLength(1);
  });

test("a blocked popup waits for the creation answer before showing failure", async ({
  page,
}) => {
  const calls = await checkout(page, "blocked");
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/v1/payments", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await held;
    return route.fallback();
  });
  await fill(page);
  await page.getByRole("switch", { name: "Is subscription" }).check();
  await page.getByRole("button", { name: "Submit", exact: true }).click();
  await expect.poll(() => calls.subscriptionStarts.length).toBe(1);
  await expect(
    page.getByRole("button", { name: "Create payment", exact: true }),
  ).toBeDisabled();
  await expect(page.locator(".result-panel")).toHaveCount(0);
  expect(calls.outcomes).toHaveLength(0);
  release();
  await expect(page.locator(".result-panel .badge")).toHaveText("failed");
  expect(calls.creates).toHaveLength(1);
  expect(calls.outcomes).toEqual(["error"]);
  expect(calls.subscriptionStarts).toHaveLength(1);
});

test("unavailable recurring checkout disables Submit before any creation", async ({
  page,
}) => {
  const calls = await checkout(page);
  await page.route("https://www.sandbox.paypal.com/web-sdk/v6/core", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: "window.paypal={createInstance:async()=>({findEligibleMethods:async()=>({isEligible:()=>false})})};",
    }),
  );
  await fill(page);
  await page.getByRole("switch", { name: "Is subscription" }).check();
  await expect(page.getByRole("alert")).toContainText(
    "PayPal checkout is unavailable",
  );
  await expect(
    page.getByRole("button", { name: "Submit", exact: true }),
  ).toBeDisabled();
  expect(calls.creates).toHaveLength(0);
  expect(calls.subscriptionStarts).toHaveLength(0);
});

test("subscription popup opens before asynchronous creation resolves and abandonment closes it", async ({
  page,
}) => {
  const calls = await checkout(page, "waiting");
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/v1/payments", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await held;
    return route.fallback();
  });
  await fill(page);
  await page.getByRole("switch", { name: "Is subscription" }).check();
  await expect(
    page.getByRole("button", { name: "Submit", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Submit", exact: true }).click();
  await expect.poll(() => calls.subscriptionStarts.length).toBe(1);
  expect(calls.creates).toHaveLength(0);
  expect(page.url()).not.toContain("paypal.com");
  await expect(
    page.getByRole("button", { name: "Create payment", exact: true }),
  ).toBeDisabled();
  release();
  await expect(
    page.getByRole("button", { name: "Create payment", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Create payment", exact: true })
    .click();
  await expect.poll(() => calls.closures).toBe(1);
  await expect(page.getByLabel("First name", { exact: true })).toHaveValue("");
  expect(calls.sdkDestroyed).toBe(1);
  expect(calls.outcomes).toHaveLength(0);
});

test("subscriptions show recorded charges and finite, unlimited or unknown remaining payments", async ({
  page,
}) => {
  await page.route("**/api/v1/subscriptions?*", (route) =>
    route.fulfill({
      json: {
        total: 3,
        page: 1,
        page_size: 20,
        items: [
          {
            id: "finite",
            client_name: "Finite subscription",
            status: "ACTIVE",
            amount: "10.00",
            currency: "ILS",
            charged_payments: 3,
            remaining_payments: 9,
            payments_unlimited: false,
          },
          {
            id: "unlimited",
            client_name: "Unlimited subscription",
            status: "ACTIVE",
            amount: "10.00",
            currency: "ILS",
            charged_payments: 4,
            remaining_payments: null,
            payments_unlimited: true,
          },
          {
            id: "unknown",
            client_name: "Unknown subscription",
            status: "ACTIVE",
            amount: "10.00",
            currency: "ILS",
            charged_payments: 0,
            remaining_payments: null,
            payments_unlimited: false,
          },
        ],
      },
    }),
  );
  await page
    .getByRole("button", { name: "Subscriptions", exact: true })
    .click();
  await expect(
    page.getByRole("columnheader", { name: "Charged payments (recorded)" }),
  ).toBeVisible();
  await expect(
    page.getByRole("columnheader", { name: "Remaining payments" }),
  ).toBeVisible();
  const finite = page
    .getByRole("row")
    .filter({ hasText: "Finite subscription" });
  await expect(
    finite.getByRole("cell", { name: "3", exact: true }),
  ).toBeVisible();
  await expect(
    finite.getByRole("cell", { name: "9", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("row")
      .filter({ hasText: "Unlimited subscription" })
      .getByText("Until cancelled"),
  ).toBeVisible();
  await expect(
    page
      .getByRole("row")
      .filter({ hasText: "Unknown subscription" })
      .getByText("Not available"),
  ).toBeVisible();
});

for (const list of ["payments", "subscriptions"]) {
  test(`${list}: failed filter hides stale rows and explicit refresh can recover`, async ({
    page,
  }) => {
    let fail = false;
    await page.route(`**/api/v1/${list}?*`, (route) =>
      route.fulfill(
        fail
          ? {
              status: 503,
              json: { detail: "Records temporarily unavailable" },
            }
          : {
              json: {
                total: 1,
                page: 1,
                page_size: 20,
                items: [
                  {
                    id: "filter-probe",
                    client_name: "Filter review record",
                    status: list === "payments" ? "completed" : "ACTIVE",
                    currency: "ILS",
                    amount: "10.00",
                  },
                ],
              },
            },
      ),
    );
    await page
      .getByRole("button", {
        name: list === "payments" ? "Payments" : "Subscriptions",
        exact: true,
      })
      .click();
    await expect(
      page.getByText("Filter review record", { exact: true }),
    ).toBeVisible();
    fail = true;
    await page.getByLabel("Currency filter").selectOption("USD");
    await expect(page.getByRole("alert")).toContainText(
      "Records temporarily unavailable",
    );
    await expect(
      page.getByRole("heading", { name: "Records unavailable", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Filter review record", { exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "No matching records", exact: true }),
    ).toHaveCount(0);
    await expect(page.getByText("0 records", { exact: true })).toHaveCount(0);
    await expect(page.getByLabel("Next page")).toHaveCount(0);
    fail = false;
    await page.getByLabel("Currency filter").selectOption("ILS");
    await page
      .getByRole("button", { name: "Refresh records", exact: true })
      .click();
    await expect(
      page.getByText("Filter review record", { exact: true }),
    ).toBeVisible();
  });
}

test("dashboard and payments omit sync history, history import and footer", async ({
  page,
}) => {
  for (const view of ["Dashboard", "Payments", "Subscriptions"]) {
    await page.getByRole("button", { name: view, exact: true }).click();
    await expect(page.locator("footer")).toHaveCount(0);
    await expect(page.getByText("Data coverage", { exact: true })).toHaveCount(
      0,
    );
    await expect(
      page.getByRole("button", { name: "Import existing PayPal history" }),
    ).toHaveCount(0);
    await expect(
      page.getByText("Amounts shown per currency", { exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByText("Dates displayed in Asia/Jerusalem", { exact: true }),
    ).toHaveCount(0);
  }
});

for (const [timezoneId, expectedDate] of [
  ["Asia/Tokyo", "22 Oct 2026, 07:30"],
  ["America/Los_Angeles", "21 Oct 2026, 15:30"],
]) {
  test.describe(`operator timezone ${timezoneId}`, () => {
    test.use({ timezoneId });
    test("payment dates and reporting filters use browser timezone", async ({
      page,
    }) => {
      let requestedZone = "",
        statisticsZone = "";
      await page.route("**/api/v1/statistics?*", (route) => {
        statisticsZone =
          new URL(route.request().url()).searchParams.get("timezone") || "";
        return route.continue();
      });
      await page.reload();
      await expect.poll(() => statisticsZone).toBe(timezoneId);
      await page.route("**/api/v1/payments?*", (route) => {
        requestedZone =
          new URL(route.request().url()).searchParams.get("timezone") || "";
        return route.fulfill({
          json: {
            total: 1,
            page: 1,
            page_size: 20,
            items: [
              {
                id: "timezone-probe",
                client_name: "Timezone test payment",
                status: "completed",
                currency: "ILS",
                amount: "10.00",
                effective_at: "2026-10-21T22:30:00Z",
              },
            ],
          },
        });
      });
      await page.getByRole("button", { name: "Payments", exact: true }).click();
      await expect(
        page.getByRole("cell", { name: expectedDate, exact: true }),
      ).toBeVisible();
      expect(requestedZone).toBe(timezoneId);
    });
  });
}

for (const [resource, label] of [
  ["payments", "Payments"],
  ["subscriptions", "Subscriptions"],
]) {
  test(`${label}: playlist pagination and shrinking last page`, async ({
    page,
  }) => {
    let total = 21;
    const requests: { page: number; size: number }[] = [];
    await page.route(`**/api/v1/${resource}?*`, (route) => {
      const params = new URL(route.request().url()).searchParams;
      const current = Number(params.get("page")),
        size = Number(params.get("page_size"));
      requests.push({ page: current, size });
      return route.fulfill({
        json: {
          page: current,
          page_size: size,
          total,
          items:
            (current - 1) * size < total
              ? [
                  {
                    id: "PAGED",
                    client_name: `Page ${current} record`,
                    currency: "ILS",
                    amount: "10.00",
                    status: "completed",
                  },
                ]
              : [],
        },
      });
    });
    await page.getByRole("button", { name: label, exact: true }).click();
    const nav = page.getByRole("navigation", {
      name: `${label} pagination`,
      exact: true,
    });
    await expect(nav).toContainText("Page 1 of 2");
    await expect(nav.getByLabel("First page", { exact: true })).toBeDisabled();
    await nav.getByLabel("Last page", { exact: true }).click();
    await expect(nav).toContainText("Page 2 of 2");
    total = 20;
    await page
      .getByRole("button", { name: "Refresh records", exact: true })
      .click();
    await expect(nav).toContainText("Page 1 of 1");
    await expect(
      page.getByText("Page 1 record", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "No matching records", exact: true }),
    ).toHaveCount(0);
    await nav.getByLabel("Items per page").selectOption("10");
    await expect(nav).toContainText("Page 1 of 2");
    await nav.getByLabel("Next page", { exact: true }).click();
    await expect(nav).toContainText("Page 2 of 2");
    await nav.getByLabel("Previous page", { exact: true }).click();
    await expect(nav).toContainText("Page 1 of 2");
    await nav.getByLabel("Items per page").selectOption("100");
    await expect(nav).toContainText("Page 1 of 1");
    expect(requests).toContainEqual({ page: 2, size: 20 });
    expect(requests).toContainEqual({ page: 1, size: 10 });
    expect(requests.at(-1)).toEqual({ page: 1, size: 100 });
  });

  test(`${label}: detail histories paginate independently beyond 100 records`, async ({
    page,
  }) => {
    const requests: URLSearchParams[] = [];
    const row = {
      id: "PAGED",
      client_name: "Paged detail",
      currency: "ILS",
      amount: "10.00",
      status: resource === "payments" ? "completed" : "ACTIVE",
    };
    await page.route(`**/api/v1/${resource}?*`, (route) =>
      route.fulfill({
        json: { page: 1, page_size: 20, total: 1, items: [row] },
      }),
    );
    await page.route(`**/api/v1/${resource}/PAGED?*`, (route) => {
      const params = new URL(route.request().url()).searchParams;
      requests.push(params);
      const portion = (kind: string) => {
        const current = Number(params.get(`${kind}_page`)),
          size = Number(params.get(`${kind}_page_size`));
        return {
          page: current,
          page_size: size,
          total: 105,
          items: Array.from({ length: 105 }, (_, index) => ({
            id: `${kind}-${index}`,
            provider_id: `${kind.toUpperCase()}-${index}`,
            currency: "ILS",
            amount: "10.00",
            status: "completed",
          })).slice((current - 1) * size, current * size),
        };
      };
      return route.fulfill({
        json: {
          ...row,
          transactions: portion("history"),
          ...(resource === "payments" ? { attempts: portion("attempt") } : {}),
        },
      });
    });
    await page.getByRole("button", { name: label, exact: true }).click();
    await page.getByText("Paged detail", { exact: true }).click();
    const dialog = page.getByRole("dialog");
    const history = dialog.getByRole("navigation", {
      name: "Financial history pagination",
      exact: true,
    });
    await expect(history).toContainText("Page 1 of 6");
    await history.getByLabel("Last page", { exact: true }).click();
    await expect(history).toContainText("Page 6 of 6");
    await expect(
      dialog.getByText("HISTORY-104", { exact: true }),
    ).toBeVisible();
    await history.getByLabel("First page", { exact: true }).click();
    await expect(history).toContainText("Page 1 of 6");
    await history.getByLabel("Items per page").selectOption("100");
    await expect(history).toContainText("Page 1 of 2");
    await history.getByLabel("Last page", { exact: true }).click();
    await expect(history).toContainText("Page 2 of 2");
    if (resource === "payments") {
      const attempts = dialog.getByRole("navigation", {
        name: "Payment attempts pagination",
        exact: true,
      });
      await expect(attempts).toContainText("Page 1 of 6");
      await attempts.getByLabel("Next page", { exact: true }).click();
      await expect(attempts).toContainText("Page 2 of 6");
      await expect(
        dialog.getByText("ATTEMPT-20", { exact: true }),
      ).toBeVisible();
      await expect(history).toContainText("Page 2 of 2");
    }
    expect(requests.at(-1)?.get("history_page_size")).toBe("100");
  });
}

for (const [resource, label, status] of [
  ["payments", "Payments", "awaiting_approval"],
  ["subscriptions", "Subscriptions", "APPROVAL_PENDING"],
]) {
  test(`${label}: unapproved subscriptions explain cancellation restriction without offering it`, async ({
    page,
  }) => {
    const row = {
      id: "UNAPPROVED",
      client_name: "Unapproved subscription",
      status,
      amount: "10.00",
      currency: "ILS",
      is_subscription: true,
      cancellable_attempt_id: null,
    };
    await page.route(`**/api/v1/${resource}?*`, (route) =>
      route.fulfill({
        json: { items: [row], total: 1, page: 1, page_size: 20 },
      }),
    );
    await page.route(`**/api/v1/${resource}/UNAPPROVED?*`, (route) =>
      route.fulfill({
        json: {
          ...row,
          transactions: { items: [], total: 0, page: 1, page_size: 20 },
          ...(resource === "payments"
            ? {
                attempts: {
                  items: [
                    {
                      id: "UNAPPROVED-ATTEMPT",
                      status: "awaiting_approval",
                      can_cancel: false,
                    },
                  ],
                  total: 1,
                  page: 1,
                  page_size: 20,
                },
              }
            : {}),
        },
      }),
    );
    await page.getByRole("button", { name: label, exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Cancel payment attempt", exact: true }),
    ).toHaveCount(0);
    await page.getByText("Unapproved subscription", { exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(
      dialog.getByText(
        /PayPal allows subscription cancellation only while Active or Suspended/,
      ),
    ).toBeVisible();
    await expect(
      dialog.getByRole("button", {
        name: "Cancel payment attempt",
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(
      dialog.getByRole("button", { name: "Cancel subscription", exact: true }),
    ).toHaveCount(0);
  });
}
