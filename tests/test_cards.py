"""Cards + page-1 classification. PDFs are generated here; numbers are test data."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from finn import cards, classify, config, db, digest, enrich, pdfs
from finn.announcements import Announcement
from finn.settings import Settings


# ---------- fixtures: generated PDFs ----------

def text_pdf(path, pages: list[list[str]]):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=A4)
    for lines in pages:
        y = 800
        for ln in lines:
            c.drawString(40, y, ln)
            y -= 16
        c.showPage()
    c.save()
    return path


def scanned_pdf(path, pages: list[list[str]]):
    from PIL import Image, ImageDraw
    imgs = []
    for lines in pages:
        im = Image.new("RGB", (1240, 1754), "white")
        d = ImageDraw.Draw(im)
        for k, ln in enumerate(lines):
            d.text((60, 80 + 28 * k), ln, fill="black")
        imgs.append(im)
    imgs[0].save(str(path), save_all=True, append_images=imgs[1:])
    return path


COVER = ["Sub: Outcome of Board Meeting", "The Board approved the audited financial results.", "Trading window",
         "under SEBI (Prohibition of Insider Trading) Regulations remains closed."]
AUDITOR = ["Independent Auditor's Report", "Opinion", "We have audited the consolidated financial results."]
STATEMENT = ["Audited Consolidated Statement of Financial Results", "Three months ended",
             "Revenue from operations 73,188 72,275 65,799", "Other income 1,337 1,568 867",
             "Total income 74,525 73,843 66,666", "Finance costs 230 199 170",
             "Depreciation and amortisation expense 1,450 1,402 1,300", "Profit before tax 17,300 16,820 15,420",
             "Profit for the period 12,900 12,600 11,500"]

MODEL_JSON = {
    "found_statement": True, "statement": "consolidated", "unit": "₹ crore", "page": 3,
    "periods": {"current": "Sep 30, 2026", "prev_q": "Jun 30, 2026", "yoy_q": "Sep 30, 2025"},
    "rows": {
        "revenue": {"label": "Revenue from operations", "current": "73,188", "prev_q": "72,275", "yoy_q": "65,799"},
        "other_income": {"label": "Other income", "current": "1,337", "prev_q": "1,568", "yoy_q": "867"},
        "total_income": {"label": "Total income", "current": "74,525", "prev_q": "73,843", "yoy_q": "66,666"},
        "finance_costs": {"label": "Finance costs", "current": "230", "prev_q": "199", "yoy_q": "170"},
        "depreciation": {"label": "Depreciation", "current": "1,450", "prev_q": "1,402", "yoy_q": "1,300"},
        "pbt": {"label": "Profit before tax", "current": "17,300", "prev_q": "16,820", "yoy_q": "15,420"},
        "pat": {"label": "Profit for the period", "current": "12,900", "prev_q": "12,600", "yoy_q": "11,500"},
    }}


class Fake:
    """Records what was sent; replies with canned JSON."""
    def __init__(self, reply):
        self.reply, self.calls = reply, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        body = self.reply(kw) if callable(self.reply) else self.reply
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(body))],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


def kinds(call):
    return [b["type"] for b in call["messages"][0]["content"]]


def pages_sent(call):
    return [b["text"] for b in call["messages"][0]["content"] if b["type"] == "text" and b["text"].startswith("Page")]


# ---------- pure logic ----------

@pytest.mark.parametrize("s,v", [("1,45,463", 145463), ("(1,234.5)", -1234.5), ("-", None), (None, None),
                                 ("₹ 12.3", 12.3), ("− 50", -50), ("abc", None), (7, 7.0)])
def test_parse_num(s, v):
    assert cards.parse_num(s) == v


def test_build_results_math_is_ours_not_the_models():
    rc = cards.build_results(MODEL_JSON)
    assert rc.status == "ok" and not rc.checks
    assert rc.metrics["revenue"].current == 73188
    assert round(rc.metrics["revenue"].qoq_pct, 2) == round((73188 - 72275) / 72275 * 100, 2)
    assert round(rc.metrics["pat"].yoy_pct, 2) == round((12900 - 11500) / 11500 * 100, 2)
    e = rc.metrics["ebitda"]
    assert e.derived and e.current == 17300 + 230 + 1450 - 1337


def test_missing_line_is_not_found_not_guessed():
    d = json.loads(json.dumps(MODEL_JSON))
    d["rows"]["depreciation"] = {"label": None, "current": None, "prev_q": None, "yoy_q": None}
    d["rows"]["pat"]["yoy_q"] = None
    rc = cards.build_results(d)
    assert rc.metrics["ebitda"].current is None            # can't derive without D&A
    assert rc.metrics["pat"].yoy_pct is None and rc.metrics["pat"].current == 12900
    assert rc.status == "partial"


def test_consistency_check_flags_misread():
    d = json.loads(json.dumps(MODEL_JSON))
    d["rows"]["revenue"]["current"] = "78,188"               # a misread digit
    rc = cards.build_results(d)
    assert rc.status == "partial" and any("total income" in c for c in rc.checks)


def test_no_statement():
    rc = cards.build_results({"found_statement": False})
    assert rc.status == "not_found" and all(m.current is None for m in rc.metrics.values())


def test_mgmt_lead_prefers_top_role_appointment():
    mc = cards.build_mgmt({"changes": [
        {"name": "A. Rao", "role": "Independent Director", "change": "appointed", "page": 2},
        {"name": "R. Iyer", "role": "Chief Financial Officer", "change": "appointed", "background": "CA, 20 yrs.", "page": 2},
        {"name": "S. Old", "role": "Chief Financial Officer", "change": "resigned", "page": 1},
        {"name": None}]})
    assert mc.lead.name == "R. Iyer" and len(mc.people) == 3


# ---------- PDF paths ----------

def test_results_text_pdf_picks_statement_page(tmp_path, tmp_data):
    pdf = text_pdf(tmp_path / "r.pdf", [COVER, AUDITOR, STATEMENT, ["Notes", "1. Segment information"]])
    fake = Fake(MODEL_JSON)
    rc, meta = cards.extract_results(pdf, fake)
    assert meta["how"].startswith("text layer") and 3 in meta["pages"] and 1 not in meta["pages"]
    assert "image" in kinds(fake.calls[0])                  # still sends the page image: tables need layout
    assert rc.metrics["revenue"].current == 73188 and rc.model == "claude-opus-5-5"
    with db.connect() as c:
        assert c.execute("SELECT model, purpose FROM llm_usage").fetchone()["purpose"] == "results_card"


def test_results_scanned_pdf_sends_first_pages(tmp_path, tmp_data):
    pdf = scanned_pdf(tmp_path / "s.pdf", [COVER, AUDITOR, STATEMENT])
    assert pdfs.is_scanned(pdfs.page_texts(pdf))
    fake = Fake(MODEL_JSON)
    rc, meta = cards.extract_results(pdf, fake)
    assert meta["how"].startswith("scanned") and pages_sent(fake.calls[0]) == ["Page 1:", "Page 2:", "Page 3:"]


def test_text_pdf_without_statement_skips_llm(tmp_path, tmp_data):
    pdf = text_pdf(tmp_path / "n.pdf", [COVER + ["The Board declared a dividend."] * 8])
    fake = Fake(MODEL_JSON)
    rc, _ = cards.extract_results(pdf, fake)
    assert rc.status == "not_found" and fake.calls == []


def test_mgmt_text_pdf_uses_text_not_images(tmp_path, tmp_data):
    pdf = text_pdf(tmp_path / "m.pdf", [["Appointment of Ms R Iyer as Chief Financial Officer w.e.f. 1 Nov 2026.",
                                         "Brief profile: Chartered accountant with 20 years in IT services."] * 4])
    fake = Fake({"changes": [{"name": "R Iyer", "role": "Chief Financial Officer", "change": "appointed",
                              "effective": "1 Nov 2026", "background": "Chartered accountant, 20 years in IT services.",
                              "prior_roles": [], "page": 1}]})
    mc, meta = cards.extract_mgmt(pdf, fake)
    assert meta["how"] == "text layer" and "image" not in kinds(fake.calls[0])
    assert mc.lead.background.startswith("Chartered")


def test_page1_text_rules_free_and_strongest_wins(tmp_path, tmp_data):
    pdf = text_pdf(tmp_path / "o.pdf", [COVER + ["The Board recommended a final dividend of Rs 5 per share.",
                                                 "Further, the Board approved the appointment of Mr X as CFO."] * 2])
    fake = Fake({})
    r = cards.classify_page1(pdf, fake)
    assert r["category"] == "results" and r["method"] == "pdf_text_rules" and fake.calls == []
    assert "dividend" in r["all"] and "insider_trading" not in r["all"]


def test_page1_scan_uses_cheap_model(tmp_path, tmp_data):
    pdf = scanned_pdf(tmp_path / "o2.pdf", [COVER])
    fake = Fake({"categories": ["management_change", "dividend"], "summary": "CFO appointed; dividend declared"})
    r = cards.classify_page1(pdf, fake)
    assert r["category"] == "management_change" and fake.calls[0]["model"] == "claude-haiku-5-5"


# ---------- end to end: enrich -> classify -> digest ----------

def test_enrich_end_to_end(tmp_path, tmp_data, monkeypatch):
    res_pdf = text_pdf(tmp_path / "r.pdf", [COVER, AUDITOR, STATEMENT])
    out_pdf = scanned_pdf(tmp_path / "o.pdf", [["Outcome of Board Meeting", "Appointment of CFO"]])
    files = {"u:res": res_pdf, "u:out": out_pdf}
    monkeypatch.setattr(pdfs, "fetch", lambda url: files[url])
    t0 = (datetime.now(config.IST).replace(tzinfo=None) - timedelta(hours=3)).isoformat(timespec="seconds")
    items = [
        Announcement("x1", "TCS", t0, "Financial Results for the quarter ended September 30, 2026",
                     "Result", "Financial Results", "u:res", "bse"),
        Announcement("x2", "DIXON", t0, "Outcome of Board Meeting held on 15th November, 2026",
                     "Board Meeting", "Outcome of Board Meeting", "u:out", "bse"),
    ]
    with db.connect() as c:
        c.executemany("INSERT INTO announcements VALUES (:id,:symbol,:dt,:headline,:category,:subcategory,:pdf_url,:source)",
                      [a.__dict__ for a in items])

    def reply(kw):
        if "first page" in kw["system"]:
            return {"categories": ["management_change"], "summary": "Appointed new CFO"}
        if "results statements" in kw["system"]:
            return MODEL_JSON
        return {"changes": [{"name": "R Iyer", "role": "Chief Financial Officer", "change": "appointed", "page": 1}]}

    fake = Fake(reply)
    st = enrich.run(items, fake)
    assert st["page1"] == 1 and st["results"] == 1 and st["mgmt"] == 1
    n = len(fake.calls)
    assert enrich.run(items, fake)["skipped_cached"] == 2 and len(fake.calls) == n   # never re-reads

    res, stats = classify.classify(items, client=None)
    by = {c.ann.id: c for c in res}
    assert by["x2"].category == "management_change" and by["x2"].method == "pdf:page1"

    d = digest.build("24h", Settings(), llm_client=None)
    lead = {i.a.id: i for i in d.need_you}
    assert lead["x1"].results.metrics["revenue"].current == 73188
    assert lead["x2"].mgmt.lead.name == "R Iyer" and lead["x2"].p1_summary == "Appointed new CFO"

    import app as webapp
    from fastapi.testclient import TestClient
    html = TestClient(webapp.app).get("/").text
    assert "73,188" in html and "page 3 of the filing" in html and "R Iyer" in html
    assert "Read by Opus 5.5" in html and "first page" in html
