import { test, expect, checkout, fill } from "./fixtures";

test("subscription import is in the heading and requests all active/suspended agreements", async ({
  page,
}) => {
  const requests: unknown[] = [];
  await page.route("**/api/v1/synchronizations", (route) => {
    requests.push(route.request().postDataJSON());
    return route.fulfill({
      json: {
        processed: 5,
        excluded: 0,
        next_page: null,
        coverage_note: "All Active/Suspended agreement pages read.",
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
  await expect.poll(() => requests.length).toBe(1);
  await expect(page.locator(".notice")).toHaveCount(0);
  await expect(page.getByLabel("Import page", { exact: true })).toHaveCount(0);
  await expect(
    page
      .locator(".page-actions")
      .getByRole("button", { name: "Import subscriptions", exact: true }),
  ).toBeVisible();
  expect(requests).toEqual([{ scope: "subscriptions" }]);
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
            total_charged: "27.50",
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
            total_charged: "40.00",
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
            total_charged: "0.00",
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
  await expect(
    page.getByRole("columnheader", { name: "Total charged (recorded)" }),
  ).toBeVisible();
  await expect(
    page.getByText("Charged counts include recorded successful payments", {
      exact: false,
    }),
  ).toHaveCount(0);
  const finite = page
    .getByRole("row")
    .filter({ hasText: "Finite subscription" });
  await expect(
    finite.getByRole("cell", { name: "3", exact: true }),
  ).toBeVisible();
  await expect(finite).toContainText("27.50");
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

for (const [resource, label, status] of [
  ["payments", "Payments", "pending"],
  ["subscriptions", "Subscriptions", "APPROVAL_PENDING"],
]) {
  test(`${label}: unapproved agreement cancellation stays disabled independently of local closure`, async ({
    page,
  }) => {
    const row = {
      id: "UNAPPROVED",
      client_name: "Unapproved subscription",
      status,
      amount: "10.00",
      currency: "ILS",
      is_subscription: true,
      can_close: resource === "payments",
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
        },
      }),
    );
    await page.getByRole("button", { name: label, exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Close checkout", exact: true }),
    ).toHaveCount(resource === "payments" ? 1 : 0);
    await page.getByText("Unapproved subscription", { exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(
      dialog.getByText(
        /PayPal allows subscription cancellation only while Active or Suspended/,
      ),
    ).toBeVisible();
    await expect(
      dialog.getByRole("button", {
        name: "Close checkout",
        exact: true,
      }),
    ).toHaveCount(resource === "payments" ? 1 : 0);
    await expect(
      dialog.getByRole("button", { name: "Cancel subscription", exact: true }),
    ).toHaveCount(0);
  });
}
