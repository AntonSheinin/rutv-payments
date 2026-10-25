# Selected integration and merchant evidence

Implementation branch: `refactor/solid-simplification`. Local only, at http://localhost:8100. No remote deployment, webhook, tunnel, Live enablement or real card test.

## Selected contracts

| Feature | Contract |
| --- | --- |
| One-time card checkout | JS SDK v6 paypal-guest-payments, PayPal-hosted guest overlay, one PayPal basic-card button as Submit |
| Backend collection | Orders v2 CAPTURE; immutable payment provider_correlation as custom_id, explicit merchant, exact currency/amount; one server capture after verified approval |
| Callbacks | SDK onApprove -> authenticated/CSRF outcome POST; cancellation/error/onWarn ends the session and returns to fail page; no SDK restart |
| Subscriptions | Catalog Products + Subscriptions v1; one explicit product/plan/subscription sequence per attempt; v6 paypal-subscriptions popup started from Submit with an asynchronous subscription-ID promise, shared outcome handling, separate term verification and no Orders capture |
| Refunds | Payments v2 capture refunds; exact amount, 180-day local cutoff, reservations and stable identity |
| Subscription refunds | Product-specific sale refund route with the same date, balance and reservation checks as capture refunds |
| Imports | Explicit bounded resource/page/window reads; no provider mutations or alternate-route lookup loop |
| Staff authentication | Configured password hashes and signed cookie, one staff access level, login/me/logout and CSRF |

The guest SDK requires targetElement to be a paypal-basic-card-button. An ordinary HTML button was rejected before launching with ERR_DEV_GUEST_CHECKOUT_TARGET_MUST_BE_BCDC_BUTTON. The actual PayPal component launched its overlay successfully; it serves as the form's single Submit control. It has PayPal-controlled branding rather than a custom spoofed payment button.

## Subscription popup contract, 2026-10-23

The operator selected popup checkout after reviewing the provider limitation:
the short guest-card overlay supports one-time orders, not subscription IDs.
Subscriptions use createPayPalSubscriptionPaymentSession with presentationMode
popup and Promise<{subscriptionId}>. Do not force the desktop modal/iframe mode;
PayPal documents cookie/authentication limitations for it. Do not feed a
subscription ID into the one-time card session or implement an iframe wrapper
around an approval URL. The browser no longer consumes an approval URL from the
backend; there is no redirect fallback. Cancellation/error closes the session,
returns Failed and never retries. Each new explicit Submit remains independent.

Read-only actual Sandbox SDK initialization from localhost succeeded with both
components loaded. Both session factories are present; RECURRING_PAYMENT PayPal
eligibility returned true for ILS/USD/EUR. This created no order or subscription
and does not prove account-side subscription approval, card availability or
billing. A real-SDK launch check on the built local dashboard also opened the popup
while its creation request was intercepted before reaching the backend. The
dashboard stayed local; after closing the popup a new empty form opened. No
provider order/subscription was created. Approval/outcome regression tests use
mocked SDK/provider responses.

Primary contract references: [PayPal subscription types](https://github.com/paypal/paypal-js/blob/main/packages/paypal-js/types/v6/components/paypal-subscriptions.d.ts),
[guest one-time types](https://github.com/paypal/paypal-js/blob/main/packages/paypal-js/types/v6/components/paypal-guest-payments.d.ts)
and [presentation modes](https://developer.paypal.com/sdk/react/reference/#paypalsubscriptionbutton).

## Actual Sandbox checkpoint, 2026-10-19

The Sandbox v6 SDK loaded and initialized from localhost HTTP. Chromium reported a secure context for localhost. Guest-card eligibility returned true for ILS, USD and EUR; advanced_cards returned false for all three. Advanced card fields were therefore not selected.

The original merchant configuration was rejected by Orders with HTTP 422 PAYEE_ACCOUNT_INVALID (debug ID f3852479c7fcf). After the user corrected .env, one new ILS 1.00 Sandbox order was created successfully. Its real hosted guest overlay opened from the initial button gesture with an asynchronous order promise, without a second Open action. No card was entered, approval/capture requested or funds collected by this proof. A subsequent test through the implemented dashboard created one Sandbox order, opened the real guest overlay, and used PayPal's Close control. The local callback POST persisted cancelled, with zero CSP errors and no capture. The labelled LOCAL-VERIFY-001 record is retained as the actual Sandbox attempt. Basic eligibility and launch do not certify every merchant permission or the full collection path.

## Remaining account-dependent verification

- Subscription import follows [List subscriptions](https://developer.paypal.com/api/subscriptions/v1/subscriptions-list/):
  `statuses=ACTIVE,SUSPENDED`, `page_size=20`, page numbers starting at 1, and
  provider `next` links controlling continuation. Each agreement is refreshed
  through its exact ID with effective plan fields. Automated pagination/status
  checks use mocks; the earlier actual single-page read does not certify complete
  merchant-wide coverage or charged installment history.

- Dashboard balance-only reads use [List all balances](https://developer.paypal.com/api/transaction-search/v1/balances-get/)
  with the `https://uri.paypal.com/services/reporting/balances/read` scope. This is
  separate from Transaction Search permission. Access for the configured merchant
  has not been tested; local contract tests mock provider responses. PayPal documents
  up to three hours of balance reporting delay.
- Refund fee accounting follows the [current Israel agreement](https://securepayments.paypal.com/il/legalhub/paypal/useragreement-full?locale.x=en_IL),
  which retains original receiving fees, while accepting only explicit fee-credit
  evidence from [Payments v2 refund breakdowns](https://github.com/paypal/paypal-rest-api-specifications/blob/main/openapi/payments_payment_v2.json)
  or the existing sale product's [detailed refund contract](https://developer.paypal.com/api/deprecated/payments/v1/definitions/detailed_refund).
  No fee reimbursement is inferred from a full or partial refund.

- Actual approved/declined/3DS Sandbox card payment and capture outcomes, refund acceptance and pending/unknown reconciliation. Automated financial tests use mocked provider responses; they prove local behavior, not account behavior.
- Guest recurring checkout, installment collection, cancellation and PayPal notification behavior on this merchant. No application configuration gate is applied. No-account recurring consent is not implied by one-time guest eligibility.
- Subscription installment sale-refund capability. No application configuration gate is applied. No transfer/send-money or alternate API is substituted.
- Subscription listing was verified with one actual read and succeeded. Transaction Search was tested with one bounded read and returned HTTP 403 NOT_AUTHORIZED (debug ID f23168400b81c). Account-wide history import needs the app/account reporting permission; it currently surfaces that rejection directly. Review the Sandbox REST app's permissions in [Apps & Credentials](https://developer.paypal.com/dashboard/applications/sandbox) and request the required reporting access if unavailable. No app permissions were changed automatically. Import exclusions/delays remain explicit; permission alone does not establish complete account history.

PayPal owns recurring charges, installment retries and outcome emails. This service never schedules them, and no requirement exists to disable PayPal's own installment retries. Account email delivery has not been certified by code tests.

## Official references

- [SDK setup](https://developer.paypal.com/sdk/js/set-up/), [guest sessions, callbacks and button requirements](https://developer.paypal.com/sdk/js/reference/).
- [Orders create](https://developer.paypal.com/api/orders/v2/orders-create), [capture](https://developer.paypal.com/api/orders/v2/orders-capture).
- [Subscription creation](https://developer.paypal.com/api/subscriptions/v1/subscriptions-create), [plan creation](https://developer.paypal.com/api/subscriptions/v1/plans-create).
- [Capture refunds](https://developer.paypal.com/checkout/refund-payment), [180-day refund rule](https://www.paypal.com/us/cshelp/article/how-do-i-issue-a-refund-help101).
- [Sandbox accounts](https://developer.paypal.com/sandbox-testing/accounts).
