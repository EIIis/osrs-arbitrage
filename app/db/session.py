import asyncio
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class Database:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine("sqlite+aiosqlite:///" + self.path.as_posix())
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        # One process, serialized ledger writes. SQLite also serializes cross-connection writes.
        self.write_lock = asyncio.Lock()

        @event.listens_for(self.engine.sync_engine, "connect")
        def configure(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    async def close(self):
        await self.engine.dispose()


def migrate(path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "app/db/migrations"))
    config.attributes["database_path"] = path.resolve()
    command.upgrade(config, "head")
