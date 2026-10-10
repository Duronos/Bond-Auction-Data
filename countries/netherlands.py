"""Netherlands: Dutch State Treasury Agency issuance results (DSLs and DTC bills)."""

import io
import re

import pandas as pd

from common import Country
from countries._http import find_links, get

RESULTS_PAGE = "https://english.dsta.nl/latest/statistical-information/issuance-results"


def spreadsheet(document_pattern: str) -> pd.ExcelFile:
    """The results page links to a document page, which links to the .ods file."""
    document_page = find_links(RESULTS_PAGE, document_pattern)[0]
    ods_url = find_links(document_page, r"\.ods$")[0]
    return pd.ExcelFile(io.BytesIO(get(ods_url).content), engine="odf")


def _tolerate_error_cells():
    """
    Some DSTA sheets contain spreadsheet error cells (e.g. #REF!), which
    pandas' ODS reader rejects, failing the whole sheet. Patch it to read
    those cells as blank instead. Uses a private pandas class, so if that
    ever changes, the affected sheets are simply skipped (see parse_sheets).
    """
    try:
        from pandas.io.excel._odfreader import ODFReader
    except ImportError:
        return
    original = ODFReader._get_cell_value
    if getattr(original, "_tolerant", False):
        return

    def get_cell_value(self, cell):
        try:
            return original(self, cell)
        except ValueError:
            return float("nan")

    get_cell_value._tolerant = True
    ODFReader._get_cell_value = get_cell_value


def parse_sheets(book: pd.ExcelFile) -> list[pd.DataFrame]:
    _tolerate_error_cells()
    sheets = []
    for name in book.sheet_names:
        if not name.strip().isdigit():  # skip buy-back and scratch sheets
            continue
        try:
            sheets.append(book.parse(name, header=None))
        except ValueError:
            continue
    return sheets


def column(sheet: pd.DataFrame, header_row: int, starts_with: str, last: bool = False):
    """
    Find a column by its header text. Layouts change over the years (older
    sheets drop the ISIN and price columns), so positions can't be fixed.
    """
    headers = sheet.iloc[header_row].astype(str).str.strip()
    hits = [i for i, h in enumerate(headers) if h.startswith(starts_with)]
    if not hits:
        return None
    return sheet.columns[hits[-1] if last else hits[0]]


def to_date(series: pd.Series) -> pd.Series:
    # Mix of real dates and text like "7 January 2015".
    return pd.to_datetime(series, errors="coerce", format="mixed", dayfirst=True)


def fetch(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    frames = []

    # --- DSL bonds: volume in EUR billions, maturity in the bond name
    #     ("2,50% 2026 due 15 January 2031"). No bid amounts are published.
    for sheet in parse_sheets(spreadsheet(r"dsl-issuance")):
        header = next((i for i in range(4) if (sheet.iloc[i].astype(str) == "DSL").any()), None)
        if header is None:
            continue
        name_col = column(sheet, header, "DSL")
        # "Total Volume" includes non-competitive bids; older sheets only have "Volume".
        volume_col = column(sheet, header, "Total Volume") or column(sheet, header, "Volume")
        rows = sheet[sheet[name_col].astype(str).str.contains("due", na=False)]
        frames.append(
            pd.DataFrame(
                {
                    "auction_date": to_date(rows[column(sheet, header, "Uitgifte")]),
                    "maturity_date": to_date(rows[name_col].astype(str).str.extract(r"due\s+(.+)$")[0]),
                    "accepted": pd.to_numeric(rows[volume_col], errors="coerce") * 1e9,
                    "auction_yield": pd.to_numeric(rows[column(sheet, header, "Yield")], errors="coerce"),
                }
            )
        )

    # --- DTC bills: volume in EUR, maturity in the programme name ("DTC 2026-03-30").
    for sheet in parse_sheets(spreadsheet(r"issuance-results-dtc")):
        program_col = column(sheet, 0, "DTC program")
        if program_col is None:
            continue
        rows = sheet[sheet[program_col].astype(str).str.contains(r"DTC\s+\d{4}-\d{2}-\d{2}", na=False)]
        frames.append(
            pd.DataFrame(
                {
                    "auction_date": to_date(rows[column(sheet, 0, "Uitgifte")]),
                    "maturity_date": pd.to_datetime(
                        rows[program_col].astype(str).str.extract(r"(\d{4}-\d{2}-\d{2})")[0], errors="coerce"
                    ),
                    "accepted": pd.to_numeric(rows[column(sheet, 0, "Volume")], errors="coerce"),
                    "auction_yield": pd.to_numeric(rows[column(sheet, 0, "Yield")], errors="coerce"),
                }
            )
        )

    df = pd.concat(frames, ignore_index=True)
    # The DSTA publishes amounts and yields but not bids, so no bid-to-cover.
    df["bid_to_cover"] = float("nan")
    # The ECB/De Nederlandsche Bank are barred from buying at primary auctions.
    df["cb_accepted"] = float("nan")
    return df


COUNTRY = Country(
    code="netherlands",
    name="Netherlands",
    fetch=fetch,
    volume_label="Amount issued (€ billions)",
    volume_divisor=1e9,
    source="Dutch State Treasury Agency (DSTA)",
    yield_note="Issue yield. The DSTA does not publish bid amounts, so there is no bid-to-cover.",
)
