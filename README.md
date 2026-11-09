# Personal OSRS flipping assistant

A single-user Discord bot for recording completed Grand Exchange purchases and sales,
watching your holdings, suggesting selling targets, and discovering possible flips.
Runs locally while your PC is on; the same process can later run on a host with persistent storage.

- **Quick:** session-length flips, using 24 hours of history.
- **Swing:** hours to seven days, using seven days of history.
- **Hold:** seven to thirty days, using thirty days of history.

The bot proposes **offer prices**, not guaranteed fills. It separates a patient sell-offer
estimate from an immediate-sale valuation. It never places in-game orders or records a
sale unless you explicitly report one. Recommendations use explainable statistical rules,
not AI predictions. They are initial heuristics, not a validated profitable strategy.

## Local setup

Requires Python 3.12 and internet access. PostgreSQL, a public web server, and Docker are optional/unneeded for local use.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -c requirements.lock
cp .env.example .env
# Fill in the Discord token and IDs; customize WIKI_USER_AGENT.
python -m app run
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`; copy the example with
`Copy-Item .env.example .env`. WSL uses the Linux commands above. Run commands from the project directory.
The database is created and migrated automatically on startup. `DB_PATH` always refers to SQLite;
legacy `DATABASE_URL` is ignored. A second instance using the same database exits immediately.
Stop with Ctrl+C. Network reconnects do not start duplicate polling loops.

The old PostgreSQL database is not imported, modified, or deleted. This release starts fresh.

## Create the private Discord bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and create an application.
2. Open **Bot**, create/reset the token, and put it in `DISCORD_TOKEN` in `.env`. Disable public installation if available.
3. Leave privileged intents, including Message Content, disabled.
4. Install to your own server using the `bot` and `applications.commands` scopes. Give it **View Channel**, **Send Messages**, **Embed Links**, **Attach Files**, and **Read Message History** in your private channel. Administrator is unnecessary.
5. Enable Developer Mode in Discord settings. Copy your user ID, server ID, and private text-channel ID into `.env`.
6. Start the bot. Commands sync only to the configured server. Run `/status` in the configured channel.

Every command, autocomplete, button, and submitted form is owner-restricted. The bot refuses
commands in other channels, servers, and DMs. Channel privacy itself is managed through Discord permissions.
Only holding alerts mention you. New suggestions appear when requested with `/suggest`.
No Discord messages are sent by the test suite.

## Typical workflow

```text
/settings budget:10m profile:balanced
/suggest mode:quick
/buy item:Coal quantity:10k price:130 mode:quick
/holdings
/position position_id:1 show_chart:True
/target set position_id:1 net_percent:2
/sell position_id:1 quantity:5k price:140
/history
```

Pick item names from autocomplete or enter an exact item ID. Quantity and GP fields accept
whole values, commas, and exact `k`/`m`/`b` suffixes: `1.5k` is 1,500; `1.5` GP is rejected.
Purchase and sale prices are **per item, before selling tax**. Optional times use ISO format,
for example `2026-11-09T14:30-06:00`; times without an offset use `TIMEZONE`.

Each purchase is a separate numbered position. Sales must reference that position and may be
partial. The ledger rejects overselling and sales predating the purchase. Realized profit uses
actual recorded sale prices; estimated profit describes only the remaining quantity.
The saved suggestion budget is manually maintained and is not your inferred in-game bank balance.

| Command | Purpose |
|---|---|
| `/buy`, `/sell` | Record completed purchases and full/partial sales |
| `/holdings`, `/position`, `/history` | Open holdings, position details, and realized profit |
| `/target set`, `/target reset` | Set a price/net ROI target or explicitly recalculate one |
| `/risk` | Set a position's immediate-sale loss warning threshold |
| `/snooze` | Mute a position for 1 minute to 30 days, then recheck |
| `/trade edit`, `/trade void` | Correct an entry or confirm a void; records remain auditable |
| `/suggest` | Up to five independently budgeted opportunities in the selected mode/profile |
| `/item` | Historical analysis and an optional price/volume chart |
| `/watch` | Add/remove/list items to monitor beyond your holdings |
| `/favorite`, `/exclude` | Prioritize analysis or exclude an item from discovery |
| `/settings`, `/status` | Budget/profile preferences, data freshness, and recent failures |

`/trade` identifies buys by position ID and sales by sale ID. A purchase with active sales cannot
be voided until those sales are voided. Purchase corrections cannot reduce quantity below units
already sold or move the purchase after a sale. Editing a purchase recalculates profit without
silently changing the existing target.

A purchase can be recorded during a market-data outage. If history is unavailable or does not
support a profitable target, the position has no automatic target. Supply your own target or
use `/target reset` after history is prepared. Targets never move downward automatically.
Forms expire after five minutes, confirmation prompts after two, and paginated views after three.
Use a fresh command if a form or old position button is no longer valid.

## Recommendations and alerts

See [the analysis specification](docs/analysis.md) for exact windows, filters, and defaults.

The bot bulk-fetches current market data and gradually prepares history for holdings first,
then watched/favorite items, then up to twenty shortlisted candidates per mode. Each history
request is serialized at least one second apart. Commands and background tasks share the cache.
A cold start can take several minutes. `/suggest` shows only prepared candidates; no results
means the bot has not found a qualifying item, not that it should relax its filters.

Alerts require two distinct, consecutive completed five-minute buckets. Repeated polling of the
same bucket does not count twice. Targets trigger once per revision. Warnings have per-mode
cooldowns. Snoozing explicitly rearms the target after expiry. Closing a position cancels pending alerts.
Missing/stale prices suppress actionable notifications. Cached values may still appear with their age.

Alert state and delivery attempts survive restarts. Transient Discord failures retry with backoff;
uncertain sends are checked against the last 100 channel messages using a stable reference.
A rare duplicate remains possible if delivery is ambiguous and its message cannot be found.
Permanent permission failures stop retries and appear in `/status`: fix the permissions, then
reset the target or snooze the position to rearm it. Bot buttons on historical alerts validate
that their position/revision is still current.

Price charts are generated on request and show missing observations as gaps. No predictive
bands or interpolated fake prices are shown.

## Data and backups

The local database defaults to `data/osrs.sqlite3`. It includes purchases, sales, targets,
preferences, market observations, audit records, and alert state. Transaction history is retained;
fine-grained prices are retained seven days and hourly/coarser prices ninety days.
Whole-market latest quotes and aggregates are replaced, not accumulated indefinitely.
Detailed history is accumulated only for holdings, watched items, favorites, and analyzed candidates.

Daily SQLite-consistent backups keep seven daily copies under `BACKUP_DIR`. For a manual backup
or transaction export, including voided entries:

```sh
python -m app backup --output /path/to/backups/osrs-before-change.sqlite3
python -m app export --output /path/to/exports/trades.csv
```

Existing output files are not overwritten. Use the backup command rather than copying a running
SQLite file: committed changes can also be in its WAL journal. Keep an additional backup outside
your PC/container for protection against disk loss.

To restore or move hosts: stop the bot, make a backup, move the backup to the destination's
`DB_PATH`, configure credentials there, then start **one** instance. Do not copy old `-wal`, `-shm`,
or lock files with a backup snapshot. Keep the original database until the new instance is verified.
There is no synchronization between local and hosted instances.

## Container and later hosting

```sh
docker compose up --build -d
docker compose logs -f bot
docker compose down
```

Compose uses a named `bot_data` volume mounted at `/data`; the container runs as an unprivileged
user. `.env` is supplied at runtime and excluded from the image. Do not use `docker compose down -v`
unless you intend to delete the database and backups. Avoid running the local process and container
simultaneously: each may have its own database lock while using the same Discord application.

To back up a running Compose bot:

```sh
docker compose exec bot python -m app backup --output /data/manual-backup.sqlite3
# Copy the backup out using the container name/ID shown by docker compose ps.
docker compose cp bot:/data/manual-backup.sqlite3 ./manual-backup.sqlite3
```

A future host needs an always-running **worker/container**, outbound HTTPS/WebSocket access,
a persistent writable disk mounted at `/data`, and environment secrets. There is no HTTP port
or health endpoint. A sleeping/ephemeral web service is unsuitable for continuous monitoring.
The obsolete Render API/PostgreSQL blueprint has been removed; no deployment is performed.

For optional Linux/WSL startup, customize [the user service example](deploy/osrs-flips.service.example),
copy it to `~/.config/systemd/user/osrs-flips.service`, then run:

```sh
systemctl --user daemon-reload
systemctl --user enable --now osrs-flips
journalctl --user -u osrs-flips -f
```

WSL must have systemd enabled. Closing/suspending Windows or stopping WSL interrupts monitoring.
User-service startup after login is sufficient; no system-wide service or unattended hosting is assumed.

## Development and verification

```sh
python -m pip install -r requirements-dev.txt -c requirements.lock
python -m pytest
ruff check app tests
ruff format --check app tests
# Explicit opt-in read-only live provider check:
python -m app check-api
```

Tests use temporary SQLite databases, captured-shape fixtures, mocked HTTP, and mocked Discord.
They never require a token. Database tests need normal OS thread/asyncio support; restrictive
execution sandboxes may prevent aiosqlite's worker callbacks from waking the event loop.
`requirements.lock` records the validated runtime and development dependency versions.

Before daily use, test the commands in your private server, exercise partial sales and corrections,
check charts, observe a simulated target/rearm, and restart the bot. A live play-session smoke test
requires your credentials and in-game observations; passing offline tests does not substitute for it.
