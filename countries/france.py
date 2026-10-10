"""France: Agence France Trésor auction history (OATs since 1999, BTF bills since 1999)."""

import io
import re

import pandas as pd

from common import Country
from countries._http import find_links, get

OAT_PAGE = "https://www.aft.gouv.fr/en/principaux-chiffres-oat"
BTF_PAGE = "https://www.aft.gouv.fr/en/btf-principaux-chiffres"

FRENCH_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "décembre": 12, "decembre": 12,
}


def maturity_from_name(name: str) -> pd.Timestamp:
    """'OAT 4% 25 avril 2009' -> 2009-04-25. The OAT file has no maturity column."""
    m = re.search(r"(\d{1,2})\s+([a-zéû]+)\s+(\d{4})\s*$", str(name).strip().lower())
    if not m or m.group(2) not in FRENCH_MONTHS:
        return pd.NaT
    return pd.Timestamp(int(m.group(3)), FRENCH_MONTHS[m.group(2)], int(m.group(1)))


def latest_file(page: str, pattern: str) -> pd.DataFrame:
    # File names carry the month (e.g. 2026-10_hist_mlt.xlsx); newest sorts last.
    url = sorted(find_links(page, pattern))[-1]
    return pd.read_excel(io.BytesIO(get(url).content), header=0)


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    # --- OATs (medium and long term) ---
    oat = latest_file(OAT_PAGE, r"_hist_mlt\.xlsx$")
    oat = oat.iloc[:, :14]
    oat.columns = [
        "type", "auction_date", "settlement", "min", "max", "isin", "line",
        "bids", "served", "btc", "nct", "total", "rate", "price",
    ]
    # adju_LT / adju_MT are nominal; adju_I is inflation-linked (OAT€i, OATi).
    oat = oat[oat["type"].isin(["adju_LT", "adju_MT"])]
    oat = pd.DataFrame(
        {
            "auction_date": oat["auction_date"],
            "maturity_date": oat["line"].map(maturity_from_name),
            "bid_to_cover": pd.to_numeric(oat["btc"], errors="coerce"),
            # Total issued includes non-competitive (NCT) top-ups; EUR millions.
            "accepted": pd.to_numeric(oat["total"], errors="coerce") * 1e6,
            # Weighted average rate, stored as a decimal (0.0381 = 3.81%).
            "auction_yield": pd.to_numeric(oat["rate"], errors="coerce") * 100,
        }
    )

    # --- BTF (Treasury bills) ---
    btf = latest_file(BTF_PAGE, r"\d{4}-\d{2}_hist_btf\.xlsx$")
    btf = btf.iloc[:, :13]
    btf.columns = [
        "auction_date", "settlement", "weeks", "maturity_date", "min", "max", "isin",
        "bids", "served", "btc", "nct", "total", "rate",
    ]
    btf = pd.DataFrame(
        {
            "auction_date": btf["auction_date"],
            "maturity_date": btf["maturity_date"],
            "bid_to_cover": pd.to_numeric(btf["btc"], errors="coerce"),
            "accepted": pd.to_numeric(btf["total"], errors="coerce") * 1e6,
            "auction_yield": pd.to_numeric(btf["rate"], errors="coerce") * 100,
        }
    )

    df = pd.concat([oat, btf], ignore_index=True)
    # The ECB/Banque de France are barred from buying at primary auctions.
    df["cb_accepted"] = float("nan")
    return df


COUNTRY = Country(
    code="france",
    name="France",
    fetch=fetch,
    volume_label="Amount issued (€ billions)",
    volume_divisor=1e9,
    source="Agence France Trésor",
    yield_note="Weighted average accepted rate.",
)
