"""
Slovakia: government bond auction results from ARDAL (the Debt and Liquidity
Management Agency), published as one PDF per auction.

The PDFs use labelled lines ("TOTAL BIDS (EUR) ...", "Maturity ...") in a few
templates over the years, so each field is found by its label. Parsed results
are cached per PDF, since past auctions never change and re-downloading every
PDF on every run would be slow.
"""

import io
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import pdfplumber
import requests

from common import Country
from countries._http import HEADERS, get

PAGE = "https://www.ardal.sk/en/government-securities/government-bonds/results-of-gov-bonds-auctions"
CACHE = Path(__file__).resolve().parent.parent / "cache" / "slovakia" / "parsed.json"
REQUEST_PAUSE = 0.5  # seconds between PDF downloads


def first(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text, re.I)
    return m.group(1).strip() if m else None


def amount(text: str | None) -> float:
    """'215,500,000' or '1 158 500 000' -> 215500000.0."""
    if not text:
        return float("nan")
    return float(re.sub(r"[\s,]", "", text))


def to_date(text: str | None) -> str | None:
    """'04 June 2029', '20 November 2017', '13.11.2009' or '21.1.2015' -> ISO date."""
    if not text:
        return None
    date = pd.to_datetime(text, dayfirst=True, errors="coerce", format="mixed")
    return None if pd.isna(date) else date.strftime("%Y-%m-%d")


def is_buy_back(url_or_text: str) -> bool:
    """Buy-backs (the state repurchasing its bonds) aren't sale auctions."""
    name = url_or_text.rsplit("/", 1)[-1]
    return bool(re.search(r"buy\s*-?\s*back|\bBB\d*\b", name, re.I))


def parse_pdf(content: bytes) -> dict | None:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    if is_buy_back(text.splitlines()[1] if len(text.splitlines()) > 1 else text):
        return None
    date_pattern = r"(\d{1,2}\.\s?\d{1,2}\.\s?\d{4}|\d{1,2} [A-Za-z]+ \d{4})"
    auction = to_date(
        first(text, r"Date of competitive part of the auction\s+" + date_pattern)
        or first(text, r"A[uc]*tion date\s+" + date_pattern)  # also the old typo "Action date"
    )
    maturity = to_date(first(text, r"Maturity\s+" + date_pattern))
    number = r"([\d][\d ,]*\d)"
    # The first "total bids" / "accepted bids" lines belong to the
    # competitive part of the auction.
    bids = amount(first(text, r"Total bids \(EUR\)\s+" + number))
    accepted = amount(first(text, r"Accepted bids \(EUR\)\s+" + number))
    issued = amount(first(text, r"TOTAL ISSUED AMOUNT \(EUR\)\s+" + number))
    # Older PDFs (around 2009-2012) only report prices, not yields.
    max_yield = first(text, r"Maximum accepted yield to maturity \(% p\. ?a\.\)\s+(-?[\d.]+)")
    if not auction or not maturity:
        return None
    return {
        "auction_date": auction,
        "maturity_date": maturity,
        "bids": bids,
        "accepted_competitive": accepted,
        # Total issued includes the non-competitive part; older PDFs only
        # give the competitive amount.
        "issued": issued if issued == issued else accepted,
        "max_yield": float(max_yield) if max_yield else None,
    }


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    html = get(PAGE).text
    # Some links use Windows-style backslashes.
    links = list(dict.fromkeys(
        urljoin(PAGE, h.replace("\\", "/")) for h in re.findall(r'href="([^"]+\.pdf)"', html, re.I)
    ))

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    for url in links:
        if url in cache:
            continue
        time.sleep(REQUEST_PAUSE)
        response = session.get(url, headers=HEADERS, timeout=60)
        # A few links on ARDAL's page are broken (404); remember them as None.
        is_pdf = response.ok and response.content[:4] == b"%PDF"
        cache[url] = parse_pdf(response.content) if is_pdf else None
        CACHE.write_text(json.dumps(cache), encoding="utf-8")

    # Skip buy-back results (file names like "gb 210 BB1.pdf"); older cache
    # entries were parsed before buy-backs were excluded in parse_pdf.
    df = pd.DataFrame([row for url, row in cache.items() if row and not is_buy_back(url)])
    df = df.drop_duplicates(subset=["auction_date", "maturity_date", "accepted_competitive"])
    return pd.DataFrame(
        {
            "auction_date": pd.to_datetime(df["auction_date"]),
            "maturity_date": pd.to_datetime(df["maturity_date"]),
            "bid_to_cover": df["bids"] / df["accepted_competitive"].where(df["accepted_competitive"] > 0),
            "accepted": df["issued"],  # EUR
            # The maximum accepted yield is the clearing yield.
            "auction_yield": pd.to_numeric(df["max_yield"], errors="coerce"),
            # The ECB/Národná banka Slovenska are barred from buying at primary auctions.
            "cb_accepted": float("nan"),
        }
    )


COUNTRY = Country(
    code="slovakia",
    name="Slovakia",
    fetch=fetch,
    volume_label="Amount issued (€ millions)",
    volume_divisor=1e6,
    source="ARDAL (parsed from its per-auction PDFs)",
    yield_note=(
        "Maximum accepted (clearing) yield, from 2017 only: earlier PDFs report "
        "prices, not yields. Bonds only; buy-backs excluded."
    ),
)
