"""Hungary: ÁKK (Government Debt Management Agency) auction data via its website's data API."""

import pandas as pd

from common import Country
from countries._http import get

# The JSON endpoint behind akk.hu/en/statistics/auction-results. It isn't
# documented; the parameters mirror what the website itself requests.
API = "https://www.akk.hu/api/data/xpose3-dwh/data"

# KTV = Hungarian Government Bonds, DKJ = Discount Treasury Bills.
SECURITY_TYPES = ["KTV", "DKJ"]


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    frames = []
    for security_type in SECURITY_TYPES:
        params = (
            f"?query=getAuction&auctionDateStart={start.date()}&auctionDateEnd={end.date()}"
            # ISSUE = regular auctions (not switches, buy-backs or retail subscriptions).
            f"&auctionType=ISSUE&securityType={security_type}"
        )
        payload = get(API + params, timeout=300).json()
        if payload.get("meta", {}).get("status") != "OK":
            raise RuntimeError(f"ÁKK API error: {payload.get('meta')}")
        frames.append(pd.DataFrame(payload["data"]))

    df = pd.concat(frames, ignore_index=True)
    # Fixed-rate only; floating-rate bonds have no yield.
    df = df[df["interestType"] == "FIX"]

    bids = pd.to_numeric(df["totalBids"], errors="coerce")
    accepted = pd.to_numeric(df["accepted"], errors="coerce")
    return pd.DataFrame(
        {
            "auction_date": pd.to_datetime(df["auctionDate"], errors="coerce"),
            "maturity_date": pd.to_datetime(df["maturityDate"], errors="coerce"),
            "bid_to_cover": bids / accepted.where(accepted > 0),
            # "issued" includes amounts added after the auction (e.g. non-competitive); HUF millions.
            "accepted": pd.to_numeric(df["issued"], errors="coerce") * 1e6,
            # The highest accepted yield is the clearing yield.
            "auction_yield": pd.to_numeric(df["maxYield"], errors="coerce"),
            # The Magyar Nemzeti Bank doesn't buy at primary auctions.
            "cb_accepted": float("nan"),
        }
    )


COUNTRY = Country(
    code="hungary",
    name="Hungary",
    fetch=fetch,
    volume_label="Amount issued (HUF billions)",
    volume_divisor=1e9,
    source="Government Debt Management Agency (ÁKK)",
    yield_note="Maximum accepted (clearing) yield.",
)
