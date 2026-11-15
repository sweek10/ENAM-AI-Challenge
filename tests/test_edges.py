"""Bad input, empty data and odd companies: nothing should crash, every page should say what happened."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from finn import config, db, prices, research as R, screener
from finn.guidance import Guidance


@pytest.fixture
def client(tmp_data, monkeypatch):
    import app as webapp
    monkeypatch.setattr(screener, "fetch", lambda t, ttl=0: (_ for _ in ()).throw(
        screener.NotFound(f"No company called {t} on screener.in.")))
    return TestClient(webapp.app, raise_server_exceptions=False)


ROUTES = ["/", "/?window=bogus", "/?window=30d", "/settings", "/api/digest", "/research", "/research?q=",
          "/research/NOSUCH", "/research/%20", "/research/..%2F..", "/research/" + "A" * 40, "/healthz"]


@pytest.mark.parametrize("path", ROUTES)
def test_no_route_crashes_on_empty_data(client, path):
    assert client.get(path).status_code < 500


def test_bad_ticker_says_so(client):
    r = client.get("/research/NOSUCH")
    assert r.status_code == 404 and "No company called NOSUCH" in r.text and 'action="/research"' in r.text
    r = client.get("/research?q=%3Cscript%3E")
    assert r.status_code == 400 and "<script>" not in r.text      # escaped, not executed


def test_empty_digest_explains_itself(client):
    html = client.get("/").text
    assert "Nothing needs you" in html and "No filings across your 15 stocks" in html


def test_digest_opens_on_shortest_window_with_filings(client):
    t = (datetime.now(config.IST).replace(tzinfo=None) - timedelta(days=2)).isoformat(timespec="seconds")
    with db.connect() as c:
        c.execute("INSERT INTO announcements VALUES (?,?,?,?,?,?,?,?)",
                  ("e1", "TCS", t, "Unaudited Financial Results for the quarter", "Result", "", "", "bse"))
    html = client.get("/").text
    assert "Nothing filed in the last 24 hours, so this shows the last 3 days" in html
    assert 'href="/?window=3d" aria-current' in html
    assert "Nothing filed" not in client.get("/?window=24h").text          # explicit choice is respected


@pytest.mark.parametrize("form,kept", [
    ({"price_move_pct": "abc", "volume_multiple": "3", "volume_lookback": "10"}, "day move"),
    ({"price_move_pct": "", "volume_multiple": "", "volume_lookback": ""}, "day move"),
    ({}, "day move")])
def test_bad_settings_keep_previous_value(client, form, kept):
    r = client.post("/settings", data=form, follow_redirects=False)
    assert r.status_code == 303 and kept.replace(" ", "+") in r.headers["location"].replace("%20", "+")
    page = client.get(r.headers["location"]).text
    assert "Kept the previous" in page and 'value="4"' in page        # default 4% kept


def test_prices_for_unknown_ticker_no_crash(tmp_data, monkeypatch):
    monkeypatch.setattr(prices, "FETCHER", lambda s, a, b: [])
    assert prices.update_prices("NOSUCH")["new_rows"] == 0 and prices.get_prices("NOSUCH") == []


def test_bank_format_margin_is_unclear():
    bank = screener.Company("HDFCBANK", "HDFC Bank", "u", ["Jun 2026", "Sep 2026", "Jun 2025", "Sep 2025"],
                            {"Revenue": [100, 110, 90, 95], "Financing Profit": [30, 31, 25, 26], "Net Profit": [20, 21, 18, 19]})
    act = R.Actuals(bank)
    g = Guidance("margin", 4, 4.2, "%", "FY27", "inr", "NIM of 4%", "x", "Aug 2026", "u")
    assert R.judge(g, act)["verdict"] == "unclear"
    g2 = Guidance("revenue_growth", 15, 15, "%", "FY27", "inr", "15% growth", "x", "Aug 2026", "u")
    assert R.judge(g2, act)["verdict"] in ("on track", "behind")


def test_ebit_guidance_not_compared_to_operating_margin():
    from tests.test_research import kpit
    g = Guidance("margin", 16, 17, "%", "FY27", "ebit", "EBIT margin 16-17%", "x", "Aug 2026", "u")
    v = R.judge(g, R.Actuals(kpit()))
    assert v["verdict"] == "unclear"


def test_screener_falls_back_to_standalone(tmp_data, monkeypatch):
    from pathlib import Path
    real = (Path(__file__).parent / "fixtures" / "screener_kpit.html").read_text()
    empty = "<html><h1>KPIT</h1><section id='quarters'><table><thead><tr><th></th></tr></thead></table></section></html>"
    seen = []

    def get(url, headers=None, timeout=None):
        seen.append(url)
        return SimpleNamespace(status_code=200, text=empty if "consolidated" in url else real)

    monkeypatch.setattr(screener.requests, "get", get)
    c = screener.fetch("KPITTECH")
    assert c.quarters and seen[-1].endswith("/company/KPITTECH/")


def test_screener_404_is_not_found(tmp_data, monkeypatch):
    monkeypatch.setattr(screener.requests, "get", lambda *a, **k: SimpleNamespace(status_code=404, text=""))
    with pytest.raises(screener.NotFound):
        screener.fetch("NOSUCH")
