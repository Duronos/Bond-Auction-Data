"""Denmark: Danmarks Nationalbank government bond (since 2008) and T-bill (since 2011) auctions."""

import io
import re

import pandas as pd

from common import Country
from countries._http import find_links, get

BOND_PAGE = "https://www.nationalbanken.dk/en/government-debt/trading-and-data/auction-results-government-bonds"
BILL_PAGE = "https://www.nationalbanken.dk/en/government-debt/trading-and-data/auction-results-t-bills"

# Danish T-bills are named by year and quarter ("DGTB 26 / I"); they mature
# at the start of the quarter's last month.
QUARTER_MONTH = {"I": 3, "II": 6, "III": 9, "IV": 12}


def read_book(page: str, pattern: str) -> list[pd.DataFrame]:
    """
    One sheet per year, each with Danish and English header rows. Since 2018
    the sheets gained Settlement and ISIN columns, so columns are renamed by
    their English header ("Date", "Security", "Bid, ...", "Sale, ...", ...).
    """
    url = find_links(page, pattern)[0]
    book = pd.ExcelFile(io.BytesIO(get(url).content))
    sheets = []
    for name in book.sheet_names:
        sheet = book.parse(name, header=None)
        header_row = next(
            i for i in range(10) if sheet.iloc[i].astype(str).str.strip().eq("Date").any()
        )
        headers = sheet.iloc[header_row].astype(str).str.replace("​", "").str.strip()
        sheet.columns = [h.split(",")[0] for h in headers]  # "Bid, DKK million" -> "Bid"
        data = sheet.iloc[header_row + 1 :]
        data = data[pd.to_datetime(data["Date"], errors="coerce").notna()].copy()
        data.attrs["year"] = int(name) if name.strip().isdigit() else None
        sheets.append(data)
    return sheets


def to_percent(values: pd.Series, year: int | None) -> pd.Series:
    """
    Sheets up to 2019 store yields as decimals (0.0209 = 2.09%); from 2020
    they're in percent. A few 2019 rows are already in percent (e.g. -0.75),
    which is too large to be a decimal Danish yield, so only small values
    are scaled.
    """
    values = pd.to_numeric(values, errors="coerce")
    if year is not None and year <= 2019:
        values = values.where(values.abs() >= 0.06, values * 100)
    return values


def bill_maturity(name: str) -> pd.Timestamp:
    m = re.search(r"(\d{2})\s*/\s*(IV|III|II|I)\b", str(name))
    if not m:
        return pd.NaT
    return pd.Timestamp(2000 + int(m.group(1)), QUARTER_MONTH[m.group(2)], 1)


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    frames = []

    for rows in read_book(BOND_PAGE, r"auctionresults-governmentbond\.xlsx$"):
        sheet_year = rows.attrs.get("year")
        name = rows["Security"].astype(str).str.strip()
        # Nominal bonds only; names containing "i" before the year
        # ("DGBi 0.10'2030") are inflation-linked.
        rows = rows[~name.str.contains(r"i\s*\d|i'", regex=True)]
        year = rows["Security"].astype(str).str.extract(r"(\d{4})")[0]
        bids = pd.to_numeric(rows["Bid"], errors="coerce")
        sold = pd.to_numeric(rows["Sale"], errors="coerce")
        frames.append(
            pd.DataFrame(
                {
                    "auction_date": pd.to_datetime(rows["Date"], errors="coerce"),
                    # Only the maturity year is in the name; Danish bonds
                    # mature on 15 November.
                    "maturity_date": pd.to_datetime(year + "-11-15", errors="coerce"),
                    "bid_to_cover": bids / sold.where(sold > 0),
                    "accepted": sold * 1e6,
                    "auction_yield": to_percent(rows["Yield"], sheet_year),
                }
            )
        )

    for rows in read_book(BILL_PAGE, r"auctionresults-tbill\.xlsx$"):
        bids = pd.to_numeric(rows["Bid"], errors="coerce")
        sold = pd.to_numeric(rows["Sale"], errors="coerce")
        frames.append(
            pd.DataFrame(
                {
                    "auction_date": pd.to_datetime(rows["Date"], errors="coerce"),
                    "maturity_date": rows["Security"].map(bill_maturity),
                    "bid_to_cover": bids / sold.where(sold > 0),
                    "accepted": sold * 1e6,
                    # Cut-off rate in percent ("-" when nothing was sold).
                    "auction_yield": to_percent(rows["Cut-off rate"], rows.attrs.get("year")),
                }
            )
        )

    df = pd.concat(frames, ignore_index=True)
    # Nationalbanken runs the auctions as the state's agent but doesn't buy at them.
    df["cb_accepted"] = float("nan")
    return df


COUNTRY = Country(
    code="denmark",
    name="Denmark",
    fetch=fetch,
    volume_label="Amount sold (DKK billions)",
    volume_divisor=1e9,
    source="Danmarks Nationalbank",
    yield_note="Bonds: yield at the cut-off price; bills: cut-off rate. Files run to the end of last year.",
)
