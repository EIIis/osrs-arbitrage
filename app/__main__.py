"""Local CLI; backup/export never connect to Discord or an external database."""

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock, Timeout
from pydantic import ValidationError

from app.config.settings import Settings
from app.core.exceptions import MarketError
from app.db.session import Database, migrate
from app.services.maintenance import backup_database, export_transactions


def main():
    parser = argparse.ArgumentParser(description="Personal OSRS Discord flipping assistant")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="Migrate the local SQLite database and run the bot")
    for name in ("backup", "export"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
    commands.add_parser("check-api", help="Opt-in live Wiki v2 compatibility check (no Discord)")
    args = parser.parse_args()
    try:
        settings = Settings()
        # Headless charts must not try to write font caches outside our data directory.
        os.environ.setdefault("MPLCONFIGDIR", str(settings.db_path.resolve().parent / ".matplotlib"))
        logging.basicConfig(
            level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
        if args.command == "backup":
            print(backup_database(settings.db_path, args.output))
        elif args.command == "export":
            print(export_transactions(settings.db_path, args.output))
        elif args.command == "check-api":
            asyncio.run(check_api(settings))
        else:
            settings.require_discord()
            settings.db_path.parent.mkdir(parents=True, exist_ok=True)
            # This is an OS-managed lock; process death releases it even if the file remains.
            with FileLock(str(settings.db_path.resolve()) + ".lock", timeout=0):
                migrate(settings.db_path)
                asyncio.run(run(settings))
    except Timeout:
        parser.exit(
            2, "Another bot instance is already using this database. Stop it before starting another.\n"
        )
    except (ValueError, ValidationError, OSError, MarketError) as exc:
        parser.exit(2, f"Startup error: {exc}\n")
    except KeyboardInterrupt:
        pass


async def run(settings):
    import discord

    from app.bot.client import FlipBot

    settings.apply_analysis_overrides()

    db = Database(settings.db_path)
    try:
        async with FlipBot(settings, db) as bot:
            try:
                await bot.start(settings.discord_token)
            except discord.LoginFailure as exc:
                raise ValueError("Discord rejected DISCORD_TOKEN. Check the bot token in .env.") from exc
    finally:
        await db.close()


async def check_api(settings):
    from app.services.wiki import WikiClient

    wiki = WikiClient(settings.wiki_api_url, settings.wiki_user_agent)
    try:
        now = int(datetime.now(UTC).timestamp())
        catalog = await wiki.catalog()
        latest = await wiki.latest(now)
        five = await wiki.averages(300, now)
        hour = await wiki.averages(3600, now)
        summaries = []
        for lookback in ("24h", "7d", "30d"):
            history = await wiki.history(453, lookback, 0, now)
            summaries.append(f"{lookback}: interval={history.interval}s, buckets={len(history.buckets)}")
        print(
            f"Wiki v2 OK: {len(catalog)} items, {len(latest)} quotes, "
            f"{len(five)} five-minute/{len(hour)} hourly aggregates; coal history: " + "; ".join(summaries)
        )
    finally:
        await wiki.close()


if __name__ == "__main__":
    sys.exit(main())
