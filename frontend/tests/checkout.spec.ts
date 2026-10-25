import { test, expect, checkout, fill } from "./fixtures";

test("signed checkout return is independent of malformed tab storage", async ({
  page,
}) => {
  const calls = await checkout(page, "waiting");
  await fill(page);
  await page
    .getByRole("button", { name: "Submit payment", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Close checkout", exact: true }),
  ).toBeEnabled();
  await page.evaluate(() =>
    sessionStorage.setItem("payments.current-submission", "malformed"),
  );
  await page.goto("/paypal/success?state=signed-proof&token=ORDER1");
  await expect(page.locator(".result-panel .badge")).toHaveText("success");
  expect(calls.creates).toHaveLength(1);
  expect(calls.outcomes).toEqual(["approved"]);
  expect(calls.closures).toBe(0);
});

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

for (const recurring of [false, true])
  test(`close ${recurring ? "subscription" : "one-time"} checkout and permit a new payment`, async ({
    page,
  }) => {
    const calls = await checkout(page, "waiting");
    await fill(page);
    if (recurring)
      await page.getByRole("switch", { name: "Is subscription" }).check();
    await page
      .getByRole("button", {
        name: recurring ? "Submit" : "Submit payment",
        exact: true,
      })
      .click();
    await expect(
      page.getByRole("button", { name: "Close checkout", exact: true }),
    ).toBeEnabled();
    await page
      .getByRole("button", { name: "Close checkout", exact: true })
      .click();
    await expect(page.locator(".result-panel .badge")).toHaveText("failed");
    expect(calls.closures).toBe(1);
    expect(calls.sdkDestroyed).toBe(1);
    expect(calls.outcomes).toHaveLength(0);
    expect(calls.creates).toHaveLength(1);
    await page.getByRole("button", { name: "Dashboard", exact: true }).click();
    await page
      .getByRole("button", { name: "Create payment", exact: true })
      .click();
    await expect(page.getByLabel("First name", { exact: true })).toHaveValue(
      "",
    );
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
    page.getByRole("button", { name: "Close checkout", exact: true }),
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
        provider_id: null,
        status: "failed",
        checkout_status: "failed",
        browser_state: "closed",
        proof: null,
        error: "Provider creation failed",
        can_close: false,
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
    provider_id: null,
    status: "failed",
    checkout_status: "failed",
    browser_state: "closed",
    proof: null,
    can_close: false,
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
  await page.getByRole("button", { name: "Dashboard", exact: true }).click();
  await expect(page).toHaveURL("/");
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
