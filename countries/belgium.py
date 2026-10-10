"""
Belgium: Belgian Debt Agency auction results for OLO bonds and Treasury
Certificates. The agency only publishes PDFs (one for the current year and
one historical file per instrument), so this parses their text line by line.
"""

import io
import re

import pandas as pd
import pdfplumber

from common import Country
from countries._http import get

BASE = "https://www.debtagency.be/sites/default/files/content/download/files/"
FILES = [
    "xx_adjuolo.pdf",             # OLO bonds, current year
    "xx_adjuolo_historical.pdf",  # OLO bonds, previous years back to 1998
    "xx_adjuct.pdf",              # Treasury Certificates (bills), current year
    "xx_adjuct_historical.pdf",   # Treasury Certificates, previous years
]

DATE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
# The "Lo/Hi bid" range (e.g. 100.70/100.95 or 104,00/104,54) appears only on
# result lines, so it marks the lines worth parsing and anchors the numbers.
LO_HI = re.compile(r"-?\d+[.,]\d+/-?\d+[.,]\d+")
NUMBER = re.compile(r"-?\d[\d.,]*")


def to_number(token: str) -> float:
    """Handle '1,647', '8,601.0', '2.800,0' and '-0.215'."""
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+,\d+", token):  # European: 2.800,0
        token = token.replace(".", "").replace(",", ".")
    else:
        token = token.replace(",", "")
    try:
        return float(token)
    except ValueError:
        return float("nan")


def parse_line(line: str, bills: bool = False):
    """
    Return (dates, total, yield, bid_to_cover) for a result line, or None.

    The numbers just before the Lo/Hi range hold the total sold, price,
    yield and bid-to-cover, but the order changed over the years:

      newer files (bonds and bills):  total, price, yield, cover
      older bond files:               total, cover, price, yield
      older bill files:               total, cover, yield, price, spread

    A price (50-200) sitting next to small numbers (yields and cover ratios
    are under 30) identifies the layout.
    """
    lo_hi = LO_HI.search(line)
    if not lo_hi:
        return None
    before = DATE.sub(" ", line[: lo_hi.start()])
    numbers = [to_number(t) for t in NUMBER.findall(before) if t not in ("-",)]
    if len(numbers) < 5:
        return None
    t = numbers[-5:]

    def is_price(x):
        return 50 <= x <= 200

    def is_small(x):
        return abs(x) < 30

    if is_price(t[2]) and is_small(t[3]) and is_small(t[4]):
        total, yld, cover = t[1], t[3], t[4]          # newer: total, price, yield, cover
    elif is_price(t[3]) and is_small(t[2]) and bills:
        total, cover, yld = t[0], t[1], t[2]          # older bills: ..., price, spread
    elif is_price(t[3]) and is_small(t[2]) and is_small(t[4]):
        total, cover, yld = t[1], t[2], t[4]          # older bonds: cover, price, yield
    else:
        return None
    return DATE.findall(line[: lo_hi.start()]), total, yld, cover


def parse_pdf(content: bytes, bills: bool) -> list[dict]:
    rows = []
    auction_date = None
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").splitlines():
                line = line.strip()
                parsed = parse_line(line, bills)
                if parsed is None:
                    # Header lines ("24/02/2025 7,278 3,045 ...") set the
                    # auction date for the security lines that follow.
                    if DATE.match(line) and "SYNDICATION" not in line.upper() and "OLO" not in line:
                        auction_date = DATE.match(line).group(1)
                    continue
                dates, total, yld, cover = parsed
                if not dates:
                    continue
                if len(dates) >= 2:
                    # Older layout: auction date (and value date) then maturity.
                    auction_date, maturity = dates[0], dates[-1]
                else:
                    maturity = dates[0]
                rows.append(
                    {"auction_date": auction_date, "maturity_date": maturity,
                     "accepted": total, "auction_yield": yld, "bid_to_cover": cover}
                )
    return rows


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for name in FILES:
        rows += parse_pdf(get(BASE + name, timeout=180).content, bills="adjuct" in name)

    df = pd.DataFrame(rows)
    df["auction_date"] = pd.to_datetime(df["auction_date"], format="%d/%m/%Y", errors="coerce")
    df["maturity_date"] = pd.to_datetime(df["maturity_date"], format="%d/%m/%Y", errors="coerce")
    df["accepted"] = df["accepted"] * 1e6  # EUR millions
    # Guard against mis-parsed lines (e.g. a page footer that happens to match).
    df = df[df["bid_to_cover"].between(0.5, 20) & df["auction_yield"].between(-2, 20)]
    # The current-year and historical files can overlap around year end.
    df = df.drop_duplicates(subset=["auction_date", "maturity_date", "accepted"])
    # The ECB/National Bank of Belgium are barred from buying at primary auctions.
    df["cb_accepted"] = float("nan")
    return df


COUNTRY = Country(
    code="belgium",
    name="Belgium",
    fetch=fetch,
    volume_label="Amount accepted (€ billions)",
    volume_divisor=1e9,
    source="Belgian Debt Agency (parsed from its PDF results)",
    yield_note="Weighted average yield. Parsed from PDFs, so treat odd single points with caution.",
)
