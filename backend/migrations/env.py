from alembic import context
from app.config import get_settings
from app.models import Base
from sqlalchemy import create_engine

config = context.config
target_metadata = Base.metadata
if context.is_offline_mode():
    context.configure(
        url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
elif (connection := config.attributes.get("connection")) is not None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(
        get_settings().database_url, connect_args={"connect_timeout": 10}
    ).connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
