"""Norway: Norges Bank primary-market transactions for government bonds (NGB) and bills (NTB)."""

import io
import warnings

import pandas as pd

from common import Country
from countries._http import get

BASE = "https://www.norges-bank.no/globalassets/marketdata/moa/statsgjeld/"
FILES = [
    "primarymarketgovtdebttransactions_ngb_no.xlsx",  # bonds
    "primarymarketgovtdebttransactions_ntb_no.xlsx",  # bills
]


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    frames = []
    for name in FILES:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # "Workbook contains no default style"
            frames.append(pd.read_excel(io.BytesIO(get(BASE + name).content), header=0))
    df = pd.concat(frames, ignore_index=True)

    # Issuance auctions only ("Utstedelsesauksjon"); the files also hold
    # syndications and changes in the state's own holdings.
    df = df[df["Type utstedelse"].astype(str).str.strip() == "Utstedelsesauksjon"]

    bids = pd.to_numeric(df["Budvolum"], errors="coerce")             # bid volume
    allocated = pd.to_numeric(df["Allokert volum"], errors="coerce")  # allocated volume
    return pd.DataFrame(
        {
            "auction_date": pd.to_datetime(df["Transaksjonsdato"], errors="coerce"),
            "maturity_date": pd.to_datetime(df["Forfallsdato"], errors="coerce"),
            "bid_to_cover": bids / allocated.where(allocated > 0),
            "accepted": allocated * 1e6,  # NOK millions
            "auction_yield": pd.to_numeric(df["Effektiv Rente"], errors="coerce"),
            # Norges Bank runs the auctions as the state's agent but doesn't buy at them.
            "cb_accepted": float("nan"),
        }
    )


# Norges Bank's daily generic yields for government bonds (3-10 years) and
# bills (3-12 months).
MARKET_URL = (
    "https://data.norges-bank.no/api/data/GOVT_GENERIC_RATES/B..GBON+GBILL"
    "?format=csv&startPeriod={start}&locale=en"
)


def fetch_market(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(get(MARKET_URL.format(start=start.date())).text), sep=";")
    # Tenor codes look like "6M" or "10Y".
    tenor = df["TENOR"].str.extract(r"(\d+)([MY])")
    return pd.DataFrame(
        {
            "date": pd.to_datetime(df["TIME_PERIOD"]),
            "tenor_months": pd.to_numeric(tenor[0]) * tenor[1].map({"M": 1, "Y": 12}),
            "market_yield": pd.to_numeric(df["OBS_VALUE"], errors="coerce"),
        }
    ).dropna()


COUNTRY = Country(
    code="norway",
    name="Norway",
    fetch=fetch,
    volume_label="Amount allocated (NOK billions)",
    volume_divisor=1e9,
    source="Norges Bank",
    yield_note="Effective yield.",
    fetch_market=fetch_market,
    market_source="Norges Bank generic government bond and bill yields",
)
