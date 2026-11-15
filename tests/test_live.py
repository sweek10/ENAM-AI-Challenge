"""Live tests: prove each real source returns data for a ticker.

Run:  pytest tests/test_live.py --live -s
Uses an isolated temp data dir, so it doesn't pollute data/.
Override the ticker with FINN_TEST_SYMBOL=TCS.
"""
import os
from datetime import date, timedelta

import pytest

from finn import announcements as ann
from finn import prices
from finn.portfolio import get_stock

pytestmark = pytest.mark.live
STOCK = get_stock(os.environ.get("FINN_TEST_SYMBOL", "RELIANCE"))
END = date.today() - timedelta(days=1)       # closed window
START = END - timedelta(days=60)              # wide enough to always contain filings


def _show(items, n=5):
    for a in items[:n]:
        print(f"   {a.dt}  [{a.category or '-'}]  {a.headline[:80]}")


def test_yahoo_eod_prices(tmp_data):
    r = prices.update_prices(STOCK.symbol, lookback_days=60)
    rows = prices.get_prices(STOCK.symbol)
    print(f"\n[yahoo] {STOCK.yahoo}: {r}  rows={len(rows)}  last={rows[-1] if rows else None}")
    assert len(rows) >= 20
    assert rows[-1]["close"] > 0 and rows[-1]["volume"] > 0
    # cached: second call must not hit the network
    prices.FETCHER, orig = (lambda *a: pytest.fail("refetched!")), prices.FETCHER
    try:
        assert prices.update_prices(STOCK.symbol)["status"] == "cached"
    finally:
        prices.FETCHER = orig


def test_bse_announcements(tmp_data):
    items = ann.parse_bse(ann._bse_raw(STOCK.bse_code, START, END), STOCK.symbol)
    print(f"\n[bse] {STOCK.symbol} ({STOCK.bse_code}) {START}..{END}: {len(items)} filings")
    _show(items)
    assert items, "BSE returned no filings"
    assert all(a.dt[:4].isdigit() for a in items)


def test_nse_announcements(tmp_data):
    try:
        raw = ann._nse_raw(STOCK.symbol, START, END)
    except Exception as e:
        pytest.xfail(f"NSE blocked this client (expected sometimes): {e}")
    items = ann.parse_nse(raw, STOCK.symbol)
    print(f"\n[nse] {STOCK.symbol}: {len(items)} filings")
    _show(items)
    assert items


def test_fallback_chain_end_to_end(tmp_data, monkeypatch):
    """Live fetch populates snapshot; then with both sources dead, snapshot still serves."""
    r = ann.get_announcements(STOCK, START, END)
    print(f"\n[chain] live source={r.source} n={len(r.items)} errors={r.errors}")
    assert r.source in ("bse", "nse") and r.items

    assert ann.get_announcements(STOCK, START, END).source == "cache"

    # wipe the disk cache, kill both sources -> must fall back to snapshot
    import shutil
    from finn import config
    shutil.rmtree(config.cache_dir())

    def dead(*a):
        raise ann.SourceError("simulated outage")

    monkeypatch.setattr(ann, "_bse_raw", dead)
    monkeypatch.setattr(ann, "_nse_raw", dead)
    r3 = ann.get_announcements(STOCK, START, END)
    print(f"[chain] after outage source={r3.source} n={len(r3.items)}")
    assert r3.source == "snapshot" and len(r3.items) == len(r.items)
