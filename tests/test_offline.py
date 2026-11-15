"""Offline tests: parsing, caching and the fallback chain. No network.

Fixture payloads mirror the shape of real BSE/NSE responses; values are test data.
"""
from datetime import date, timedelta

import pytest

from finn import announcements as ann
from finn import prices
from finn.portfolio import get_stock

REL = get_stock("RELIANCE")
D0, D1 = date(2026, 11, 1), date(2026, 11, 14)  # closed window -> cached forever

BSE_ROWS = [{
    "NEWSID": "abc-123", "SCRIP_CD": 500325, "NEWSSUB": "Reliance Industries Ltd - 500325 - Outcome",
    "HEADLINE": "Outcome of Board Meeting - unaudited results", "NEWS_DT": "2026-11-10T18:30:02.42",
    "CATEGORYNAME": "Result", "SUBCATNAME": "Financial Results", "ATTACHMENTNAME": "x1.pdf", "PDFFLAG": 0,
}, {
    "NEWSID": "abc-124", "HEADLINE": "Allotment of ESOP", "NEWS_DT": "2026-11-12T10:00:00",
    "CATEGORYNAME": "Company Update", "SUBCATNAME": "Allotment of ESOP / ESPS",
    "ATTACHMENTNAME": "x2.pdf", "PDFFLAG": 1,
}]
NSE_ROWS = [{
    "symbol": "RELIANCE", "desc": "Outcome of Board Meeting", "attchmntText": "Board approved results",
    "attchmntFile": "https://nsearchives.nseindia.com/corporate/x.pdf", "sort_date": "2026-11-10 18:31:00",
    "seq_id": "999",
}]


def boom(*a, **k):
    raise ann.SourceError("blocked")


def test_parse_bse():
    items = ann.parse_bse(BSE_ROWS, "RELIANCE")
    assert items[0].id == "bse:abc-123"
    assert items[0].dt == "2026-11-10T18:30:02"
    assert items[0].category == "Result"
    assert items[0].pdf_url.endswith("/AttachLive/x1.pdf")
    assert items[1].pdf_url.endswith("/AttachHis/x2.pdf")


def test_parse_nse():
    items = ann.parse_nse(NSE_ROWS, "RELIANCE")
    assert items[0].id == "nse:999" and items[0].dt == "2026-11-10T18:31:00"


def test_bse_first_then_cache(tmp_data, monkeypatch):
    calls = []
    monkeypatch.setattr(ann, "_bse_raw", lambda *a: calls.append(1) or BSE_ROWS)
    monkeypatch.setattr(ann, "_nse_raw", boom)
    r = ann.get_announcements(REL, D0, D1)
    assert r.source == "bse" and len(r.items) == 2
    assert r.items[0].dt > r.items[1].dt  # newest first
    # second call: served from disk, no network
    r2 = ann.get_announcements(REL, D0, D1)
    assert r2.source == "cache" and len(r2.items) == 2 and len(calls) == 1


def test_nse_when_bse_fails(tmp_data, monkeypatch):
    monkeypatch.setattr(ann, "_bse_raw", boom)
    monkeypatch.setattr(ann, "_nse_raw", lambda *a: NSE_ROWS)
    r = ann.get_announcements(REL, D0, D1)
    assert r.source == "nse" and len(r.items) == 1 and "bse" in r.errors[0]


def test_snapshot_when_both_fail(tmp_data, monkeypatch):
    # a previous successful run wrote the snapshot
    ann.update_snapshot("RELIANCE", ann.parse_bse(BSE_ROWS, "RELIANCE"))
    monkeypatch.setattr(ann, "_bse_raw", boom)
    monkeypatch.setattr(ann, "_nse_raw", boom)
    r = ann.get_announcements(REL, D0, D1)
    assert r.source == "snapshot" and r.stale and len(r.items) == 2 and len(r.errors) == 2


def test_never_raises_with_nothing(tmp_data, monkeypatch):
    monkeypatch.setattr(ann, "_bse_raw", boom)
    monkeypatch.setattr(ann, "_nse_raw", boom)
    r = ann.get_announcements(REL, D0, D1)
    assert r.source == "none" and r.items == []


def test_window_filter_and_sqlite(tmp_data, monkeypatch):
    from finn import db
    monkeypatch.setattr(ann, "_bse_raw", lambda *a: BSE_ROWS)
    r = ann.get_announcements(REL, date(2026, 11, 11), D1)
    assert [a.id for a in r.items] == ["bse:abc-124"]
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM announcements").fetchone()[0] == 1


def _fake_bars(start, end):
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append({"date": d.isoformat(), "open": 1, "high": 2, "low": 0.5,
                        "close": 1.5, "adj_close": 1.5, "volume": 1000})
        d += timedelta(days=1)
    return out


def test_prices_incremental_no_refetch(tmp_data, monkeypatch):
    calls = []

    def fake(sym, start, end):
        calls.append((sym, start, end))
        return _fake_bars(start, end)

    monkeypatch.setattr(prices, "FETCHER", fake)
    r1 = prices.update_prices("TCS", lookback_days=30)
    assert r1["status"] == "fetched" and r1["new_rows"] > 15
    n = len(prices.get_prices("TCS"))

    # within refetch window -> no call at all
    assert prices.update_prices("TCS")["status"] == "cached" and len(calls) == 1

    # forced -> only asks for dates after the last stored bar
    prices.update_prices("TCS", force=True)
    if len(calls) == 2:
        last_stored = prices.get_prices("TCS")[n - 1]["date"]
        assert calls[1][1].isoformat() > last_stored
    assert len(prices.get_prices("TCS")) >= n


def test_prices_bo_fallback(tmp_data, monkeypatch):
    monkeypatch.setattr(prices, "FETCHER",
                        lambda sym, s, e: [] if sym.endswith(".NS") else _fake_bars(s, e))
    assert prices.update_prices("DIXON", lookback_days=10)["new_rows"] > 0
