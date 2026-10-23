"""Request-scoped PostgreSQL sessions for direct API requests."""

from collections.abc import Generator
from typing import Annotated

from alembic import command
from alembic.config import Config
from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def get_db(request: Request) -> Generator[Session]:
    """Yield a request-scoped session; closing it rolls back uncommitted changes."""
    with request.app.state.database_session() as session:
        yield session


Db = Annotated[Session, Depends(get_db, scope="function")]


def session_factory() -> sessionmaker[Session]:
    """Create PostgreSQL sessions that retain loaded values after commit."""
    engine = create_engine(
        get_settings().database_url, pool_pre_ping=True, connect_args={"connect_timeout": 10}
    )
    return sessionmaker(engine, expire_on_commit=False)


def migrate_database(engine: Engine) -> None:
    """Upgrade the schema before serving requests, using the application's database."""
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
