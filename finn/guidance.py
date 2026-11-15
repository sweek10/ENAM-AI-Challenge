"""Find numeric guidance in concall transcripts. Deterministic: no LLM.

A guidance statement is a sentence spoken by management (not the moderator or an
analyst) that looks forward ("expect", "guide", "FY27", "next year"...) and
carries a number we can act on (a %, a range, "mid-teens", an amount).
Each one is tagged with a metric, a target (low, high), a period and a basis
(rupee vs constant-currency), so it can be held against reported results.
"""
import re
from dataclasses import asdict, dataclass

FWD = re.compile(
    r"\b(expect\w*|guid\w*|target\w*|aim\w*|aspir\w*|outlook|anticipat\w*|project\w*|"
    r"should (?:be|grow|see|come|end|remain|deliver)|will (?:be|grow|see|achieve|deliver|remain|reach|come|end|maintain)|"
    r"going forward|next (?:year|fiscal|quarter|two quarters)|in the (?:coming|next) (?:year|quarters?)|"
    r"we are confident|we remain confident|confident of|plan(?:ning)? to|estimate\w*)\b", re.I)
STRONG_FWD = re.compile(r"\b(guid\w*|expect\w*|target\w*|aspir\w*|outlook|anticipat\w*)\b", re.I)
BACKWARD = re.compile(r"\b(achieved|stood at|came in at|came at|is at|was at|improved from|moved from|had guided|"
                      r"registering|registered|translat\w+ (?:to|into)|reported|"
                      r"we delivered|we grew|we have grown|was up|were up|declined|recorded|improvement of|"
                      r"quarter gone by|year gone by|for the quarter|this quarter|last quarter|in the quarter)\b", re.I)

WORD_RANGES = [
    (r"high single[- ]digit", 7, 9), (r"mid single[- ]digit", 4, 6), (r"low single[- ]digit", 1, 3),
    (r"low double[- ]digit", 10, 12), (r"double[- ]digit", 10, None), (r"low[- ]teens?", 11, 13),
    (r"mid[- ]teens?", 14, 16), (r"high[- ]teens?", 17, 19), (r"early twent\w+|low twent\w+", 20, 22),
    (r"mid twent\w+", 24, 26), (r"high twent\w+", 27, 29),
]
NUM = r"(\d{1,3}(?:\.\d+)?)"
RANGE_PCT = re.compile(rf"{NUM}\s*%?\s*(?:to|-|–|—|and)\s*{NUM}\s*(?:%|percent|per cent)", re.I)
SINGLE_PCT = re.compile(rf"{NUM}\s*(?:%|percent|per cent)", re.I)
AMOUNT = re.compile(r"(?:rs\.?|inr|₹|usd|\$)\s*([\d,]+(?:\.\d+)?)\s*(crores?|cr|lakhs?|billion|bn|million|mn)?|"
                    r"([\d,]+(?:\.\d+)?)\s*(crores?|cr\b|billion|bn\b|million|mn\b)", re.I)

METRICS = [
    ("margin", re.compile(r"\b(ebitda|ebit|operating)\s+margin|\bmargins?\b|\bopm\b|\bebitda\b", re.I)),
    ("capex", re.compile(r"\bcapex\b|capital expenditure", re.I)),
    ("profit_growth", re.compile(r"\b(pat|net profit|profit after tax|bottom[- ]?line|earnings)\b.{0,40}\b(grow|growth)", re.I)),
    ("revenue_growth", re.compile(r"\b(revenue|top[- ]?line|sales|turnover)\b.{0,60}\b(grow\w*|growth|increase)|"
                                  r"\b(grow\w*|growth)\b.{0,40}\b(revenue|top[- ]?line|sales|turnover)\b", re.I)),
]
# Numbers about something other than the company's own revenue or overall margin. Comparing these with
# reported revenue or operating margin gave wrong verdicts (loan-book growth, order inflow, one segment's margin).
NOT_COMPANY_LEVEL = re.compile(
    r"\b(aum|assets under management|loan|loans|advances|deposits?|credit|book|orders?|order inflow|inflows?|backlog|"
    r"pipeline|volumes?|industry|market|system|gdp|segment|portfolio|vertical|division|geograph\w*|region\w*|"
    r"exports?|domestic|ppm|p&m|eps|dividend|roe|roa|roce|nim|cost of funds|tax rate|etr|attrition|"
    r"utili[sz]ation|headcount|capacity|deal|wins|subsidiar\w*|businesses|this business|that business|cagr)\b", re.I)
EBIT_ONLY = re.compile(r"\bebit\b(?!da)", re.I)
CC = re.compile(r"constant currency|\bcc\b|in dollar terms|dollar revenue|usd revenue|\$ revenue|in usd", re.I)


@dataclass
class Guidance:
    metric: str                 # revenue_growth | margin | profit_growth | capex | other
    low: float | None
    high: float | None
    unit: str                   # "%" | "₹ crore" | ...
    period: str | None          # "FY27" | "Q3FY27" | "H2FY27" | None
    basis: str                  # "inr" | "cc" | "ebit" (margin guided on EBIT, not comparable)
    quote: str                  # verbatim sentence
    speaker: str
    call: str                   # "Nov 2026"
    source: str                 # transcript url
    score: float = 0.0

    def to_json(self):
        return asdict(self)


# ---------- transcript structure ----------

NOT_A_SPEAKER = re.compile(r"^(MANAGEMENT|PARTICIPANTS?|ANALYSTS?|SPEAKERS?|DATE|TIME|NOTE|DISCLAIMER|SUB|SUBJECT|"
                           r"MODERATORS?|CHAIRPERSON|PRESENT|ATTENDEES|REF|DEAR SIR.*)$", re.I)
SPEAKER = re.compile(r"^\s*([A-Z][A-Za-z.'’\- ]{1,48}?)\s*[:：]\s+(?=\S)", re.M)


def turns(text: str) -> list[tuple[str, str]]:
    """[(speaker, words)] in order. Speaker names are 'Firstname Lastname:' at line start.
    Parsing starts at the first 'Moderator:' so cover-letter labels (Kind Attn:, Ref.:) aren't speakers."""
    out = []
    start = re.search(r"^\s*(Moderator|Operator)\s*:", text, re.M | re.I)
    text = text[start.start():] if start else text
    marks = [(m.start(), m.end(), _clean_name(m[1])) for m in SPEAKER.finditer(text)
             if len(m[1].split()) <= 5 and not re.search(r"\d", m[1]) and not NOT_A_SPEAKER.match(m[1].strip())]
    for k, (s, e, name) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(text)
        out.append((name, text[e:end]))
    return out


def _clean_name(n: str) -> str:
    n = re.sub(r"^(mr|ms|mrs|dr)\.?\s+", "", n.strip(), flags=re.I)
    return re.sub(r"\s+", " ", n)


def management(text: str, ts: list[tuple[str, str]]) -> set[str]:
    """Names on the cover page's MANAGEMENT block, else speakers who mostly answer rather than ask."""
    names = {n for n, _ in ts if n.lower() not in ("moderator", "operator", "participant")}
    m = re.search(r"(?:^|\n)\s*(?:MANAGEMENT|Management)\s*:(.{0,1800}?)(?:MODERATOR\s*:|ANALYSTS?\s*:|PARTICIPANTS?\s*:|\n\s*Moderator\s*:|$)",
                  text[:12000], re.S)
    head = m[1] if m else ""
    listed = {n for n in names if len(n) > 3 and n.split()[-1].lower() in head.lower()}
    stats = {}
    for n, w in ts:
        if n not in names:
            continue
        q, words = w.count("?"), len(w.split())
        a = stats.setdefault(n, [0, 0, 0])
        a[0] += words
        a[1] += q
        a[2] += 1
    answerers = {n for n, (words, q, k) in stats.items() if words / k > 90 and q / k < 0.6}
    return listed | answerers


# ---------- statements ----------

def _sentences(s: str) -> list[str]:
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\b(Mr|Ms|Mrs|Dr|vs|approx|Rs|No|Q\d)\.", lambda m: m[0].replace(".", "§"), s)
    return [x.replace("§", ".").strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z“\"])", s) if len(x.strip()) > 25]


def fiscal_year(call_label: str) -> int | None:
    """Indian FY (Apr-Mar) the call's 'this year' refers to. 'May 2026' -> 2027 (FY27 just began)."""
    m = re.match(r"([A-Za-z]{3})\w*\s+(\d{4})", call_label or "")
    if not m:
        return None
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    mo = months.index(m[1].lower()) + 1 if m[1].lower() in months else None
    if not mo:
        return None
    y = int(m[2])
    return y + 1 if mo >= 4 else y


def period_of(sentence: str, fy: int | None) -> str | None:
    s = sentence.lower()
    if re.search(r"\b(beyond|after|post|from)\s+fy\s?'?\d{2,4}", s):
        return None                 # "beyond FY25" is not FY25
    m = re.search(r"\b(q[1-4])\s*(?:of\s*)?fy\s?'?(\d{2,4})\b", s)
    if m:
        return f"{m[1].upper()}FY{m[2][-2:]}"
    m = re.search(r"\b(h[12])\s*(?:of\s*)?fy\s?'?(\d{2,4})\b", s)
    if m:
        return f"{m[1].upper()}FY{m[2][-2:]}"
    m = re.search(r"\bfy\s?'?(\d{2,4})\b|\bfiscal\s?(?:year\s?)?'?(\d{2,4})\b", s)
    if m:
        return f"FY{(m[1] or m[2])[-2:]}"
    if fy is None:
        return None
    if re.search(r"\bnext (?:fiscal|financial)? ?year\b|\bnext fiscal\b", s):
        return f"FY{(fy + 1) % 100:02d}"
    if re.search(r"\b(this|current|full|the) (?:fiscal |financial )?year\b|\bfor the year\b|\bannual\b", s):
        return f"FY{fy % 100:02d}"
    if re.search(r"\bsecond half\b|\bh2\b", s):
        return f"H2FY{fy % 100:02d}"
    return None


def target_of(sentence: str) -> tuple[float | None, float | None, str]:
    for pat, lo, hi in WORD_RANGES:
        if re.search(pat, sentence, re.I):
            return float(lo), (float(hi) if hi is not None else None), "%"
    m = RANGE_PCT.search(sentence)
    if m:
        a, b = float(m[1]), float(m[2])
        if a <= b and b <= 100:
            return a, b, "%"
    m = SINGLE_PCT.search(sentence)
    if m and float(m[1]) <= 100:
        v = float(m[1])
        return v, v, "%"
    m = AMOUNT.search(sentence)
    if m:
        raw = (m[1] or m[3] or "").replace(",", "")
        unit = (m[2] or m[4] or "").lower()
        try:
            v = float(raw)
        except ValueError:
            return None, None, ""
        unit = "₹ crore" if unit.startswith("cr") else (unit or "amount")
        return v, v, unit
    return None, None, ""


def metric_of(sentence: str) -> str:
    for name, pat in METRICS:
        if pat.search(sentence):
            return name
    return "other"


def extract(text: str, call: str, source: str, per_call: int = 8) -> list[Guidance]:
    ts = turns(text)
    mgmt = management(text, ts)
    fy = fiscal_year(call)
    found: list[Guidance] = []
    for speaker, words in ts:
        if speaker not in mgmt:
            continue
        for sent in _sentences(words):
            if "?" in sent or not FWD.search(sent):
                continue
            if BACKWARD.search(sent) and not STRONG_FWD.search(sent):
                continue
            if BACKWARD.search(sent) and len(re.findall(r"\d+(?:\.\d+)?\s*%", sent)) >= 2:
                continue            # "stood at 12% against guidance of 15%": actual and target mixed, skip
            lo, hi, unit = target_of(sent)
            if lo is None:
                continue
            metric = metric_of(sent)
            if metric in ("revenue_growth", "margin", "profit_growth") and NOT_COMPANY_LEVEL.search(sent):
                metric = "other"            # still shown, as unclear, but never judged against company totals
            if unit != "%" and metric != "capex":
                continue            # deal sizes, financing amounts: not checkable guidance
            if metric == "other" and not STRONG_FWD.search(sent):
                continue
            period = period_of(sent, fy)
            score = (2 if re.search(r"guid", sent, re.I) else 0) + (1 if period else 0) + \
                    (1 if metric != "other" else 0) + (1 if lo != hi else 0)
            basis = "cc" if CC.search(sent) else ("ebit" if metric == "margin" and EBIT_ONLY.search(sent) else "inr")
            found.append(Guidance(metric, lo, hi, unit, period, basis,
                                  sent[:420], speaker, call, source, score))
    found.sort(key=lambda g: -g.score)
    seen, out = set(), []
    for g in found:
        key = (g.metric, g.period, g.low, g.high)
        if key in seen:
            continue
        seen.add(key)
        out.append(g)
    return out[:per_call]
