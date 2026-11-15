"""The brief's two special cards, plus page-1 classification of vague filings. No LLM.

Principles:
  * Numbers come straight from the filing's text layer, located by row label and
    by the date printed above each column; code does all arithmetic.
  * Anything not found is None and renders as "not found". No guessing.
  * Every card records the source page so the UI can link straight to it.
  * Cross-checks (total income = revenue + other income) flag misreads.
"""
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import classify, mgmt, pdfs, statement
from .announcements import Announcement
from .mgmt import Person

PARSER = "text parser"


def pct_change(cur, base) -> float | None:
    if cur is None or base is None or base == 0:
        return None
    return (cur - base) / abs(base) * 100


# ---------- results card ----------

METRICS = ["revenue", "ebitda", "pbt", "pat"]
METRIC_NAMES = {"revenue": "Revenue", "ebitda": "EBITDA", "pbt": "PBT", "pat": "PAT"}
COLS = ("current", "prev_q", "yoy_q")


@dataclass
class Metric:
    name: str
    current: float | None = None
    prev_q: float | None = None
    yoy_q: float | None = None
    qoq_pct: float | None = None
    yoy_pct: float | None = None
    label: str = ""          # row label as printed, or the derivation
    derived: bool = False


@dataclass
class ResultsCard:
    statement: str | None
    unit: str | None
    page: int | None
    periods: dict
    metrics: dict[str, Metric]
    checks: list[str] = field(default_factory=list)   # human-readable warnings
    status: str = "ok"                                 # ok | partial | not_found
    method: str = PARSER

    def to_json(self) -> dict:
        d = asdict(self)
        d["metrics"] = {k: asdict(v) for k, v in self.metrics.items()}
        return d

    @staticmethod
    def from_json(d: dict) -> "ResultsCard":
        d = {k: v for k, v in d.items() if k in ResultsCard.__dataclass_fields__}
        d["metrics"] = {k: Metric(**v) for k, v in d["metrics"].items()}
        return ResultsCard(**d)


def not_found(reason: str) -> ResultsCard:
    return ResultsCard(None, None, None, {}, {m: Metric(METRIC_NAMES[m]) for m in METRICS}, [reason], "not_found")


def build_results(vals: dict[str, dict[str, float | None]], labels: dict[str, str], *, statement_kind=None,
                  unit=None, page=None, periods=None, notes=None) -> ResultsCard:
    """vals: row -> {current, prev_q, yoy_q} as printed. All arithmetic happens here."""
    get = lambda r, c: (vals.get(r) or {}).get(c)
    metrics: dict[str, Metric] = {}
    for k in ("revenue", "pbt", "pat"):
        metrics[k] = Metric(METRIC_NAMES[k], **{c: get(k, c) for c in COLS}, label=labels.get(k, ""))

    # EBITDA is not a statutory line: derive it, and only if every input was printed.
    e = {}
    for c in COLS:
        parts = [get("pbt", c), get("finance_costs", c), get("depreciation", c), get("other_income", c)]
        e[c] = None if any(p is None for p in parts) else round(parts[0] + parts[1] + parts[2] - parts[3], 2)
    fin, rev = get("finance_costs", "current"), get("revenue", "current")
    if fin is not None and rev and fin > 0.3 * rev:      # a lender: interest is its main cost, EBITDA means nothing
        e = {c: None for c in COLS}
        notes = list(notes or []) + ["EBITDA isn't meaningful for a lender, so it isn't shown."]
    metrics["ebitda"] = Metric("EBITDA", **e, derived=True, label="PBT + finance costs + depreciation − other income")

    for m in metrics.values():
        m.qoq_pct = pct_change(m.current, m.prev_q)
        m.yoy_pct = pct_change(m.current, m.yoy_q)
    metrics = {k: metrics[k] for k in METRICS}

    checks = list(notes or [])
    names = {"current": "this quarter", "prev_q": "previous quarter", "yoy_q": "year-ago quarter"}
    for c in COLS:
        r, o, t = get("revenue", c), get("other_income", c), get("total_income", c)
        if None not in (r, o, t) and t and abs((r + o) - t) / abs(t) > 0.01:
            checks.append(f"Revenue + other income ≠ total income for the {names[c]}; verify against the filing.")
        p, b = get("pat", c), get("pbt", c)
        if None not in (p, b) and b > 0 and p > b * 1.5:
            checks.append(f"PAT is well above PBT for the {names[c]}; possible misread.")
    if get("revenue", "current") is not None and get("revenue", "current") <= 0:
        checks.append("Revenue is not positive; possible misread.")

    if all(m.current is None for m in metrics.values()):
        return not_found("No results figures found in the filing's text.")
    missing = [m for m in metrics.values() if m.current is None]
    status = "ok" if not missing and not checks else "partial"
    return ResultsCard(statement_kind, unit, page, periods or {}, metrics, checks, status)


_STATEMENT_HINTS = [
    (r"revenue\s+from\s+operations", 4), (r"profit\s+before\s+tax", 4), (r"total\s+income", 1),
    (r"(three|3)\s+months\s+ended|quarter\s+ended", 2), (r"finance\s+costs", 1), (r"depreciation", 1),
]


def pick_statement_pages(texts: list[str]) -> list[int]:
    """0-based pages most likely to hold the results statement, consolidated first."""
    scored = []
    for i, t in enumerate(texts):
        low = t.lower()
        s = sum(w for pat, w in _STATEMENT_HINTS if re.search(pat, low))
        if s >= 8:
            s += 3 if "consolidated" in low else 0
            s -= 4 if re.search(r"segment\s+(revenue|information|results)", low) and "profit before tax" not in low else 0
            scored.append((s, i))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [i for _, i in scored[:2]]


def extract_results(pdf: Path) -> tuple[ResultsCard, dict]:
    texts = pdfs.page_texts(pdf, 40)
    if pdfs.is_scanned(texts[:6]):
        return not_found("This filing is an image without a text layer, so the figures can't be read."), \
            {"how": "scanned, no text"}
    # candidates: best-scoring pages plus any page that mentions revenue from operations
    cands = pick_statement_pages(texts) + [i for i, t in enumerate(texts) if re.search(r"revenue\s+from\s+operations", t, re.I)]
    cands = list(dict.fromkeys(cands))[:12]
    parsed, page = statement.find_and_parse(pdf, cands)
    if parsed is None:
        return not_found("No results statement found in the filing's text."), {"how": "text layer", "pages": [c + 1 for c in cands]}
    vals = {k: {c: (cell.value if cell else None) for c, cell in row.items()} for k, row in parsed.rows.items()}
    card = build_results(vals, parsed.labels, statement_kind=parsed.statement, unit=parsed.unit, page=page,
                         periods=parsed.periods, notes=parsed.notes)
    return card, {"how": "text layer, positions", "pages": [page]}


# ---------- management change card ----------

_TOP = re.compile(r"\b(ceo|cfo|coo|md)\b|chief|managing director|chair|whole[- ]?time|executive director", re.I)


@dataclass
class MgmtCard:
    people: list[Person]
    status: str = "ok"            # ok | not_found
    method: str = PARSER
    note: str = ""

    @property
    def lead(self) -> Person | None:
        if not self.people:
            return None
        order = {"appointed": 0, "resigned": 1, "retired": 2, "ceased": 3, "re-appointed": 4}
        return sorted(self.people, key=lambda p: (0 if _TOP.search(p.role or "") else 1,
                                                  order.get(p.change or "", 5)))[0]

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "MgmtCard":
        d = {k: v for k, v in d.items() if k in MgmtCard.__dataclass_fields__}
        d["people"] = [Person(**{k: v for k, v in p.items() if k in Person.__dataclass_fields__}) for p in d["people"]]
        return MgmtCard(**d)


def extract_mgmt(pdf: Path, max_pages: int = 8) -> tuple[MgmtCard, dict]:
    texts = pdfs.page_texts(pdf, max_pages)
    if pdfs.is_scanned(texts[:3]):
        return MgmtCard([], "not_found", note="This filing is an image without a text layer."), {"how": "scanned"}
    people = mgmt.parse(texts)
    card = MgmtCard(people, "ok" if people else "not_found",
                    note="" if people else "No named person found in the filing's text.")
    return card, {"how": "text layer"}


# ---------- page-1 classification for vague headlines ----------

VAGUE = re.compile(r"outcome of (the )?board meeting|regulation\s*30|general update|^updates?$|intimation|"
                   r"disclosure under|announcement under|company update|change in management", re.I)


def is_vague(a: Announcement) -> bool:
    if classify.rule_classify(a.headline):
        return False
    return bool(VAGUE.search(a.headline) or VAGUE.search(a.subcategory or ""))


def strongest(categories: list[str]) -> str | None:
    """A board outcome is as important as its most important decision."""
    cats = [c for c in categories if c in classify.BASE]
    return max(cats, key=lambda c: classify.BASE[c]) if cats else None


def rule_categories(text: str) -> list[str]:
    found = []
    for cat, _name, pat in classify.RULES:
        if cat != "unclassified" and pat.search(text) and cat not in found:
            found.append(cat)
    if re.search(r"trading window", text, re.I) and "insider_trading" in found:
        found.remove("insider_trading")   # PIT is cited only to announce the closed window
    return found


def classify_page1(pdf: Path) -> dict:
    """Rules on the first page's text. Returns {"category", "all", "method"}."""
    text = (pdfs.page_texts(pdf, 1) or [""])[0]
    if len(text.strip()) < 200:
        return {"category": None, "all": [], "method": "no text on page 1"}
    # Drop the letter's subject line so 'Outcome of Board Meeting' itself doesn't count.
    body = re.split(r"\bsub(?:ject)?\s*[:.-]", text, maxsplit=1, flags=re.I)[-1]
    cats = rule_categories(body)
    return {"category": strongest(cats), "all": cats, "method": "page-1 text rules"}
