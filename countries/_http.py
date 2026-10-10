"""Small download helpers shared by the country loaders."""

import re
from urllib.parse import urljoin

import requests

# Some agency sites reject requests without a browser-like User-Agent.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def get(url: str, timeout: int = 120) -> requests.Response:
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response


def find_links(page_url: str, pattern: str) -> list[str]:
    """
    Return absolute URLs of every href on `page_url` matching `pattern`.

    Several agencies put the date in their file names (e.g. 2026-10_hist_mlt.xlsx),
    so loaders look the current link up from the page instead of hard-coding it.
    """
    html = get(page_url).text
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
    links = [urljoin(page_url, h.strip()) for h in hrefs if re.search(pattern, h)]
    if not links:
        raise RuntimeError(f"No link matching {pattern!r} on {page_url}")
    # Keep page order but drop duplicates.
    return list(dict.fromkeys(links))
