"""Regression test against real BSE filings, values checked by eye against each PDF page.

Runs only when the PDFs are in the local cache (after `python -m scripts.verify_cards`);
the filings themselves are not committed.
"""
import pytest

from finn import cards, pdfs
from scripts.verify_cards import FILINGS

URL = {name: url for name, _, _, url in FILINGS}

# (current, previous quarter, year-ago quarter), ₹ crore, consolidated, as printed in the filing
EXPECTED_RESULTS = {
    "tcs_results": {"page": 8, "revenue": (73188, 72275, 65799), "pbt": (18638, 17944, 16068),
                    "pat": (13934, 13420, 12131), "ebitda": (18815, 17888, 16843)},
    "itc_results": {"page": 9, "revenue": (30578.40, 29523.30, 21255.86), "pbt": (5694.62, 5860.85, 6978.68),
                    "pat": (4229.33, 4508.79, 5186.55), "ebitda": (5716.13, 5672.65, 6849.13)},
    "dixon_results": {"page": 11, "revenue": (21793.64, 15547.66, 14855.04), "pbt": (532.35, 868.98, 923.59),
                      "pat": (410.87, 717.83, 745.70), "ebitda": (599.63, 471.76, 562.48)},
}
EXPECTED_MGMT = {
    "hdfc_mgmt": ("Sanjay D’Souza", "retired", "October 31, 2026"),
    "maruti_mgmt": ("Kazunari Yamaguchi", "resigned", "30th September 2026"),
    "dixon_mgmt": ("Prithvi Vachani", "appointed", "30-10-2026"),
}


def cached(name):
    p = pdfs._path(URL[name])
    if not p.exists():
        pytest.skip(f"{name} not downloaded; run python -m scripts.verify_cards")
    return p


@pytest.mark.parametrize("name", list(EXPECTED_RESULTS))
def test_results_match_filing(name):
    card, _ = cards.extract_results(cached(name))
    exp = EXPECTED_RESULTS[name]
    assert card.statement == "consolidated" and card.page == exp["page"] and card.unit == "₹ crore"
    for k in ("revenue", "pbt", "pat", "ebitda"):
        m = card.metrics[k]
        got = (m.current, m.prev_q, m.yoy_q)
        assert got == pytest.approx(exp[k], abs=0.011), k


@pytest.mark.parametrize("name", list(EXPECTED_MGMT))
def test_mgmt_matches_filing(name):
    card, _ = cards.extract_mgmt(cached(name))
    lead = card.lead
    assert (lead.name, lead.change, lead.effective) == EXPECTED_MGMT[name]


def test_board_outcome_page1():
    r = cards.classify_page1(cached("dixon_outcome"))
    assert r["category"] == "results" and "management_change" in r["all"]
