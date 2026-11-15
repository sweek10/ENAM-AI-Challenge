import pytest

from finn import classify as C
from finn.announcements import Announcement

from .corpus import CORPUS


def mk(i, cat, sub, head):
    return Announcement(id=f"t:{i}", symbol="TEST", dt=f"2026-11-{10 + i % 5:02d}T10:00:00",
                        headline=head, category=cat, subcategory=sub, pdf_url="", source="bse")


ITEMS = [mk(i, c, s, h) for i, (c, s, h, _, _) in enumerate(CORPUS)]


def _rule(a):
    return C.rule_classify(a.headline) or C.rule_classify(f"{a.category} | {a.subcategory}")


@pytest.mark.parametrize("row", [r for r in CORPUS if r[4]], ids=lambda r: r[2][:50])
def test_rules_correct(row):
    cat, sub, head, expected, _ = row
    hit = _rule(mk(0, cat, sub, head))
    assert hit and hit[0] == expected, f"got {hit}"


@pytest.mark.parametrize("row", [r for r in CORPUS if not r[4]], ids=lambda r: r[2][:50])
def test_rules_abstain_on_ambiguous(row):
    cat, sub, head, *_ = row
    assert _rule(mk(0, cat, sub, head)) is None


def test_ambiguous_items_stay_visible_as_unreviewed(tmp_data):
    res, st = C.classify(ITEMS)
    n_amb = sum(1 for r in CORPUS if not r[4])
    assert st["unreviewed"] == n_amb
    assert st["rule:headline"] + st["rule:exchange_cat"] == len(ITEMS) - n_amb
    leftovers = [r for r in res if r.method == "unreviewed"]
    assert all(r.tier == "routine" for r in leftovers)       # folded, not hidden


def test_page1_label_used_for_ambiguous(tmp_data):
    import json
    from finn import db
    amb = next(a for a, r in zip(ITEMS, CORPUS) if not r[4])
    with db.connect() as c:
        c.execute(C.ENRICH_SCHEMA)
        c.execute("INSERT INTO enrich VALUES (?,?,?,?)",
                  (amb.id, "page1", json.dumps({"category": "results", "all": ["results"]}), 0))
    res, st = C.classify(ITEMS)
    got = next(r for r in res if r.ann.id == amb.id)
    assert got.category == "results" and got.method == "pdf:page1" and st["pdf:page1"] == 1


def test_priority_ordering():
    by = {r[2]: r for r in CORPUS}
    sc = lambda h: C.score(C.rule_classify(h)[0], h, C.Prefs())[0]
    assert sc("Unaudited Financial Results for the quarter ended September 30, 2026") > \
        sc("Board Meeting Intimation for Considering Financial Results for the quarter ending 30th September 2026") > \
        sc("Copy of Newspaper Publication of Financial Results for Q2 FY27")
    assert sc("Resignation of Mr. A. Sharma as Chief Financial Officer") > sc("Appointment of Independent Director")
    assert sc("ICRA downgrades long term rating to A+ with negative outlook") > sc("CRISIL reaffirms credit rating of the Company")
    assert C.tier_of(sc("Closure of Trading Window")) == "hidden"
    assert sc("Acquisition of 100% stake in XYZ Technologies Pvt Ltd") > sc("Incorporation of a wholly owned subsidiary in Singapore")


def test_prefs_mute_and_care():
    h = "Record date for Interim Dividend"
    assert C.score("dividend", h, C.Prefs(mute=frozenset({"dividend"})))[0] == -1
    assert C.score("dividend", h, C.Prefs(care=frozenset({"dividend"})))[0] > C.score("dividend", h, C.Prefs())[0]
