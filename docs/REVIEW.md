# Direct-flow implementation review

The worker/polling architecture has been removed. Its earlier review findings are superseded by PLAN.md and the target implementation; previous verification entries remain historical engineering notes, not proof of the new runtime.

- Routes perform direct request/response actions. Provider HTTP is awaited outside short database transactions. Persisted attempts/refund reservations provide duplicate protection, not queued work or replay.
- Checkout browser state and financial state are separate. A verified capture is collection; a redirect/approval is not. Concurrent callbacks claim once; uncertain actions remain blocked.
- One PayPal guest component opens from Submit. One-time and recurring products retain their distinct documented contracts. No wallet/card fallback chain or custom card collector was added.
- Currency validation is ILS/USD/EUR only. Client name/agreement fields replace the combined reference; no historical aliases remain.
- Authentication has one access level, one cookie and login/me/logout. CSRF/origin checks, safe logs and private diagnostics remain.
- Explicit provider reads are bounded and never mutate PayPal. They do not guarantee complete, immediate account history.
- Docker builds must rebuild the local Python package rather than reuse a cached wheel based only on unchanged pyproject metadata. Image smoke tests inspect the installed code, non-root identity and migrated schema.

Remaining limitations are concrete account verification items documented in INTEGRATION.md. No generic framework, recovery subsystem, scheduled job, webhook ingress, deployment directory or compatibility implementation is retained.

The subsequent silent-fallback audit findings are fixed: missing subscription prices cannot refresh old values, malformed imports cannot become successful empty pages, unavailable statistics cannot display zero totals, and action responses require explicit outcomes. Removed unused reporting coverage fields and their dead UI branches with a versioned schema migration. See VERIFICATION.md for regression and migration results; mocked provider tests do not certify actual financial acceptance.


## Local list investigation, 2026-10-22

The active localhost:8100 listener is Docker Compose, not the previous native
Uvicorn process. Compose explicitly overrides DATABASE_URL to its own db service
and named volume. Read-only checks found zero payments, transactions or
subscriptions there; the native configuration still points to the separate local
development database with 10 payments (nine cancelled, one partially refunded),
two transactions and zero subscriptions. Authenticated list requests return HTTP
200 and total=0, matching the running Compose database. The missing rows are a
runtime/database selection issue; no records were deleted or moved during review.
The running image contains the subscription-progress helper and has neither of
the removed configuration gates.

Confirmed code finding (P2): frontend/src/main.tsx retains the previous listing
when a filter/page/refresh request fails. A browser probe loaded a mocked ILS
payment, changed Currency to USD and returned HTTP 503 for that request. The USD
selector and error banner appeared while the old ILS row remained displayed.
Clear or explicitly invalidate the listing when starting a new query and show an
unavailable state on error rather than presenting stale rows under current
filters. This is distinct from the current genuinely empty Compose database.
The finding remains open; this review did not change application behavior.


Resolution, 2026-10-22: both findings above are addressed. Failed list requests
invalidate old results and show an explicit unavailable state; both register
browser regressions pass. The existing Sandbox data was backed up and restored
into Compose's persistent database with matching full-row fingerprints, leaving
the original untouched. The running unmocked Payments page displays all 10 rows.
See VERIFICATION.md for checks and scope.


## Pagination/detail review, 2026-10-22

Open confirmed findings:

- P2: Payment and subscription detail financial histories are truncated to 100
  records with no continuation controls. Payment history's limited query also
  lacks ORDER BY, despite the UI labelling it latest 100. A dedicated-database
  probe created 102 legitimate linked ledger rows: the register reports 102,
  while payment detail returns only 100. No UI route reaches the omitted rows.
- P2: The main-list success handler accepts a now-out-of-range page unchanged.
  A browser reproduction opened page 2 of a 26-record list, then refreshed after
  the total dropped to 25. It stayed on page 2 with No matching records although
  page 1 still contained results. Refunds changing filter membership can trigger
  this; page state must be corrected from the returned total/page size.
- P2: Payment detail loads all attempts and the frontend renders all of them.
  The same dedicated-database probe confirmed 101 attempts returned in one
  response with no page bounds or controls. Paginate this growing history rather
  than adding another fixed cutoff.

The main lists' missing playlist-style page-size/First/Last/Page X of Y controls
remain the product gap identified by the preceding verification. No application
behavior or real financial records were changed during this review.


Resolution, 2026-10-22: all three pagination/detail findings are fixed. Payment
and subscription detail histories return bounded page objects with stable date
and ID ordering; payment attempts are separately paginated and only the actual
latest attempt can offer cancellation. Removed the arbitrary 100-row cutoff and
label. Main and detail lists correct out-of-range pages when totals shrink.
Shared playlist-style controls provide First/Previous/Next/Last, Page X of Y and
10/20/50/100 rows per page. Refund eligibility still reads the complete ledger.
All 128 backend tests and 33 browser tests pass; see VERIFICATION.md.


## 2026-10-23: Subscription checkout cancellation defect

Confirmed open defect: services.cancel_attempt calls the subscription cancellation
API for APPROVAL_PENDING/APPROVED states. PayPal's official billing_subscriptions_v1
specification defines SUBSCRIPTION_STATUS_INVALID for cancellation unless the
subscription is ACTIVE or SUSPENDED. The current mock incorrectly accepts pending
subscription cancellation. Our error handling then leaves the attempt unknown;
this does not establish provider cancellation. Correct the unsupported mutation
and its mock contract without locally declaring inaccessible subscriptions
cancelled, enabling replacement charges, or replaying cancellation.

Additional actual read-only evidence: the original billing plan remains readable
and ACTIVE using the current configured Sandbox credentials; the saved agreement
and checkout subscription IDs match. Local audit events show checkout submission
followed by checkout.cancel_requested. The direct subscription GET remains 404
INVALID_RESOURCE_ID as recorded in VERIFICATION.md. The invalid cancellation call
is confirmed, but the provider-side reason for the subscription lookup's 404 has
not been established; do not assert expiry or successful cancellation.


Resolution, 2026-10-23: removed the unsupported subscription branch from checkout
cancellation entirely. Only one-time checkouts expose that action; direct
subscription-attempt calls return 409 before state changes/provider calls. Active
and suspended agreements retain their separate cancellation endpoint. The mock
now rejects other subscription states according to PayPal's contract. UI explains
the restriction on subscription forms, results and detail panels. Existing UNKNOWN
records are not changed or unlocked merely because a lookup returns 404. The
provider-side missing-resource question remains unresolved, separately from the
fixed invalid cancellation call.


## 2026-10-23: Previous-attempt lockout resolved

The operator superseded the original pending/unknown replacement gate. New
payment navigation and explicit Submit are independent of every previous
checkout. Failed local checkout results are terminal, while provider/financial
facts and refund reservations remain separate and truthful. Removed the old
same-intent restart path and can_restart contract; no compatibility path remains.
No unsupported subscription cancellation was reintroduced.


## 2026-10-23: Review of shared checkout/popup callbacks

Two confirmed open findings; application code has not been changed in this review.

- P1: PaymentForm.approve always navigates in finally after its outcome POST,
  even if the component was unmounted and a new payment was submitted while that
  POST was pending. Navigation contains no initiating attempt identity, so the
  result page resolves the newer tab submission and closes it as Failed. A
  delayed old response therefore disrupts a separately authorized new checkout.
  Ignore stale UI callbacks after unmount and keep result selection bound to its
  initiating attempt rather than mutable current-submission storage.
- P2: PaymentForm.creationFailed checks abandoned only on entry. If response-loss
  lookup/closure is pending when the operator opens a new form, its later onSaved
  call still replaces that form with the previous checkout result. Recheck the
  originating component's lifetime after awaits before UI updates/navigation.

Both were reproduced through mocked browser HTTP/SDK paths against the built
local frontend. These async interleavings are missing from the current passing
browser suite. See VERIFICATION.md; no provider mutation was performed.


Resolution, 2026-10-23: the operator clarified that a new attempt must wait for
open requests to answer, and each accepted new Submit must close all unfinished
local attempts belonging to that operator. Implemented a per-operator checkout
request guard (including across tabs), frontend API-response tracking and
new-attempt/navigation blocking, and atomic local supersession during Submit.
No provider cancellation or financial reservation release is implied. Late
callbacks check unmount state; result navigation carries the original signed
attempt state. Removed the prior saved-record lookup from new-form navigation.
A blocked/closed popup also waits for an outstanding creation response before
navigating to failure. Regression coverage now includes the originally reported
approval and saved-submission lookup interleavings under this clarified rule.
