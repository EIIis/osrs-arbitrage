"""Headless, on-demand charts. Rendering runs off the Discord event loop."""

import threading
from datetime import UTC, datetime
from io import BytesIO
from zoneinfo import ZoneInfo

import matplotlib.dates as mdates
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

_chart_lock = threading.Lock()


def chart(name, rows, zone="America/Chicago", purchase=None, target=None) -> BytesIO:
    with _chart_lock:
        figure = Figure(figsize=(10, 5), layout="constrained")
        FigureCanvasAgg(figure)
        prices, volumes = figure.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [3, 1]})
        dates, highs, lows, totals = [], [], [], []
        previous_end = None
        for row in sorted(rows, key=lambda b: b.start):
            date = datetime.fromtimestamp(row.start, UTC)
            if previous_end is not None and row.start > previous_end:
                dates.append(datetime.fromtimestamp(previous_end, UTC))
                highs.append(float("nan"))
                lows.append(float("nan"))
                totals.append(float("nan"))
            dates.append(date)
            highs.append(float(row.high) if row.high is not None else float("nan"))
            lows.append(float(row.low) if row.low is not None else float("nan"))
            totals.append(row.high_volume + row.low_volume)
            previous_end = row.end
        prices.plot(dates, highs, label="Observed high average", linewidth=1)
        prices.plot(dates, lows, label="Observed low average", linewidth=1)
        if purchase:
            prices.axhline(purchase, color="gray", linestyle="--", label="Purchase")
        if target:
            prices.axhline(target, color="green", linestyle="--", label="Target")
        prices.set(title=name, ylabel="GP")
        prices.legend(loc="best", fontsize=8)
        prices.ticklabel_format(axis="y", style="plain", useOffset=False)
        volumes.plot(dates, totals, linewidth=1)
        volumes.set(ylabel="Volume", xlabel=zone)
        volumes.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M", tz=ZoneInfo(zone)))
        output = BytesIO()
        figure.savefig(output, format="png", dpi=120)
        figure.clear()
        output.seek(0)
        return output
