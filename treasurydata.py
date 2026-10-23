"""
treasurydata.py

Pulls the most recent YEARS years of government bond auction data for every
OECD country with a usable public source (see countries/ and the README),
and compares demand across maturity buckets. Shared analysis and charts
live in common.py.

Each run writes into its own dated folder, e.g. runs/2026-10-10_143012_20y/:
  auction_totals.csv   auctions per country and maturity bucket
  <country>/           one folder per country, e.g. us/, france/
      <country>_bid_to_cover_20y.png         (where bids are published)
      <country>_accepted_20y.png
      <country>_auction_yield_20y.png
      <country>_central_bank_share_20y.png   (US and Canada only)
      <country>_nondealer_share_20y.png      (US only)
      <country>_auction_20y_monthly.csv
      <country>_recent_yield_12m.png         every auction vs the daily market yield
      <country>_recent_bid_to_cover_12m.png  every auction's bid-to-cover
      <country>_recent_auctions_12m.csv      every auction, with the market yield that day
      <country>_market_yields_12m.csv        daily market yields (where published)
  global/
      global_<measure>_20y.png        one panel per maturity, a line per country
      global_<measure>_grid_20y.png   one panel per country
      global_10year_recent_yield_12m.png  10-year auctions vs daily market yield
      global_auction_20y_monthly.csv

Install:
    pip install -r requirements.txt

Run:
    python treasurydata.py
"""

import sys
import warnings
from datetime import datetime
from pathlib import Path

# The agencies' spreadsheets trigger harmless openpyxl style warnings.
warnings.filterwarnings("ignore", module="openpyxl")

# Bucket labels use characters like "≤" that the default Windows console
# encoding (cp1252) can't print.
sys.stdout.reconfigure(encoding="utf-8")

# Verify HTTPS against the Windows certificate store. Python's bundled CA list
# (certifi) fails on the Treasury API's Entrust/Sectigo chain.
import truststore
truststore.inject_into_ssl()

import pandas as pd

import countries
from common import (
    BUCKETS, country_plots, global_plots, global_recent_plot, make_monthly_data, prepare,
    recent_auctions, recent_plots, save_plot,
)

# Rolling lookback window ending today.
YEARS = 20
END_DATE = pd.Timestamp.today().normalize()
START_DATE = END_DATE - pd.DateOffset(years=YEARS)

# The recent charts show every auction and every trading day (no averaging)
# over this many months.
RECENT_MONTHS = 12
RECENT_START = END_DATE - pd.DateOffset(months=RECENT_MONTHS)

# Each run gets its own timestamped folder under runs/.
RUNS_DIR = Path(__file__).resolve().parent / "runs"


def save_outputs(folder: Path, prefix: str, monthly: pd.DataFrame, plots: dict):
    folder.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(folder / f"{prefix}_auction_{YEARS}y_monthly.csv", index=False)
    for chart, (plot, formula) in plots.items():
        save_plot(plot, folder / f"{prefix}_{chart}_{YEARS}y.png", formula=formula)
    print(f"  saved {len(plots)} charts + CSV to {folder.name}/")


def save_recent(folder: Path, prefix: str, recent: pd.DataFrame, market, plots: dict):
    folder.mkdir(parents=True, exist_ok=True)
    suffix = f"{RECENT_MONTHS}m"
    recent.to_csv(folder / f"{prefix}_recent_auctions_{suffix}.csv", index=False)
    if market is not None:
        market.sort_values(["tenor_months", "date"]).to_csv(
            folder / f"{prefix}_market_yields_{suffix}.csv", index=False
        )
    for chart, (plot, formula) in plots.items():
        save_plot(plot, folder / f"{prefix}_{chart}_{suffix}.png", formula=formula)
    print(f"  saved {len(plots)} recent charts ({len(recent)} auctions) to {folder.name}/")


def fetch_market(country):
    """Daily market yields for the recent window, or None if unavailable."""
    if not country.fetch_market:
        return None
    try:
        market = country.fetch_market(RECENT_START, END_DATE)
        market = market[market["date"] >= RECENT_START]
        print(f"  market yields to {market['date'].max().date()}")
        return market
    except Exception as exc:
        # The auction charts don't depend on this, so carry on without it.
        print(f"  market yields unavailable: {exc!r}")
        return None


def main():
    run_dir = RUNS_DIR / f"{datetime.now():%Y-%m-%d_%H%M%S}_{YEARS}y"
    print(f"Auctions from {START_DATE.date()} through {END_DATE.date()} -> {run_dir}\n")

    all_monthly = []
    all_recent = []
    totals = []
    for country in countries.ALL:
        print(f"{country.name}:")
        try:
            raw = country.fetch(START_DATE, END_DATE)
            auctions = prepare(raw, START_DATE, END_DATE)
            monthly = make_monthly_data(auctions, country.volume_divisor)
        except Exception as exc:
            # One country's site being down shouldn't sink the whole run.
            print(f"  FAILED: {exc!r}")
            continue

        print(
            f"  {len(auctions):,} auctions, "
            f"{auctions['auction_date'].min().date()} to {auctions['auction_date'].max().date()}"
        )
        save_outputs(run_dir / country.code, country.code, monthly, country_plots(country, monthly))
        all_monthly.append(monthly.assign(country=country.name))

        market = fetch_market(country)
        try:
            recent = recent_auctions(auctions, market, RECENT_START)
            save_recent(
                run_dir / country.code, country.code, recent, market,
                recent_plots(country, recent, market, RECENT_START, END_DATE, RECENT_MONTHS),
            )
        except Exception as exc:
            print(f"  recent charts FAILED: {exc!r}")
            continue
        all_recent.append((country.name, recent, market))

        totals.append(
            {
                "country": country.name,
                **auctions["bucket"].value_counts().reindex(BUCKETS, fill_value=0).to_dict(),
                "total_auctions": len(auctions),
                "first_auction": auctions["auction_date"].min().date(),
                "last_auction": auctions["auction_date"].max().date(),
            }
        )

    if not all_monthly:
        raise RuntimeError("No country data could be downloaded.")

    # One-glance summary of how many auctions each country contributed.
    totals = pd.DataFrame(totals)
    grand_total = totals[BUCKETS + ["total_auctions"]].sum().to_dict()
    totals.loc[len(totals)] = {"country": "All countries", **grand_total}
    totals.to_csv(run_dir / "auction_totals.csv", index=False)
    print(f"Saved auction_totals.csv\n\n{totals.to_string(index=False)}\n")

    print("Global:")
    combined = pd.concat(all_monthly, ignore_index=True)
    combined["country"] = pd.Categorical(
        combined["country"], categories=[c.name for c in countries.ALL], ordered=True
    )
    save_outputs(run_dir / "global", "global", combined, global_plots(combined))
    for chart, (plot, formula) in global_recent_plot(
        all_recent, [c.name for c in countries.ALL], RECENT_START, END_DATE, RECENT_MONTHS
    ).items():
        save_plot(plot, run_dir / "global" / f"global_{chart}_{RECENT_MONTHS}m.png", formula=formula)

    print("\nLatest month per country (all maturities, median bid-to-cover / yield):")
    latest = (
        combined.sort_values("month")
        .groupby(["country", "bucket"], observed=True)
        .tail(1)
        [["country", "bucket", "month", "bid_to_cover", "auction_yield", "cb_share_pct"]]
        .sort_values(["country", "bucket"])
    )
    print(latest.to_string(index=False))


if __name__ == "__main__":
    main()
