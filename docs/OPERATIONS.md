# Direct local operations

Run one local API process. Startup obtains a pinned PostgreSQL advisory lock, applies the clean baseline and verifies environment/merchant ownership. Readiness and mutation entry points verify that original ownership; a lost lock/connection blocks mutations until restart and is never transparently reacquired. Read-only investigation stays available. There are no processing tasks or queued operations to restart/recover.

Payments is the single register for checkout and financial outcomes. The payments table owns the stable payment identity and current status; immutable payment_attempts and subscriptions refer to payment_id. Transactions remain the financial ledger: accepted collection, linked refund/reversal/restoration, and failed capture observations. The database permits at most one non-failed collection per payment while preserving failed observations for an explicit new attempt. Imported collections receive payment rows without invented operator intent. Each subscription installment is its own payment; subscription activation alone is not collection.

Refunds update the payment to partially_refunded/refunded only from verified financial facts; pending or uncertain refunds remain reserved. The original gross amount and immutable ledger facts are preserved. A payment is listed once regardless of its attempts/refund entries. Details expose the attempts and financial history. The Collected filter includes completed/partially refunded payments, and Refunded includes partial/full refunds; Checkout result filters terminal Success/Failed. The old request API and page are removed without aliases.

Wait for open requests to answer before starting a new attempt. A singleton-process, per-operator guard rejects overlapping checkout mutations, including from other tabs; it never queues or retries them and holds no database lock over provider HTTP. After the answer, every accepted new Submit closes all that operator's unfinished local checkouts as Failed and creates a fresh payment/attempt. Other operators and terminal results are unchanged. Prior saved provider state does not gate the new Submit. Reusing an existing intent for a different submission is rejected; replaying the same original submission returns the saved result without a provider call. Exact provider identities still correlate financial entries; never match by name/amount alone.

## One attempt and uncertain outcomes

Cancel payment attempt locally closes only an unclaimed one-time checkout. The generic close endpoint freezes unfinished checkout as Failed for either product and prevents future approval callbacks from capturing. It performs no PayPal mutation, never claims provider subscription cancellation, and cannot undo a capture already dispatched. The separate Cancel subscription action remains limited to ACTIVE/SUSPENDED agreements.

Create payment opens a blank form after outstanding API requests finish. Submit/approval/lookup/closure responses must be consumed before another attempt can begin. An error response also releases the UI request lock; no old action is automatically resent. Leaving an active embedded checkout destroys the SDK session and freezes its local result as Failed. A late creation response is closed without reopening checkout. Returning from a hosted checkout without completing it also ends local checkout as Failed. No old intent or non-card input is reused automatically.

An explicit Submit saves the intended payment, immutable child attempt and stable provider mutation identities before contacting PayPal. Each outbound mutation is dispatched at most once. Transactions/row locks are released before HTTP. Financial facts and identities survive process restart; startup does not replay anything.

Approved is not collected. Only a verified provider collection is completed. Pending/unknown is not failed. A browser/3DS error closes the checkout session but cannot undo a dispatched capture or establish that no subscription was activated. A duplicate callback returns the saved result and never captures again. A later error cannot overwrite a collection already confirmed by an explicit read.

Terminal checkout status is success or failed; NULL exists only during the current active execution. Success requires verified provider approval/completion, not an arbitrary success URL. Failed checkouts are never reopened or promoted, including after late callbacks and explicit reads. New explicit attempts remain permitted while old provider outcomes are uncertain. They are independent actions and do not establish that an earlier provider operation did not execute.

If a response is lost, inspect by the original intent without resending any mutation. Explicit synchronization remains read-only and may update financial/provider state without changing a terminal checkout result. An unavailable ID can leave financial state uncertain. New payment navigation and Submit stay available. No force-failed financial override or capture recovery is introduced.

PayPal owns subscription renewal schedules, failed-installment retries and outcome emails. This service issues no renewal charge, debt collection or notification work. Without polling/webhooks, later installments, external refunds and restrictions appear only after explicit reads. Display last verification time and coverage instead of claiming live balances.

## Refunds and reporting

Completed collections keep their original gross amount after refund; adjustments are separate ledger rows. Completed refunds reduce net collected. Pending/uncertain refunds reserve their amount and cannot be silently released or resubmitted. Refund confirmation locks the original collection briefly, checks the exact amount and UTC deadline, reserves, commits, then calls PayPal once. Concurrent confirmations cannot overspend the local remaining balance. Full remaining sends the exact amount reviewed by staff. Revalidate stale forms; do not silently change their amount.

A partially refunded payment remains refundable up to its remaining balance: original gross minus completed refunds and pending/uncertain refund reservations. Multiple explicit partial refunds may cumulatively reach, but never exceed, the original payment amount. Stale capture reads cannot erase full/partial refund evidence. A provider-confirmed full refund exhausts the balance even before its individual ledger entries are imported. An external partial refund with no known completed amount has an unknown balance and stays disabled; a pending reservation does not establish the missing completed-refund amount.

The local 180-day cutoff uses the original aware provider payment timestamp. Missing timestamp/balance/product evidence cannot enable refund. An external restriction/refund can make saved information stale; PayPal may reject the action, and that rejection never triggers a fallback/retry. Subscription installments require their supported refund product; refunding an installment does not cancel its subscription.

Keep ILS/USD/EUR totals separate. Decimal is authoritative; money at API boundaries is text. Unknown fees remain unknown. Statistics describe available synchronized records, not the PayPal account balance. Explicit imports process one page/window, record exclusions and do not assert continuous account-wide coverage. Unresolved partial/reversal mappings remain coverage gaps rather than guessed revenue.

## Local configuration and diagnostics

Keep .env and .local-access.json private. AUTH_USERS entries contain only password_hash; API_KEYS entries only sha256. All authenticated identities have the same access. Preserve password hashes/merchant/client credentials when editing configuration; use single-quoted JSON values in .env so Compose does not interpolate Argon2 dollar signs. Restart after configuration changes.

INFO logs safe lifecycle/action identities; DEBUG adds route-template and provider timing. Do not enable HTTP-library or SQL-parameter logging. Supply PayPal's safe debug ID to support when provider errors need investigation. Do not copy raw provider responses, authorization headers, customer data or approval/return queries into logs.

Bind the API to loopback, leave Compose PostgreSQL unpublished, use localhost consistently and keep health/OpenAPI/business routes authenticated. No public ingress or certificate/tunnel configuration is required for this selected local Sandbox flow. Keep Sandbox and live databases separate and retain the explicit live gate. Never perform live financial actions as routine verification.


Checkout result navigation is bound to the initiating signed attempt state,
not whichever submission happens to be current in tab storage. An unmounted
form ignores later approval/lookup responses for UI navigation. New-Submit
closure changes local checkout permission only, never provider cancellation or
verified collection/refund evidence. Existing unknown financial outcomes and
refund reservations remain intact.
