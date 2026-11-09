import csv
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from filelock import FileLock
from sqlalchemy import create_engine

from app.config.settings import Settings
from app.db.base import Base
from app.db.session import migrate
from app.domain.types import RULES, Mode
from app.services.maintenance import backup_database, daily_backup, export_transactions

NOW = 1794268800
ROOT = Path(__file__).resolve().parents[1]


async def test_wal_backup_restore_export_and_rotation(database, ledger, tmp_path):
    result = await ledger.buy("buy", 453, 100, 100, Mode.QUICK, NOW)
    await ledger.sell("sale", result["position_id"], 40, 120, NOW)
    dest = backup_database(database.path, tmp_path / "backup.sqlite3")
    with sqlite3.connect(dest) as s:
        assert s.execute("select quantity from positions").fetchone()[0] == 100
        assert s.execute("select quantity from sales").fetchone()[0] == 40
    export = export_transactions(database.path, tmp_path / "trades.csv")
    rows = list(csv.DictReader(export.open()))
    assert [r["kind"] for r in rows] == ["buy", "sale"]
    with pytest.raises(ValueError):
        backup_database(database.path, dest)
    with pytest.raises(FileExistsError):
        export_transactions(database.path, export)
    for day in range(10):
        daily_backup(database.path, tmp_path / "backups", NOW + day * 86400)
    assert len(list((tmp_path / "backups").glob("osrs-*.sqlite3"))) == 7
    assert not list(tmp_path.glob(".osrs-backup-*"))


def test_migration_matches_schema_and_is_repeatable(tmp_path):
    path = tmp_path / "fresh.sqlite3"
    migrate(path)
    migrate(path)
    engine = create_engine("sqlite:///" + path.as_posix())
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "app/db/migrations"))
    config.attributes["database_path"] = path
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine.dispose()


def test_second_process_fails_before_connecting_discord(tmp_path):
    path = tmp_path / "bot.sqlite3"
    env = {
        **os.environ,
        "DB_PATH": str(path),
        "DISCORD_TOKEN": "unused-test-token",
        "DISCORD_OWNER_ID": "1",
        "DISCORD_GUILD_ID": "2",
        "DISCORD_CHANNEL_ID": "3",
    }
    with FileLock(str(path) + ".lock", timeout=0):
        process = subprocess.run(
            [sys.executable, "-m", "app", "run"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert process.returncode == 2
    assert "Another bot instance" in process.stderr
    assert not path.exists()


def test_missing_configuration_is_actionable():
    settings = Settings(_env_file=None, discord_token="", discord_owner_id=0)
    with pytest.raises(ValueError, match="DISCORD_TOKEN"):
        settings.require_discord()


def test_overrides_are_validated_and_applied():
    original = RULES.copy()
    try:
        settings = Settings(_env_file=None, analysis_overrides={"quick": {"min_roi": "1.25"}})
        settings.apply_analysis_overrides()
        assert str(RULES[Mode.QUICK].min_roi) == "1.25"
        with pytest.raises(ValueError):
            Settings(_env_file=None, analysis_overrides={"quick": {"min_roi": "NaN"}})
    finally:
        RULES.update(original)


def test_invalid_timezone_is_actionable():
    with pytest.raises(ValueError, match="IANA timezone"):
        Settings(_env_file=None, timezone="not/a/timezone")
