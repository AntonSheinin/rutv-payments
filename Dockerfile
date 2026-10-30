FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim AS python-base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/service/.venv/bin:$PATH"
WORKDIR /service

FROM python-base AS backend
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
COPY backend/app/ ./backend/app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --reinstall-package paypal-payment-service

FROM python-base AS runtime
RUN groupadd --gid 10001 payments \
    && useradd --uid 10001 --gid payments --create-home --shell /usr/sbin/nologin payments
COPY --from=backend /service/.venv ./.venv
COPY alembic.ini ./
COPY backend/migrations/ ./backend/migrations/
COPY --from=frontend /build/dist ./frontend/dist
USER payments:payments
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
