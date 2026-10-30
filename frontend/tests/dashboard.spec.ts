import { test, expect } from "./fixtures";
import type { BalanceSnapshot, Stats } from "../src/types";

const snapshot: BalanceSnapshot = {
  balances: [
    { currency: "ILS", total: "123.45" },
    { currency: "USD", total: "0.00" },
  ],
  as_of: "2026-10-25T08:00:15Z",
  last_synced_at: "2026-10-25T09:35:42Z",
};
const stats: Stats = {
  currencies: [
    {
      currency: "ILS",
      collected: "100.00",
      refunds: "20.00",
      reversals: "0.00",
      restored: "0.00",
      net_before_fees: "80.00",
      net_after_fees: "77.00",
      known_fees: "3.00",
      fees_complete: true,
      completed_count: 2,
      pending_count: 0,
      failed_count: 0,
    },
    {
      currency: "USD",
      collected: "10.00",
      refunds: "0.00",
      reversals: "0.00",
      restored: "0.00",
      net_before_fees: "10.00",
      net_after_fees: null,
      known_fees: "0.00",
      fees_complete: false,
      completed_count: 1,
      pending_count: 0,
      failed_count: 0,
    },
  ],
  trends: [
    {
      date: "2026-09-30 00:00:00",
      currency: "ILS",
      kind: "collection",
      amount: "10.00",
    },
    {
      date: "2026-10-01 00:00:00",
      currency: "ILS",
      kind: "collection",
      amount: "90.00",
    },
  ],
  current_subscriptions: { ACTIVE: 1, SUSPENDED: 0 },
  balance: null,
  coverage: [],
  scope: "Recorded collections",
  timezone: "UTC",
  environment: "sandbox",
  filters: {},
};

test.use({ timezoneId: "America/Los_Angeles" });

test("dashboard defaults, combined metrics and calendar filters", async ({
  page,
}) => {
  const requests: URLSearchParams[] = [];
  await page.clock.setFixedTime(new Date("2026-10-25T12:00:00Z"));
  await page.route("**/api/v1/statistics?*", (route) => {
    requests.push(new URL(route.request().url()).searchParams);
    return route.fulfill({ json: stats });
  });
  await page.reload();
  await expect(page.locator(".metric")).toHaveCount(4);
  expect(requests.at(-1)?.get("currency")).toBe("ILS");
  expect(requests.at(-1)?.get("start_date")).toBe("2026-10-01");
  expect(requests.at(-1)?.get("end_date")).toBe("2026-10-31");
  expect(requests.at(-1)?.get("timezone")).toBe("America/Los_Angeles");
  const collected = page
    .locator(".metric")
    .filter({ hasText: "Collected / Net collected" });
  await expect(collected).toContainText("100.00 /");
  await expect(collected).toContainText("77.00");
  const health = page
    .locator(".metric")
    .filter({ hasText: "Subscription health" });
  await expect(health.locator(".status-row")).toHaveCount(2);
  await expect(health).not.toContainText(/CANCELLED|EXPIRED|total agreements/);
  await expect(page.locator(".bar-chart small")).toHaveText([
    "30 Sept",
    "1 Oct",
  ]);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByLabel("From", { exact: true }).fill("2026-09-01");
  await page.getByLabel("Minimum amount").fill("5");
  await page.getByLabel("Search payments and agreements").fill("agreement");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect.poll(() => requests.at(-1)?.get("q")).toBe("agreement");
  expect(requests.at(-1)?.get("start_date")).toBe("2026-09-01");
  expect(requests.at(-1)?.get("min_amount")).toBe("5");
  await page.getByRole("button", { name: "Reset filters" }).click();
  await expect(page.getByLabel("Currency filter")).toHaveValue("ILS");
  await expect(page.getByLabel("From", { exact: true })).toHaveValue(
    "2026-10-01",
  );
  await page.getByLabel("Currency filter").selectOption("");
  await expect(collected).toContainText("USD:");
  await expect(collected).toContainText("Net unavailable");
  await page.setViewportSize({ width: 390, height: 900 });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > innerWidth,
  );
  expect(overflow).toBe(false);
});

test("balance sync is explicit, retains prior values on error and formats minutes", async ({
  page,
}) => {
  let balance: BalanceSnapshot | null = null;
  let fail = false;
  let reads = 0;
  await page.route("**/api/v1/statistics?*", (route) =>
    route.fulfill({ json: { ...stats, balance } }),
  );
  await page.route("**/api/v1/balance/synchronize", (route) => {
    reads++;
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["x-csrf-token"]).toBeTruthy();
    if (fail)
      return route.fulfill({
        status: 403,
        json: { detail: "PayPal balance permission denied" },
      });
    balance = snapshot;
    return route.fulfill({ json: balance });
  });
  await page.reload();
  const card = page.locator(".metric").filter({ hasText: "PayPal balance" });
  await expect(card).toContainText("Not synchronized");
  expect(reads).toBe(0);
  await page
    .getByRole("button", { name: "Sync PayPal balance", exact: true })
    .click();
  await expect(card).toContainText("123.45");
  await expect(card).toContainText("Last synced: 25 Oct 2026, 02:35");
  await expect(card).toContainText("Balance as of: 25 Oct 2026, 01:00");
  await page.getByLabel("Currency filter").selectOption("EUR");
  await expect(card).toContainText("Unavailable for this currency");
  await page.getByLabel("Currency filter").selectOption("USD");
  await expect(card).toContainText("$0.00");
  expect(reads).toBe(1);
  fail = true;
  await page
    .getByRole("button", { name: "Sync PayPal balance", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("permission denied");
  await expect(card).toContainText("Last synced: 25 Oct 2026, 02:35");
  await expect(card).toContainText("$0.00");
  await page.reload();
  await expect(card).toContainText("Last synced: 25 Oct 2026, 02:35");
  expect(reads).toBe(2);
});
