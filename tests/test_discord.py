from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.client import FlipBot
from app.bot.interface import Pages, PositionActions, PositionModal, authorized
from app.config.settings import Settings
from app.domain.types import Bucket, Mode
from app.services.charts import chart

NOW = 1794268800


@pytest.fixture
async def bot(database):
    bot = FlipBot(
        Settings(_env_file=None, discord_owner_id=1, discord_guild_id=2, discord_channel_id=3), database
    )
    yield bot
    await bot.close()


def interaction(user=1, guild=2, channel=3):
    return SimpleNamespace(
        user=SimpleNamespace(id=user),
        guild_id=guild,
        channel_id=channel,
        id=777,
        response=SimpleNamespace(
            is_done=lambda: False,
            send_message=AsyncMock(),
            defer=AsyncMock(),
            send_modal=AsyncMock(),
            edit_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


@pytest.mark.parametrize(("user", "guild", "channel"), [(9, 2, 3), (1, 9, 3), (1, 2, 9), (1, None, 3)])
async def test_unauthorized_commands_and_components(bot, user, guild, channel):
    i = interaction(user, guild, channel)
    assert not authorized(bot.settings, i)
    assert not await bot.tree.interaction_check(i)
    assert not await Pages(bot, ["x"]).interaction_check(i)
    assert not await PositionActions(bot, 1, 1).interaction_check(i)


async def test_registered_commands_and_serialization(bot):
    names = {command.name for command in bot.tree.get_commands()}
    assert names == {
        "buy",
        "sell",
        "holdings",
        "position",
        "target",
        "risk",
        "snooze",
        "history",
        "trade",
        "suggest",
        "item",
        "watch",
        "favorite",
        "exclude",
        "settings",
        "status",
    }
    payloads = [command.to_dict(bot.tree) for command in bot.tree.get_commands()]
    assert all(p["name"] for p in payloads)
    assert not bot.intents.message_content


async def test_autocomplete_is_restricted_and_exact_ids(bot):
    buy = bot.tree.get_command("buy")
    autocomplete = buy._params["item"].autocomplete
    assert await autocomplete(interaction(user=9), "Coal") == []
    choices = await autocomplete(interaction(), "coal")
    assert choices[0].value == "453"


async def test_pagination_and_stale_position_buttons(bot):
    p = await bot.ledger.buy("buy", 453, 10, 100, Mode.QUICK, NOW)
    view = PositionActions(bot, p["position_id"], 1)
    assert await view.interaction_check(interaction())
    await bot.ledger.target("target", p["position_id"], NOW, price=120)
    from app.core.exceptions import UserError

    with pytest.raises(UserError, match="changed"):
        await view.interaction_check(interaction())
    pages = Pages(bot, ["first", "second"])
    await pages.next.callback(interaction())
    assert pages.index == 1 and pages.next.disabled
    assert "Page 2/2" in pages.embed().footer.text


async def test_form_timeout_and_owner_recheck(bot):
    form = PositionModal(bot, 1, 1, "sell")
    assert form.timeout == 300
    await form.on_submit(interaction(user=9))
    assert await bot.ledger.holdings() == []


def test_headless_chart_with_gaps_and_markers():
    rows = [
        Bucket(NOW - 900, 300, Decimal("120.5"), Decimal("100.25"), 100, 100, NOW),
        Bucket(NOW - 300, 300, None, Decimal("101"), 0, 50, NOW),
    ]
    output = chart("Coal — quick", rows, purchase=100, target=120)
    assert output.read(8) == b"\x89PNG\r\n\x1a\n"
    assert output.getbuffer().nbytes > 10000
