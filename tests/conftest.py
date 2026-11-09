import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "osrs-test-matplotlib"))

import pytest

from app.db.session import Database, migrate
from app.models import Item
from app.services.ledger import Ledger


@pytest.fixture
async def database(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    migrate(path)
    db = Database(path)
    async with db.sessions() as session, session.begin():
        session.add_all(
            [
                Item(id=453, name="Coal", members=False, buy_limit=13000),
                Item(id=440, name="Iron ore", members=False, buy_limit=13000),
                Item(id=999999, name="Test item", members=True, buy_limit=None),
            ]
        )
    yield db
    await db.close()


@pytest.fixture
async def ledger(database):
    return Ledger(database)
