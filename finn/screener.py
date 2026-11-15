"""screener.in: the quarterly results table and the concall transcript links for any NSE ticker.

Only primary data is used: screener's table of reported quarterly numbers and links
to the transcripts the companies filed. Screener's own AI summaries are ignored.
"""
import re
from dataclasses import asdict, dataclass, field

import requests

from . import cache

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
BASE = "https://www.screener.in/company/{t}/{v}"
PAGE_TTL = 24 * 3600
TICKER_RX = re.compile(r"^[A-Z0-9&\-]{1,20}$")


class NotFound(Exception):
    pass


@dataclass
class Concall:
    label: str          # "Nov 2026" as screener lists it
    transcript: str     # direct PDF url


@dataclass
class Company:
    ticker: str
    name: str
    url: str
    quarters: list[str]                 # "Sep 2025", oldest first
    rows: dict[str, list[float | None]]  # "Sales", "OPM %", "Operating Profit", "Profit before tax", "Net Profit"
    concalls: list[Concall] = field(default_factory=list)   # newest first

    def to_json(self):
        return asdict(self)

    @staticmethod
    def from_json(d):
        d = dict(d)
        d["concalls"] = [Concall(**c) for c in d.get("concalls", [])]
        return Company(**d)


def normalise_ticker(t: str) -> str:
    t = (t or "").strip().upper().replace(".NS", "").replace(" ", "")
    if not TICKER_RX.match(t):
        raise NotFound(f"“{t or '(empty)'}” doesn't look like an NSE ticker.")
    return t


def direct_pdf(url: str) -> str:
    """BSE's viewer link -> the PDF itself."""
    m = re.search(r"Pname=([\w-]+\.pdf)", url, re.I)
    if m:
        return f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/{m[1]}"
    m = re.search(r"^(https?://[^/]+)/[^?]*pdf-viewer\?pdf=([^&]+\.pdf)", url, re.I)   # e.g. marutisuzuki.com viewer
    if m:
        from urllib.parse import unquote
        return m[1] + unquote(m[2])
    return url


def _num(s: str) -> float | None:
    s = (s or "").strip().replace(",", "").replace("%", "").replace("+", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_html(ticker: str, html: str, url: str) -> Company:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.select_one("h1")
    name = h1.get_text(strip=True) if h1 else ticker
    quarters, rows = [], {}
    q = soup.select_one("#quarters table")
    if q:
        quarters = [th.get_text(strip=True) for th in q.select("thead th")][1:]
        for tr in q.select("tbody tr"):
            cells = [td.get_text(strip=True) for td in tr.select("td")]
            if not cells:
                continue
            label = re.sub(r"\s*\+$", "", cells[0]).strip()
            if label in ("Sales", "Revenue", "Operating Profit", "Financing Profit", "OPM %", "Financing Margin %",
                         "Profit before tax", "Net Profit", "Other Income", "Interest", "Depreciation"):
                rows[label] = [_num(c) for c in cells[1:]]
    concalls = []
    for li in soup.select(".concalls li"):
        lab = li.select_one("div")
        link = next((a for a in li.select("a") if "transcript" in (a.get("title", "") + a.get_text()).lower()), None)
        if lab and link and link.get("href"):
            concalls.append(Concall(lab.get_text(strip=True), direct_pdf(link["href"])))
    return Company(ticker, name, url, quarters, rows, concalls)


def fetch(ticker: str, ttl: float = PAGE_TTL) -> Company:
    """Consolidated view if it has numbers, else standalone. Cached for a day."""
    t = normalise_ticker(ticker)
    body, fresh = cache.read("screener", t, ttl)
    if body is not None and fresh:
        return Company.from_json(body)
    last_err = None
    for view in ("consolidated/", ""):
        url = BASE.format(t=t, v=view)
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        except requests.RequestException as e:
            last_err = e
            continue
        if r.status_code == 404:
            continue
        if r.status_code != 200:
            last_err = RuntimeError(f"screener.in answered HTTP {r.status_code}")
            continue
        c = parse_html(t, r.text, url)
        if c.quarters and any(v is not None for v in (c.rows.get("Sales") or c.rows.get("Revenue") or [])):
            cache.write("screener", t, c.to_json())
            return c
    if body is not None:          # stale beats nothing
        return Company.from_json(body)
    if last_err:
        raise RuntimeError(f"Couldn't reach screener.in for {t}: {last_err}")
    raise NotFound(f"No company called {t} on screener.in.")
