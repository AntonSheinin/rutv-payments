import { test, expect } from "@playwright/test";

for (const environment of ["sandbox", "live"] as const) {
  test(`environment badge shows ${environment === "live" ? "Live" : "Sandbox"}`, async ({
    page,
  }) => {
    await page.route("**/api/v1/**", (route) => {
      if (new URL(route.request().url()).pathname === "/api/v1/auth/me") {
        return route.fulfill({
          json: {
            name: "test-staff",
            csrf: "test-csrf",
            options: {
              environment,
              paypal_configured: false,
              paypal_client_id: "",
              maximum_amount: "1000.00",
              currencies: ["ILS", "USD", "EUR"],
              period_limits: { DAY: 365, WEEK: 52, MONTH: 12, YEAR: 1 },
            },
          },
        });
      }
      return route.fulfill({
        status: 503,
        json: {
          detail: "Dashboard data unavailable in this isolated UI check",
        },
      });
    });
    await page.goto("/");
    const badge = page.locator(".environment");
    await expect
      .poll(() => badge.innerText())
      .toBe(environment === "live" ? "Live" : "Sandbox");
  });
}
