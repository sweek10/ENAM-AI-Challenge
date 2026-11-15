from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from finn import alerts, db, digest
from finn.settings import Settings, load, save


def _seed_prices(sym, closes, vols):
    d0 = datetime(2026, 10, 1)
    with db.connect() as c:
        c.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,?,?)",
                      [(sym, (d0 + timedelta(days=i)).date().isoformat(), p, p, p, p, p, v)
                       for i, (p, v) in enumerate(zip(closes, vols))])


def _seed_ann(rows):
    with db.connect() as c:
        c.executemany("INSERT INTO announcements VALUES (?,?,?,?,?,?,?,?)", rows)


def test_alert_thresholds_configurable(tmp_data):
    _seed_prices("DIXON", [100] * 20 + [94], [1000] * 20 + [3500])   # -6%, 3.5x
    _seed_prices("TCS", [100] * 20 + [101], [1000] * 21)              # +1%, 1x
    a = alerts.check(Settings())
    assert [x.move.symbol for x in a] == ["DIXON"] and set(a[0].kinds) == {"price", "volume"}
    assert round(a[0].move.change_pct, 1) == -6.0 and round(a[0].move.vol_multiple, 1) == 3.5
    # user loosens price, tightens volume -> volume-only alert
    a = alerts.check(Settings(price_move_pct=8, volume_multiple=3))
    assert a[0].kinds == ["volume"]
    assert alerts.check(Settings(price_move_pct=0.5, volume_multiple=5))[-1].move.symbol == "TCS"


def test_settings_roundtrip_and_clamp(tmp_data):
    save(Settings(price_move_pct=99, volume_multiple=0, volume_lookback=2, watch=["results"], mute=["results", "dividend"]))
    s = load()
    assert s.price_move_pct == 20 and s.volume_multiple == 1.1 and s.volume_lookback == 5
    assert s.mute == ["dividend"]   # can't watch and mute the same thing


def test_digest_window_tiers_and_grouping(tmp_data):
    now = datetime(2026, 11, 15, 12, 0)
    _seed_ann([
        ("a1", "TCS", "2026-11-15T09:00:00", "Unaudited Financial Results for the quarter ended September 30, 2026", "Result", "", "", "bse"),
        ("a2", "TCS", "2026-11-15T09:05:00", "Investor Presentation on Q2 FY27", "Company Update", "", "", "bse"),
        ("a3", "TCS", "2026-11-15T10:00:00", "Closure of Trading Window", "Company Update", "", "", "bse"),
        ("a4", "ITC", "2026-11-15T08:00:00", "Notice of Postal Ballot", "AGM/EGM", "", "", "bse"),
        ("a5", "ITC", "2026-11-10T08:00:00", "Resignation of CFO", "Company Update", "", "", "bse"),  # outside 24h
    ])
    d = digest.build("24h", Settings(), now=now)
    assert d.total == 4
    assert [i.a.id for i in d.need_you] == ["a1"]
    assert d.need_you[0].first and d.need_you[0].label == "Results"
    assert [i.a.id for i in d.notable] == ["a2"] and d.notable[0].label == "Results deck"
    assert "ITC" in d.routine and [i.a.id for i in d.hidden] == ["a3"]
    assert digest.build("7d", Settings(), now=now).total == 5


def test_grouping_same_company_same_day(tmp_data):
    now = datetime(2026, 11, 15, 12, 0)
    _seed_ann([
        ("b1", "LT", "2026-11-15T09:00:00", "Unaudited Financial Results for Q2 FY27", "Result", "", "", "bse"),
        ("b2", "LT", "2026-11-15T09:10:00", "Acquisition of 51% stake in XYZ", "Company Update", "", "", "bse"),
    ])
    d = digest.build("24h", Settings(), now=now)
    assert len(d.need_you) == 1 and len(d.need_you[0].related) == 1


def test_pages_render(tmp_data, monkeypatch):
    import app as webapp
    c = TestClient(webapp.app)
    assert c.get("/").status_code == 200
    assert "Nothing needs you" in c.get("/?window=7d").text
    r = c.post("/settings", data={"price_move_pct": "3", "volume_multiple": "2.5", "volume_lookback": "10",
                                  "cat_results": "watch", "cat_dividend": "mute"}, follow_redirects=False)
    assert r.status_code == 303 and load().price_move_pct == 3 and load().mute == ["dividend"]
    assert "Settings saved" in c.get("/settings?saved=1").text
    assert c.get("/api/digest").json()["total"] == 0
