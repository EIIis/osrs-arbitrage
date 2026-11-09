import csv
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


def backup_database(source: Path, destination: Path) -> Path:
    source, destination = source.resolve(), destination.resolve()
    if not source.is_file():
        raise ValueError(f"Database not found: {source}")
    if source == destination:
        raise ValueError("Backup destination must differ from the live database.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("Backup destination already exists; choose a new filename.")
    # Build and validate the complete snapshot before publishing its destination name.
    fd, name = tempfile.mkstemp(prefix=".osrs-backup-", dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with (
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src,
            closing(sqlite3.connect(temporary)) as dest,
        ):
            src.backup(dest)
            dest.execute("PRAGMA journal_mode=DELETE")
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup integrity check failed.")
        # Hard link creates the final name atomically without replacing an existing file.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def daily_backup(source: Path, directory: Path, now: int):
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")
    destination = directory / f"osrs-{stamp}.sqlite3"
    if not destination.exists():
        backup_database(source, destination)
    for old in sorted(directory.glob("osrs-????-??-??.sqlite3"), reverse=True)[7:]:
        old.unlink()
    return destination


def export_transactions(source: Path, destination: Path):
    if destination.resolve() == source.resolve():
        raise ValueError("Export cannot overwrite the database.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        rows = connection.execute("""
          SELECT 'buy' kind, id trade_id, id position_id, item_id, quantity,
                 buy_price unit_price, bought_at occurred_at, voided_at FROM positions
          UNION ALL
          SELECT 'sale', s.id, s.position_id, p.item_id, s.quantity, s.price, s.sold_at, s.voided_at
          FROM sales s JOIN positions p ON p.id=s.position_id
          ORDER BY occurred_at, kind, trade_id
        """)
        with destination.open("x", newline="") as output:
            writer = csv.writer(output)
            writer.writerow([entry[0] for entry in rows.description])
            writer.writerows(rows)
    return destination
