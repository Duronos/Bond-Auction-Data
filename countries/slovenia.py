"""Slovenia: Ministry of Finance list of Treasury bills issued (3, 6, 9, 12 and 18 months)."""

import io

import pandas as pd

from common import Country
from countries._http import find_links, get

PAGE = "https://www.gov.si/en/topics/borrowing-and-state-budget-debt-management-2/"


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    # The file name carries the as-of date ("...-as-at-30-September-2026.xlsx").
    url = find_links(PAGE, r"Treasury-bills-issued.*\.xlsx$")[0]
    book = pd.ExcelFile(io.BytesIO(get(url).content))

    frames = []
    for name in book.sheet_names:  # one sheet per bill tenor: TZ, SZ, DEZ, DZ, OZ
        sheet = book.parse(name, header=0).iloc[:, :7]
        sheet.columns = ["code", "isin", "issue_date", "maturity_date", "rate", "currency", "amount"]
        # Euro issues only; earlier bills were in tolars (SIT).
        sheet = sheet[sheet["currency"].astype(str).str.strip() == "EUR"]
        frames.append(
            pd.DataFrame(
                {
                    "auction_date": pd.to_datetime(sheet["issue_date"], errors="coerce"),
                    "maturity_date": pd.to_datetime(sheet["maturity_date"], errors="coerce"),
                    "accepted": pd.to_numeric(sheet["amount"], errors="coerce"),
                    # Interest rate stored as a decimal (0.0281 = 2.81%).
                    "auction_yield": pd.to_numeric(sheet["rate"], errors="coerce") * 100,
                }
            )
        )

    df = pd.concat(frames, ignore_index=True)
    # Bid amounts aren't in this file, so there's no bid-to-cover.
    df["bid_to_cover"] = float("nan")
    # The ECB/Banka Slovenije are barred from buying at primary auctions.
    df["cb_accepted"] = float("nan")
    return df


COUNTRY = Country(
    code="slovenia",
    name="Slovenia",
    fetch=fetch,
    # Slovenian bill auctions are small (tens of millions), so use millions.
    volume_label="Amount issued (€ millions)",
    volume_divisor=1e6,
    source="Ministry of Finance of Slovenia",
    yield_note="Bill interest rate at issue (bills only; bonds are mostly syndicated). Dated by issue date.",
)
