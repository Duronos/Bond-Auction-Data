"""United Kingdom: Debt Management Office outright gilt auction results (report D2.1A)."""

import io

import pandas as pd
import requests

from common import Country

EXPORT_URL = (
    "https://www.dmo.gov.uk/umbraco/surface/DataExport/GetDataExport"
    "?reportCode=D2.1A&exportFormatValue=xls&parameters=%26COP%3D%26ISIN%3D"
)

COLUMNS = [
    "auction_date",
    "gilt_name",
    "nominal_auction",
    "nominal_paof",
    "nominal_total",
    "cash_raised",
    "bid_to_cover",
    "avg_price",
    "yield_at_aap",
    "tail_bp",
]


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    response = requests.get(EXPORT_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=120)
    response.raise_for_status()

    df = pd.read_excel(io.BytesIO(response.content), header=None, engine="xlrd")
    df = df.iloc[:, : len(COLUMNS)]
    df.columns = COLUMNS

    # Data rows are the ones whose first cell is a date; the rest is
    # titles, headers and footnotes.
    df["auction_date"] = pd.to_datetime(df["auction_date"], errors="coerce", format="mixed")
    df = df[df["auction_date"].notna()].copy()

    # Nominal gilts only (index-linked gilts quote real yields).
    df = df[~df["gilt_name"].str.contains("Index-linked", case=False, na=False)]

    # The report gives only the maturity year (e.g. "4 7/8% Treasury Gilt 2036");
    # mid-year is close enough for maturity buckets.
    year = df["gilt_name"].str.extract(r"(\d{4})\s*$")[0]
    maturity = pd.to_datetime(year + "-07-01", errors="coerce")

    num = lambda col: pd.to_numeric(
        df[col].astype(str).str.replace(",", ""), errors="coerce"
    )
    return pd.DataFrame(
        {
            "auction_date": df["auction_date"],
            "maturity_date": maturity,
            "bid_to_cover": num("bid_to_cover"),
            # Includes the post-auction option facility (PAOF) take-up.
            "accepted": num("nominal_total") * 1e6,
            "auction_yield": num("yield_at_aap"),
            # The Bank of England doesn't buy at gilt auctions.
            "cb_accepted": float("nan"),
        }
    )


COUNTRY = Country(
    code="uk",
    name="United Kingdom",
    fetch=fetch,
    volume_label="Nominal issued (£ billions)",
    volume_divisor=1e9,
    source="UK Debt Management Office",
    yield_note="Yield at the average accepted price; gilts only (no T-bills).",
)
