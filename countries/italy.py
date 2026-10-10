"""Italy: Banca d'Italia auction results (history since 2002 plus the current year)."""

import io
import zipfile

import pandas as pd

from common import Country
from countries._http import get

BASE = "https://www.bancaditalia.it/compiti/operazioni-mef/risultati-aste/"
FILES = ["storico_aste.zip", "Aste_anno_corrente.zip"]

COLUMNS = {
    0: "auction_date", 4: "round", 6: "maturity_date", 8: "type",
    12: "bids", 13: "assigned", 15: "bot_yield", 16: "yield",
}

# Nominal fixed-rate and zero-coupon securities. Excluded: CCT/CCTEU
# (floating) and BTPI (inflation-linked).
NOMINAL_TYPES = ("BOT", "BTP", "CTZ", "BTPGREEN")


def read_zip(name: str) -> pd.DataFrame:
    archive = zipfile.ZipFile(io.BytesIO(get(BASE + name).content))
    sheet = pd.read_excel(io.BytesIO(archive.read(archive.namelist()[0])), header=None)
    df = sheet[list(COLUMNS)].rename(columns=COLUMNS)
    df["auction_date"] = pd.to_datetime(df["auction_date"], errors="coerce", format="mixed")
    return df[df["auction_date"].notna()]


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    df = pd.concat([read_zip(name) for name in FILES], ignore_index=True)

    df["type"] = df["type"].astype(str).str.strip()
    # Ordinary auctions only ("O"); "S" rows are supplementary placements
    # reserved for specialist dealers right after the main auction.
    df = df[df["type"].isin(NOMINAL_TYPES) & (df["round"].astype(str).str.strip() == "O")]
    df = df.drop_duplicates(subset=["auction_date", "maturity_date", "type", "assigned"])

    bids = pd.to_numeric(df["bids"], errors="coerce")
    assigned = pd.to_numeric(df["assigned"], errors="coerce")
    # BOT bills report their yield in a separate column (and 0 in the
    # general one), so pick the right column by security type.
    yields = pd.to_numeric(df["yield"], errors="coerce").where(
        df["type"] != "BOT", pd.to_numeric(df["bot_yield"], errors="coerce")
    )
    return pd.DataFrame(
        {
            "auction_date": df["auction_date"],
            "maturity_date": pd.to_datetime(df["maturity_date"], errors="coerce"),
            "bid_to_cover": bids / assigned.where(assigned > 0),
            "accepted": assigned * 1e6,  # EUR millions
            "auction_yield": yields,
            # The ECB/Banca d'Italia are barred from buying at primary auctions.
            "cb_accepted": float("nan"),
        }
    )


COUNTRY = Country(
    code="italy",
    name="Italy",
    fetch=fetch,
    volume_label="Amount assigned (€ billions)",
    volume_divisor=1e9,
    source="Banca d'Italia",
    yield_note="Gross annual yield at the allotment price.",
)
