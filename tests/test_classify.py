from types import SimpleNamespace

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


class FakeClient:
    """Mimics anthropic.Anthropic().messages.create; labels everything press_release."""
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        rows = kw["messages"][0]["content"].splitlines()[1:]
        body = '{"r":[' + ",".join(f'[{i},"press_release"]' for i in range(len(rows))) + "]}"
        n_in = (len(kw["system"]) + len(kw["messages"][0]["content"])) // 4
        return SimpleNamespace(content=[SimpleNamespace(text=body)],
                               usage=SimpleNamespace(input_tokens=n_in, output_tokens=len(body) // 3))


def test_llm_only_for_leftovers_then_cached(tmp_data):
    fake = FakeClient()
    res, st = C.classify(ITEMS, client=fake)
    n_amb = sum(1 for r in CORPUS if not r[4])
    assert st["llm"] == n_amb and fake.calls == 1          # one batched call
    assert st["rule:headline"] + st["rule:exchange_cat"] == len(ITEMS) - n_amb
    assert st["input_tokens"] > 0 and st["cost_usd"] > 0

    res2, st2 = C.classify(ITEMS, client=fake)              # second run: zero LLM
    assert fake.calls == 1 and st2["llm_cache"] == n_amb and st2["input_tokens"] == 0

    from finn import db
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 1


def test_no_api_key_degrades_visibly(tmp_data):
    res, st = C.classify(ITEMS, client=None)
    leftovers = [r for r in res if r.method == "no_llm"]
    assert leftovers and all(r.tier == "routine" for r in leftovers)


def test_llm_garbage_and_errors_dont_break(tmp_data):
    class Bad(FakeClient):
        def create(self, **kw):
            return SimpleNamespace(content=[SimpleNamespace(text="sorry, no")],
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=3))

    class Down(FakeClient):
        def create(self, **kw):
            raise RuntimeError("503")

    _, st = C.classify(ITEMS, client=Bad())
    assert st["no_llm"] > 0
    _, st = C.classify(ITEMS, client=Down())
    assert st["no_llm"] > 0 and st["llm_errors"]


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
