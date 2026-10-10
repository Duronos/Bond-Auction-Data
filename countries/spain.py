"""
Spain: Tesoro Público "Central Government Debt: Auctions" statistics file.

The Treasury only publishes per-auction results in bulk for the current and
previous year (one sheet each); older results exist only as individual web
pages. So Spain's history is short and rolls forward each year.
"""

import io
import re

import pandas as pd

from common import Country
from countries._http import get

AUCTIONS_URL = "https://www.tesoro.es/documents/d/tesoro/11i-xlsx"

# Tenor codes like "L-6m" (Letras), "B-5y" (Bonos), "O-10y" (Obligaciones).
TENOR = re.compile(r"^[A-Z€i]+-(\d+)([my])$")


def maturity(auction_date, asset) -> pd.Timestamp:
    """The file gives a tenor, not a maturity date, so add it to the auction date."""
    m = TENOR.match(str(asset).strip())
    if not m or pd.isna(auction_date):
        return pd.NaT
    n, unit = int(m.group(1)), m.group(2)
    return auction_date + (pd.DateOffset(months=n) if unit == "m" else pd.DateOffset(years=n))


def side(sheet: pd.DataFrame, asset, r1, r2, allotted, rate) -> pd.DataFrame:
    """Each row lists a short-term (bills) and a long-term (bonds) issue side by side."""
    rows = sheet[sheet[asset].astype(str).str.match(r"^[A-Z€i]+-\d+[my]$")]
    # Inflation-linked issues ("O€i-10y") quote real yields.
    rows = rows[~rows[asset].astype(str).str.contains("€i")]
    bids = pd.to_numeric(rows[r1], errors="coerce").fillna(0) + pd.to_numeric(rows[r2], errors="coerce").fillna(0)
    sold = pd.to_numeric(rows[allotted], errors="coerce")
    return pd.DataFrame(
        {
            "auction_date": rows["date"],
            "maturity_date": [maturity(d, a) for d, a in zip(rows["date"], rows[asset])],
            # Bids from both rounds over the total allotted.
            "bid_to_cover": bids / sold.where(sold > 0),
            "accepted": sold * 1e6,  # EUR millions
            # Stop-out (bills) / marginal (bonds) rate: the clearing yield.
            "auction_yield": pd.to_numeric(rows[rate], errors="coerce"),
        }
    )


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    book = pd.ExcelFile(io.BytesIO(get(AUCTIONS_URL).content))
    frames = []
    for name in book.sheet_names:  # one sheet per year
        sheet = book.parse(name, header=None)
        # The date is only on the first row of each auction day.
        sheet["date"] = pd.to_datetime(sheet[0], errors="coerce", format="mixed").ffill()
        frames.append(side(sheet, asset=1, r1=2, r2=3, allotted=4, rate=5))   # bills
        frames.append(side(sheet, asset=7, r1=8, r2=9, allotted=10, rate=12))  # bonds

    df = pd.concat(frames, ignore_index=True)
    # The ECB/Banco de España are barred from buying at primary auctions.
    df["cb_accepted"] = float("nan")
    return df


COUNTRY = Country(
    code="spain",
    name="Spain",
    fetch=fetch,
    volume_label="Amount allotted (€ billions)",
    volume_divisor=1e9,
    source="Tesoro Público (Spanish Treasury)",
    yield_note="Stop-out/marginal (clearing) yield. Only the last two years are published in bulk.",
)
