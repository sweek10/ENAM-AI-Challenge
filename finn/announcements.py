"""Corporate announcements: BSE API -> NSE API -> stale cache -> committed snapshot.

Every successful live fetch is (1) cached raw on disk, (2) upserted into SQLite,
(3) merged into data/snapshots/announcements/<SYMBOL>.json, which is committed
to git so the deployed demo works even if both exchanges block it.
"""
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime

import requests

from . import cache, config, db
from .portfolio import Stock

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

BSE_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
BSE_HEADERS = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
               "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"}
BSE_PDF = "https://www.bseindia.com/xml-data/corpfiling/{folder}/{name}"

NSE_HOME = "https://www.nseindia.com/"
NSE_URL = "https://www.nseindia.com/api/corporate-announcements"
NSE_HEADERS = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
               "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"}

TIMEOUT = 15
SNAPSHOT_MAX = 300


@dataclass
class Announcement:
    id: str
    symbol: str
    dt: str            # ISO, IST, "YYYY-MM-DDTHH:MM:SS"
    headline: str
    category: str
    subcategory: str
    pdf_url: str
    source: str        # "bse" | "nse"


@dataclass
class FetchResult:
    items: list[Announcement]
    source: str        # "cache" | "bse" | "nse" | "stale_cache" | "snapshot" | "none"
    errors: list[str]

    @property
    def stale(self) -> bool:
        return self.source in ("stale_cache", "snapshot", "none")


# ---------- raw fetchers (network) ----------

class SourceError(RuntimeError):
    pass


def _json(resp: requests.Response):
    if resp.status_code != 200:
        raise SourceError(f"HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError:
        raise SourceError(f"non-JSON response ({resp.text[:80]!r})")


def _bse_raw(bse_code: str, start: date, end: date) -> list[dict]:
    s = requests.Session()
    rows: list[dict] = []
    for page in range(1, 21):
        body = _json(s.get(BSE_URL, headers=BSE_HEADERS, timeout=TIMEOUT, params={
            "pageno": page, "strCat": "-1", "strPrevDate": start.strftime("%Y%m%d"),
            "strScrip": bse_code, "strSearch": "P", "strToDate": end.strftime("%Y%m%d"),
            "strType": "C", "subcategory": "-1",
        }))
        if not isinstance(body, dict) or "Table" not in body:
            raise SourceError(f"unexpected BSE payload keys: {list(body)[:5] if isinstance(body, dict) else type(body)}")
        table = body["Table"] or []
        rows.extend(table)
        total = (body.get("Table1") or [{}])[0].get("ROWCNT")
        if not table or (total is not None and len(rows) >= int(total)):
            break
    return rows


def _nse_raw(symbol: str, start: date, end: date) -> list[dict]:
    s = requests.Session()
    s.get(NSE_HOME, headers={"User-Agent": UA}, timeout=TIMEOUT)  # sets anti-bot cookies
    body = _json(s.get(NSE_URL, headers=NSE_HEADERS, timeout=TIMEOUT, params={
        "index": "equities", "symbol": symbol,
        "from_date": start.strftime("%d-%m-%Y"), "to_date": end.strftime("%d-%m-%Y"),
    }))
    if isinstance(body, dict):  # some variants wrap the list
        body = body.get("data", [])
    if not isinstance(body, list):
        raise SourceError("unexpected NSE payload")
    return body


# ---------- parsers (pure) ----------

def _iso(s: str, fmts: tuple[str, ...]) -> str:
    s = (s or "").strip()
    for f in fmts:
        try:
            return datetime.strptime(s, f).isoformat()
        except ValueError:
            continue
    return s


def parse_bse(rows: list[dict], symbol: str) -> list[Announcement]:
    out = []
    for r in rows:
        name = r.get("ATTACHMENTNAME") or ""
        folder = "AttachHis" if str(r.get("PDFFLAG")) == "1" else "AttachLive"
        dt_raw = (r.get("NEWS_DT") or r.get("DT_TM") or "")[:19]
        out.append(Announcement(
            id=f"bse:{r.get('NEWSID')}",
            symbol=symbol,
            dt=_iso(dt_raw, ("%Y-%m-%dT%H:%M:%S",)),
            headline=(r.get("HEADLINE") or r.get("NEWSSUB") or "").strip(),
            category=(r.get("CATEGORYNAME") or "").strip(),
            subcategory=(r.get("SUBCATNAME") or "").strip(),
            pdf_url=BSE_PDF.format(folder=folder, name=name) if name else "",
            source="bse",
        ))
    return out


def parse_nse(rows: list[dict], symbol: str) -> list[Announcement]:
    out = []
    for r in rows:
        if r.get("sort_date"):
            dt = _iso(r["sort_date"], ("%Y-%m-%d %H:%M:%S",))
        else:
            dt = _iso(r.get("an_dt", ""), ("%d-%b-%Y %H:%M:%S",))
        out.append(Announcement(
            id=f"nse:{r.get('seq_id') or r.get('an_dt')}",
            symbol=symbol,
            dt=dt,
            headline=(r.get("attchmntText") or r.get("desc") or "").strip(),
            category=(r.get("desc") or "").strip(),
            subcategory="",
            pdf_url=r.get("attchmntFile") or "",
            source="nse",
        ))
    return out


# ---------- persistence ----------

def _store(items: list[Announcement]) -> None:
    if not items:
        return
    with db.connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO announcements(id,symbol,dt,headline,category,subcategory,pdf_url,source) "
            "VALUES (:id,:symbol,:dt,:headline,:category,:subcategory,:pdf_url,:source)",
            [asdict(a) for a in items],
        )


def _snapshot_path(symbol: str):
    return config.snapshot_dir() / f"{symbol.upper()}.json"


def load_snapshot(symbol: str) -> list[Announcement]:
    p = _snapshot_path(symbol)
    if not p.exists():
        return []
    return [Announcement(**a) for a in json.loads(p.read_text())["items"]]


def update_snapshot(symbol: str, items: list[Announcement]) -> None:
    merged = {a.id: a for a in load_snapshot(symbol)}
    merged.update({a.id: a for a in items})
    keep = sorted(merged.values(), key=lambda a: a.dt, reverse=True)[:SNAPSHOT_MAX]
    _snapshot_path(symbol).write_text(json.dumps({
        "symbol": symbol.upper(),
        "saved_at": datetime.now(config.IST).isoformat(timespec="seconds"),
        "items": [asdict(a) for a in keep],
    }, indent=1))


def _in_window(items, start: date, end: date):
    lo, hi = start.isoformat(), end.isoformat() + "T23:59:59"
    return sorted((a for a in items if lo <= a.dt <= hi), key=lambda a: a.dt, reverse=True)


# ---------- public API ----------

def get_announcements(stock: Stock, start: date, end: date, use_nse: bool = True) -> FetchResult:
    """Announcements for `stock` with start <= date <= end, newest first. Never raises."""
    today = datetime.now(config.IST).date()
    ttl = None if end < today else config.OPEN_WINDOW_TTL_SEC
    key = f"{stock.symbol}|{start}|{end}"

    body, fresh = cache.read("announcements", key, ttl)
    if body is not None and fresh:
        return FetchResult([Announcement(**a) for a in body["items"]], "cache", [])

    errors: list[str] = []
    attempts = [("bse", lambda: parse_bse(_bse_raw(stock.bse_code, start, end), stock.symbol))]
    if use_nse:
        attempts.append(("nse", lambda: parse_nse(_nse_raw(stock.symbol, start, end), stock.symbol)))

    for name, fn in attempts:
        try:
            items = _in_window(fn(), start, end)
        except Exception as e:  # network, block, schema drift — all fall through
            errors.append(f"{name}: {type(e).__name__}: {e}")
            log.warning("announcements %s %s failed: %s", stock.symbol, name, e)
            continue
        cache.write("announcements", key, {"source": name, "items": [asdict(a) for a in items]})
        _store(items)
        update_snapshot(stock.symbol, items)
        return FetchResult(items, name, errors)

    if body is not None:  # expired cache beats nothing
        return FetchResult([Announcement(**a) for a in body["items"]], "stale_cache", errors)

    snap = load_snapshot(stock.symbol)
    if snap:
        return FetchResult(_in_window(snap, start, end), "snapshot", errors)
    return FetchResult([], "none", errors)
