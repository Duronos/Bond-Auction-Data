"""Ireland: NTMA bond issuance history (since 2003) and Treasury bill auctions (2009-2022)."""

import io

import pandas as pd

from common import Country
from countries._http import find_links, get

PAGE = "https://www.ntma.ie/business-areas/funding-and-debt-management/government-securities"
BILLS_FILE = "https://assets.ntma.ie/general/Treasury-Bills-Issuance-2009-2022-Sep-2022.xlsx"


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    frames = []

    # --- Bonds. Columns: year, type, date, bond, price, yield, bid-to-cover,
    #     competitive sold, non-competitive sold, total sold (EUR m).
    bond_url = find_links(PAGE, r"Bond-Issuance-Historical.*\.xlsx$")[0]
    bonds = pd.read_excel(io.BytesIO(get(bond_url).content), header=0).iloc[:, :10]
    bonds.columns = ["year", "type", "date", "bond", "price", "yield", "btc", "comp", "noncomp", "total"]
    # Auctions only; syndications have no bid-to-cover.
    bonds = bonds[bonds["type"].astype(str).str.strip().str.lower() == "auction"]
    # Only the maturity year is given ("3.1% Treasury Bond 2036"); mid-year
    # is close enough for maturity buckets.
    year = bonds["bond"].astype(str).str.extract(r"(\d{4})\s*$")[0]
    frames.append(
        pd.DataFrame(
            {
                "auction_date": pd.to_datetime(bonds["date"], errors="coerce"),
                "maturity_date": pd.to_datetime(year + "-07-01", errors="coerce"),
                "bid_to_cover": pd.to_numeric(bonds["btc"], errors="coerce"),
                "accepted": pd.to_numeric(bonds["total"], errors="coerce") * 1e6,
                # Stored as a decimal (0.03231 = 3.231%).
                "auction_yield": pd.to_numeric(bonds["yield"], errors="coerce") * 100,
            }
        )
    )

    # --- Treasury bills (Ireland stopped issuing them after 2022, so this
    #     file never changes). In October 2026 the NTMA removed its link from
    #     the page, but the file is still online, so fall back to its known
    #     address; if that goes too, carry on with bonds only.
    try:
        bill_url = find_links(PAGE, r"Treasury-Bills-Issuance.*\.xlsx$")[0]
    except RuntimeError:
        bill_url = BILLS_FILE
    try:
        bills = pd.read_excel(io.BytesIO(get(bill_url).content), header=None)
        frames.append(parse_bills(bills))
    except Exception as exc:
        print(f"  note: Irish T-bill file unavailable ({exc!r}); using bonds only")

    df = pd.concat(frames, ignore_index=True)
    # The ECB/Central Bank of Ireland are barred from buying at primary auctions.
    df["cb_accepted"] = float("nan")
    return df


def parse_bills(sheet: pd.DataFrame) -> pd.DataFrame:
    """Find columns by header text; the header row is the first row mentioning 'Maturity'."""
    header_row = next(
        i for i in range(15) if sheet.iloc[i].astype(str).str.contains("Maturity", case=False).any()
    )
    headers = sheet.iloc[header_row].astype(str).str.lower()
    data = sheet.iloc[header_row + 1 :]

    def col(*words):
        for i, h in enumerate(headers):
            if all(w in h for w in words):
                return data.iloc[:, i]
        return pd.Series(float("nan"), index=data.index)

    yields = pd.to_numeric(col("yield"), errors="coerce")
    # Some files store yields as decimals, others as percentages.
    if yields.abs().median() < 0.2:
        yields = yields * 100
    return pd.DataFrame(
        {
            "auction_date": pd.to_datetime(col("date"), errors="coerce"),
            "maturity_date": pd.to_datetime(col("maturity"), errors="coerce"),
            "bid_to_cover": pd.to_numeric(col("cover"), errors="coerce"),
            "accepted": pd.to_numeric(col("amount"), errors="coerce") * 1e6,
            "auction_yield": yields,
        }
    )


COUNTRY = Country(
    code="ireland",
    name="Ireland",
    fetch=fetch,
    volume_label="Amount sold (€ billions)",
    volume_divisor=1e9,
    source="National Treasury Management Agency (NTMA)",
    yield_note="Auction yield. Bill data ends in 2022.",
)
