"""Canada: Bank of Canada Valet API (nominal bond and treasury bill auctions)."""

import pandas as pd
import requests

from common import Country

VALET_URL = "https://www.bankofcanada.ca/valet/observations/group/{group}/json"


def fetch_group(group: str, prefix: str) -> pd.DataFrame:
    """Download a Valet auction group and strip the series prefix from columns."""
    response = requests.get(VALET_URL.format(group=group), timeout=120)
    response.raise_for_status()
    rows = [
        {k.removeprefix(prefix): (v["v"] if isinstance(v, dict) else v) for k, v in obs.items()}
        for obs in response.json()["observations"]
    ]
    return pd.DataFrame(rows)


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    num = lambda col: pd.to_numeric(df.get(col), errors="coerce")
    coverage = num("COVERAGE")

    # The Bank buys a fixed percentage *of* the amount auctioned (not on top
    # of it). Since 2022 it shows a dash when it doesn't participate, so a
    # blank on an auction that has results means zero.
    boc = num("BOC_PURCHASE").where(coverage.isna(), num("BOC_PURCHASE").fillna(0))

    return pd.DataFrame(
        {
            "auction_date": df["AUCTION_DATE"],
            "maturity_date": df["MATURITY_DATE"],
            "bid_to_cover": coverage,
            "accepted": num("AMOUNT").where(coverage.notna()) * 1e6,
            "auction_yield": num("HIGH_YIELD"),
            "cb_accepted": boc * 1e6,
        }
    )


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    bonds = normalise(fetch_group("AUC_BOND", "AUC_BOND_"))
    bills = normalise(fetch_group("AUC_TBILL", "AUC_TBILL_"))
    return pd.concat([bonds, bills], ignore_index=True)


COUNTRY = Country(
    code="canada",
    name="Canada",
    fetch=fetch,
    volume_label="Amount auctioned (C$ billions)",
    volume_divisor=1e9,
    source="Bank of Canada Valet API",
    yield_note="High (clearing) yield.",
    central_bank="Bank of Canada",
)
