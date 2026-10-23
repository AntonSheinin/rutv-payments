# Working in this repository

## Project and architecture

This is an internal PayPal payment service for staff and trusted integrations.
End users never access this service. Card entry and guest checkout belong on
PayPal-hosted pages or PayPal-controlled frames; never collect or store card data.

- Backend: Python 3.14, FastAPI, SQLAlchemy, Alembic and PostgreSQL 17.
- Frontend: React, TypeScript and Vite, under `frontend/`.
- Python dependencies: `pyproject.toml` and `uv.lock`. Use uv, not pip or requirements files.
- Deployment: `docker-compose.yml` contains only `api` and `db`.
- FastAPI startup acquires singleton database ownership and applies migrations.
  Financial actions execute directly within requests. Run one Uvicorn process.
  No background worker, polling, scheduled retries or startup replay.
- The image serves the built frontend and runs as `payments:payments`, UID/GID 10001.

Read `README.md` for setup, `PLAN.md` for product rules,
`docs/OPERATIONS.md` for uncertain-outcome semantics and `docs/INTEGRATION.md` for
merchant-specific verification still required. Record material verification
results in `docs/VERIFICATION.md` without claiming mocked tests prove live behavior.

## Code organization and style

- `backend/app/main.py`: HTTP routes, application lifecycle and shared resources.
- `auth.py`, `schemas.py`, `services.py`: authentication, validation and business rules.
- `paypal.py`, `normalize.py`, `sync.py`: provider HTTP, verified financial facts and explicit bounded reads.
- `models.py`, `db.py`, `storage.py`: persistence, migrations and environment ownership.
- `reporting.py`, `serialization.py`: search, statistics and API representations.
- `backend/migrations/`: versioned schema changes; `backend/tests/`: backend tests.

Prefer small, direct changes using existing code paths. Avoid speculative
abstractions, duplicate validation and unrelated refactors. Add complete type hints
and docstrings to backend modules, classes and functions. Comment non-trivial
financial, concurrency and recovery decisions. Catch specific expected exceptions;
unexpected programming or persistence failures must remain visible.

Use lazy logging arguments. INFO describes lifecycle and meaningful activity;
DEBUG adds safe timing and diagnostic context. Never log secrets, authorization
headers, SQL parameters, raw provider payloads, approval URLs or customer details.

## Payment correctness and access

- Preserve durable action identities, refund reservations, database constraints and stable
  provider idempotency keys. A timeout is an uncertain result, not permission to
  repeat a charge or refund with a new identity.
- Verify provider merchant, amount, currency and correlation. Approval or a browser
  redirect does not prove collection. No webhook route is needed.
- Keep manual synchronization read-only at PayPal. There is no capture recovery or
  mutation replay. Preserve singleton ownership and graceful client cleanup.
- Lost database ownership fails readiness and blocks mutations until restart.
  Preserve read-only investigation and uncertain financial reservations.
- Use Decimal for money and strings at API boundaries. Keep totals separate per
  currency; unknown fees are not zero. Payment-cycle input applies only to subscriptions.
- Preserve staff authentication, CSRF/origin checks and private health endpoints.
  All authenticated staff have the same access. Bind locally; no public ingress.
- Keep Sandbox and live databases separate. Preserve the explicit live gate. Check current official PayPal documentation when
  changing provider behavior; account eligibility requires actual integration testing.
- Do not expose or commit `.env`, `.local-access.json`, tokens or database dumps.
  Use `.env.example` for configuration documentation. Do not perform live financial
  actions as part of routine development or verification.

## Validation

Install development dependencies with `uv sync --locked --extra dev`. After a
dependency change, update `uv.lock` with `uv lock`. Run relevant checks:

```sh
uv run --locked --extra dev ruff check backend
uv run --locked --extra dev ruff format --check backend
uv run --locked --extra dev mypy backend/app
uv run --locked --extra dev pytest -q
```

Backend tests drop and recreate tables. Use only a dedicated database whose name
starts with `payments_test`, configured through `TEST_DATABASE_URL`; never point
tests at the application database. See `backend/tests/conftest.py` for the default.

From `frontend/`, run `npm ci`, `npm run build` and `npm run format:check`.
Use `npm.cmd` on Windows PowerShell if required. Browser tests use `npm test` and
require Chromium, a running local application and the configured local test login;
see `frontend/playwright.config.ts` and `frontend/tests/staff.spec.ts`.

For persistence changes, verify Alembic upgrade and schema comparison against a
dedicated database. For deployment changes, build and smoke-test the container and
check `docker compose config --services`; avoid printing resolved configuration
containing secrets. `.github/workflows/checks.yml` runs backend checks, frontend
build/format checks and migration checks; it does not deploy or run browser tests.
