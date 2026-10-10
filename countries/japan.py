"""Japan: Ministry of Finance historical JGB and T-bill auction results."""

import io

import pandas as pd
import requests

from common import Country

BASE_URL = "https://www.mof.go.jp/english/policy/jgbs/auction/past_auction_results/"

# Nominal fixed-rate JGB sheets. Skipped: 15-year floating, 10-year
# inflation-indexed and the GX (climate transition) bonds.
JGB_SHEETS = ["40年債", "30年債", "20年債", "10年債", "5年債", "2年債", "6年債", "4年債", "割3年", "TB "]


def download(name: str) -> pd.ExcelFile:
    response = requests.get(BASE_URL + name, headers={"User-Agent": "Mozilla/5.0"}, timeout=120)
    response.raise_for_status()
    return pd.ExcelFile(io.BytesIO(response.content), engine="xlrd")


def parse_sheet(sheet: pd.DataFrame, unit: float) -> pd.DataFrame:
    """
    Parse one MOF sheet. Column layouts differ between sheets, so columns are
    found by their English header text.
    """
    header_row = next(
        i for i in range(10)
        if sheet.iloc[i].astype(str).str.contains("Auction Date").any()
    )
    headers = sheet.iloc[header_row].astype(str).str.replace(r"\s+", " ", regex=True)
    data = sheet.iloc[header_row + 1 :]

    def col(match):
        hits = [i for i, h in enumerate(headers) if match(h)]
        return data.iloc[:, hits[0]] if hits else pd.Series(float("nan"), index=data.index)

    num = lambda s: pd.to_numeric(s, errors="coerce")

    auction_date = pd.to_datetime(col(lambda h: h.startswith("Auction Date")), errors="coerce")
    comp_bids = num(col(lambda h: h.startswith("Amounts of Competitive Bids")))
    comp_accepted = num(col(lambda h: h.startswith("Amounts of Bids Accepted") and "Non-price" not in h))

    # Non-competitive and non-price-competitive allotments add to the amount sold.
    extra_idx = [i for i, h in enumerate(headers) if "Non-price" in h or "Non-competitive" in h]
    extra = data.iloc[:, extra_idx].apply(pd.to_numeric, errors="coerce").sum(axis=1)

    out = pd.DataFrame(
        {
            "auction_date": auction_date,
            "maturity_date": pd.to_datetime(col(lambda h: h.startswith("Maturity Date")), errors="coerce"),
            # MOF's bid-to-cover: competitive bids / competitive bids accepted.
            "bid_to_cover": comp_bids / comp_accepted.where(comp_accepted > 0),
            "accepted": (comp_accepted + extra) * unit,
            # Clearing yield (the yield at the lowest accepted price).
            "auction_yield": num(col(lambda h: "Lowest Accepted Price" in h and h.startswith("Yield")
                                     or h.startswith("Highest Accepted Yield"))),
            # The Bank of Japan is barred from buying at JGB auctions.
            "cb_accepted": float("nan"),
        }
    )
    return out[out["auction_date"].notna()]


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    frames = []

    jgb = download("Auction_Results_for_JGBs.xls")
    for name in JGB_SHEETS:
        if name in jgb.sheet_names:
            # Amounts are in units of 100 million yen.
            frames.append(parse_sheet(jgb.parse(name, header=None), unit=1e8))

    # One sheet per fiscal year from FY2008; amounts in billions of yen.
    tbills = download("Auction_Results_for_T-bills.xls")
    for name in tbills.sheet_names:
        frames.append(parse_sheet(tbills.parse(name, header=None), unit=1e9))

    df = pd.concat(frames, ignore_index=True)
    # The old "TB" sheet in the JGB file overlaps FY2008 in the T-bill file.
    return df.drop_duplicates(subset=["auction_date", "maturity_date"], keep="last")


COUNTRY = Country(
    code="japan",
    name="Japan",
    fetch=fetch,
    volume_label="Amount sold (¥ trillions)",
    volume_divisor=1e12,
    source="Ministry of Finance Japan",
    yield_note="Clearing yield (yield at the lowest accepted price).",
)
