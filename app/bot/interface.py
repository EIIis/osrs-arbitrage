"""Discord adapters. Services own accounting and analysis, not command handlers."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from sqlalchemy import select

from app.core.exceptions import UserError
from app.domain.analysis import InsufficientData, analyze
from app.domain.calculations import amount, parse_time, profit, target_for_roi
from app.domain.types import RULES, Mode, Profile
from app.models import AlertDelivery
from app.services.charts import chart

logger = logging.getLogger(__name__)


def authorized(settings, interaction) -> bool:
    return (
        interaction.user.id == settings.discord_owner_id
        and interaction.guild_id == settings.discord_guild_id
        and interaction.channel_id == settings.discord_channel_id
    )


async def reply(interaction, text: str, **kwargs):
    if interaction.response.is_done():
        return await interaction.followup.send(text, **kwargs)
    return await interaction.response.send_message(text, **kwargs)


async def allow(bot, interaction) -> bool:
    if not authorized(bot.settings, interaction):
        await reply(
            interaction, "This bot is restricted to its owner and configured private channel.", ephemeral=True
        )
        return False
    return True


def gp(value: int | None) -> str:
    return "Unavailable" if value is None else f"{value:,} GP"


def format_time(stamp: int, zone: str) -> str:
    return datetime.fromtimestamp(stamp, UTC).astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M %Z")


def decimal_input(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
        if not parsed.is_finite():
            raise InvalidOperation
        return parsed
    except InvalidOperation as exc:
        raise UserError("Enter a finite percentage, such as 2.5.") from exc


class OwnerTree(app_commands.CommandTree):
    async def interaction_check(self, interaction):
        return await allow(self.client, interaction)

    async def on_error(self, interaction, error):
        cause = getattr(error, "original", error)
        if isinstance(cause, (UserError, InsufficientData)):
            message = str(cause)
        elif isinstance(error, app_commands.CheckFailure):
            message = "This command is unavailable here."
        else:
            logger.error("Command failed", exc_info=(type(cause), cause, cause.__traceback__))
            message = "The operation could not be completed. Check /status and try again."
        await reply(interaction, message, ephemeral=True)


class GuardedView(discord.ui.View):
    def __init__(self, bot, timeout=180):
        super().__init__(timeout=timeout)
        self.bot = bot

    async def interaction_check(self, interaction):
        return await allow(self.bot, interaction)

    async def on_error(self, interaction, error, item):
        logger.error("Component failed", exc_info=(type(error), error, error.__traceback__))
        await reply(
            interaction,
            str(error) if isinstance(error, UserError) else "Action failed. Try the slash command.",
            ephemeral=True,
        )


class Pages(GuardedView):
    def __init__(self, bot, pages):
        super().__init__(bot)
        self.pages = pages or ["No records yet."]
        self.index = 0
        self.update_buttons()

    def update_buttons(self):
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index == len(self.pages) - 1

    def embed(self):
        result = discord.Embed(description=self.pages[self.index], color=0xD4AF37)
        result.set_footer(
            text=f"Page {self.index + 1}/{len(self.pages)} • Offer prices are estimates, not fills."
        )
        return result

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction, button):
        self.index = max(0, self.index - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction, button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)


async def pages(bot, interaction, lines, per_page=5):
    view = Pages(bot, ["\n\n".join(lines[i : i + per_page]) for i in range(0, len(lines), per_page)])
    await interaction.followup.send(embed=view.embed(), view=view)


async def check_position(bot, interaction, position_id, revision):
    if not await allow(bot, interaction):
        return False
    holding = await bot.ledger.holding(position_id)
    if holding.remaining <= 0 or holding.position.target_revision != revision:
        raise UserError("This position changed or closed. Use /position for its current state.")
    return True


class PositionModal(discord.ui.Modal):
    def __init__(self, bot, position_id, revision, action):
        super().__init__(
            title={
                "sell": "Record actual sale",
                "target": "Change selling target",
                "snooze": "Snooze alerts",
            }[action],
            timeout=300,
        )
        self.bot, self.position_id, self.revision, self.action = bot, position_id, revision, action
        if action == "sell":
            self.quantity = discord.ui.TextInput(label="Quantity actually sold", max_length=30)
            self.price = discord.ui.TextInput(label="Actual sale price per item, before tax", max_length=30)
            self.add_item(self.quantity)
            self.add_item(self.price)
        else:
            self.value = discord.ui.TextInput(
                label="Target price per item" if action == "target" else "Minutes to snooze", max_length=30
            )
            self.add_item(self.value)

    async def on_submit(self, interaction):
        if not await check_position(self.bot, interaction, self.position_id, self.revision):
            return
        await interaction.response.defer(ephemeral=True)
        now = int(time.time())
        if self.action == "sell":
            result = await self.bot.ledger.sell(
                str(interaction.id),
                self.position_id,
                amount(str(self.quantity)),
                amount(str(self.price)),
                now,
            )
            text = f"Recorded sale #{result['sale_id']}. Realized profit: {gp(result['profit'])}."
        elif self.action == "target":
            result = await self.bot.ledger.target(
                str(interaction.id), self.position_id, now, price=amount(str(self.value))
            )
            text = f"Target set to {gp(result['target'])}."
        else:
            result = await self.bot.ledger.snooze(
                str(interaction.id), self.position_id, amount(str(self.value)), now
            )
            text = "Snoozed until " + format_time(result["snoozed_until"], self.bot.settings.timezone)
        await interaction.followup.send(text, ephemeral=True)

    async def on_error(self, interaction, error):
        logger.error("Form failed", exc_info=(type(error), error, error.__traceback__))
        await reply(
            interaction,
            str(error) if isinstance(error, UserError) else "Form expired or failed. Use the slash command.",
            ephemeral=True,
        )


class PositionActions(GuardedView):
    def __init__(self, bot, position_id, revision):
        super().__init__(bot, timeout=None)
        self.position_id, self.revision = position_id, revision
        for button, name in ((self.sell, "sell"), (self.target, "target"), (self.snooze, "snooze")):
            button.custom_id = f"position:{position_id}:revision:{revision}:{name}"

    async def interaction_check(self, interaction):
        return await check_position(self.bot, interaction, self.position_id, self.revision)

    async def show(self, interaction, action):
        await interaction.response.send_modal(
            PositionModal(self.bot, self.position_id, self.revision, action)
        )

    @discord.ui.button(label="Record sale", style=discord.ButtonStyle.success)
    async def sell(self, interaction, button):
        await self.show(interaction, "sell")

    @discord.ui.button(label="Change target", style=discord.ButtonStyle.secondary)
    async def target(self, interaction, button):
        await self.show(interaction, "target")

    @discord.ui.button(label="Snooze", style=discord.ButtonStyle.secondary)
    async def snooze(self, interaction, button):
        await self.show(interaction, "snooze")


class ConfirmVoid(GuardedView):
    def __init__(self, bot, kind, trade_id):
        super().__init__(bot, timeout=120)
        self.kind, self.trade_id = kind, trade_id

    @discord.ui.button(label="Confirm void", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        await self.bot.ledger.edit(str(interaction.id), self.kind, self.trade_id, int(time.time()), void=True)
        self.stop()
        await interaction.edit_original_response(content="Entry voided; accounting recalculated.", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)


def alert_embed(reference, kind, data):
    title = "Sell offer target supported" if "target" in kind else "Holding needs attention"
    embed = discord.Embed(title=f"{data['item_name']} — {title}", color=0xD4AF37)
    embed.description = (
        f"Position **#{data['position_id']}** • {data['mode']}\n"
        f"{data['quantity']:,} remaining, bought at {gp(data['buy_price'])} each.\n"
        f"Target: {gp(data['target'])}\n"
        f"Historical sell-offer range: {gp(data['sell_low'])}–{gp(data['sell_high'])}\n"
        f"Estimated patient-sale profit: {gp(data['passive_profit'])}\n"
        f"Immediate-sale estimate after tax: {gp(data['immediate_profit'])}\n"
        f"Immediate-sale price reaches target: {'yes' if data['instant_target_hit'] else 'no'}\n"
        f"Observed two-sided hourly volume: {data['hourly_volume']:,}\n"
        f"Quantity supported by participation cap: {data['supported_quantity']:,}\n"
        f"Quotes are {data['quote_age']}s old.\n\n" + "\n".join(data["warnings"]) + "\n" + data["explanation"]
    )
    embed.set_footer(text=f"Ref: {reference} • Offer estimate; no sale has been executed.")
    return embed


def register_commands(bot):
    tree = bot.tree

    async def item_autocomplete(interaction, current):
        if not authorized(bot.settings, interaction):
            return []
        items = await bot.market.items()
        text = current.casefold()
        return [
            app_commands.Choice(name=f"{i.name} ({i.id})"[:100], value=str(i.id))
            for i in items
            if text in i.name.casefold() or text == str(i.id)
        ][:25]

    @tree.command(name="buy", description="Record a completed purchase as a separate position.")
    @app_commands.autocomplete(item=item_autocomplete)
    async def buy(
        interaction: discord.Interaction,
        item: str,
        quantity: str,
        price: str,
        mode: Mode,
        bought_at: str | None = None,
        target: str | None = None,
    ):
        await interaction.response.defer()
        now = int(time.time())
        chosen = await bot.market.resolve(item)
        q, px = amount(quantity), amount(price)
        # Record keeping must remain available when history/provider access is unavailable.
        suggested, reason = await bot.market.suggested_target(chosen.id, px, mode, now)
        if target is not None:
            suggested, reason = amount(target), "Owner-specified target."
        result = await bot.ledger.buy(
            str(interaction.id),
            chosen.id,
            q,
            px,
            mode,
            now,
            parse_time(bought_at, now, bot.settings.timezone),
            suggested,
            reason,
        )
        await interaction.followup.send(
            f"Position **#{result['position_id']}**: {q:,} {chosen.name} at {gp(px)} each.\n"
            f"{mode.value.title()} • Target: {gp(suggested)}\n{reason}"
        )

    @tree.command(name="sell", description="Record a completed full or partial sale.")
    async def sell(
        interaction: discord.Interaction,
        position_id: int,
        quantity: str,
        price: str,
        sold_at: str | None = None,
    ):
        await interaction.response.defer()
        now = int(time.time())
        result = await bot.ledger.sell(
            str(interaction.id),
            position_id,
            amount(quantity),
            amount(price),
            now,
            parse_time(sold_at, now, bot.settings.timezone),
        )
        await interaction.followup.send(
            f"Sale **#{result['sale_id']}** recorded. Realized profit: **{gp(result['profit'])}**."
        )

    @tree.command(name="holdings", description="Open positions with separate realized and estimated profit.")
    async def holdings(interaction: discord.Interaction):
        await interaction.response.defer()
        lines, now = [], int(time.time())
        for h in await bot.ledger.holdings():
            q, _ = await bot.market.snapshot(h.item.id)
            estimate = profit(h.position.buy_price, q.low, h.remaining, h.item.id) if q and q.low else None
            age = f"{now - q.low_time}s old" if q and q.low_time else "no recent quote"
            lines.append(
                f"**#{h.position.id} — {h.item.name}** ({h.position.mode})\n"
                f"{h.remaining:,} remaining • Cost/unit {gp(h.position.buy_price)} • Target {gp(h.position.target_price)}\n"
                f"Immediate-sale estimate {gp(estimate)} ({age}) • Realized {gp(h.realized)}"
            )
        await pages(bot, interaction, lines)

    @tree.command(name="position", description="Position accounting, estimates, and related sale records.")
    async def position(interaction: discord.Interaction, position_id: int, show_chart: bool = False):
        await interaction.response.defer()
        h = await bot.ledger.holding(position_id)
        p = h.position
        quote, _ = await bot.market.snapshot(h.item.id)
        estimate = profit(p.buy_price, quote.low, h.remaining, h.item.id) if quote and quote.low else None
        text = (
            f"**#{p.id} — {h.item.name}** ({p.mode})\nBought {p.quantity:,} at {gp(p.buy_price)} on "
            f"{format_time(p.bought_at, bot.settings.timezone)}\n"
            f"{h.remaining:,} remaining • Realized {gp(h.realized)}\n"
            f"Immediate-sale estimate: {gp(estimate)}"
            + (f" (quote {int(time.time()) - quote.low_time}s old)" if quote and quote.low_time else "")
            + f"\nBreak-even sale/unit {gp(target_for_roi(p.buy_price, Decimal(0), h.item.id))}"
            f"\nTarget {gp(p.target_price)} • Loss warning {p.loss_percent}%\n{p.target_reason}"
        )
        if p.snoozed_until > int(time.time()):
            text += "\nSnoozed until " + format_time(p.snoozed_until, bot.settings.timezone)
        sales = [
            f"Sale #{s.id}: {s.quantity:,} at {gp(s.price)}" + (" (voided)" if s.voided_at else "")
            for s in h.sales
        ]
        await pages(bot, interaction, [text] + sales, per_page=5)
        if show_chart:
            await send_chart(interaction, h.item, Mode(p.mode), p.buy_price, p.target_price)

    target_group = app_commands.Group(
        name="target", description="Set or explicitly recalculate a position target."
    )

    @target_group.command(name="set", description="Set a unit sale price or net percentage-profit target.")
    async def target_set(
        interaction: discord.Interaction,
        position_id: int,
        price: str | None = None,
        net_percent: str | None = None,
    ):
        await interaction.response.defer()
        result = await bot.ledger.target(
            str(interaction.id),
            position_id,
            int(time.time()),
            price=amount(price) if price else None,
            percent=decimal_input(net_percent) if net_percent else None,
        )
        await interaction.followup.send(
            f"Target set to {gp(result['target'])}. It will not be lowered automatically."
        )

    @target_group.command(
        name="reset", description="Recalculate a supported historical target, or clear an unsupported one."
    )
    async def target_reset(interaction: discord.Interaction, position_id: int):
        await interaction.response.defer()
        now = int(time.time())
        h = await bot.ledger.holding(position_id)
        await bot.market.ensure_history(h.item.id, Mode(h.position.mode), now)
        price, reason = await bot.market.suggested_target(
            h.item.id, h.position.buy_price, Mode(h.position.mode), now
        )
        result = await bot.ledger.target(
            str(interaction.id), position_id, now, price=price, reset=True, reason=reason
        )
        await interaction.followup.send(f"Target: {gp(result['target'])}\n{reason}")

    tree.add_command(target_group)

    @tree.command(
        name="risk", description="Override the immediate-sale loss warning threshold for a position."
    )
    async def risk(interaction: discord.Interaction, position_id: int, loss_percent: str):
        await interaction.response.defer()
        result = await bot.ledger.risk(
            str(interaction.id), position_id, decimal_input(loss_percent), int(time.time())
        )
        await interaction.followup.send(f"Loss warning set to {result['loss_percent']}%.")

    @tree.command(name="snooze", description="Mute a position, then recheck its target after expiry.")
    async def snooze(interaction: discord.Interaction, position_id: int, minutes: int):
        await interaction.response.defer()
        result = await bot.ledger.snooze(str(interaction.id), position_id, minutes, int(time.time()))
        await interaction.followup.send(
            "Snoozed until " + format_time(result["snoozed_until"], bot.settings.timezone)
        )

    @tree.command(
        name="history", description="Recorded sales and realized profit, including closed positions."
    )
    async def history(interaction: discord.Interaction):
        await interaction.response.defer()
        lines, total = [], 0
        for h in await bot.ledger.holdings(include_closed=True):
            for s in h.sales:
                if s.voided_at is None:
                    value = profit(h.position.buy_price, s.price, s.quantity, h.item.id)
                    total += value
                    lines.append(
                        f"**Sale #{s.id} — {h.item.name}**, position #{h.position.id}\n"
                        f"{s.quantity:,} at {gp(s.price)} • Net profit {gp(value)}\n"
                        f"{format_time(s.sold_at, bot.settings.timezone)}"
                    )
        await pages(bot, interaction, [f"**Total realized profit: {gp(total)}**"] + lines)

    trade_group = app_commands.Group(name="trade", description="Correct or void recorded trades.")

    @trade_group.command(name="edit", description="Correct an actual purchase or sale without overselling.")
    async def trade_edit(
        interaction: discord.Interaction,
        kind: Literal["buy", "sale"],
        trade_id: int,
        quantity: str | None = None,
        price: str | None = None,
        occurred_at: str | None = None,
    ):
        await interaction.response.defer()
        now = int(time.time())
        await bot.ledger.edit(
            str(interaction.id),
            kind,
            trade_id,
            now,
            quantity=amount(quantity) if quantity else None,
            price=amount(price) if price else None,
            occurred_at=parse_time(occurred_at, now, bot.settings.timezone) if occurred_at else None,
        )
        await interaction.followup.send(
            "Trade corrected. Quantities and profit have been recalculated; your target price is unchanged."
        )

    @trade_group.command(name="void", description="Confirm removal of an incorrect trade from accounting.")
    async def trade_void(interaction: discord.Interaction, kind: Literal["buy", "sale"], trade_id: int):
        await interaction.response.send_message(
            f"Void {kind} #{trade_id}? The entry will remain in the audit history.",
            view=ConfirmVoid(bot, kind, trade_id),
            ephemeral=True,
        )

    tree.add_command(trade_group)

    @tree.command(
        name="suggest", description="Analyze independent opportunities within a manually supplied GP budget."
    )
    async def suggest(
        interaction: discord.Interaction,
        mode: Mode,
        budget: str | None = None,
        profile: Profile | None = None,
    ):
        await interaction.response.defer()
        preferences = await bot.ledger.preferences()
        selected_budget = amount(budget) if budget else preferences.budget
        selected_profile = profile or Profile(preferences.profile)
        results, rejected = await bot.market.suggest(
            mode, selected_profile, selected_budget, int(time.time())
        )
        lines = [
            f"**{mode.value.title()} • {selected_profile.value} • Budget {gp(selected_budget)}**\n"
            "Each suggestion independently uses this budget; these are not portfolio allocations."
        ]
        for result in results:
            a = result.analysis
            lines.append(
                f"**{result.name} ({result.item_id})**\n"
                f"Buy offer {gp(a.buy_low)}–{gp(a.buy_high)} • Sell offer {gp(a.sell_low)}–{gp(a.sell_high)}\n"
                f"Quantity: {result.quantity if result.quantity is not None else 'omitted: unknown buy limit'}\n"
                f"Conservative net/unit {gp(result.unit_profit)} • Total {gp(result.total_profit) if result.quantity is not None else 'unavailable'}\n"
                f"{result.explanation}"
            )
        if not results:
            lines.append(
                "**No qualifying suggestions. Filters were not relaxed.**\n"
                + "\n".join(f"{count} × {reason}" for reason, count in rejected.most_common(8))
                + "\nHistory preparation runs in the background; see /status."
            )
        await pages(bot, interaction, lines, per_page=2)

    async def send_chart(interaction, item, mode, purchase=None, target=None):
        now = int(time.time())
        await bot.market.ensure_history(item.id, mode, now)
        rows = await bot.market.observations(item.id, mode, now)
        image = await asyncio.to_thread(
            chart, f"{item.name} — {mode.value}", rows, bot.settings.timezone, purchase, target
        )
        await interaction.followup.send(file=discord.File(image, filename=f"item-{item.id}-{mode.value}.png"))

    @tree.command(
        name="item", description="Historical price analysis with an optional price and volume chart."
    )
    @app_commands.autocomplete(item=item_autocomplete)
    async def item_details(
        interaction: discord.Interaction, item: str, mode: Mode = Mode.QUICK, show_chart: bool = False
    ):
        await interaction.response.defer()
        now = int(time.time())
        chosen = await bot.market.resolve(item)
        await bot.market.ensure_history(chosen.id, mode, now)
        quote, _ = await bot.market.snapshot(chosen.id)
        text = f"**{chosen.name} ({chosen.id}) — {mode.value}**\nBuy limit: {chosen.buy_limit or 'unknown'}\n"
        if quote:
            text += (
                f"Observed instant-buy {gp(quote.high)} • Instant-sell {gp(quote.low)}\n"
                f"Fresh for mode: {'yes' if quote.fresh(now, RULES[mode].freshness) else 'no'}\n"
            )
        try:
            a = analyze(await bot.market.observations(chosen.id, mode, now), mode, now)
            text += (
                f"Entry band {gp(a.buy_low)}–{gp(a.buy_high)}\n"
                f"Historical exit band {gp(a.sell_low)}–{gp(a.sell_high)}\n"
                f"Trend {a.trend_percent:+.2f}% • Coverage {a.coverage:.0%}\n"
                "Historical bands are offer estimates, not guaranteed future prices."
            )
        except InsufficientData as exc:
            text += str(exc)
        await interaction.followup.send(text)
        if show_chart:
            await send_chart(interaction, chosen, mode)

    @tree.command(name="watch", description="Manage the watchlist; holdings are monitored automatically.")
    @app_commands.autocomplete(item=item_autocomplete)
    async def watch(
        interaction: discord.Interaction, action: Literal["add", "remove", "list"], item: str | None = None
    ):
        await interaction.response.defer()
        if action == "list":
            await pages(
                bot, interaction, [f"{i.name} ({i.id})" for i in await bot.market.items() if i.watched], 15
            )
            return
        if not item:
            raise UserError("Choose an item to add or remove.")
        chosen = await bot.market.resolve(item)
        await bot.ledger.item_flag(
            str(interaction.id), chosen.id, "watched", action == "add", int(time.time())
        )
        await interaction.followup.send(
            f"{chosen.name}: watchlist {'enabled' if action == 'add' else 'disabled'}."
        )

    @tree.command(name="favorite", description="Prioritize an item for analysis without bypassing filters.")
    @app_commands.autocomplete(item=item_autocomplete)
    async def favorite(interaction: discord.Interaction, item: str, enabled: bool = True):
        await interaction.response.defer()
        chosen = await bot.market.resolve(item)
        await bot.ledger.item_flag(str(interaction.id), chosen.id, "favorite", enabled, int(time.time()))
        await interaction.followup.send(f"{chosen.name}: favorite {'enabled' if enabled else 'disabled'}.")

    @tree.command(
        name="exclude", description="Exclude an item from suggestions; owned positions remain monitored."
    )
    @app_commands.autocomplete(item=item_autocomplete)
    async def exclude(interaction: discord.Interaction, item: str, enabled: bool = True):
        await interaction.response.defer()
        chosen = await bot.market.resolve(item)
        await bot.ledger.item_flag(str(interaction.id), chosen.id, "excluded", enabled, int(time.time()))
        await interaction.followup.send(
            f"{chosen.name}: discovery exclusion {'enabled' if enabled else 'disabled'}."
        )

    @tree.command(
        name="settings", description="View or set a manual suggestion budget and default risk profile."
    )
    async def settings(
        interaction: discord.Interaction, budget: str | None = None, profile: Profile | None = None
    ):
        await interaction.response.defer()
        if budget or profile:
            result = await bot.ledger.settings(
                str(interaction.id), amount(budget) if budget else None, profile, int(time.time())
            )
        else:
            p = await bot.ledger.preferences()
            result = {"budget": p.budget, "profile": p.profile}
        await interaction.followup.send(
            f"Suggestion budget: {gp(result['budget'])} • Default profile: {result['profile']}\n"
            "This is manually maintained; trades do not change it."
        )

    @tree.command(
        name="status", description="Monitoring, price freshness, preparation, and recent delivery errors."
    )
    async def status(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        now = int(time.time())
        lines = [
            f"Discord: {'connected' if bot.is_ready() else 'connecting'}",
            f"Open positions: {len(await bot.ledger.holdings())}",
            f"History requests in progress: {bot.market.preparing}",
            f"History preparation jobs checked: {bot.market.history_progress[0]}/{bot.market.history_progress[1]}",
        ]
        for name, stamp in bot.market.last_success.items():
            lines.append(f"{name}: last successful refresh {now - stamp}s ago")
        for name, error in list(bot.market.last_errors.items())[-8:]:
            lines.append(f"{name}: {error[:180]}")
        for failure in await bot.alerts.failures():
            lines.append(f"Alert {failure.reference} failed: {failure.error}")
        await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)


async def restore_views(bot):
    async with bot.db.sessions() as session:
        rows = (
            await session.scalars(
                select(AlertDelivery).where(
                    AlertDelivery.status == "sent", AlertDelivery.message_id.is_not(None)
                )
            )
        ).all()
        for row in rows:
            bot.add_view(PositionActions(bot, row.position_id, row.revision), message_id=row.message_id)
