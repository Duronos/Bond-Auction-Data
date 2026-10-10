"""Switzerland: Federal Finance Administration Confederation bond auction results (since 2011)."""

import io

import pandas as pd

from common import Country
from countries._http import find_links, get

# The SNB runs the auctions for the Confederation and links the results file here.
PAGE = "https://www.snb.ch/en/the-snb/mandates-goals/services-to-the-confederation/confederation-bonds"


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    url = find_links(PAGE, r"resultate-anleihen\.xlsx$")[0]
    book = pd.ExcelFile(io.BytesIO(get(url).content))

    frames = []
    for name in book.sheet_names:  # one sheet per year
        sheet = book.parse(name, header=None)
        # Columns: auction, settlement, maturity, ISIN, fungible ISIN, bond,
        # coupon, total bids (CHF m), bids without price, amount issued
        # (CHF m), price, yield (decimal), own tranches.
        rows = sheet[pd.to_datetime(sheet[0], errors="coerce", format="mixed").notna()]
        bids = pd.to_numeric(rows[7], errors="coerce")
        issued = pd.to_numeric(rows[9], errors="coerce")
        frames.append(
            pd.DataFrame(
                {
                    "auction_date": pd.to_datetime(rows[0], errors="coerce"),
                    "maturity_date": pd.to_datetime(rows[2], errors="coerce"),
                    "bid_to_cover": bids / issued.where(issued > 0),
                    "accepted": issued * 1e6,
                    "auction_yield": pd.to_numeric(rows[11], errors="coerce") * 100,
                }
            )
        )

    df = pd.concat(frames, ignore_index=True)
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
    yield_note="Yield at the issue price. Bonds only (money-market claims aren't included); data from 2011.",
)
