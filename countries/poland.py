"""Poland: Ministry of Finance transaction databases for T-bills (since 1991) and bonds (since 1994)."""

import io
import warnings

import pandas as pd

from common import Country
from countries._http import find_links, get

PAGE = "https://www.gov.pl/web/finance/transaction-database"


def read_attachment(url: str) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return pd.read_excel(io.BytesIO(get(url).content), sheet_name=0, header=0)


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    # The page links two .xlsm workbooks (bills and bonds) as /attachment/<id>.
    # Tell them apart by their columns rather than relying on link order.
    books = [read_attachment(url) for url in find_links(PAGE, r"/attachment/")]
    bonds = next(b for b in books if "Oprocentowanie" in b.columns)
    bills = next(b for b in books if "Oprocentowanie" not in b.columns)

    frames = []

    # --- Bonds. Deal type S = sale (O = buy-back); operation AS = sale
    # auction (switch auctions, supplementary sales etc. are other codes).
    # Coupon type S = fixed, O = zero-coupon; Z = floating, I = indexed are excluded.
    b = bonds[
        (bonds["TypTransakcji"] == "S")
        & (bonds["TypOperacji"] == "AS")
        & bonds["Oprocentowanie"].isin(["S", "O"])
    ]
    frames.append(normalise(
        b, date="DataTransakcji", maturity="DataWykupu", demand="PopytŁączny",
        sold="SprzedażLubOdkupŁącznie", max_yield="RentMax",
    ))

    # --- Bills. Same codes; columns have bilingual names.
    bills.columns = [str(c).split("\n")[0] for c in bills.columns]  # keep the Polish part
    s = bills[(bills["Typ Transakcji"] == "S") & (bills["Typ Operacji"] == "AS")]
    frames.append(normalise(
        s, date="Data Transakcji", maturity="Data Wykupu", demand="Popyt Łączny",
        sold="Sprzedaż/Odkup Łącznie", max_yield="Rentowność Maksymalna",
    ))

    df = pd.concat(frames, ignore_index=True)
    # The National Bank of Poland runs the auctions but doesn't buy at them.
    df["cb_accepted"] = float("nan")
    return df


def normalise(rows, date, maturity, demand, sold, max_yield) -> pd.DataFrame:
    demand = pd.to_numeric(rows[demand], errors="coerce")
    sold = pd.to_numeric(rows[sold], errors="coerce")
    return pd.DataFrame(
        {
            "auction_date": pd.to_datetime(rows[date], errors="coerce"),
            "maturity_date": pd.to_datetime(rows[maturity], errors="coerce"),
            "bid_to_cover": demand / sold.where(sold > 0),
            "accepted": sold * 1e6,  # PLN millions
            # The maximum accepted yield is the clearing yield; stored as a decimal.
            "auction_yield": pd.to_numeric(rows[max_yield], errors="coerce") * 100,
        }
    )


COUNTRY = Country(
    code="poland",
    name="Poland",
    fetch=fetch,
    volume_label="Amount sold (PLN billions)",
    volume_divisor=1e9,
    source="Ministry of Finance of Poland",
    yield_note="Maximum accepted (clearing) yield.",
)
