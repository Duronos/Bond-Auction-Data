"""
Czech Republic: Czech National Bank auction results.

Three files:
  - government bond auctions since 2022 (live export)
  - government bond auctions up to 2021 (history file)
  - Treasury bill auctions up to 2021 (history file); the CNB doesn't publish
    a downloadable file of T-bill auctions since 2022
"""

import io
import warnings

import pandas as pd

from common import Country
from countries._http import get

BONDS_CURRENT = (
    "https://www.cnb.cz/system/modules/cz.nelasoft.opencms.cnb.auction/elements/sdExcel.jsp?lang=en"
)
HISTORY = "https://www.cnb.cz/export/sites/cnb/cs/financni-trhy/.galleries/trh_statnich_dluhopisu/"
BONDS_HISTORY = HISTORY + "sd/AUKCE_SD_HISTORIE.xlsx"
BILLS_HISTORY = HISTORY + "spp/aukce_spp_historie.xlsx"


def read(url: str, header: int) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "Workbook contains no default style"
        df = pd.read_excel(io.BytesIO(get(url).content), header=header)
    # Headers contain runs of spaces and line breaks; normalise them.
    df.columns = [" ".join(str(c).split()) for c in df.columns]
    return df


def column(df: pd.DataFrame, starts_with: str) -> pd.Series:
    return df[next(c for c in df.columns if c.startswith(starts_with))]


def bonds(df: pd.DataFrame, units: float, yield_scale: float) -> pd.DataFrame:
    # Fixed-rate bonds only; floating-rate bonds quote a discount margin, not a yield.
    df = df[column(df, "Coupon").astype(str).str.strip() != "Float"]
    bids = pd.to_numeric(column(df, "Total Amount Bid"), errors="coerce")
    sold = pd.to_numeric(column(df, "Total Amount Sold"), errors="coerce")
    return pd.DataFrame(
        {
            "auction_date": pd.to_datetime(column(df, "Auction Date"), errors="coerce"),
            "maturity_date": pd.to_datetime(column(df, "Maturity Date"), errors="coerce"),
            # Both exclude the Ministry of Finance's own purchases for its books.
            "bid_to_cover": bids / sold.where(sold > 0),
            "accepted": sold * units,
            # The highest accepted yield is the clearing yield.
            "auction_yield": pd.to_numeric(column(df, "Max. Yield"), errors="coerce") * yield_scale,
        }
    )


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    frames = [
        # Since 2022: amounts in CZK, yields in percent.
        bonds(read(BONDS_CURRENT, header=0), units=1, yield_scale=1),
        # Up to 2021 (Czech and English header rows): amounts in CZK millions,
        # yields as decimals (0.027 = 2.7%).
        bonds(read(BONDS_HISTORY, header=1), units=1e6, yield_scale=100),
    ]

    bills = read(BILLS_HISTORY, header=1)
    requested = pd.to_numeric(column(bills, "Reguested"), errors="coerce")  # sic
    offered = pd.to_numeric(column(bills, "Auctioned"), errors="coerce")
    sold = pd.to_numeric(column(bills, "Sold"), errors="coerce")
    frames.append(
        pd.DataFrame(
            {
                # Auctions before 1999 have no auction date; they're outside
                # the window anyway.
                "auction_date": pd.to_datetime(column(bills, "Auction Date"), errors="coerce"),
                "maturity_date": pd.to_datetime(column(bills, "Maturity Date"), errors="coerce"),
                # Bids over the amount offered, not sold: in 2016-20 the
                # Ministry often sold a sliver of what was bid (e.g. 15m of
                # 7,515m requested at negative yields), so bids / sold gives
                # ratios of several hundred that reflect its choices, not demand.
                "bid_to_cover": requested / offered.where(offered > 0),
                "accepted": sold * 1e6,  # CZK millions
                # Only the average yield is published for bills (in percent).
                "auction_yield": pd.to_numeric(column(bills, "Average Yield"), errors="coerce"),
            }
        )
    )

    df = pd.concat(frames, ignore_index=True)
    # The history file and the live export can overlap at the turn of 2022.
    df = df.drop_duplicates(subset=["auction_date", "maturity_date", "accepted"])
    # The CNB bought T-bills at some auctions before 1999 (outside the window);
    # it doesn't buy at auctions now.
    df["cb_accepted"] = float("nan")
    return df


COUNTRY = Country(
    code="czechia",
    name="Czech Republic",
    fetch=fetch,
    volume_label="Amount sold (CZK billions)",
    volume_divisor=1e9,
    source="Czech National Bank",
    yield_note=(
        "Bonds: maximum accepted (clearing) yield. Bills: average yield; "
        "T-bill data ends in 2021."
    ),
)
