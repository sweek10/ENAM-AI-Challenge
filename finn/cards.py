"""The brief's two special cards, plus page-1 classification of vague filings.

Principles:
  * The model copies what is printed; code does all arithmetic (EBITDA, QoQ, YoY).
  * Anything not printed is None and renders as "not found". No guessing.
  * Every card records the source page so the UI can link straight to it.
  * Cross-checks (total income = revenue + other income) flag misreads.
"""
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import classify, config, llm, pdfs
from .announcements import Announcement

# ---------- number parsing ----------

def parse_num(s) -> float | None:
    """'1,45,463' -> 145463 ; '(1,234.5)' -> -1234.5 ; '-' / '' / None -> None."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace("−", "-").replace("₹", "").replace(" ", "")
    if t in ("", "-", "–", "—", "nil", "Nil", "NIL", "N.A.", "NA"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def pct_change(cur, base) -> float | None:
    if cur is None or base is None or base == 0:
        return None
    return (cur - base) / abs(base) * 100


# ---------- results card ----------

RESULTS_SYSTEM = """You read quarterly financial results statements filed by Indian listed companies (SEBI format).
Rules:
- Copy every number exactly as printed, as a string, including commas and brackets. Never compute, round, estimate or infer.
- If a line is not printed in the statement, use null. Do not substitute a different line.
- Prefer the CONSOLIDATED statement if one is shown; otherwise use STANDALONE.
- Columns: "current" = quarter just ended; "prev_q" = the immediately preceding quarter; "yoy_q" = the same quarter a year earlier. Ignore half-year and full-year columns.
Reply with JSON only."""

RESULTS_SCHEMA = """{
 "found_statement": true|false,
 "statement": "consolidated"|"standalone",
 "unit": "as printed, e.g. ₹ crore / ₹ lakh / ₹ million",
 "page": <page number where the statement is printed>,
 "periods": {"current": "...", "prev_q": "...", "yoy_q": "..."},
 "rows": {
   "revenue":        {"label": "exact row label, normally Revenue from operations", "current": "...", "prev_q": "...", "yoy_q": "..."},
   "other_income":   {...same keys...},
   "total_income":   {...},
   "finance_costs":  {...},
   "depreciation":   {"label": "Depreciation and amortisation expense", ...},
   "pbt":            {"label": "Profit before tax (after exceptional items)", ...},
   "pat":            {"label": "Profit for the period", ...}
 }
}"""

METRICS = ["revenue", "ebitda", "pbt", "pat"]
METRIC_NAMES = {"revenue": "Revenue", "ebitda": "EBITDA", "pbt": "PBT", "pat": "PAT"}


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
    status: str = "ok"                                 # ok | partial | not_found | error
    model: str = ""
    cost_usd: float = 0.0

    def to_json(self) -> dict:
        d = asdict(self)
        d["metrics"] = {k: asdict(v) for k, v in self.metrics.items()}
        return d

    @staticmethod
    def from_json(d: dict) -> "ResultsCard":
        d = dict(d)
        d["metrics"] = {k: Metric(**v) for k, v in d["metrics"].items()}
        return ResultsCard(**d)


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


def build_results(data: dict | None) -> ResultsCard:
    """Turn the model's verbatim copy into a card. All arithmetic happens here."""
    if not data or not data.get("found_statement"):
        return ResultsCard(None, None, None, {}, {m: Metric(METRIC_NAMES[m]) for m in METRICS},
                           ["No results statement found in the pages read."], "not_found")
    rows = data.get("rows") or {}
    vals = {k: {c: parse_num((rows.get(k) or {}).get(c)) for c in ("current", "prev_q", "yoy_q")} for k in
            ("revenue", "other_income", "total_income", "finance_costs", "depreciation", "pbt", "pat")}
    labels = {k: (rows.get(k) or {}).get("label") or "" for k in vals}

    metrics: dict[str, Metric] = {}
    for k in ("revenue", "pbt", "pat"):
        metrics[k] = Metric(METRIC_NAMES[k], **vals[k], label=labels[k])

    # EBITDA is not a statutory line: derive it, and only if every input was printed.
    e = {}
    for c in ("current", "prev_q", "yoy_q"):
        parts = [vals["pbt"][c], vals["finance_costs"][c], vals["depreciation"][c], vals["other_income"][c]]
        e[c] = None if any(p is None for p in parts) else parts[0] + parts[1] + parts[2] - parts[3]
    metrics["ebitda"] = Metric("EBITDA", **e, derived=True,
                               label="PBT + finance costs + depreciation − other income")

    for m in metrics.values():
        m.qoq_pct = pct_change(m.current, m.prev_q)
        m.yoy_pct = pct_change(m.current, m.yoy_q)
    metrics = {k: metrics[k] for k in METRICS}

    checks = []
    for c in ("current", "prev_q", "yoy_q"):
        r, o, t = vals["revenue"][c], vals["other_income"][c], vals["total_income"][c]
        if None not in (r, o, t) and t and abs((r + o) - t) / abs(t) > 0.01:
            checks.append(f"Revenue + other income ≠ total income in the {c.replace('_', ' ')} column; verify against the filing.")
        p, b = vals["pat"][c], vals["pbt"][c]
        if None not in (p, b) and b > 0 and p > b * 1.5:
            checks.append(f"PAT is well above PBT in the {c.replace('_', ' ')} column; possible misread.")
    if vals["revenue"]["current"] is not None and vals["revenue"]["current"] <= 0:
        checks.append("Revenue is not positive; possible misread.")

    missing = [m for m in metrics.values() if m.current is None]
    status = "ok" if not missing and not checks else "partial"
    return ResultsCard(data.get("statement"), data.get("unit"), data.get("page"), data.get("periods") or {},
                       metrics, checks, status)


def extract_results(pdf: Path, client, max_scan_pages: int = 6) -> tuple[ResultsCard, dict]:
    texts = pdfs.page_texts(pdf, 30)
    n = pdfs.num_pages(pdf)
    if pdfs.is_scanned(texts[:max_scan_pages]):
        pages, how = list(range(min(n, max_scan_pages))), "scanned: first pages as images"
    else:
        pages = pick_statement_pages(texts)
        how = "text layer: picked statement page(s)"
        if not pages:
            card = build_results(None)
            card.checks = ["Text layer has no results statement (searched all pages)."]
            return card, {"pages": [], "how": how}
        pages = sorted(set(pages + [p + 1 for p in pages if p + 1 < n]))[:3]
    content = []
    for p in pages:
        content.append(llm.text_block(f"Page {p + 1}:"))
        content.append(llm.image_block(pdfs.render(pdf, p, dpi=110 if len(pages) <= 3 else 90)))
    content.append(llm.text_block("Extract the quarterly results into exactly this JSON shape:\n" + RESULTS_SCHEMA))
    data, usage, c = llm.call_json(client, config.EXTRACT_MODEL, RESULTS_SYSTEM, content, 1500, "results_card")
    card = build_results(data)
    card.model, card.cost_usd = config.EXTRACT_MODEL, c
    return card, {"pages": [p + 1 for p in pages], "how": how, "usage": usage}


# ---------- management change card ----------

MGMT_SYSTEM = """You read Indian stock-exchange disclosures about changes in directors and key managerial personnel.
Rules:
- Use ONLY what the filing says. Do not add anything you know about the person from elsewhere.
- background: at most two short sentences, taken from the filing's brief profile (education, prior roles, experience). null if the filing gives no profile.
- If a field is not stated, use null.
Reply with JSON only."""

MGMT_SCHEMA = """{"changes": [
  {"name": "...", "role": "designation as stated", "change": "appointed"|"resigned"|"retired"|"re-appointed"|"ceased"|"other",
   "effective": "date as stated or null", "background": "... or null", "prior_roles": ["...", "..."],
   "reason": "stated reason (for resignations) or null", "page": <page number>}
]}"""

_TOP = re.compile(r"\b(ceo|cfo|coo|md)\b|chief|managing director|chair|whole[- ]?time|executive director", re.I)


@dataclass
class Person:
    name: str
    role: str | None
    change: str | None
    effective: str | None
    background: str | None
    prior_roles: list[str]
    reason: str | None
    page: int | None


@dataclass
class MgmtCard:
    people: list[Person]
    status: str = "ok"            # ok | not_found | error
    model: str = ""
    cost_usd: float = 0.0
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
        d = dict(d)
        d["people"] = [Person(**p) for p in d["people"]]
        return MgmtCard(**d)


def build_mgmt(data: dict | None) -> MgmtCard:
    rows = (data or {}).get("changes") or []
    people = []
    for r in rows:
        if not r.get("name"):
            continue
        people.append(Person(r.get("name"), r.get("role"), r.get("change"), r.get("effective"),
                             r.get("background"), [x for x in (r.get("prior_roles") or []) if x][:4],
                             r.get("reason"), r.get("page")))
    return MgmtCard(people, "ok" if people else "not_found",
                    note="" if people else "No named person found in the filing.")


def extract_mgmt(pdf: Path, client, max_pages: int = 3) -> tuple[MgmtCard, dict]:
    texts = pdfs.page_texts(pdf, max_pages)
    n = min(pdfs.num_pages(pdf), max_pages)
    if pdfs.is_scanned(texts):
        content = []
        for p in range(n):
            content += [llm.text_block(f"Page {p + 1}:"), llm.image_block(pdfs.render(pdf, p, dpi=100))]
        how = "scanned: page images"
    else:   # text is far cheaper than images for a letter
        content = [llm.text_block("\n\n".join(f"--- Page {i + 1} ---\n{t[:6000]}" for i, t in enumerate(texts[:n])))]
        how = "text layer"
    content.append(llm.text_block("List every change in this exact JSON shape:\n" + MGMT_SCHEMA))
    data, usage, c = llm.call_json(client, config.EXTRACT_MODEL, MGMT_SYSTEM, content, 900, "mgmt_card")
    card = build_mgmt(data)
    card.model, card.cost_usd = config.EXTRACT_MODEL, c
    return card, {"how": how, "usage": usage}


# ---------- page-1 classification for vague headlines ----------

VAGUE = re.compile(r"outcome of (the )?board meeting|regulation\s*30|general update|^updates?$|intimation|"
                   r"disclosure under|announcement under|company update", re.I)


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


PAGE1_SYSTEM = ("You classify the first page of an Indian stock-exchange filing. List every decision or event it "
                "announces, using only these categories: " + ", ".join(classify.CATEGORIES) +
                '. Reply with JSON only: {"categories": [...], "summary": "one line, max 15 words, what was decided"}')


def classify_page1(pdf: Path, client) -> dict:
    """Returns {"category", "all", "method", "summary"?}. Text layer -> rules (free); scan -> classify model."""
    text = (pdfs.page_texts(pdf, 1) or [""])[0]
    if len(text.strip()) > 200:
        cats = rule_categories(text)
        if cats:
            return {"category": strongest(cats), "all": cats, "method": "pdf_text_rules"}
    if client is None:
        return {"category": None, "all": [], "method": "unavailable"}
    content = [llm.image_block(pdfs.render(pdf, 0, dpi=90)), llm.text_block("Classify this page.")]
    data, usage, c = llm.call_json(client, config.CLASSIFY_MODEL, PAGE1_SYSTEM, content, 120, "page1_classify")
    cats = [x for x in (data or {}).get("categories", []) if x in classify.CATEGORIES and x != "unclassified"]
    return {"category": strongest(cats), "all": cats, "method": "pdf_image_llm",
            "summary": (data or {}).get("summary"), "cost_usd": c}
