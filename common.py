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

import math
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd
import matplotlib.pyplot as plt

from plotnine import (
    ggplot,
    aes,
    geom_line,
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
    for ax in fig.axes:
        labels = [l for l in ax.get_xticklabels() if l.get_text()]
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
