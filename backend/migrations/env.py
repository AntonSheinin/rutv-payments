"""Run migrations on the supplied app connection or an explicitly selected CLI database."""

import os

from alembic import context
from app.models import Base
from app.settings import get_settings
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url


def migration_url() -> URL:
    """Permit CLI test overrides only for dedicated databases named payments_test*."""
    if (test_url := os.environ.get("TEST_DATABASE_URL")) is not None:
        url = make_url(test_url)
        if not (url.database or "").startswith("payments_test"):
            raise RuntimeError("TEST_DATABASE_URL requires a dedicated payments_test database")
        return url
    return get_settings().database_url


config = context.config
target_metadata = Base.metadata
if context.is_offline_mode():
    context.configure(url=migration_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif (connection := config.attributes.get("connection")) is not None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(
        migration_url(), connect_args={"connect_timeout": 10}
    ).connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
