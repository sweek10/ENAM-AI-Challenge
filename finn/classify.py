"""Announcement classification: rules first, LLM only for what rules can't place.

Pipeline per announcement:
  1. rules on the headline (most specific signal)
  2. rules on the exchange's own category/subcategory fields
  3. cached LLM label (classified before -> free)
  4. LLM (Haiku 5.5), batched, only for the leftovers
Rules are recomputed every run (free, deterministic). LLM labels are stored in
SQLite keyed by announcement id + prompt version, so each filing costs at most once.
"""
import json
import os
import re
import time
from dataclasses import dataclass

from . import config, db
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


# ---------- LLM ----------
PROMPT_VERSION = "v1"
BATCH = 40

SYSTEM = f"""You classify Indian stock-exchange corporate announcements.
Return exactly one category per item from: {", ".join(CATEGORIES)}.
Guide:
results = quarterly/annual results, their board meeting, results deck or newspaper ad.
M&A = acquisition, merger, demerger, JV, investing in or selling a subsidiary stake.
fund_raise = rights issue, QIP, preferential allotment, buyback, debt/commercial paper.
management_change = CEO/CFO/MD/director joining, resigning, retiring.
litigation = lawsuits, regulatory orders, fines, insolvency proceedings.
insider_trading = promoter/insider buying or selling own shares.
earnings_call = concall notice, transcript, recording.  investor_meetings = analyst/fund meetings, conferences.
credit_rating = rating assigned/changed/reaffirmed.  dividend = cash dividend, record date.
bonus_split = bonus issue or split.  capex = new plant/capacity spend.
press_release = order wins, products, partnerships, clarifications, other news.
postal_ballot, agm_egm = those meetings' notices and results.  pledging = promoter pledge changes.
unclassified = routine paperwork that does not move the stock.
Reply with JSON only: {{"r":[[index,"category"],...]}}"""


@dataclass
class Classified:
    ann: Announcement
    category: str
    method: str        # "rule:headline" | "rule:exchange_cat" | "llm_cache" | "llm" | "no_llm"
    rule: str
    score: int
    tier: str
    reason: str


def _price(model: str) -> tuple[float, float]:
    return config.MODEL_PRICES_PER_MTOK.get(model, (0.0, 0.0))


def _default_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return anthropic.Anthropic()


def _llm_batch(client, items: list[Announcement]) -> tuple[dict[str, str], dict]:
    lines = [f"{i}|{a.category}|{a.subcategory}|{a.headline[:220]}" for i, a in enumerate(items)]
    resp = client.messages.create(
        model=config.CLASSIFY_MODEL, max_tokens=16 + 12 * len(items), temperature=0, system=SYSTEM,
        messages=[{"role": "user", "content": "index|exchange_category|subcategory|headline\n" + "\n".join(lines)}],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content)
    m = re.search(r"\{.*\}", text, re.S)
    labels: dict[str, str] = {}
    try:
        for idx, cat in json.loads(m.group(0))["r"] if m else []:
            if isinstance(idx, int) and 0 <= idx < len(items):
                labels[items[idx].id] = cat if cat in CATEGORIES else "unclassified"
    except (ValueError, KeyError, TypeError):
        pass
    u = resp.usage
    return labels, {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens}


def _log_usage(conn, purpose: str, n: int, usage: dict) -> None:
    pin, pout = _price(config.CLASSIFY_MODEL)
    cost = usage["input_tokens"] / 1e6 * pin + usage["output_tokens"] / 1e6 * pout
    conn.execute(
        "INSERT INTO llm_usage(ts,model,purpose,n_items,input_tokens,output_tokens,cost_usd) VALUES (?,?,?,?,?,?,?)",
        (time.time(), config.CLASSIFY_MODEL, purpose, n, usage["input_tokens"], usage["output_tokens"], cost),
    )


def classify(items: list[Announcement], prefs: Prefs | None = None, client="default") -> tuple[list[Classified], dict]:
    """Classify + prioritise. Returns (results sorted by score desc, run stats)."""
    prefs = prefs or Prefs()
    if client == "default":
        client = _default_client()
    stats = {"total": len(items), "rule:headline": 0, "rule:exchange_cat": 0, "pdf:page1": 0, "llm_cache": 0,
             "llm": 0, "no_llm": 0, "llm_calls": 0, "models": set(), "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

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

    with db.connect() as conn:
        if pending:  # page 1 of the PDF already read by the enrichment step?
            conn.execute("CREATE TABLE IF NOT EXISTS enrich (id TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL,"
                         " ts REAL NOT NULL, PRIMARY KEY (id, kind))")
            q = ",".join("?" * len(pending))
            for r in conn.execute(f"SELECT id, data FROM enrich WHERE kind='page1' AND id IN ({q})",
                                  [a.id for a in pending]):
                d = json.loads(r["data"])
                if d.get("category"):
                    labelled[r["id"]] = (d["category"], "pdf:page1", d.get("method", ""))
                    if d.get("method") == "pdf_image_llm":
                        stats["models"].add(config.CLASSIFY_MODEL)
            pending = [a for a in pending if a.id not in labelled]
        if pending:
            q = ",".join("?" * len(pending))
            rows = conn.execute(
                f"SELECT id, category, model FROM llm_labels WHERE prompt_version=? AND id IN ({q})",
                [PROMPT_VERSION, *[a.id for a in pending]]).fetchall()
            cached = {r["id"]: r["category"] for r in rows}
            stats["models"].update(r["model"] for r in rows)
            for a in pending:
                if a.id in cached:
                    labelled[a.id] = (cached[a.id], "llm_cache", "")
            pending = [a for a in pending if a.id not in cached]

        if pending and client is not None:
            for i in range(0, len(pending), BATCH):
                chunk = pending[i:i + BATCH]
                try:
                    labels, usage = _llm_batch(client, chunk)
                except Exception as e:  # API down -> degrade, don't break the digest
                    labels, usage = {}, {"input_tokens": 0, "output_tokens": 0}
                    stats.setdefault("llm_errors", []).append(f"{type(e).__name__}: {e}")
                else:
                    stats["llm_calls"] += 1
                    stats["models"].add(config.CLASSIFY_MODEL)
                    _log_usage(conn, "classify", len(chunk), usage)
                stats["input_tokens"] += usage["input_tokens"]
                stats["output_tokens"] += usage["output_tokens"]
                for a in chunk:
                    if a.id in labels:
                        labelled[a.id] = (labels[a.id], "llm", "")
                        conn.execute(
                            "INSERT OR REPLACE INTO llm_labels(id,category,model,prompt_version,ts) VALUES (?,?,?,?,?)",
                            (a.id, labels[a.id], config.CLASSIFY_MODEL, PROMPT_VERSION, time.time()))

    pin, pout = _price(config.CLASSIFY_MODEL)
    stats["cost_usd"] = stats["input_tokens"] / 1e6 * pin + stats["output_tokens"] / 1e6 * pout

    out = []
    for a in items:
        cat, method, rule = labelled.get(a.id, ("unclassified", "no_llm", ""))
        stats[method] += 1
        if method == "no_llm":  # unknown and no LLM available: keep visible, never bury silently
            s, why = 20, "unreviewed (no LLM)"
        else:
            s, why = score(cat, f"{a.headline} | {a.category} | {a.subcategory}", prefs)
        out.append(Classified(a, cat, method, rule, s, tier_of(s), why))
    out.sort(key=lambda c: (c.score, c.ann.dt), reverse=True)
    stats["models"] = sorted(stats["models"])
    return out, stats
