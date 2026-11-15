"""Announcement classification and priority. Deterministic: no LLM.

Pipeline per announcement:
  1. rules on the headline (most specific signal)
  2. rules on the exchange's own category/subcategory fields
  3. rules on the first page of the filing PDF (read once by the enrichment step)
Anything still unplaced is shown as "unreviewed", never silently dropped.
"""
import json
import re
from dataclasses import dataclass

from . import db
from .announcements import Announcement

CATEGORIES = [
    "results", "M&A", "fund_raise", "management_change", "litigation", "insider_trading",
    "earnings_call", "investor_meetings", "credit_rating", "dividend", "bonus_split", "capex",
    "press_release", "postal_ballot", "agm_egm", "pledging", "unclassified",
]

# ---------- rules ----------
# Ordered: specific/routine traps first, broad patterns last. First match wins.
_R = lambda p: re.compile(p, re.I)
RULES: list[tuple[str, str, re.Pattern]] = [
    ("unclassified", "routine_compliance", _R(
        r"trading window|reg(ulation)?\.?\s*74\s*\(5\)|depositories and participants|"
        r"loss of share cert|duplicate share|\besop|\besos|\besps|employee stock|"
        r"compliance certificate|reg(ulation)?\.?\s*40\s*\(9\)|reg(ulation)?\.?\s*7\s*\(3\)|"
        r"secretarial compliance|business responsibility|\bbrsr\b|\besg\b|sustainability report|"
        r"investor (complaints|grievance)|statement of deviation|monitoring agency|\bxbrl\b|"
        r"(statutory|secretarial|cost|internal) auditor|share transfer agent|\brta\b|"
        r"change (in|of) (registered office|name|address)|appoint\w* of (the )?scrutini[sz]er")),
    ("pledging", "pledge", _R(r"pledg|encumbr|reg(ulation)?\.?\s*31\s*\(")),
    ("insider_trading", "insider", _R(
        r"insider trading|\bsast\b|takeover regulations|reg(ulation)?\.?\s*29\s*\(|"
        r"reg(ulation)?\.?\s*7\s*\(2\)|sebi \(pit\)|prohibition of insider")),
    ("postal_ballot", "postal_ballot", _R(r"postal ballot|e-?voting result")),
    ("agm_egm", "agm_egm", _R(
        r"\bagm\b|\begm\b|annual general meeting|extra-?\s?ordinary general meeting|"
        r"general meeting|voting results|shareholders'? meeting")),
    ("earnings_call", "earnings_call", _R(
        r"transcript|earnings call|conference call|con\.?\s?call|concall|"
        r"audio recording|call recording|earnings conference")),
    ("investor_meetings", "investor_meet", _R(
        r"analyst|investor meet|institutional investor|investor conference|"
        r"one[- ]on[- ]one|group meeting|roadshow|investor day")),
    ("credit_rating", "credit_rating", _R(
        r"credit rating|\bratings?\b.*\b(icra|crisil|care|india ratings|fitch|moody|acuite|brickwork)\b|"
        r"\b(icra|crisil|acuite)\b")),
    ("results", "results", _R(
        r"financial results?|\bresults? for the (quarter|half|year|period)|unaudited|"
        r"audited (standalone|consolidated)|quarterly results|half[- ]year(ly)? results|"
        r"\bq[1-4]\s?(fy)?\s?'?\d{2}|investor presentation|earnings (presentation|release)|"
        r"shareholders'? letter|financial statements? for")),
    ("dividend", "dividend", _R(r"dividend")),
    ("bonus_split", "bonus_split", _R(r"\bbonus\b|stock split|sub-?division of (equity )?shares|\bsplit\b")),
    ("fund_raise", "fund_raise", _R(
        r"\bqip\b|qualified institution|rights issue|preferential (issue|allotment)|buy-?back|"
        r"commercial paper|non-?convertible debenture|\bncds?\b|\bbonds?\b|fund[- ]?rais|"
        r"raising of funds|\bwarrants\b|private placement|issue of (equity|securities)")),
    ("M&A", "m_and_a", _R(
        r"acqui|merger|amalgamation|demerger|scheme of arrangement|joint venture|\bjv\b|"
        r"stake (sale|purchase)|divest|disinvest|incorporation of (a )?(new )?(wholly[- ]owned )?subsidiary|"
        r"investment in .{0,40}subsidiary|share purchase agreement|slump sale|business transfer")),
    ("litigation", "litigation", _R(
        r"litigation|dispute|penalt|show[- ]cause|\bnclt\b|\bnclat\b|insolvency|\bibc\b|\bcirp\b|"
        r"demand (notice|order)|tax demand|warning letter|regulatory action|search and seizure|"
        r"\bfined?\b|adjudicat|arbitra|writ petition|high court|supreme court|tribunal|"
        r"enforcement directorate|form 483|import alert|order (passed|issued) by")),
    ("capex", "capex", _R(
        r"\bcapex\b|capital expenditure|capacity expansion|expansion of capacity|"
        r"new (plant|facility|unit|manufacturing)|greenfield|brownfield|commercial production|"
        r"commission(ing|ed) of")),
    ("management_change", "management_change", _R(
        r"resign|appoint|cessation|retire|change in (management|directorate|directors?|kmp)|"
        r"key managerial|chief (executive|financial|operating|technology|business|risk|human)|"
        r"\b(ceo|cfo|coo|cto|md)\b|managing director|whole[- ]?time director|"
        r"independent director|company secretary|compliance officer|senior management")),
    ("press_release", "press_release", _R(
        r"press release|media release|order (win|received|receipt)|award of (order|contract)|"
        r"receipt of order|\bbags?\b|\bwins?\b|launch|partnership|collaborat|\bmou\b|"
        r"memorandum of understanding|clarification|rumou?r|news item|spurt in volume|"
        r"price movement|new product")),
    ("unclassified", "routine_generic", _R(r"newspaper (publication|advertisement)|copy of newspaper")),
]


def rule_classify(text: str) -> tuple[str, str] | None:
    for cat, name, pat in RULES:
        if pat.search(text or ""):
            return cat, name
    return None


# ---------- priority ----------
BASE = {
    "results": 90, "M&A": 85, "management_change": 70, "fund_raise": 75, "litigation": 75,
    "pledging": 70, "credit_rating": 60, "capex": 65, "bonus_split": 55, "insider_trading": 50,
    "dividend": 45, "press_release": 40, "earnings_call": 40, "agm_egm": 25,
    "postal_ballot": 25, "investor_meetings": 20, "unclassified": 5,
}
# (category, pattern, score, reason) — overrides base when the pattern matches.
MODIFIERS: list[tuple[str, re.Pattern, int, str]] = [
    ("results", _R(r"newspaper|publication|advertisement"), 10, "results ad"),
    ("results", _R(r"intimation|to consider|will be held|is scheduled|prior intimation"), 35, "results date"),
    ("results", _R(r"investor presentation|earnings presentation"), 55, "results deck"),
    ("credit_rating", _R(r"downgrad|negative|watch|suspend|withdraw"), 85, "rating cut/watch"),
    ("credit_rating", _R(r"upgrad|positive"), 70, "rating upgrade"),
    ("credit_rating", _R(r"reaffirm|affirm"), 30, "rating reaffirmed"),
    ("management_change", _R(r"re-?appoint|independent director|company secretary|compliance officer"), 35, "minor role"),
    ("management_change", _R(r"\b(ceo|cfo|coo|md)\b|chief|managing director|chairman|whole[- ]?time"), 85, "top role"),
    ("litigation", _R(r"warning letter|import alert|form 483|insolvency|nclt|enforcement directorate|search"), 85, "serious regulatory"),
    ("pledging", _R(r"invoc|invoke|creation|created"), 80, "pledge created/invoked"),
    ("pledging", _R(r"release|revoc"), 45, "pledge released"),
    ("press_release", _R(r"clarification|rumou?r|spurt in volume|price movement"), 55, "market clarification"),
    ("press_release", _R(r"order|contract|bags?|wins?"), 50, "order win"),
    ("earnings_call", _R(r"transcript"), 50, "transcript (feeds research)"),
    ("earnings_call", _R(r"intimation|schedule|invitation"), 30, "call scheduled"),
    ("insider_trading", _R(r"promoter"), 60, "promoter dealing"),
    ("fund_raise", _R(r"buy-?back"), 80, "buyback"),
    ("fund_raise", _R(r"commercial paper|non-?convertible|\bncds?\b|debenture|\bbonds?\b"), 40, "routine debt issue"),
    ("M&A", _R(r"incorporation of|wholly[- ]owned subsidiar(y|ies) with the company|investment in .{0,40}subsidiary"), 40, "internal restructuring"),
]

TIERS = [(70, "top"), (40, "notable"), (15, "routine")]  # below 15 -> "hidden"


@dataclass
class Prefs:
    care: frozenset = frozenset({"results", "M&A", "management_change"})
    mute: frozenset = frozenset()
    care_boost: int = 10


def score(category: str, text: str, prefs: Prefs) -> tuple[int, str]:
    s, reason = BASE.get(category, 5), category
    for cat, pat, val, why in MODIFIERS:
        if cat == category and pat.search(text):
            s, reason = val, why
            break
    if category in prefs.mute:
        return -1, "muted"
    if category in prefs.care and s >= 40:
        s, reason = min(100, s + prefs.care_boost), reason + " · on watchlist"
    return s, reason


def tier_of(s: int) -> str:
    return next((name for cut, name in TIERS if s >= cut), "hidden")


@dataclass
class Classified:
    ann: Announcement
    category: str
    method: str        # "rule:headline" | "rule:exchange_cat" | "pdf:page1" | "unreviewed"
    rule: str
    score: int
    tier: str
    reason: str


ENRICH_SCHEMA = ("CREATE TABLE IF NOT EXISTS enrich (id TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL,"
                 " ts REAL NOT NULL, PRIMARY KEY (id, kind))")


def classify(items: list[Announcement], prefs: Prefs | None = None) -> tuple[list[Classified], dict]:
    """Classify + prioritise. Returns (results sorted by score desc, run stats)."""
    prefs = prefs or Prefs()
    stats = {"total": len(items), "rule:headline": 0, "rule:exchange_cat": 0, "pdf:page1": 0, "unreviewed": 0}

    labelled: dict[str, tuple[str, str, str]] = {}  # id -> (category, method, rule)
    pending: list[Announcement] = []
    for a in items:
        hit = rule_classify(a.headline)
        if hit:
            labelled[a.id] = (hit[0], "rule:headline", hit[1])
            continue
        hit = rule_classify(f"{a.category} | {a.subcategory}")
        if hit:
            labelled[a.id] = (hit[0], "rule:exchange_cat", hit[1])
            continue
        pending.append(a)

    if pending:  # page 1 of the PDF already read by the enrichment step?
        with db.connect() as conn:
            conn.execute(ENRICH_SCHEMA)
            q = ",".join("?" * len(pending))
            for r in conn.execute(f"SELECT id, data FROM enrich WHERE kind='page1' AND id IN ({q})",
                                  [a.id for a in pending]):
                d = json.loads(r["data"])
                if d.get("category"):
                    labelled[r["id"]] = (d["category"], "pdf:page1", ",".join(d.get("all", [])))

    out = []
    for a in items:
        cat, method, rule = labelled.get(a.id, ("unclassified", "unreviewed", ""))
        stats[method] += 1
        if method == "unreviewed":  # unknown: keep visible, never bury silently
            s, why = 20, "unreviewed"
        else:
            s, why = score(cat, f"{a.headline} | {a.category} | {a.subcategory}", prefs)
        out.append(Classified(a, cat, method, rule, s, tier_of(s), why))
    out.sort(key=lambda c: (c.score, c.ann.dt), reverse=True)
    return out, stats
