"""Germany: Finanzagentur issuance history since 1999 (Bubills, Schatz, Bobl, Bund)."""

import io

import pandas as pd
import requests

from common import Country

HISTORY_URL = (
    "https://www.deutsche-finanzagentur.de/fileadmin/user_upload/"
    "Institutionelle-investoren/auktionen/emissionshistorie_en.xlsx"
)

# Column positions in the sheet (the header spans three merged rows).
COLUMNS = {
    1: "auction_date",
    3: "security_type",   # Bubill, Schatz, Bobl, Bund, Green, ILB, USD-Bond
    5: "maturity_date",
    9: "process",         # Auc, M-A (multi-ISIN auction), Syn, OH
    10: "bids",
    13: "allotted",
    16: "avg_yield",
}


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    response = requests.get(HISTORY_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=120)
    response.raise_for_status()

    df = pd.read_excel(io.BytesIO(response.content), header=None)
    df = df[list(COLUMNS)].rename(columns=COLUMNS)
    df["auction_date"] = pd.to_datetime(df["auction_date"], errors="coerce", format="mixed")
    df = df[df["auction_date"].notna()].copy()

    # Auctions only: syndicates and taps into own holdings have no bidding.
    # Inflation-linked and USD bonds are excluded, like TIPS in the US data.
    df = df[
        df["process"].isin(["Auc", "M-A"])
        & ~df["security_type"].isin(["ILB", "USD-Bond"])
    ]

    bids = pd.to_numeric(df["bids"], errors="coerce")
    allotted = pd.to_numeric(df["allotted"], errors="coerce")

    return pd.DataFrame(
        {
            "auction_date": df["auction_date"],
            "maturity_date": df["maturity_date"],
            # The file's own ratio is rounded to 0.1, so recompute it.
            # Allotted excludes the issuer's retention quote.
            "bid_to_cover": bids / allotted.where(allotted > 0),
            "accepted": allotted * 1e6,
            "auction_yield": pd.to_numeric(df["avg_yield"], errors="coerce"),
            # The ECB/Bundesbank are barred from buying at primary auctions.
            "cb_accepted": float("nan"),
        }
    )


COUNTRY = Country(
    code="germany",
    name="Germany",
    fetch=fetch,
    volume_label="Allotted at auction (€ billions)",
    volume_divisor=1e9,
    source="Bundesrepublik Deutschland – Finanzagentur",
    yield_note="Weighted average accepted yield.",
)
