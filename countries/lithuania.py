"""
Lithuania: government securities auctions on Nasdaq Vilnius, via Nasdaq
Baltic's statistics download (since 2008).

The auction file has no maturity dates, only ISINs. Maturities come from
Nasdaq Baltic's bond list for bonds still listed, and from each bond's
instrument page for matured ones. Maturities never change, so lookups are
cached on disk.
"""

import io
import json
import re
import time
from pathlib import Path

import pandas as pd
import requests

from common import Country
from countries._http import HEADERS, get

AUCTIONS = (
    "https://nasdaqbaltic.com/statistics/en/auctions_vln/govdebt"
    "?filter=1&start={start}&end={end}&download=1"
)
BOND_LIST = "https://nasdaqbaltic.com/statistics/en/bonds?download=1"
INSTRUMENT = "https://nasdaqbaltic.com/statistics/en/instrument/{isin}/trading"

CACHE = Path(__file__).resolve().parent.parent / "cache" / "lithuania" / "maturities.json"


def maturities(isins: list[str]) -> dict[str, str | None]:
    """ISIN -> maturity date (YYYY-MM-DD), from the bond list, cache or instrument pages."""
    known = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}

    listed = pd.read_excel(io.BytesIO(get(BOND_LIST).content))
    for isin, date in zip(listed["ISIN"], pd.to_datetime(listed["Maturity date"], errors="coerce")):
        if pd.notna(date):
            known[isin] = date.strftime("%Y-%m-%d")

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    for isin in isins:
        if isin in known:
            continue
        response = requests.get(INSTRUMENT.format(isin=isin), headers=HEADERS, timeout=60)
        m = re.search(r"Maturity<span>(\d{2}\.\d{2}\.\d{4})</span>", response.text) if response.ok else None
        # None = no instrument page (some old bills were never listed).
        known[isin] = pd.to_datetime(m.group(1), format="%d.%m.%Y").strftime("%Y-%m-%d") if m else None
        CACHE.write_text(json.dumps(known, indent=0, sort_keys=True), encoding="utf-8")
        time.sleep(0.5)  # be polite: one page per matured bond, first run only

    CACHE.write_text(json.dumps(known, indent=0, sort_keys=True), encoding="utf-8")
    return known


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    url = AUCTIONS.format(start=start.date(), end=end.date())
    df = pd.read_excel(io.BytesIO(get(url).content))

    lookup = maturities(sorted(df["ISIN"].dropna().unique()))
    num = lambda col: pd.to_numeric(df[col], errors="coerce")
    bids = num("Total value bid at competitive auction") + num(
        "Total value bid at non-competitive auction"
    ).fillna(0)
    accepted = num("Approved bids by nominal value")

    return pd.DataFrame(
        {
            "auction_date": pd.to_datetime(df["Auction Date"], errors="coerce"),
            "maturity_date": pd.to_datetime(df["ISIN"].map(lookup), errors="coerce"),
            "bid_to_cover": bids / accepted.where(accepted > 0),
            "accepted": accepted,  # EUR
            # The highest accepted yield is the clearing yield.
            "auction_yield": num("Max yield %"),
            # The Bank of Lithuania (Eurosystem) can't buy at primary auctions.
            "cb_accepted": float("nan"),
        }
    )


COUNTRY = Country(
    code="lithuania",
    name="Lithuania",
    fetch=fetch,
    volume_label="Amount accepted (€ millions)",
    volume_divisor=1e6,
    source="Nasdaq Baltic (Vilnius government securities auctions)",
    yield_note="Maximum accepted (clearing) yield.",
)
