"""Configuration has no I/O at import time and never reads legacy DATABASE_URL."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    discord_token: str = ""
    discord_owner_id: int = Field(default=0, ge=0)
    discord_guild_id: int = Field(default=0, ge=0)
    discord_channel_id: int = Field(default=0, ge=0)
    db_path: Path = Path("data/osrs.sqlite3")
    backup_dir: Path = Path("data/backups")
    timezone: str = "America/Chicago"
    wiki_api_url: str = "https://prices.runescape.wiki/api/v2/osrs"
    wiki_user_agent: str = "osrs-arbitrage/0.2 (personal Discord flipping assistant)"
    log_level: str = "INFO"
    default_budget: int = Field(default=0, ge=0, le=2**63 - 1)
    history_request_interval: float = Field(default=1.0, ge=1.0)
    history_shortlist_size: int = Field(default=20, ge=1, le=20)

    analysis_overrides: dict[str, dict[str, Decimal]] = Field(default_factory=dict)

    @field_validator("analysis_overrides")
    @classmethod
    def valid_overrides(cls, value):
        for mode, fields in value.items():
            if mode not in ("quick", "swing", "hold"):
                raise ValueError("Unknown analysis mode")
            for name, threshold in fields.items():
                if (
                    name not in ("min_roi", "decline_warning")
                    or not threshold.is_finite()
                    or not 0 < threshold <= 100
                ):
                    raise ValueError(
                        "Override min_roi/decline_warning with a percentage greater than 0 and at most 100"
                    )
        return value

    def apply_analysis_overrides(self):
        from app.domain.types import RULES, Mode

        for mode, fields in self.analysis_overrides.items():
            RULES[Mode(mode)] = replace(RULES[Mode(mode)], **fields)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Use an IANA timezone such as America/Chicago.") from exc
        return value

    def require_discord(self) -> None:
        missing = [
            key
            for key in ("discord_token", "discord_owner_id", "discord_guild_id", "discord_channel_id")
            if not getattr(self, key)
        ]
        if missing:
            raise ValueError("Configure " + ", ".join(key.upper() for key in missing) + " in .env")
