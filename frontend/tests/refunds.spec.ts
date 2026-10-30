import { test, expect } from "./fixtures";

test("refund button beside amount opens only its form and preserves exact reviewed amount", async ({
  page,
}) => {
  const row = {
    id: "REFUNDABLE",
    is_subscription: false,
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
  const inactive = page
    .getByRole("row")
    .filter({ hasText: "Expired client" })
    .getByRole("button", { name: "Refund", exact: true });
  await expect(inactive).toBeDisabled();
  const active = eligible.getByRole("button", { name: "Refund", exact: true });
  await expect(active).toBeEnabled();
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
    is_subscription: false,
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
