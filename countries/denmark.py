"""
Denmark: Danmarks Nationalbank government bond (since 2008) and T-bill (since
2011) auctions. The spreadsheets cover finished years; the current year's
auctions are read from the table on each results page.
"""

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


def read_page_table(page: str) -> pd.DataFrame:
    """
    The current year's auctions, from the HTML table on the results page.
    Same English headers as the spreadsheets; yields are in percent.
    """
    # Read as text and clean ourselves: amounts use "," for thousands
    # ("5,870"), and at least one rate is typed with a decimal comma ("2,66").
    table = pd.read_html(io.StringIO(get(page).text), header=0, thousands=None)[0].astype(str)
    table.columns = [str(h).split(",")[0].strip() for h in table.columns]
    table = table[pd.to_datetime(table["Date"], errors="coerce", dayfirst=True).notna()].copy()
    for col in ("Bid", "Sale"):
        table[col] = table[col].str.replace(",", "", regex=False)
    for col in ("Yield", "Cut-off rate"):
        if col in table.columns:
            table[col] = table[col].str.replace(",", ".", regex=False)
    table.attrs["year"] = None  # percent, like the 2020+ sheets
    return table


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
    # Newer names carry the date: "DGTB 02/12/26 SKBV IV" (day/month/year).
    m = re.search(r"(\d{2})/(\d{2})/(\d{2})\b", str(name))
    if m:
        return pd.Timestamp(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1)))
    # Older names only give year and quarter: "DGTB 26 / I", "SKBV-18/II".
    m = re.search(r"(\d{2})\s*/\s*(IV|III|II|I)\b", str(name))
    if not m:
        return pd.NaT
    return pd.Timestamp(2000 + int(m.group(1)), QUARTER_MONTH[m.group(2)], 1)


def to_date(values: pd.Series) -> pd.Series:
    # Spreadsheet cells are real dates; page cells are text like "07-10-2026".
    return pd.to_datetime(values, errors="coerce", dayfirst=True, format="mixed")


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    frames = []

    bond_sheets = read_book(BOND_PAGE, r"auctionresults-governmentbond\.xlsx$") + [read_page_table(BOND_PAGE)]
    for rows in bond_sheets:
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
                    "auction_date": to_date(rows["Date"]),
                    # Only the maturity year is in the name; Danish bonds
                    # mature on 15 November.
                    "maturity_date": pd.to_datetime(year + "-11-15", errors="coerce"),
                    "bid_to_cover": bids / sold.where(sold > 0),
                    "accepted": sold * 1e6,
                    "auction_yield": to_percent(rows["Yield"], sheet_year),
                }
            )
        )

    bill_sheets = read_book(BILL_PAGE, r"auctionresults-tbill\.xlsx$") + [read_page_table(BILL_PAGE)]
    for rows in bill_sheets:
        bids = pd.to_numeric(rows["Bid"], errors="coerce")
        sold = pd.to_numeric(rows["Sale"], errors="coerce")
        frames.append(
            pd.DataFrame(
                {
                    "auction_date": to_date(rows["Date"]),
                    "maturity_date": rows["Security"].map(bill_maturity),
                    "bid_to_cover": bids / sold.where(sold > 0),
                    "accepted": sold * 1e6,
                    # Cut-off rate in percent ("-" when nothing was sold).
                    "auction_yield": to_percent(rows["Cut-off rate"], rows.attrs.get("year")),
                }
            )
        )

    df = pd.concat(frames, ignore_index=True)
    # Once a year ends, its auctions move from the page table into the
    # spreadsheet; drop any overlap.
    df = df.drop_duplicates(subset=["auction_date", "maturity_date", "accepted"])
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
    yield_note="Bonds: yield at the cut-off price; bills: cut-off rate.",
)
