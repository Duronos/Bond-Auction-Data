"""
South Korea: Korea Treasury Bond (KTB) auction results from the Ministry of
Economy and Finance's KTB site (English pages, since 2018).

The site lists auctions ten per page, newest first, and publishes each
auction's results as its own web page with a small table. Past auctions
never change, so detail pages are cached on disk after the first run.
"""

import io
import json
import re
import time
from pathlib import Path

import pandas as pd
import requests

from common import Country
from countries._http import HEADERS

BASE = "https://ktb.moef.go.kr/eng/"
LIST_PAGE = BASE + "aucRes.do?pageIndex={page}"
DETAIL_PAGE = BASE + "aucResDetail.do?nttId={ntt}&bbsId=BBSMSTR_000000000004"

CACHE = Path(__file__).resolve().parent.parent / "cache" / "korea"
REQUEST_PAUSE = 0.5  # seconds between uncached requests
MAX_PAGES = 1000  # safety stop for the list pages (there are ~170 today)


def listed_auctions(start: pd.Timestamp, session: requests.Session) -> list[tuple[str, pd.Timestamp, str]]:
    """(page id, auction date, title) for every auction since `start`, newest first."""
    found = []
    seen = set()
    page = 1
    while page <= MAX_PAGES:
        html = session.get(LIST_PAGE.format(page=page), headers=HEADERS, timeout=60).text
        ids = re.findall(r"bbsDetail\('(\d+)'", html)
        # Past the last page the site repeats the final page instead of
        # returning nothing, so stop when a page brings nothing new.
        if not ids or set(ids) <= seen:
            break
        seen.update(ids)
        table = pd.read_html(io.StringIO(html))[0]
        # Dates look like "2026.10.07" (older ones "2018.01. 9").
        dates = pd.to_datetime(
            table["Auction Date"].astype(str).str.replace(" ", ""), format="%Y.%m.%d", errors="coerce"
        )
        found += list(zip(ids, dates, table["Title"].astype(str)))
        if dates.min() < start:
            break
        page += 1
        time.sleep(REQUEST_PAUSE)
    return [row for row in found if pd.notna(row[1]) and row[1] >= start]


def detail_html(ntt: str, session: requests.Session) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{ntt}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["html"]
    time.sleep(REQUEST_PAUSE)
    response = session.get(DETAIL_PAGE.format(ntt=ntt), headers=HEADERS, timeout=60)
    response.raise_for_status()
    path.write_text(json.dumps({"html": response.text}), encoding="utf-8")
    return response.text


def number(text) -> float:
    """'4,582 billion won' -> 4582.0, '2.130%' -> 2.13."""
    m = re.search(r"-?[\d,]*\.?\d+", str(text))
    return float(m.group(0).replace(",", "")) if m else float("nan")


def parse_detail(html: str, auction_date: pd.Timestamp, title: str) -> dict | None:
    tables = pd.read_html(io.StringIO(html))
    # A few pages titled like auctions are really post-auction option
    # notices with no results table; skip anything without one.
    if tables[-1].shape[1] < 2:
        return None
    fields = tables[-1].iloc[:, :2].astype(str)
    # Labels vary slightly between years ("Bid- to-Cover Ratio (%)", double spaces).
    values = {" ".join(k.lower().split()): v for k, v in zip(fields.iloc[:, 0], fields.iloc[:, 1])}

    def field(starts_with):
        return next((v for k, v in values.items() if k.startswith(starts_with)), None)

    # Tenor from the title: "30Y KTB 04500-5609" or "30-year Treasury Bonds ...".
    tenor = re.search(r"(\d+)\s*(?:Y\b|-year)", title, re.I)
    issue = pd.to_datetime(field("issue date"), errors="coerce")
    if not tenor or pd.isna(issue) or field("amount accepted") is None:
        return None
    received = number(field("amount received"))
    accepted = number(field("amount accepted"))
    rate = number(field("highest accepted rate") or field("accepted rate"))
    if rate > 20:
        # At least one page has a typo (an "accepted rate" of 301.4%, likely
        # the bid-to-cover in the wrong row); KTB yields are nowhere near 20%.
        rate = float("nan")
    return {
        "auction_date": auction_date,
        # KTBs are reopened for months after first issue, so the bond's
        # maturity is its original issue date plus the tenor.
        "maturity_date": issue + pd.DateOffset(years=int(tenor.group(1))),
        "bid_to_cover": received / accepted if accepted else float("nan"),
        "accepted": accepted * 1e9,  # billions of won
        # The highest accepted rate is the clearing yield. Some pages only
        # give a single "accepted rate" (everyone allotted at one rate).
        "auction_yield": rate,
    }


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    session = requests.Session()
    rows = []
    for ntt, auction_date, title in listed_auctions(start, session):
        # Skip non-competitive post-auction option allotments and
        # inflation-linked bonds (KTBi), which quote real yields.
        if "option" in title.lower() or "ktbi" in title.lower():
            continue
        row = parse_detail(detail_html(ntt, session), auction_date, title)
        if row:
            rows.append(row)
    df = pd.DataFrame(rows)
    # The Bank of Korea doesn't buy at KTB auctions.
    df["cb_accepted"] = float("nan")
    return df


COUNTRY = Country(
    code="korea",
    name="South Korea",
    fetch=fetch,
    volume_label="Amount accepted (₩ trillions)",
    volume_divisor=1e12,
    source="Ministry of Economy and Finance (Korea Treasury Bond auctions)",
    yield_note="Highest accepted (clearing) rate. KTB bonds only; English results pages start in 2018.",
)
