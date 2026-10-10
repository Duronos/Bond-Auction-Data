"""
European Union: EU-Bonds and EU-Bills auctions run by the European Commission
(the EU's own borrowing, e.g. NextGenerationEU), since September 2021.

Not a country, but treated like one. The ECB is the euro area's central bank;
like the national central banks it is barred from buying at primary
auctions, so there is no central bank share.

The Commission publishes one web page per auction. Recent years are linked
from yearly listing pages; older years' listings moved to an archive that
blocks automated access, but the result pages themselves are still live at
date-based addresses. So for those years this tries the address for every
auction weekday (bills on Wednesdays, bonds on Mondays). Pages for past
auctions never change, so they're cached on disk after the first run.
"""

import io
import json
import re
import time
from pathlib import Path

import pandas as pd
import requests

from common import Country
from countries._http import HEADERS, find_links

BASE = "https://commission.europa.eu"
INDEX = (
    BASE + "/strategy-and-policy/eu-budget/eu-borrower-investor-relations/"
    "news-and-press-material/auction-announcements-and-results_en"
)
RESULT_URL = BASE + "/news-and-media/news/results-{d:%d-%m-%Y}-auction-eu-{kind}-{d:%Y-%m-%d}_en"
FIRST_AUCTION = pd.Timestamp("2021-09-01")
WEEKDAY = {"bills": "W-WED", "bonds": "W-MON"}

CACHE = Path(__file__).resolve().parent.parent / "cache" / "eu"
# Seconds between uncached requests. Slow, but the site rate-limits hard and
# only the first run fetches everything (later runs read the cache).
REQUEST_PAUSE = 3.0


def cached_page(url: str, session: requests.Session) -> str | None:
    """Fetch a result page, caching both hits and misses (past pages never change)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = CACHE / (re.sub(r"[^\w\-]", "_", url.rsplit("/", 1)[-1]) + ".json")
    if key.exists():
        return json.loads(key.read_text(encoding="utf-8"))["html"]

    # The site rate-limits bursts (HTTP 429), so pace uncached requests and
    # back off when told to.
    for attempt in range(6):
        time.sleep(REQUEST_PAUSE)
        response = session.get(url, headers=HEADERS, timeout=60)
        if response.status_code != 429:
            break
        retry_after = float(response.headers.get("Retry-After") or 0)
        time.sleep(max(retry_after, 30 * (attempt + 1)))
    if response.status_code not in (200, 404):
        response.raise_for_status()
    html = response.text if response.status_code == 200 else None
    key.write_text(json.dumps({"html": html}), encoding="utf-8")
    return html


def label_value(table: pd.DataFrame, starts_with: str) -> pd.Series:
    """Row of the (transposed-style) results table whose label starts with the text."""
    labels = table.iloc[:, 0].astype(str).str.strip().str.lower()
    hits = table[labels.str.startswith(starts_with.lower())]
    if hits.empty:
        return pd.Series(float("nan"), index=table.columns[1:])
    return hits.iloc[0, 1:]


def millions(values: pd.Series) -> pd.Series:
    """'2 273' -> 2273.0 (thin-space thousands separators)."""
    cleaned = values.astype(str).str.replace(r"[\s  ,]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


def to_date(values: pd.Series):
    """Day-first dates written several ways over the years: 15/06/2022, 15-06-2022, 01-03-23."""
    return pd.to_datetime(values.values, format="mixed", dayfirst=True, errors="coerce")


def percent(values: pd.Series) -> pd.Series:
    """'3.109%' or, on some older pages, '2,760%' (decimal comma)."""
    cleaned = values.astype(str).str.replace("%", "").str.replace(",", ".").str.strip()
    return pd.to_numeric(cleaned, errors="coerce")


def parse_results(html: str) -> pd.DataFrame:
    """One results page -> one row per security auctioned that day."""
    try:
        # Read every cell as text: some older pages use decimal commas
        # ("2,20"), which pandas would otherwise read as 220.
        table = pd.read_html(io.StringIO(html), thousands=None)[0].astype(str)
    except ValueError:
        return pd.DataFrame()
    # Bills label the date "Date of auction"; bonds "Competitive auction".
    dates = label_value(table, "Date of auction")
    if dates.isna().all():
        dates = label_value(table, "Competitive auction")
    bids = millions(label_value(table, "Volume bids"))
    allotted = millions(label_value(table, "Volume allotment"))
    non_comp = millions(label_value(table, "Non-competitive allocation")).fillna(0)
    return pd.DataFrame(
        {
            "auction_date": to_date(dates),
            "maturity_date": to_date(label_value(table, "Maturity")),
            # Computed from the volumes rather than read from the "Cover
            # ratio" row, whose number format varies between pages.
            "bid_to_cover": (bids / allotted.where(allotted > 0)).values,
            "accepted": ((allotted + non_comp) * 1e6).values,  # EUR millions
            # Weighted average yield: the only yield bond results publish, so
            # bills use it too for consistency. A few early bill pages only
            # give the highest accepted yield, so fall back to that.
            "auction_yield": percent(label_value(table, "Weighted average yield")).fillna(
                percent(label_value(table, "Highest accepted yield"))
            ).values,
        }
    )


def result_urls(start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    # Current years: the listing pages linked from the index (eu-bills-2026_en, ...).
    listing_pages = find_links(INDEX, r"commission\.europa\.eu/eu-(bills|bonds)-\d{4}_en$")
    urls = []
    listed_years = set()
    for page in listing_pages:
        listed_years.add(int(re.search(r"(\d{4})_en$", page).group(1)))
        urls += find_links(page, r"results-.*-auction-eu-(bills|bonds)")

    # Earlier years: try the date-based address on each auction weekday.
    first_listed = pd.Timestamp(min(listed_years), 1, 1) if listed_years else end
    probe_from = max(start, FIRST_AUCTION)
    for kind, freq in WEEKDAY.items():
        for day in pd.date_range(probe_from, first_listed - pd.Timedelta(days=1), freq=freq):
            urls.append(RESULT_URL.format(d=day, kind=kind))
    return list(dict.fromkeys(urls))


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    session = requests.Session()
    frames = []
    for url in result_urls(start, end):
        html = cached_page(url, session)
        if html:
            frames.append(parse_results(html))
    df = pd.concat(frames, ignore_index=True).drop_duplicates()
    df["cb_accepted"] = float("nan")
    return df


COUNTRY = Country(
    code="eu",
    name="European Union",
    fetch=fetch,
    volume_label="Amount allotted (€ billions)",
    volume_divisor=1e9,
    source="European Commission (EU-Bonds and EU-Bills auctions)",
    yield_note="Weighted average yield. EU auctions began in September 2021.",
)
