# Analysis and operating rules

Source: [OSRS Wiki price API v2](https://oldschool.runescape.wiki/w/RuneScape%3AReal-time_Prices).
The API high is an observed instant-buy price and low is an observed instant-sell price.
A patient buyer may bid around low; a patient seller may offer around high. These are observations,
not an order book. Prices can exceed signed 32-bit GP, and average prices can contain two decimal places.
History uses the provider's actual timestep, startTimestamp, and endTimestamp metadata.

## Windows and bands

| Mode | History | Entry window | Exit window / quantiles | Trend comparison | Minimum net ROI |
|---|---|---|---|---|---|
| Quick | 24h | 1h | 1h / 25th–50th | Last 30m vs prior 30m | 0.5% |
| Swing | 7d | 6h | 7d / 40th–60th | Last 6h vs prior 6h | 2% |
| Hold | 30d | 24h | 30d / 40th–60th | Last 24h vs prior 24h | 5% |

Entry uses the 25th–50th percentiles of valid low averages. Quantiles use linear interpolation
at `(n-1)*p`. Round buy bands up and sell bands down to whole GP. Conservative profit uses the
upper buy and lower sell. Midpoints are `(high+low)/2`; trend is the percentage difference
between medians in the two windows. Swing/hold require a nonnegative trend; quick allows at
most a 1% recent decline. Latest low must fall within 1% of the entry band.

Require 80% valid coverage over the historical window. Coverage is the union of valid interval
durations, so duplicate/overlapping buckets do not count twice. A valid interval has positive
prices and positive volume on both sides. Missing buckets stay missing. All buckets must be
completed at evaluation time, and observations collected after a replay time cannot be used.
If the provider's interval is too coarse for a required comparison window, analysis is unavailable.

## Liquidity, sizing, ranking

| Profile | Minimum observed hourly volume on each side | Participation cap |
|---|---:|---:|
| Conservative | 100 | 2% |
| Balanced | 50 | 5% |
| Aggressive | 10 | 10% |

Cap suggested quantity by `budget / upper_buy`, known buy limit minus recorded buys in the
preceding four hours, and participation times the quieter-side historical volume. Sizing uses
1h volume for quick, 24h for swing, and 7d for hold. No quantity is supplied for unknown limits.
Recorded-buy limits are conservative approximations, not synchronized in-game limit timers.
Shared limit groups and unrecorded trades are not known.

Exclude bonds from discovery because conversion costs are outside the ordinary tax model.
Favorites receive priority in the twenty-item shortlist without bypassing quality filters.
An excluded item is still monitored if owned. A shortlist is a bounded discovery sample,
not a guarantee that the bot has deeply analyzed every listed item.

Rank candidates within a mode using weighted percentile ranks: 50% conservative total GP profit,
20% net ROI, 20% quieter-side sizing volume, and 10% lower volatility. Volatility is median absolute
hour-over-hour midpoint percentage change, using medians of actual observations in each hour;
missing hours are skipped. Ties break by item ID. Unknown-quantity candidates have no projected
total profit and are not assigned a fictitious full-budget position size.

## Tax

Tax is `min(unit_sale_price // 50, 5_000_000)` per item, except the reviewed exemption list
in `app/domain/tax_exemptions.json`. Multiply **after** calculating per-item tax. Actual trades
are whole GP; observations retain hundredths of GP. Break-even/net ROI targets find the smallest
whole sale price meeting required net proceeds. The exemptions include energy-potion dose
variants but only specifically listed teleport/jewelry variants. See the
[Wiki exemption list](https://oldschool.runescape.wiki/w/Grand_Exchange#Exempt_from_tax).
Review this small data file when Jagex changes the tax rules; item mapping does not include tax status.

## Alerts and freshness

Latest high/low trade timestamps and local retrieval must be no more than 5m/30m/2h old for
quick/swing/hold, respectively. Five-minute alert aggregates must have ended within ten minutes;
hourly aggregates must have ended within two hours. Future-dated values are not actionable.
A target must be supported by both latest high and five-minute high average in two consecutive,
distinct, completed five-minute buckets. The current patient-sale estimate is the lower of
those two observations. Immediate-sale profit uses latest low. No action executes in-game.

Downside warnings use a 5% immediate-sale net loss by default (adjustable with `/risk`) or a
midpoint decline of 2%/5%/10% in the mode's trend window. They require the same two-bucket
confirmation. Warning cooldowns are 30m/6h/24h. A missing/stale observation breaks the confirmation
streak. Coincident target and warning signals produce one message that explains both.

A target stays fixed until you change/reset it. A suggested target must have fresh market data,
adequate history, a qualifying trend, and the minimum mode ROI against your actual purchase price.
An unsupported purchase can still be recorded with no target. Changes in history never silently
lower your existing target.

Refresh latest quotes every minute, bulk five-minute averages every five minutes, hourly averages
hourly, and mapping daily. History refresh TTLs are 5m/15m/1h. A server returning a long Retry-After
pauses that request path until its retry time; normal transient failures receive bounded retries.
All thresholds are initial engineering defaults, not calibrated probabilities or profit guarantees.

Optional `ANALYSIS_OVERRIDES` in `.env` can override `min_roi` and `decline_warning` per mode,
for example `ANALYSIS_OVERRIDES='{"quick":{"min_roi":"1","decline_warning":"3"}}'`.
Values are percentages and apply on restart. Other defaults live in `app/domain/types.py`.

Thirty-day history currently arrives in six-hour intervals. Hold-mode preparation also fetches
seven-day context for genuine hourly volatility; it never creates synthetic hourly prices.
If the provider cannot supply hourly observations, volatility is unavailable and contributes
no stability credit to ranking instead of being reported as zero risk.
