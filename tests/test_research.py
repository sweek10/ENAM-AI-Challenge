"""Pull side: screener parsing, guidance extraction, verdicts, page. Network is stubbed."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finn import cache, guidance as G, pdfs, research as R, screener
from finn.guidance import Guidance

FIX = Path(__file__).parent / "fixtures"
SAMPLE = (FIX / "transcript_sample.txt").read_text()


def kpit() -> screener.Company:
    return screener.parse_html("KPITTECH", (FIX / "screener_kpit.html").read_text(), "https://www.screener.in/company/KPITTECH/consolidated/")


def text_pdf(path, text):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=A4)
    y = 800
    import textwrap
    for ln in text.splitlines():
        for chunk in textwrap.wrap(ln, 95) or [""]:
            c.drawString(30, y, chunk)
            y -= 14
            if y < 40:
                c.showPage()
                y = 800
    c.save()
    return path


# ---------- screener ----------

def test_screener_parse_real_markup():
    c = kpit()
    assert c.name == "KPIT Technologies Ltd" and c.quarters[-1] == "Sep 2026" and len(c.quarters) == 13
    assert c.rows["Sales"][-1] == 1691 and c.rows["OPM %"][-1] == 16
    labels = [x.label for x in c.concalls]
    assert labels[:5] == ["Nov 2026", "Aug 2026", "May 2026", "Feb 2026", "Nov 2025"]
    assert c.concalls[0].transcript == "https://www.bseindia.com/xml-data/corpfiling/AttachHis/a7331811-fe14-4c79-896d-f0a9df901335.pdf"
    assert "Jul 2022" not in labels                    # no transcript link -> skipped


@pytest.mark.parametrize("bad", ["", "   ", "TCS; DROP", "../etc", "A" * 30, "kpit tech!"])
def test_bad_tickers_rejected(bad):
    with pytest.raises(screener.NotFound):
        screener.normalise_ticker(bad)


def test_ticker_normalised():
    assert screener.normalise_ticker(" kpittech.ns ") == "KPITTECH" and screener.normalise_ticker("M&M") == "M&M"


# ---------- guidance ----------

def test_extract_management_only_with_targets():
    gs = G.extract(SAMPLE, "Aug 2026", "u")
    by = {(g.metric, g.basis): g for g in gs if g.period == "FY27"}
    rev = by[("revenue_growth", "inr")]
    assert (rev.low, rev.high, rev.period) == (18, 20, "FY27") and rev.speaker == "Ravi Kumar"
    assert by[("margin", "inr")].low == 20 and by[("margin", "inr")].high == 21
    assert by[("revenue_growth", "cc")].low == 15
    assert any(g.period == "FY28" and g.low == 10 and g.high is None for g in gs)   # "double-digit next year"
    assert not any("Rahul" in g.speaker for g in gs)          # analyst's question isn't guidance
    assert not any("This quarter we grew" in g.quote for g in gs)


@pytest.mark.parametrize("call,fy", [("May 2026", 2027), ("Aug 2026", 2027), ("Nov 2026", 2027), ("Feb 2026", 2026)])
def test_fiscal_year_of_call(call, fy):
    assert G.fiscal_year(call) == fy


@pytest.mark.parametrize("s,period", [("for FY27 we expect", "FY27"), ("next year we see", "FY28"),
                                      ("for the full year", "FY27"), ("in Q3 FY27", "Q3FY27"),
                                      ("in the second half", "H2FY27"), ("over time", None)])
def test_period(s, period):
    assert G.period_of(s, 2027) == period


@pytest.mark.parametrize("s,lo,hi", [("growth of 18% to 20%", 18, 20), ("mid-teens growth", 14, 16),
                                     ("double-digit growth", 10, None), ("margin of about 21%", 21, 21),
                                     ("between 12 and 14 percent", 12, 14)])
def test_targets(s, lo, hi):
    a, b, unit = G.target_of(s)
    assert (a, b, unit) == (lo, hi, "%")


# ---------- verdicts against KPIT's real reported quarters ----------

def g(metric, lo, hi, period, basis="inr", unit="%"):
    return Guidance(metric, lo, hi, unit, period, basis, "q", "s", "Aug 2026", "u")


def test_growth_behind_part_way_through_year():
    act = R.Actuals(kpit())
    v = R.judge(g("revenue_growth", 18, 20, "FY27"), act)
    # FY27 so far = Jun 2026 + Sep 2026 = 3,366 vs 3,127 a year earlier = +7.6%
    assert v["verdict"] == "behind" and v["quarters"] == 2 and v["of"] == 4
    assert round(v["actual"], 1) == round((1675 + 1691) / (1539 + 1588) * 100 - 100, 1)


def test_margin_missed_for_completed_year():
    act = R.Actuals(kpit())
    v = R.judge(g("margin", 20, 21, "FY26"), act)
    # FY26 = Jun 25..Mar 26: OP 295+298+311+317 = 1,221 on sales 1,539+1,588+1,617+1,711 = 6,455 -> 18.9%
    assert v["verdict"] == "missed" and round(v["actual"], 1) == round(1221 / 6455 * 100, 1)


def test_delivered_and_too_early_and_unclear():
    act = R.Actuals(kpit())
    assert R.judge(g("revenue_growth", 10, None, "FY26"), act)["verdict"] == "delivered"   # FY26 grew ~11%
    assert R.judge(g("revenue_growth", 15, 17, "FY28"), act)["verdict"] == "too early"
    assert R.judge(g("revenue_growth", 15, 17, "FY27", basis="cc"), act)["verdict"] == "unclear"
    assert R.judge(g("revenue_growth", 15, 17, None), act)["verdict"] == "unclear"
    assert R.judge(g("capex", 400, 400, "FY27", unit="₹ crore"), act)["verdict"] == "unclear"


# ---------- assembly and page ----------

@pytest.fixture
def stubbed(tmp_data, tmp_path, monkeypatch):
    pdf = text_pdf(tmp_path / "t.pdf", SAMPLE * 3)
    monkeypatch.setattr(screener, "fetch", lambda t, ttl=0: kpit() if t == "KPITTECH" else (_ for _ in ()).throw(
        screener.NotFound(f"No company called {t} on screener.in.")))
    calls = []
    monkeypatch.setattr(pdfs, "fetch", lambda url: calls.append(url) or pdf)
    return calls


def test_build_caches_transcripts(stubbed):
    r = R.build("kpittech")
    assert r["ticker"] == "KPITTECH" and len(r["calls"]) == 4 and len(stubbed) == 4
    rev = next(x for x in r["groups"] if x["metric"] == "revenue_growth" and x["basis"] == "inr" and x["period"] == "FY27")
    assert rev["verdict"] == "behind" and len(rev["trail"]) == 4 and rev["trail"][-1]["change"] == "kept"
    assert r["headline"].startswith("Promised 18–20% revenue growth for FY27.")
    R.build("KPITTECH")
    assert len(stubbed) == 4                                  # second build: transcripts from cache


def test_page_renders_and_bad_ticker_404(stubbed):
    import app as webapp
    c = TestClient(webapp.app)
    html = c.get("/research/KPITTECH").text
    assert "Promised vs delivered" in html and "Behind" in html and "transcript" in html
    r = c.get("/research/NOSUCHCO")
    assert r.status_code == 404 and "No company called NOSUCHCO" in r.text
    assert c.get("/research?q=bad%20ticker!").status_code == 400
    assert c.get("/research?q=kpittech", follow_redirects=False).headers["location"] == "/research/KPITTECH"
    assert "Which company?" in c.get("/research").text


def test_offline_falls_back_to_snapshot(stubbed, monkeypatch):
    R.build("KPITTECH")                                       # writes data/research/KPITTECH.json
    monkeypatch.setattr(screener, "fetch", lambda t, ttl=0: (_ for _ in ()).throw(RuntimeError("timeout")))
    r = R.build("KPITTECH")
    assert r["stale"] and "unreachable" in r["stale"] and r["groups"]


def test_offline_without_snapshot_is_503(tmp_data, monkeypatch):
    import app as webapp
    monkeypatch.setattr(screener, "fetch", lambda t, ttl=0: (_ for _ in ()).throw(RuntimeError("timeout")))
    r = TestClient(webapp.app).get("/research/ZOMATO")
    assert r.status_code == 503 and "Try again" in r.text


def test_company_without_transcripts(tmp_data, monkeypatch):
    c = kpit()
    c.concalls = []
    monkeypatch.setattr(screener, "fetch", lambda t, ttl=0: c)
    r = R.build("KPITTECH")
    assert r["no_transcripts"] and r["groups"] == [] and r["quarters"]
    import app as webapp
    assert "No earnings-call transcripts on file" in TestClient(webapp.app).get("/research/KPITTECH").text


def test_failed_transcripts_keep_complete_saved_copy(stubbed, monkeypatch):
    good = R.build("KPITTECH")
    assert good["groups"] and not good["problems"]
    cache.write("guidance", "unused", {})                         # leave cache dir in place
    import shutil
    from finn import config
    shutil.rmtree(config.cache_dir() / "guidance")                 # forget extracted guidance
    monkeypatch.setattr(pdfs, "fetch", lambda url: (_ for _ in ()).throw(RuntimeError("403 from BSE")))
    r = R.build("KPITTECH")
    assert r["stale"] and "saved" in r["stale"] and len(r["groups"]) == len(good["groups"])


def test_viewer_links_resolve_to_pdf():
    u = ("https://www.marutisuzuki.com/pdf-viewer?pdf=%2Fcontent%2Fdam%2Farena-eds%2Fcorporate%2Fpdf%2F"
         "company-reports%2F2026-2027%2FTranscript_earnings_call_Maruti_Suzuki_Q2_FY_27.pdf")
    assert screener.direct_pdf(u) == ("https://www.marutisuzuki.com/content/dam/arena-eds/corporate/pdf/"
                                     "company-reports/2026-2027/Transcript_earnings_call_Maruti_Suzuki_Q2_FY_27.pdf")


@pytest.mark.parametrize("sentence", [
    "Consensus was a system credit growth of around 10.5% to 11.5% for FY26.",
    "We expect AUM growth of 22% to 23% for FY27.",
    "We remain confident of our order inflow growth guidance of 10% to 12% for the current year.",
    "The EBITDA margin in the Projects & Manufacturing portfolio for Q3 FY26 is at 8.1%.",
    "We expect the domestic segment margin to be 18% next year.",
    "We are taking a target of 12% to 15% revenue CAGR over the plan period.",
    "On the revenue front, growth for FY26 stood at 12% compared to our guidance of 15%."])
def test_not_company_level_never_judged(sentence):
    """Real false positives from HDFC Bank, Bajaj Finance and L&T calls: never compared with company totals."""
    text = f"MANAGEMENT: MR. A B\nModerator: Welcome.\nA B: {sentence} " + "We will keep investing. " * 30
    assert all(g.metric == "other" for g in G.extract(text, "Aug 2026", "u"))


def test_margin_far_from_reported_is_unclear():
    act = R.Actuals(kpit())
    assert R.judge(g("margin", 8.5, 8.5, "FY26"), act)["verdict"] == "unclear"     # reported ~18.9%


def test_beyond_fy_is_not_that_year():
    assert G.period_of("Beyond FY25, we will be improving margins to 12%", 2026) is None
