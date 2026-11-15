"""Cards + page-1 classification, all deterministic. PDFs are generated here; numbers are test data."""
from datetime import datetime, timedelta

import pytest

from finn import cards, classify, config, db, digest, enrich, mgmt, pdfs, statement
from finn.announcements import Announcement
from finn.settings import Settings


# ---------- generated PDFs ----------

def table_pdf(path, *, order=("2026-09-30", "2026-06-30", "2025-09-30"), title="Consolidated",
              rows=None, row_numbers=False, ocr_slip=False, cover=None):
    """A SEBI-style statement: right-aligned figures under dated column headers, then half-year columns."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=A4)
    if cover:
        y = 800
        for ln in cover:
            c.drawString(40, y, ln)
            y -= 16
        c.showPage()
    c.setFont("Helvetica", 8)
    c.drawString(40, 800, f"Statement of Unaudited {title} Financial Results for the quarter ended 30.09.2026")
    c.drawString(470, 785, "(Rs in Crores)")
    cols = [300, 360, 420, 480, 540]
    dates = [*order, "2026-09-30", "2025-09-30"]
    c.drawString(270, 770, "3 Months ended")
    c.drawString(450, 770, "6 Months ended")
    for x, d in zip(cols, dates):
        y_, m_, d_ = d.split("-")
        c.drawRightString(x, 758, f"{d_}.{m_}.{y_}")
    base = rows or {
        "Revenue from operations": [73188, 72275, 65799, 145463, 129236],
        "Other income": [1337, 1568, 867, 2905, 2527],
        "Total income (1+2)": [74525, 73843, 66666, 148368, 131763],
        "Finance costs": [252, 273, 229, 525, 424],
        "Depreciation and amortisation expense": [1262, 1239, 1413, 2501, 2774],
        "Profit before exceptional items and tax": [18638, 18612, 17203, 37250, 34182],
        "Profit before tax (5+6)": [18638, 17944, 16068, 36582, 33047],
        "Profit for the period (7-8)": [13934, 13420, 12131, 27354, 24950],
    }
    y = 740
    for k, (label, vals) in enumerate(base.items()):
        c.drawString(40, y, label)
        if row_numbers:
            c.drawString(250, y, str(k + 1))
        for j, (x, v) in enumerate(zip(cols, vals)):
            txt = f"{v:,.2f}"
            if ocr_slip and label.startswith("Profit for") and j == 2:
                txt = txt.replace("1", "l", 1)          # scanner read '1' as 'l'
            c.drawRightString(x, y, txt)
        y -= 14
    c.showPage()
    c.save()
    return path


def text_pdf(path, pages):
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


# ---------- tokens and dates ----------

@pytest.mark.parametrize("tok,val,fixed", [
    ("1,45,463", 145463, False), ("(1,234.50)", -1234.5, False), ("73,188", 73188, False),
    ("51B6.55", 5186.55, True), ("16,66", 16.66, True), ("30,", None, False), ("-", None, False)])
def test_to_number(tok, val, fixed):
    c = statement.to_number(tok, decimal_style=True)
    assert (c.value if c else None) == val
    if c and val is not None:
        assert c.ocr_fixed == fixed


@pytest.mark.parametrize("txt,iso", [("30.09.2026", "2026-09-30"), ("30-Sep-26", "2026-09-30"),
                                     ("September 30, 2026", "2026-09-30"), ("June 30, 2026", "2026-06-30"),
                                     ("31st March 2026", "2026-03-31"), ("Unaudited", None)])
def test_parse_date(txt, iso):
    d = statement.parse_date(txt)
    assert (d.isoformat() if d else None) == iso


# ---------- statement parser on generated layouts ----------

def _card(path):
    card, _ = cards.extract_results(path)
    return card


def test_standard_column_order(tmp_path):
    rc = _card(table_pdf(tmp_path / "a.pdf"))
    assert rc.status == "ok" and rc.statement == "consolidated" and rc.unit == "₹ crore"
    assert (rc.metrics["revenue"].current, rc.metrics["revenue"].prev_q, rc.metrics["revenue"].yoy_q) == (73188, 72275, 65799)
    assert rc.metrics["pbt"].current == 18638 and rc.metrics["pbt"].prev_q == 17944   # not the pre-exceptional row
    assert rc.metrics["ebitda"].current == 18638 + 252 + 1262 - 1337
    assert round(rc.metrics["pat"].yoy_pct, 2) == round((13934 - 12131) / 12131 * 100, 2)


def test_itc_style_order_is_read_from_headers(tmp_path):
    """Current, year-ago, previous quarter: values must follow the dates, not positions."""
    rows = {"Revenue from operations": [300, 200, 290, 590, 400], "Other income": [5, 6, 7, 12, 11],
            "Total income": [305, 206, 297, 602, 411], "Profit before tax": [50, 40, 45, 95, 80],
            "Profit for the period": [40, 30, 35, 75, 60]}
    rc = _card(table_pdf(tmp_path / "b.pdf", order=("2026-09-30", "2025-09-30", "2026-06-30"), rows=rows))
    r = rc.metrics["revenue"]
    assert (r.current, r.prev_q, r.yoy_q) == (300, 290, 200)


def test_row_number_column_and_ocr_slip(tmp_path):
    rc = _card(table_pdf(tmp_path / "c.pdf", row_numbers=True, ocr_slip=True))
    assert rc.metrics["revenue"].current == 73188
    assert rc.metrics["pat"].yoy_q == 12131                       # 'l2,131.00' corrected...
    assert rc.status == "partial" and any("scan errors" in c for c in rc.checks)   # ...and flagged


def test_missing_line_is_not_found_not_guessed(tmp_path):
    rows = {"Revenue from operations": [300, 200, 290, 590, 400], "Other income": [5, 6, 7, 12, 11],
            "Profit before tax": [50, 40, 45, 95, 80], "Profit for the period": [40, 30, 35, 75, 60]}
    rc = _card(table_pdf(tmp_path / "d.pdf", rows=rows))
    assert rc.metrics["ebitda"].current is None                   # no D&A / finance costs printed
    assert rc.metrics["revenue"].current == 300 and rc.status == "partial"


def test_consistency_check_flags_misread():
    vals = {"revenue": {"current": 78188, "prev_q": 72275, "yoy_q": 65799},
            "other_income": {"current": 1337, "prev_q": 1568, "yoy_q": 867},
            "total_income": {"current": 74525, "prev_q": 73843, "yoy_q": 66666},
            "pbt": {"current": 18638}, "pat": {"current": 13934}}
    rc = cards.build_results(vals, {})
    assert rc.status == "partial" and any("total income" in c for c in rc.checks)


def test_standalone_used_when_no_consolidated(tmp_path):
    rc = _card(table_pdf(tmp_path / "e.pdf", title="Standalone"))
    assert rc.statement == "standalone" and rc.metrics["revenue"].current == 73188


def test_no_statement(tmp_path):
    rc = _card(text_pdf(tmp_path / "n.pdf", [["The Board declared a dividend of Rs 5 per share."] * 20]))
    assert rc.status == "not_found" and all(m.current is None for m in rc.metrics.values())


# ---------- management parser ----------

TABLE = """Annexure - B
Particulars Details
Name Mr. Sarada Prasana Nayak
Reason For Change Appointed as Head (Production and Production Engineering)
Date of Appointment
/cessation and term of
appointment
1st October 2026
Brief Profile Mr. Sarada Prasana Nayak is responsible for the manufacturing
domain. Mr Nayak is an alumnus of Utkal university. He is a graduate in Mechanical Engineering.
He has over 30 years of experience in planning large scale CAPEX programs.
Disclosure of relationships
between directors
NA"""

LETTER = ("We wish to inform you that Mr. Sanjay D’Souza has superannuated from the services of the Bank with effect from "
          "the close of business hours on October 31, 2026 and has accordingly ceased to be the Group Head – Business "
          "Banking and a Senior Management Personnel of the Bank.")

RESIGN = ("pursuant to the withdrawal of nomination by Suzuki Motor Corporation, Japan, Mr. Kazunari Yamaguchi has "
          "resigned from the post of Whole-time Director designated as Director (Production) w.e.f. close of business "
          "hours of 30th September 2026.")


def test_mgmt_table():
    [p] = mgmt.parse([TABLE])
    assert p.name == "Sarada Prasana Nayak" and p.change == "appointed" and p.effective == "1st October 2026"
    assert p.role == "Head (Production and Production Engineering)"
    flat = " ".join(TABLE.split())
    assert "graduate" in p.background and "responsible for" not in p.background   # informative, not the job blurb
    assert all(sent.strip() in flat for sent in p.background.split(". ") if sent)   # verbatim from the filing


def test_mgmt_letter_superannuation():
    [p] = mgmt.parse([LETTER])
    assert p.name == "Sanjay D’Souza" and p.change == "retired" and p.effective == "October 31, 2026"
    assert p.role.startswith("Group Head – Business Banking")


def test_mgmt_resignation_reason_and_merge():
    people = mgmt.parse([RESIGN, TABLE])
    by = {p.name.split()[-1]: p for p in people}
    assert by["Yamaguchi"].change == "resigned" and "withdrawal of nomination" in by["Yamaguchi"].reason
    assert by["Nayak"].background
    mc = cards.MgmtCard(people)
    assert mc.lead.name == "Kazunari Yamaguchi"        # whole-time director outranks a senior manager


def test_mgmt_boilerplate_not_read_as_change():
    t = TABLE.replace("Reason For Change Appointed as", "Reason for change viz. appointment, reappointment, "
                      "resignation, removal, death or otherwise Appointment as")
    [p] = mgmt.parse([t])
    assert p.change == "appointed"


# ---------- page-1 classification ----------

def test_page1_strongest_decision_wins(tmp_path):
    pdf = text_pdf(tmp_path / "o.pdf", [["Sub: Outcome of Board Meeting held on 30th October, 2026",
                                         "The Board approved the Un-Audited Financial Results for the quarter.",
                                         "The Board recommended a final dividend of Rs 5 per share.",
                                         "Appointment of Mr. X as an Additional Director.",
                                         "The trading window under SEBI (Prohibition of Insider Trading) remains closed."] * 2])
    r = cards.classify_page1(pdf)
    assert r["category"] == "results" and {"dividend", "management_change"} <= set(r["all"])
    assert "insider_trading" not in r["all"]


# ---------- end to end: enrich -> classify -> digest ----------

def test_enrich_end_to_end(tmp_path, tmp_data, monkeypatch):
    res_pdf = table_pdf(tmp_path / "r.pdf")
    out_pdf = text_pdf(tmp_path / "o.pdf", [["Sub: Outcome of Board Meeting",
                                             "The Board approved the appointment of Mr. Ravi Iyer as Chief Financial Officer.",
                                             "Mr. Ravi Iyer has been appointed as Chief Financial Officer with effect from 1st November 2026."] * 3])
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

    st = enrich.run(items)
    assert st["page1"] == 1 and st["results"] == 1 and st["mgmt"] == 1
    assert enrich.run(items)["skipped_cached"] == 3          # never re-reads a filing

    res, stats = classify.classify(items)
    by = {c.ann.id: c for c in res}
    assert by["x2"].category == "management_change" and by["x2"].method == "pdf:page1"
    assert stats["pdf:page1"] == 1

    d = digest.build("24h", Settings())
    lead = {i.a.id: i for i in d.need_you}
    assert lead["x1"].results.metrics["revenue"].current == 73188
    assert lead["x2"].mgmt.lead.name == "Ravi Iyer"

    import app as webapp
    from fastapi.testclient import TestClient
    html = TestClient(webapp.app).get("/").text
    assert "73,188" in html and "page 1 of the filing" in html and "Ravi Iyer" in html
    assert "by reading the filing&#39;s first page" in html or "by reading the filing's first page" in html
