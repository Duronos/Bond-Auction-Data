"""
Switzerland: Federal Finance Administration auction results for Confederation
bonds (since 2011) and money market debt register claims, the Confederation's
bills (since 2012).
"""

import io

import pandas as pd

from common import Country
from countries._http import find_links, get

# The SNB runs the auctions for the Confederation and links each results file
# from these pages.
BOND_PAGE = "https://www.snb.ch/en/the-snb/mandates-goals/services-to-the-confederation/confederation-bonds"
BILL_PAGE = "https://www.snb.ch/en/the-snb/mandates-goals/services-to-the-confederation/book-claims"


def read_results(page: str, pattern: str, max_years: float | None = None) -> pd.DataFrame:
    """
    Both files have one sheet per year with German, French, Italian and
    English header rows; data rows are the ones starting with a date. The
    columns differ between files and between years (e.g. older bill sheets
    have no "days" column), so columns are found by their English header.
    """
    url = find_links(page, pattern)[0]
    book = pd.ExcelFile(io.BytesIO(get(url).content))

    frames = []
    for name in book.sheet_names:
        sheet = book.parse(name, header=None)
        english = next(
            (i for i in range(15) if str(sheet.iloc[i, 0]).strip() == "Auction"), None
        )
        if english is None:
            continue
        headers = sheet.iloc[english].astype(str).str.strip().str.lower()

        def col(starts_with):
            return next(i for i, h in enumerate(headers) if h.startswith(starts_with))

        rows = sheet[pd.to_datetime(sheet[0], errors="coerce", format="mixed").notna()]
        total_bids = pd.to_numeric(rows[col("total bids")], errors="coerce")
        amount = pd.to_numeric(rows[col("amount of issue")], errors="coerce")
        frames.append(
            pd.DataFrame(
                {
                    "auction_date": pd.to_datetime(rows[0], errors="coerce"),
                    "maturity_date": pd.to_datetime(rows[col("maturity")], errors="coerce"),
                    "bid_to_cover": total_bids / amount.where(amount > 0),
                    "accepted": amount * 1e6,  # CHF millions
                    # Stored as a decimal (0.0036 = 0.36%).
                    "auction_yield": pd.to_numeric(rows[col("yield")], errors="coerce") * 100,
                }
            )
        )
    df = pd.concat(frames, ignore_index=True)
    if max_years is not None:
        # Drop rows whose maturity contradicts the instrument (the bills file
        # has at least one typo, e.g. a 3-month claim listed as maturing in 2029).
        term = (df["maturity_date"] - df["auction_date"]).dt.days / 365.25
        df = df[term <= max_years]
    return df


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    df = pd.concat(
        [
            read_results(BOND_PAGE, r"resultate-anleihen\.xlsx$"),
            # Money market claims run up to 12 months.
            read_results(BILL_PAGE, r"resultate-gmbf\.xlsx$", max_years=1.5),
        ],
        ignore_index=True,
    )
    # The SNB acts as auction agent but doesn't buy at the auctions.
    df["cb_accepted"] = float("nan")
    return df


COUNTRY = Country(
    code="switzerland",
    name="Switzerland",
    fetch=fetch,
    volume_label="Amount issued (CHF billions)",
    volume_divisor=1e9,
    source="Swiss Federal Finance Administration / SNB",
    yield_note="Yield at the issue price. Bonds from 2011, money market claims (bills) from 2012.",
)
