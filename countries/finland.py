"""Finland: State Treasury open data API (serial bond and Treasury bill issues since 2000)."""

import pandas as pd

from common import Country
from countries._http import get

# Public endpoints listed on the State Treasury's API portal (avoindata.tutkihallintoa.fi).
API = "https://api.tutkihallintoa.fi/central-government-debt/v1/"


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    bonds = pd.DataFrame(get(API + "serial-bond-issues").json())
    bills = pd.DataFrame(get(API + "treasury-bill-issues").json())

    # Auctions only: syndications, private placements and "other issuance"
    # (e.g. bills sold to dealers on request) have no competitive bidding.
    bonds = bonds[bonds["typeOfTransaction"] == "Bond auction"]
    bills = bills[(bills["typeOfTransaction"] == "Issuance via auctions") & (bills["currency"] == "EUR")]

    df = pd.concat([bonds, bills], ignore_index=True)
    bids = pd.to_numeric(df["bidAmount"], errors="coerce")
    issued = pd.to_numeric(df["issuedAmount"], errors="coerce")
    return pd.DataFrame(
        {
            "auction_date": pd.to_datetime(df["pricingDate"], errors="coerce"),
            "maturity_date": pd.to_datetime(df["maturityDate"], errors="coerce"),
            # Recompute where the published ratio is missing.
            "bid_to_cover": pd.to_numeric(df["bidToCover"], errors="coerce").fillna(
                bids / issued.where(issued > 0)
            ),
            "accepted": issued,  # EUR
            "auction_yield": pd.to_numeric(df["issueYield"], errors="coerce"),
            # The ECB/Bank of Finland are barred from buying at primary auctions.
            "cb_accepted": float("nan"),
        }
    )


COUNTRY = Country(
    code="finland",
    name="Finland",
    fetch=fetch,
    volume_label="Amount issued (€ billions)",
    volume_divisor=1e9,
    source="State Treasury of Finland (open data API)",
    yield_note="Issue yield.",
)
