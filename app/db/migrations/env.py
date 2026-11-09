from alembic import context
from sqlalchemy import create_engine

from app import models  # noqa: F401
from app.db.base import Base

config = context.config
path = config.attributes.get("database_path")
if path is None:
    from app.config.settings import Settings

    path = Settings().db_path.resolve()
path.parent.mkdir(parents=True, exist_ok=True)
url = "sqlite:///" + path.as_posix()
if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
