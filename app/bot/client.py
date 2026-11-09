import asyncio
import logging
import time
from contextlib import suppress

import discord
from discord.ext import tasks

from app.bot.interface import OwnerTree, PositionActions, alert_embed, register_commands, restore_views
from app.services.alerts import Alerts, PermanentDeliveryError
from app.services.ledger import Ledger
from app.services.maintenance import daily_backup
from app.services.market import Market
from app.services.wiki import WikiClient

logger = logging.getLogger(__name__)


class FlipBot(discord.Client):
    def __init__(self, settings, db):
        super().__init__(intents=discord.Intents.none(), allowed_mentions=discord.AllowedMentions.none())
        self.settings, self.db = settings, db
        self.ledger = Ledger(db, settings.default_budget)
        self.wiki = WikiClient(
            settings.wiki_api_url, settings.wiki_user_agent, settings.history_request_interval
        )
        self.market = Market(db, self.wiki, self.ledger, settings.history_shortlist_size)
        self.alerts = Alerts(db, self.ledger, self.market)
        self.tree = OwnerTree(self)
        self.due = {}
        register_commands(self)

    async def setup_hook(self):
        guild = discord.Object(id=self.settings.discord_guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        await restore_views(self)
        # setup_hook runs once; reconnecting on_ready must not start duplicate tasks.
        self.poll_loop.start()
        self.history_loop.start()
        self.alert_loop.start()
        self.maintenance_loop.start()

    async def guarded(self, key, operation):
        try:
            await operation()
            self.market.last_errors.pop(key, None)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Background %s failed", key)
            self.market.last_errors[key] = f"{type(exc).__name__}: {exc}"
            return False

    @tasks.loop(seconds=15)
    async def poll_loop(self):
        now = int(time.time())
        if now >= self.due.get("catalog", 0):
            ok = await self.guarded("catalog", self.market.catalog)
            self.due["catalog"] = now + (86400 if ok else 60)
        if not await self.market.items():
            return
        for key, interval, operation in [
            ("latest", 60, lambda: self.market.refresh_latest(int(time.time()))),
            ("300s", 300, lambda: self.market.refresh_averages(300, int(time.time()))),
            ("3600s", 3600, lambda: self.market.refresh_averages(3600, int(time.time()))),
        ]:
            if now >= self.due.get(key, 0):
                ok = await self.guarded(key, operation)
                self.due[key] = now + (interval if ok else 60)

    @tasks.loop(seconds=60)
    async def history_loop(self):
        await self.guarded("history", lambda: self.market.refresh_histories(int(time.time())))

    @tasks.loop(seconds=15)
    async def alert_loop(self):
        async def work():
            await self.alerts.evaluate(int(time.time()))
            await self.alerts.deliver(self.send_alert, self.find_reference, int(time.time()))

        await self.guarded("alerts", work)

    @tasks.loop(hours=1)
    async def maintenance_loop(self):
        async def work():
            now = int(time.time())
            await self.market.prune(now)
            await asyncio.to_thread(daily_backup, self.db.path, self.settings.backup_dir, now)
            self.market.last_success["backup"] = now

        await self.guarded("maintenance", work)

    @poll_loop.before_loop
    @history_loop.before_loop
    @alert_loop.before_loop
    @maintenance_loop.before_loop
    async def before_background(self):
        await self.wait_until_ready()

    async def alert_channel(self):
        channel = self.get_channel(self.settings.discord_channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(self.settings.discord_channel_id)
            except (discord.Forbidden, discord.NotFound) as exc:
                raise PermanentDeliveryError(
                    "Cannot access the configured private channel; check IDs and permissions."
                ) from exc
        if getattr(getattr(channel, "guild", None), "id", None) != self.settings.discord_guild_id:
            raise PermanentDeliveryError("Configured alert channel is not in the configured server.")
        return channel

    async def send_alert(self, reference, kind, data):
        channel = await self.alert_channel()
        try:
            message = await channel.send(
                f"<@{self.settings.discord_owner_id}>",
                embed=alert_embed(reference, kind, data),
                view=PositionActions(self, data["position_id"], data["revision"]),
                allowed_mentions=discord.AllowedMentions(
                    users=[discord.Object(id=self.settings.discord_owner_id)]
                ),
            )
            return message.id
        except (discord.Forbidden, discord.NotFound) as exc:
            raise PermanentDeliveryError(
                "Cannot post alerts; check Send Messages and Embed Links permissions."
            ) from exc

    async def find_reference(self, reference):
        channel = await self.alert_channel()
        try:
            async for message in channel.history(limit=100):
                if message.author.id != self.user.id:
                    continue
                if any(
                    embed.footer.text and embed.footer.text.startswith(f"Ref: {reference} •")
                    for embed in message.embeds
                ):
                    return message.id
        except discord.Forbidden as exc:
            raise PermanentDeliveryError(
                "Read Message History is required to reconcile uncertain alert deliveries."
            ) from exc
        return None

    async def close(self):
        loops = [self.poll_loop, self.history_loop, self.alert_loop, self.maintenance_loop]
        running = [loop.get_task() for loop in loops if loop.get_task()]
        for loop in loops:
            loop.cancel()
        for task in running:
            with suppress(asyncio.CancelledError):
                await task
        await self.wiki.close()
        await super().close()
