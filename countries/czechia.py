"""Czech Republic: Czech National Bank government bond auction results (since 2022)."""

import io
import warnings

import pandas as pd

from common import Country
from countries._http import get

EXCEL_URL = (
    "https://www.cnb.cz/system/modules/cz.nelasoft.opencms.cnb.auction/elements/sdExcel.jsp?lang=en"
)


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "Workbook contains no default style"
        df = pd.read_excel(io.BytesIO(get(EXCEL_URL).content), header=0)

    # Fixed-rate bonds only; floating-rate bonds quote a discount margin, not a yield.
    df = df[df["Coupon"].astype(str).str.strip() != "Float"]

    bids = pd.to_numeric(df["Total Amount Bid except MoF"], errors="coerce")
    sold = pd.to_numeric(df["Total Amount Sold except MoF"], errors="coerce")
    return pd.DataFrame(
        {
            "auction_date": pd.to_datetime(df["Auction Date"], errors="coerce"),
            "maturity_date": pd.to_datetime(df["Maturity Date"], errors="coerce"),
            # Excludes the Ministry of Finance's own purchases for its books.
            "bid_to_cover": bids / sold.where(sold > 0),
            "accepted": sold,  # CZK
            # The highest accepted yield is the clearing yield.
            "auction_yield": pd.to_numeric(df["Max. Yield / Discount Margin"], errors="coerce"),
            # The CNB runs the auctions but doesn't buy at them.
            "cb_accepted": float("nan"),
        }
    )


COUNTRY = Country(
    code="czechia",
    name="Czech Republic",
    fetch=fetch,
    volume_label="Amount sold (CZK billions)",
    volume_divisor=1e9,
    source="Czech National Bank",
    yield_note="Maximum accepted (clearing) yield. Bonds only; the CNB's downloadable history starts in 2022.",
)
