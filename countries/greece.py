"""Greece: Public Debt Management Agency Treasury bill auction history (13, 26 and 52 weeks)."""

import io

import pandas as pd

from common import Country
from countries._http import get

BILLS_PAGE = (
    "https://www.pdma.gr/en/debt-instruments-issuance/t-bills-historical-data/"
    "t-bills-historical-data-of-treasury-bills"
)


def amount(values: pd.Series) -> pd.Series:
    """
    Amounts are EUR millions, but the site mixes number formats: "1.021"
    means 1,021 (a thousands separator) while "981.000" means 981. Parse the
    text, then treat any value under 10 as thousands, since no Greek bill
    auction is under EUR 10 million.
    """
    parsed = pd.to_numeric(values.astype(str).str.replace(",", ""), errors="coerce")
    return parsed.where(parsed >= 10, parsed * 1000)


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    html = get(BILLS_PAGE).text
    # One HTML table per bill maturity; read amounts as text to handle the
    # mixed formats ourselves.
    tables = pd.read_html(io.StringIO(html), thousands=None, converters={
        "AMOUNT AUCTIONED": str, "AMOUNT OFFERED": str, "AMOUNT ACCEPTED": str,
    })

    frames = []
    for t in tables:
        t.columns = [str(c).strip().upper() for c in t.columns]
        yield_col = next((c for c in t.columns if "YIELD" in c), None)
        if "AUCTION DATE" not in t.columns or yield_col is None:
            continue
        auctioned = amount(t["AMOUNT AUCTIONED"])
        offered = amount(t["AMOUNT OFFERED"])  # i.e. bids received
        accepted = amount(t["AMOUNT ACCEPTED"])
        frames.append(
            pd.DataFrame(
                {
                    "auction_date": pd.to_datetime(t["AUCTION DATE"], dayfirst=True, errors="coerce"),
                    "maturity_date": pd.to_datetime(t["MATURITY DATE"], dayfirst=True, errors="coerce"),
                    # PDMA's coverage ratio: bids / amount auctioned. "Accepted"
                    # also includes a non-competitive top-up after the auction.
                    "bid_to_cover": offered / auctioned.where(auctioned > 0),
                    "accepted": accepted * 1e6,
                    "auction_yield": pd.to_numeric(
                        t[yield_col].astype(str).str.replace("%", ""), errors="coerce"
                    ),
                }
            )
        )

    df = pd.concat(frames, ignore_index=True)
    # The ECB/Bank of Greece are barred from buying at primary auctions.
    df["cb_accepted"] = float("nan")
    return df


COUNTRY = Country(
    code="greece",
    name="Greece",
    fetch=fetch,
    volume_label="Amount accepted (€ billions)",
    volume_divisor=1e9,
    source="Greek Public Debt Management Agency (PDMA)",
    yield_note="Bills only (bonds are mostly syndicated). Bid-to-cover = bids ÷ amount auctioned.",
)
