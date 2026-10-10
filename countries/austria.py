"""Austria: OeKB (auction agent) chronological list of Government Bond issues since 1999."""

import io

import pandas as pd

from common import Country
from countries._http import find_links, get

PAGE = (
    "https://www.oekb.at/en/capital-market-services/our-range-of-data-knowledge-creates-an-advantage/"
    "data-on-austrian-government-bonds/statistics-and-publications-on-government-bonds.html"
)


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    url = find_links(PAGE, r"Government-Bond-Issues-chronological\.xlsx$")[0]
    sheet = pd.read_excel(io.BytesIO(get(url).content), header=None)

    # The header row is the one starting with "ISIN"; units are on the next row.
    header_row = sheet.index[sheet[0].astype(str).str.strip() == "ISIN"][0]
    headers = sheet.iloc[header_row].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    data = sheet.iloc[header_row + 2 :]

    def col(starts_with):
        i = next(i for i, h in enumerate(headers) if h.startswith(starts_with))
        return data.iloc[:, i]

    df = pd.DataFrame(
        {
            "auction_date": pd.to_datetime(col("Issue date"), errors="coerce"),
            "maturity_date": pd.to_datetime(col("Redemption date"), errors="coerce"),
            "instrument": col("Funding instrument").astype(str),
            # Competitive + non-competitive; excludes the Bund's own quota.
            "accepted": pd.to_numeric(col("thereof competitive"), errors="coerce").fillna(0)
            + pd.to_numeric(col("thereof non-competitive"), errors="coerce").fillna(0),
            "auction_yield": pd.to_numeric(col("Yield"), errors="coerce"),
        }
    )
    # Auctions only (syndications have no competitive/non-competitive split).
    df = df[df["instrument"].str.contains("auction", case=False, na=False)].drop(columns="instrument")
    # Yields may be stored as decimals; convert to percent.
    if df["auction_yield"].abs().median() < 0.2:
        df["auction_yield"] *= 100

    # OeKB publishes amounts and yields but not total bids, so no bid-to-cover.
    df["bid_to_cover"] = float("nan")
    # The ECB/Oesterreichische Nationalbank are barred from buying at primary auctions.
    df["cb_accepted"] = float("nan")
    return df


COUNTRY = Country(
    code="austria",
    name="Austria",
    fetch=fetch,
    volume_label="Amount sold at auction (€ billions)",
    volume_divisor=1e9,
    source="OeKB (Austrian Government Bond auction agent)",
    yield_note="Yield for the investor. Total bids aren't published, so there is no bid-to-cover.",
)
