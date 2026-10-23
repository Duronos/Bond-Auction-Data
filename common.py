"""
Shared analysis and charting for every country.

Each country loader (see countries/) returns one row per auction with these
standard columns:

    auction_date   Timestamp
    maturity_date  Timestamp
    bid_to_cover   float   total bid / amount sold
    accepted       float   amount sold, in local currency units
    auction_yield  float   clearing (or average) yield, %
    cb_accepted    float   central bank's take at auction, local currency
                           units; NaN where the central bank doesn't
                           participate or doesn't report it

Loaders may add extra columns (e.g. the US bidder-class breakdown).
"""

import copy
import math
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from plotnine import (
    ggplot,
    aes,
    geom_line,
    geom_point,
    labs,
    theme_minimal,
    theme,
    element_text,
    scale_y_continuous,
    scale_x_datetime,
    facet_wrap,
    coord_cartesian,
    scale_color_manual,
    scale_linetype_manual,
    guides,
    guide_legend,
)

# Maturity buckets, shortest first. The order matters: it sets the legend
# order and the top-to-bottom panel order on the global charts.
BUCKETS = ["Short (≤2Y)", "Intermediate (2–10Y)", "Long (>10Y)"]

# Optional per-auction columns summed into the monthly table when present.
# Only the US loader provides these (who won each auction: primary dealers,
# direct bidders, indirect bidders).
EXTRA_SUM_COLUMNS = [
    "primary_dealer_accepted",
    "direct_bidder_accepted",
    "indirect_bidder_accepted",
]


@dataclass
class Country:
    """
    Everything the pipeline needs to know about one country. Each file in
    countries/ builds one of these; adding a country means writing a new
    `fetch` and filling in the labels below.
    """

    code: str                    # folder / file prefix, e.g. "canada"
    name: str                    # display name, e.g. "Canada"
    fetch: Callable[[pd.Timestamp, pd.Timestamp], pd.DataFrame]
    volume_label: str            # y-axis label for the volume chart
    volume_divisor: float        # local units -> volume_label units
    source: str                  # chart caption source line
    yield_note: str              # how the yield is defined for this country
    central_bank: str | None = None  # name, if it buys at auction
    # Functions taking the monthly table and returning extra
    # {chart_name: (plot, formula)} entries, e.g. the US non-dealer chart.
    extra_plots: list = field(default_factory=list)
    # Optional daily secondary-market yields for the recent charts: takes
    # (start, end) and returns one row per date and maturity with columns
    # date, tenor_months, market_yield (%).
    fetch_market: Callable[[pd.Timestamp, pd.Timestamp], pd.DataFrame] | None = None
    market_source: str | None = None  # caption: who publishes it and what kind of yield


def prepare(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Filter to the window, drop auctions without results, and bucket."""
    df = df.copy()
    # errors="coerce" turns unparseable dates into NaT (missing) instead of
    # crashing; those rows are filtered out below.
    df["auction_date"] = pd.to_datetime(df["auction_date"], errors="coerce")
    df["maturity_date"] = pd.to_datetime(df["maturity_date"], errors="coerce")

    # Time left until the security is repaid, measured from the auction.
    # A reopened 10-year bond sold 6 months later counts as 9.5 years here.
    df["years_to_maturity"] = (
        (df["maturity_date"] - df["auction_date"]).dt.days / 365.25
    )

    # Announced-but-not-yet-held auctions have no results; drop them so
    # they don't show up as empty months.
    df = df[
        df["auction_date"].between(start, end)
        & df["maturity_date"].notna()
        & (df["years_to_maturity"] > 0)
        & df["accepted"].notna()
        & (df["accepted"] > 0)
    ].copy()

    # Sort each auction into a bucket. right=True makes the upper edge
    # inclusive, so exactly 2 years is Short and exactly 10 is Intermediate.
    df["bucket"] = pd.cut(
        df["years_to_maturity"],
        bins=[0, 2, 10, math.inf],
        labels=BUCKETS,
        right=True,
        ordered=True,
    )
    # Snap every date to the first of its month (2026-09-17 -> 2026-09-01)
    # so auctions can be grouped by month.
    df["month"] = df["auction_date"].dt.to_period("M").dt.to_timestamp()
    return df


def make_monthly_data(df: pd.DataFrame, volume_divisor: float) -> pd.DataFrame:
    """
    Aggregate by month and maturity bucket.

    bid_to_cover and auction_yield are monthly medians of auction-level values.
    Currency totals are gross auction amounts, not net investment flows.

    Medians are used (rather than means) so one unusual auction in a month
    can't drag the whole month's value around.
    """
    # Each entry is new_column=(source_column, how_to_combine).
    aggs = dict(
        auctions=("auction_date", "count"),
        bid_to_cover=("bid_to_cover", "median"),
        accepted=("accepted", "sum"),
        auction_yield=("auction_yield", "median"),
        # min_count=1 keeps months with no central-bank reporting as NaN
        # rather than a misleading 0.
        cb_accepted=("cb_accepted", lambda s: s.sum(min_count=1)),
    )
    for col in EXTRA_SUM_COLUMNS:
        if col in df.columns:
            aggs[col] = (col, "sum")
    # What the issuer bought at its own auctions (Czech T-bills), kept out of
    # bid-to-cover and volume but recorded in the monthly CSV.
    if "issuer_retained" in df.columns:
        aggs["issuer_retained"] = ("issuer_retained", lambda s: s.sum(min_count=1))

    # One row per (month, bucket). observed=True skips bucket/month
    # combinations with no auctions instead of creating empty rows.
    monthly = (
        df.groupby(["month", "bucket"], observed=True)
        .agg(**aggs)
        .reset_index()
        .sort_values(["bucket", "month"])
    )

    # Convert to chart units, e.g. dollars -> billions of dollars.
    monthly["volume"] = monthly["accepted"] / volume_divisor

    # Central bank's slice of everything sold that month. .where(... != 0)
    # turns a zero denominator into NaN so we never divide by zero.
    monthly["cb_share_pct"] = (
        100 * monthly["cb_accepted"] / monthly["accepted"].where(monthly["accepted"] != 0)
    )

    # Columns that get a smoothed "_3m" version for the charts.
    rolled = ["bid_to_cover", "volume", "auction_yield", "cb_share_pct"]

    # Direct + indirect bidders are used here as a rough proxy for
    # non-primary-dealer demand (US only).
    if all(c in monthly.columns for c in EXTRA_SUM_COLUMNS):
        bidder_total = monthly[EXTRA_SUM_COLUMNS].sum(axis=1)
        monthly["nondealer_share_pct"] = (
            100
            * (monthly["direct_bidder_accepted"] + monthly["indirect_bidder_accepted"])
            / bidder_total.where(bidder_total != 0)
        )
        rolled.append("nondealer_share_pct")

    # 3-month rolling averages make the multi-year charts easier to read.
    # Grouping by bucket keeps each bucket's average to its own history, and
    # min_periods=1 lets the first two months average over what exists.
    for col in rolled:
        monthly[f"{col}_3m"] = (
            monthly.groupby("bucket", observed=True)[col]
            .transform(lambda s: s.rolling(3, min_periods=1).mean())
        )

    return monthly


# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------

# Look shared by every chart: clean background, 12x6 inches, slanted year
# labels so they don't collide, legend underneath.
BASE_THEME = theme_minimal() + theme(
    figure_size=(12, 6),
    axis_text_x=element_text(rotation=30, ha="right"),
    legend_position="bottom",
)

# A tick every year keeps long windows readable.
X_YEARS = scale_x_datetime(date_breaks="1 year", date_labels="%Y")

# Eight categorical colours, in a fixed order chosen so neighbouring colours
# stay distinguishable for colour-blind readers (blue, orange, aqua, yellow,
# magenta, green, violet, red).
SERIES_COLORS = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
    "#e87ba4", "#008300", "#4a3aa7", "#e34948",
]
# The first three are distinct from each other in every combination, so
# they carry the three maturity buckets.
BUCKET_COLORS = dict(zip(BUCKETS, SERIES_COLORS[:3]))
# More than eight series can't each get a distinct colour, so the global
# charts pair the eight colours with four line styles: 8 x 4 = 32 unique
# combinations, enough for every country (25 today).
LINE_STYLES = ["solid", "dashed", "dotted", "dashdot"]


def country_styles(countries: list[str]) -> tuple[dict, dict]:
    """
    Fixed colour and line style per country, assigned by its position in the
    full country list (not by which countries appear in a chart), so a
    country keeps the same look on every chart and in every run.
    """
    colors = {c: SERIES_COLORS[i % len(SERIES_COLORS)] for i, c in enumerate(countries)}
    styles = {c: LINE_STYLES[(i // len(SERIES_COLORS)) % len(LINE_STYLES)] for i, c in enumerate(countries)}
    return colors, styles

# Written in matplotlib's LaTeX-style "mathtext": text between $...$ is
# typeset as maths, \dfrac draws a fraction, and "\ " is a visible space.
BID_TO_COVER_FORMULA = (
    r"Bid-to-cover $= \dfrac{\mathrm{Total\ tendered\ (bid)}}{\mathrm{Total\ accepted\ (sold)}}$"
)


def wrap(caption: str, width: int = 140) -> str:
    """Break long captions onto several lines so they aren't cut off at the edge."""
    return "\n".join(textwrap.wrap(caption, width))


def line_chart(data, y, color, title, subtitle, ylab, caption, y_limits=None, facet=False):
    """
    Build one line chart of `y` over time, with one line per `color` group.

    color="bucket"   one country, a line per maturity (the country charts)
    color="country"  a line per country; with facet=True, a separate panel
                     per maturity bucket stacked vertically (the global charts)
    """
    # Drop months missing this metric so lines don't break or plot as 0.
    data = data.dropna(subset=[y]).copy()
    legend_title = "Maturity" if color == "bucket" else "Country"

    if color == "country":
        # Styles come from the full country list so they never shift, then
        # countries with no data for this metric are dropped from the legend.
        colors, styles = country_styles(list(data[color].cat.categories))
        data[color] = data[color].cat.remove_unused_categories()
        present = list(data[color].cat.categories)
        mapping = aes(x="month", y=y, color=color, linetype=color)
        scales = [
            scale_color_manual(values={c: colors[c] for c in present}, name=legend_title),
            scale_linetype_manual(values={c: styles[c] for c in present}, name=legend_title),
            # Same title and layout on both guides, so they merge into one
            # legend showing each country's colour and line style together.
            guides(color=guide_legend(ncol=6), linetype=guide_legend(ncol=6)),
            theme(legend_key_width=36),
        ]
    else:
        mapping = aes(x="month", y=y, color=color)
        scales = [scale_color_manual(values=BUCKET_COLORS, name=legend_title)]

    # plotnine builds charts in layers joined with "+": data and axes
    # mapping, then the lines, then labels, then axis scale and styling.
    plot = (
        ggplot(data, mapping)
        + geom_line(size=1.0 if not facet else 0.8)
        + labs(
            title=title,
            subtitle=subtitle,
            x="Auction month",
            y=ylab,
            caption=wrap(caption),
        )
        + X_YEARS
        + BASE_THEME
    )
    # Added one at a time: plotnine can't combine some of these with "+".
    for layer in scales:
        plot += layer
    if y_limits:
        plot += scale_y_continuous(limits=y_limits)
    if facet:
        # Three stacked panels need a taller figure. These are added in two
        # steps because plotnine can't combine a facet and a theme with "+".
        plot += facet_wrap("bucket", ncol=1)
        plot += theme(figure_size=(12, 12))
    return plot


def grid_chart(data, y, title, subtitle, ylab, caption):
    """
    Small multiples: one panel per country, a line per maturity bucket.
    All panels share the y-axis so countries can be compared by eye.
    """
    data = data.dropna(subset=[y]).copy()
    data["country"] = data["country"].cat.remove_unused_categories()
    n_countries = data["country"].nunique()
    n_rows = -(-n_countries // 4)  # ceiling division: 4 panels per row

    # One extreme episode (e.g. Japan's 2012 bill auctions at ~22x cover)
    # would squash every other panel, so zoom the shared axis to the 99th
    # percentile. coord_cartesian zooms without dropping data: the extreme
    # lines just run off the top of their panel.
    low, high = data[y].min(), data[y].quantile(0.99)
    zoomed = data[y].max() > high * 1.05
    if zoomed:
        caption += " Axis capped at the 99th percentile; a few extreme values run off the top."

    plot = (
        ggplot(data, aes(x="month", y=y, color="bucket"))
        + geom_line(size=0.6)
        + labs(title=title, subtitle=subtitle, x="Auction month", y=ylab,
               caption=wrap(caption, 170))
        + scale_color_manual(values=BUCKET_COLORS, name="Maturity")
        # Small panels can't fit a label every year, so tick every 5 years.
        + scale_x_datetime(date_breaks="5 years", date_labels="%Y")
        + BASE_THEME
    )
    if zoomed:
        plot += coord_cartesian(ylim=(min(low, 0), high * 1.05))
    plot += facet_wrap("country", ncol=4)
    plot += theme(figure_size=(14, 2.6 * n_rows + 2))
    return plot


def has_data(monthly: pd.DataFrame, column: str) -> bool:
    """True if the column exists and has at least one value to plot."""
    return column in monthly.columns and monthly[column].notna().any()


def country_plots(country: Country, monthly: pd.DataFrame) -> dict:
    """
    Return {chart_name: (plot, formula_or_None)} for one country.

    Not every country publishes every measure (e.g. the Netherlands reports
    no bids, so it has no bid-to-cover), so each chart is only built when
    its data exists.
    """
    name = country.name
    plots = {}

    if has_data(monthly, "bid_to_cover_3m"):
        plots["bid_to_cover"] = (
            line_chart(
                monthly, "bid_to_cover_3m", "bucket",
                f"{name}: government bond auction demand by maturity",
                "3-month average of monthly median bid-to-cover ratios",
                "Bid-to-cover ratio",
                f"Source: {country.source}. Higher bid-to-cover = more bid per unit sold.",
            ),
            BID_TO_COVER_FORMULA,
        )

    plots["accepted"] = (
        line_chart(
            monthly, "volume_3m", "bucket",
            f"{name}: gross government securities sold at auction",
            "3-month average of monthly auction totals",
            country.volume_label,
            f"Source: {country.source}. Gross primary-market auction volume, not net borrowing.",
        ),
        None,
    )

    if has_data(monthly, "auction_yield_3m"):
        plots["auction_yield"] = (
            line_chart(
                monthly, "auction_yield_3m", "bucket",
                f"{name}: government auction yields by maturity",
                "3-month average of monthly median auction yields",
                "Auction yield (%)",
                f"{country.yield_note} Lower yield = cheaper borrowing for the government.",
            ),
            None,
        )

    # Only for countries whose central bank buys at auction and reports it
    # (currently the US and Canada).
    if country.central_bank and has_data(monthly, "cb_share_pct"):
        plots["central_bank_share"] = (
            line_chart(
                monthly, "cb_share_pct_3m", "bucket",
                f"{name}: {country.central_bank} share of auction awards",
                "Central bank purchases at auction as a share of total sold, 3-month average",
                "Central bank share (%)",
                f"Source: {country.source}.",
            ),
            None,
        )

    # Country-specific charts (e.g. the US non-dealer share).
    for extra in country.extra_plots:
        plots.update(extra(monthly))

    return plots


def global_plots(combined: pd.DataFrame) -> dict:
    """
    Charts comparing every country, one panel per maturity bucket.

    `combined` is every country's monthly table stacked together with a
    `country` column added. There's no volume chart here because each
    country's volume is in its own currency.

    Each measure gets two versions:
      <measure>          one panel per maturity, a line per country (direct
                         comparison, but crowded with many countries)
      <measure>_grid     one small panel per country, a line per maturity
                         (easier to read each country's own trend)
    """
    measures = [
        (
            "bid_to_cover", "bid_to_cover_3m", "Bid-to-cover ratio",
            "Government bond auction demand",
            "3-month average of monthly median bid-to-cover ratios",
            "Countries define bid-to-cover slightly differently; compare trends more than levels.",
            BID_TO_COVER_FORMULA,
        ),
        (
            "auction_yield", "auction_yield_3m", "Auction yield (%)",
            "Government auction yields",
            "3-month average of monthly median auction yields",
            "Some countries report the clearing (highest accepted) yield, others the "
            "average accepted yield; see each country's own chart.",
            None,
        ),
    ]

    plots = {}
    for key, column, ylab, title, subtitle, caption, formula in measures:
        if not has_data(combined, column):
            continue
        plots[key] = (
            line_chart(
                combined, column, "country",
                f"{title}: all countries", f"{subtitle}, by maturity",
                ylab, caption, facet=True,
            ),
            formula,
        )
        plots[f"{key}_grid"] = (
            grid_chart(combined, column, f"{title}: by country", subtitle, ylab, caption),
            formula,
        )

    if has_data(combined, "cb_share_pct_3m"):
        plots["central_bank_share"] = (
            line_chart(
                combined, "cb_share_pct_3m", "country",
                "Central bank share of government auction awards",
                "Central bank purchases at auction as a share of total sold, 3-month average",
                "Central bank share (%)",
                "Only countries whose central bank buys at primary auctions and reports it "
                "appear; most central banks (BoE, ECB, BoJ, ...) are barred from doing so.",
                facet=True,
            ),
            None,
        )
    return plots


# --------------------------------------------------------------------------
# Recent charts: every auction and every trading day, no averaging
# --------------------------------------------------------------------------

# Standard maturities for the recent charts, in months (1 month to 40 years).
# Each auction goes in the panel of the nearest one, if its time to maturity
# is within 25% of it (a reopened 10-year with 9 years 10 months left counts
# as a 10-year). Only panels with auctions are drawn. Neighbours are at most
# 1.5x apart, so from 1 month up nearly every auction gets a panel.
RECENT_TENORS = [1, 2, 3, 4, 6, 9, 12, 18, 24, 36, 48, 60, 84, 120, 180, 240, 300, 360, 480]
TENOR_TOLERANCE = 1.25  # max ratio between an auction's maturity and its panel's

RECENT_COLORS = {"Daily market yield": SERIES_COLORS[0], "Auction": SERIES_COLORS[1]}


def tenor_label(months: float) -> str:
    return f"{months:g}-month" if months < 12 else f"{months / 12:g}-year"


def assign_tenor(years_to_maturity: pd.Series) -> pd.Series:
    """Nearest standard maturity in months, or NaN if none is close enough."""
    months = years_to_maturity.to_numpy(dtype=float)[:, None] * 12
    tenors = np.array(RECENT_TENORS, dtype=float)
    # Compare on a log scale so "within 25%" means the same at 3 months and 30 years.
    distance = np.abs(np.log(months / tenors))
    nearest = distance.argmin(axis=1)
    close = distance[np.arange(len(months)), nearest] <= np.log(TENOR_TOLERANCE)
    return pd.Series(np.where(close, tenors[nearest], np.nan), index=years_to_maturity.index)


def recent_auctions(auctions: pd.DataFrame, market: pd.DataFrame | None,
                    start: pd.Timestamp) -> pd.DataFrame:
    """
    One row per auction since `start`, with the market yield for the same
    maturity on the auction day (or the last trading day before it).
    """
    recent = auctions[auctions["auction_date"] >= start].copy()
    recent["tenor_months"] = assign_tenor(recent["years_to_maturity"])
    recent = recent.dropna(subset=["tenor_months"]).sort_values("auction_date")

    if market is not None and not market.empty:
        market = market.rename(columns={"date": "auction_date"}).astype(
            {"auction_date": recent["auction_date"].dtype, "tenor_months": float}
        ).sort_values("auction_date")
        # Up to 5 days back covers weekends and holidays, but not a source
        # that has stopped updating.
        recent = pd.merge_asof(
            recent, market, on="auction_date", by="tenor_months",
            direction="backward", tolerance=pd.Timedelta(days=5),
        )
    else:
        recent["market_yield"] = np.nan
    recent["auction_minus_market_bp"] = (recent["auction_yield"] - recent["market_yield"]) * 100

    recent["tenor"] = recent["tenor_months"].map(tenor_label)
    return recent[[
        "auction_date", "maturity_date", "years_to_maturity", "tenor", "tenor_months",
        "auction_yield", "market_yield", "auction_minus_market_bp", "bid_to_cover", "accepted",
    ]]


def recent_chart(points: pd.DataFrame, lines: pd.DataFrame, facet: str, y: str, ylab: str,
                 title: str, subtitle: str, caption: str, start, end):
    """
    Auctions as dots and (optionally) daily market yields as lines, one
    panel per `facet` value. Each panel gets its own y-axis so moves of a
    few basis points stay visible.
    """
    points = points.dropna(subset=[y]).assign(series="Auction")
    lines = lines.dropna(subset=["market_yield"]).assign(series="Daily market yield")
    n_rows = -(-points[facet].nunique() // 4)

    plot = ggplot()
    if not lines.empty:
        plot += geom_line(lines, aes(x="date", y="market_yield", color="series"), size=0.7)
    plot += geom_point(points, aes(x="auction_date", y=y, color="series"), size=2.2)
    plot += scale_color_manual(values=RECENT_COLORS)
    plot += scale_x_datetime(date_breaks="2 months", date_labels="%b %Y", limits=(start, end))
    plot += labs(title=title, subtitle=subtitle, x="Date", y=ylab, color="",
                 caption=wrap(caption, 170))
    # A copy: plotnine adds the figure_size below into the theme object
    # itself, which would resize every chart drawn after this one.
    plot += copy.deepcopy(BASE_THEME)
    plot += facet_wrap(facet, ncol=4, scales="free_y")
    plot += theme(figure_size=(14, 2.8 * n_rows + 2.2))
    return plot


def recent_plots(country: Country, recent: pd.DataFrame, market: pd.DataFrame | None,
                 start, end, months: int) -> dict:
    """Per-auction yield (with daily market yields) and bid-to-cover, last `months` months."""
    if recent.empty:
        return {}
    tenors = sorted(recent["tenor_months"].unique())
    panels = pd.CategoricalDtype([tenor_label(t) for t in tenors], ordered=True)
    points = recent.assign(panel=recent["tenor"].astype(panels))

    lines = pd.DataFrame(columns=["date", "market_yield", "panel"])
    if market is not None and not market.empty:
        lines = market[market["tenor_months"].isin(tenors) & (market["date"] >= start)]
        lines = lines.assign(panel=lines["tenor_months"].map(tenor_label).astype(panels))

    market_note = (
        f" Line: daily market yield ({country.market_source})." if country.market_source
        else " No free daily market yield source found for this country, so auctions only."
    )
    plots = {}
    if points["auction_yield"].notna().any():
        plots["recent_yield"] = (
            recent_chart(
                points, lines, "panel", "auction_yield", "Yield (%)",
                f"{country.name}: every auction vs the daily market yield, last {months} months",
                "Each dot is one auction; panels group auctions by maturity (within 25%)",
                f"Auctions: {country.source}. {country.yield_note}{market_note}",
                start, end,
            ),
            None,
        )
    if points["bid_to_cover"].notna().any():
        plots["recent_bid_to_cover"] = (
            recent_chart(
                points, lines.iloc[0:0], "panel", "bid_to_cover", "Bid-to-cover ratio",
                f"{country.name}: bid-to-cover at every auction, last {months} months",
                "Each dot is one auction; panels group auctions by maturity (within 25%)",
                f"Source: {country.source}. Higher bid-to-cover = more bid per unit sold.",
                start, end,
            ),
            BID_TO_COVER_FORMULA,
        )
    return plots


# Maturities drawn on the market-only chart, where the source has them (in
# months: 3-month to 30-year). At most seven, so each gets its own colour.
MARKET_CHART_TENORS = [3, 12, 24, 60, 120, 240, 360]


def market_plot(country: Country, market: pd.DataFrame, start, end, months: int) -> dict:
    """Daily market yields on their own: one line per maturity, one panel."""
    tenors = [t for t in MARKET_CHART_TENORS if t in set(market["tenor_months"])]
    if not tenors:
        return {}
    labels = [tenor_label(t) for t in tenors]
    data = market[market["tenor_months"].isin(tenors) & (market["date"] >= start)]
    data = data.assign(maturity=pd.Categorical(data["tenor_months"].map(tenor_label),
                                               categories=labels, ordered=True))
    plot = (
        ggplot(data, aes(x="date", y="market_yield", color="maturity"))
        + geom_line(size=0.8)
        + scale_color_manual(values=dict(zip(labels, SERIES_COLORS)), name="Maturity")
        + scale_x_datetime(date_breaks="1 month", date_labels="%b %Y", limits=(start, end))
        + labs(
            title=f"{country.name}: daily government bond market yields, last {months} months",
            subtitle="Yield on existing bonds as they trade each day, by maturity",
            x="Date", y="Market yield (%)",
            caption=wrap(f"Source: {country.market_source}."),
        )
        + copy.deepcopy(BASE_THEME)
    )
    return {"market_yields": (plot, None)}


def global_market_plots(markets: pd.DataFrame, country_order: list[str], start, end,
                        months: int, tenor_months: int = 120) -> dict:
    """
    Daily market yields for every country that publishes them (`markets` has
    a `country` column):
      <tenor>_market_yields   one chart, a line per country at one maturity
      market_yields_grid      one panel per country, a line per maturity
    """
    markets = markets[markets["date"] >= start]
    label = tenor_label(tenor_months)
    plots = {}

    one = markets[markets["tenor_months"] == tenor_months].copy()
    if not one.empty:
        # Same colour and line style per country as on the other global charts.
        colors, styles = country_styles(country_order)
        one["country"] = pd.Categorical(one["country"], categories=country_order, ordered=True)
        one["country"] = one["country"].cat.remove_unused_categories()
        present = list(one["country"].cat.categories)
        plot = (
            ggplot(one, aes(x="date", y="market_yield", color="country", linetype="country"))
            + geom_line(size=0.8)
            + scale_color_manual(values={c: colors[c] for c in present}, name="Country")
            + scale_linetype_manual(values={c: styles[c] for c in present}, name="Country")
            + guides(color=guide_legend(ncol=5), linetype=guide_legend(ncol=5))
            + scale_x_datetime(date_breaks="1 month", date_labels="%b %Y", limits=(start, end))
            + labs(
                title=f"{label.capitalize()} government bond market yields, last {months} months",
                subtitle="Yield on existing bonds as they trade each day, every country with a free daily source",
                x="Date", y="Market yield (%)",
                caption=wrap("Each country's own published series (central bank or debt office): par, "
                             "benchmark or fitted-curve yields. Switzerland's source is updated monthly, "
                             "so its line stops at the end of last month."),
            )
            + copy.deepcopy(BASE_THEME)
            + theme(legend_key_width=36)
        )
        plots[f"{label.replace('-', '')}_market_yields"] = (plot, None)

    tenors = [t for t in MARKET_CHART_TENORS if t in set(markets["tenor_months"])]
    grid = markets[markets["tenor_months"].isin(tenors)].copy()
    if not grid.empty:
        labels = [tenor_label(t) for t in tenors]
        grid["maturity"] = pd.Categorical(grid["tenor_months"].map(tenor_label),
                                          categories=labels, ordered=True)
        grid["country"] = pd.Categorical(grid["country"], categories=country_order, ordered=True)
        grid["country"] = grid["country"].cat.remove_unused_categories()
        n_rows = -(-grid["country"].nunique() // 4)
        plot = (
            ggplot(grid, aes(x="date", y="market_yield", color="maturity"))
            + geom_line(size=0.6)
            + scale_color_manual(values=dict(zip(labels, SERIES_COLORS)), name="Maturity")
            + scale_x_datetime(date_breaks="2 months", date_labels="%b %Y", limits=(start, end))
            + labs(
                title=f"Government bond market yields by maturity, last {months} months",
                subtitle="One panel per country, a line per maturity; each panel has its own scale",
                x="Date", y="Market yield (%)",
                caption=wrap("Each country's own published series. Not every country publishes every "
                             "maturity (e.g. the UK only 5, 10 and 20 years).", 170),
            )
            + copy.deepcopy(BASE_THEME)
        )
        plot += facet_wrap("country", ncol=4, scales="free_y")
        plot += theme(figure_size=(14, 2.8 * n_rows + 2.2))
        plots["market_yields_grid"] = (plot, None)
    return plots


def global_recent_plot(recents: list[tuple[str, pd.DataFrame, pd.DataFrame | None]],
                       country_order: list[str], start, end, months: int,
                       tenor_months: int = 120) -> dict:
    """One panel per country: its 10-year auctions and daily 10-year market yield."""
    countries = pd.CategoricalDtype(country_order, ordered=True)
    points, lines = [], []
    for name, recent, market in recents:
        points.append(recent[recent["tenor_months"] == tenor_months].assign(country=name))
        if market is not None and not market.empty:
            lines.append(market[(market["tenor_months"] == tenor_months)
                                & (market["date"] >= start)].assign(country=name))
    points = pd.concat(points, ignore_index=True).dropna(subset=["auction_yield"])
    lines = pd.concat(lines, ignore_index=True) if lines else pd.DataFrame(
        columns=["date", "market_yield", "country"])
    # A panel needs at least one auction; then keep only those countries' lines.
    lines = lines[lines["country"].isin(points["country"])]
    if points.empty:
        return {}
    points["country"] = points["country"].astype(countries).cat.remove_unused_categories()
    lines["country"] = lines["country"].astype(points["country"].dtype)

    label = tenor_label(tenor_months)
    return {
        f"{label.replace('-', '')}_recent_yield": (
            recent_chart(
                points, lines, "country", "auction_yield", "Yield (%)",
                f"{label.capitalize()} government bond yields: daily market vs every auction, "
                f"last {months} months",
                f"Each dot is one auction of a roughly {label} bond; each panel has its own scale",
                "Countries without a line have no free daily market yield source. Market yields "
                "are each central bank's or debt office's published series (par, benchmark or "
                "zero-coupon), so small gaps between line and dots are partly definitional.",
                start, end,
            ),
            None,
        )
    }


def save_plot(plot, path: Path, formula: str | None = None):
    """
    Render a plot, bold every year label ending in 0 or 5, optionally
    add a formula box in the header beside the (left-aligned) title, where
    it can't cover any data, and save it.

    plotnine can't style individual tick labels, so this finishes the
    figure in matplotlib.
    """
    fig = plot.draw()  # plotnine -> a matplotlib Figure we can edit
    fig.canvas.draw()  # populate tick label text

    # One Axes per panel (three on the faceted global charts). Skip axes
    # where every label would be bold (the 5-yearly grid ticks), since then
    # bold no longer marks anything out.
    # Only plain year labels ("2025") count; month labels ("Oct 2025") on
    # the recent charts are left alone.
    for ax in fig.axes:
        labels = [l for l in ax.get_xticklabels() if l.get_text().isdigit()]
        marked = [l for l in labels if l.get_text()[-1:] in ("0", "5")]
        if len(marked) < len(labels):
            for label in marked:
                label.set_fontweight("bold")

    if formula:
        # Figure coordinates run 0-1 from the bottom-left corner, so
        # (0.985, 0.99) anchors the box's top-right just inside the corner.
        fig.text(
            0.985,
            0.99,
            formula,
            ha="right",
            va="top",
            fontsize=12,
            bbox=dict(boxstyle="round,pad=0.5", facecolor="white", edgecolor="#bbbbbb"),
        )

    fig.savefig(path, dpi=160)
    # Free the figure's memory; a run draws ~20 charts.
    plt.close(fig)
