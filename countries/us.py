"""United States: U.S. Treasury Fiscal Data API (auctions_query)."""

import io

import pandas as pd
import requests

from common import Country, line_chart

API_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/"
    "fiscal_service/v1/accounting/od/auctions_query"
)

# Keep API pages moderate and paginate so the script remains robust.
PAGE_SIZE = 500


def fetch_raw(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Download all Treasury auction records in the requested date range."""
    rows = []
    page = 1

    while True:
        params = {
            "filter": (
                f"auction_date:gte:{start.date().isoformat()},"
                f"auction_date:lte:{end.date().isoformat()}"
            ),
            "sort": "auction_date",
            "page[number]": page,
            "page[size]": PAGE_SIZE,
            "format": "json",
        }

        response = requests.get(API_URL, params=params, timeout=60)
        response.raise_for_status()
        payload = response.json()

        page_rows = payload.get("data", [])
        rows.extend(page_rows)

        total_pages = int(payload.get("meta", {}).get("total-pages", 1))
        if page >= total_pages or not page_rows:
            break
        page += 1

    if not rows:
        raise RuntimeError("Treasury API returned no auction records.")

    return pd.DataFrame(rows)


def numeric_column(df: pd.DataFrame, name: str) -> pd.Series:
    """
    Convert a Treasury API column to numeric.
    If a field is unavailable, return NaN so the rest of the script still runs.
    """
    if name not in df.columns:
        return pd.Series(float("nan"), index=df.index, dtype="float64")
    return pd.to_numeric(df[name].replace({"null": None, "": None}), errors="coerce")


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    df = fetch_raw(start, end)

    # Restrict to nominal Bills, Notes, and Bonds.
    # Treasury's dataset can also contain TIPS and FRNs.
    df = df[df["security_type"].isin(["Bill", "Note", "Bond"])].copy()
    for flag_col in ["inflation_index_security", "tips", "floating_rate"]:
        if flag_col in df.columns:
            flag = df[flag_col].astype(str).str.lower()
            df = df[~flag.isin(["yes", "y", "true"])].copy()

    out = pd.DataFrame(
        {
            "auction_date": df["auction_date"],
            "maturity_date": df["maturity_date"],
            "bid_to_cover": numeric_column(df, "bid_to_cover_ratio"),
            "accepted": numeric_column(df, "total_accepted"),
            # Notes and bonds report high_yield; bills leave it blank and
            # report the bond-equivalent high_investment_rate instead.
            "auction_yield": numeric_column(df, "high_yield").fillna(
                numeric_column(df, "high_investment_rate")
            ),
            # total_accepted already includes the Fed's SOMA amount.
            "cb_accepted": numeric_column(df, "soma_accepted"),
            "primary_dealer_accepted": numeric_column(df, "primary_dealer_accepted"),
            "direct_bidder_accepted": numeric_column(df, "direct_bidder_accepted"),
            "indirect_bidder_accepted": numeric_column(df, "indirect_bidder_accepted"),
        }
    )
    return out


MARKET_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv"
)


def fetch_market(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Daily Treasury par yield curve rates, one CSV per calendar year."""
    frames = []
    for year in range(start.year, end.year + 1):
        response = requests.get(MARKET_URL.format(year=year), timeout=120)
        response.raise_for_status()
        frames.append(
            pd.read_csv(io.StringIO(response.text))
            .melt(id_vars="Date", var_name="label", value_name="market_yield")
        )
    df = pd.concat(frames, ignore_index=True)
    # Column labels look like "3 Mo", "1.5 Month" or "10 Yr".
    parts = df["label"].str.extract(r"([\d.]+)\s*(Mo|Month|Yr)")
    return pd.DataFrame(
        {
            "date": pd.to_datetime(df["Date"], format="%m/%d/%Y"),
            "tenor_months": pd.to_numeric(parts[0]) * parts[1].map({"Mo": 1, "Month": 1, "Yr": 12}),
            "market_yield": pd.to_numeric(df["market_yield"], errors="coerce"),
        }
    ).dropna()


def nondealer_plot(monthly: pd.DataFrame) -> dict:
    return {
        "nondealer_share": (
            line_chart(
                monthly, "nondealer_share_pct_3m", "bucket",
                "United States: non-primary-dealer share of Treasury auction awards",
                "Direct + indirect bidder awards as a share of reported bidder awards",
                "Non-dealer share (%)",
                "A rough proxy for end-investor participation; not a count of unique buyers.",
                y_limits=(0, 100),
            ),
            None,
        )
    }


COUNTRY = Country(
    code="us",
    name="United States",
    fetch=fetch,
    volume_label="Accepted amount (US$ billions)",
    volume_divisor=1e9,
    source="U.S. Treasury Fiscal Data API",
    yield_note="Bills use the bond-equivalent investment rate; notes and bonds use the high yield.",
    central_bank="Federal Reserve (SOMA)",
    extra_plots=[nondealer_plot],
    fetch_market=fetch_market,
    market_source="U.S. Treasury daily par yield curve rates",
)
