import { test, expect } from "./fixtures";

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

  test(`${label}: financial histories paginate beyond 100 records`, async ({
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
    expect(requests.at(-1)?.get("history_page_size")).toBe("100");
  });
}
