"""Deterministic parser for SEBI-format quarterly results statements. No LLM.

Why positions, not text order: in OCR'd filings the text stream often runs
column by column, so rows are rebuilt from word coordinates. Why read the
date headers: companies order the quarter columns differently (TCS: current,
previous quarter, year-ago; ITC: current, year-ago, previous quarter).
"""
import re
from dataclasses import dataclass, field
from datetime import date

NUM = re.compile(r"^\(?-?(\d{1,3}(,\d{2,3})+|\d+)(\.\d+)?\)?$")
YEAR = re.compile(r"^(19|20)\d{2}$")
OCR_DIGITISH = re.compile(r"^\(?[\dOIlBS][\dOIlBS,.]*\)?$")
OCR_MAP = str.maketrans({"O": "0", "I": "1", "l": "1", "B": "8", "S": "5"})

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

ROWS = {
    "revenue": re.compile(r"^(total\s+)?revenue\s+from\s+operations", re.I),
    "other_income": re.compile(r"^other\s+income", re.I),
    "total_income": re.compile(r"^total\s+income\b(?!.*comprehensive)", re.I),
    "finance_costs": re.compile(r"finance\s+costs?", re.I),
    "depreciation": re.compile(r"depreciation", re.I),
    "pbt": re.compile(r"^(net\s+)?profit\s*(/\s*\(?loss\)?\s*)?before\s+tax", re.I),
    "pat": re.compile(r"^(net\s+)?profit\s*(/\s*\(?loss\)?\s*)?(for\s+the\s+(period|quarter|year)|after\s+tax)", re.I),
}
LABEL_PREFIX = re.compile(r"^(\(?[a-hj-z]\)|\(?[ivx]{1,4}\)|[ivx]{1,4}\.|\d{1,2}[.)]?|[A-H]\s)\s*", re.I)


@dataclass
class Cell:
    raw: str
    value: float | None
    ocr_fixed: bool = False


@dataclass
class Parsed:
    statement: str | None                     # consolidated / standalone
    unit: str | None
    periods: dict[str, str]                   # current/prev_q/yoy_q -> header text
    rows: dict[str, dict[str, Cell | None]]   # metric -> column -> cell
    labels: dict[str, str]
    notes: list[str] = field(default_factory=list)


# ---------- tokens ----------

def to_number(tok: str, decimal_style: bool) -> Cell | None:
    t = tok.strip().replace("−", "-")
    if t in ("-", "–", "—"):
        return Cell(t, None)
    fixed = False
    if not NUM.match(t) and OCR_DIGITISH.match(t) and sum(ch.isdigit() for ch in t) >= 2:
        t2 = t.translate(OCR_MAP)
        if NUM.match(t2):
            t, fixed = t2, True
    if not NUM.match(t):
        return None
    # OCR sometimes prints the decimal point as a comma: '16,66' on a page of '16.66'-style figures
    if decimal_style and re.search(r",\d{2}\)?$", t) and "." not in t:
        t, fixed = t[::-1].replace(",", ".", 1)[::-1], True
    neg = t.startswith("(") and t.endswith(")") or t.startswith("-")
    v = float(t.strip("()-").replace(",", ""))
    return Cell(tok, -v if neg else v, fixed)


def is_num_token(tok: str) -> bool:
    t = tok.strip()
    return bool(NUM.match(t) or (OCR_DIGITISH.match(t) and sum(c.isdigit() for c in t) >= 2
                                 and NUM.match(t.translate(OCR_MAP))))


# ---------- dates in column headers ----------

def parse_date(text: str) -> date | None:
    s = text.lower().replace(",", " ")
    m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", s)
    if m:
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        return _mk(y, mo, d)
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?[\s./-]*([a-z]{3})[a-z]*[\s./-]*(\d{2,4})", s)
    if m and m[2] in MONTHS:
        return _mk(int(m[3]), MONTHS[m[2]], int(m[1]))
    m = re.search(r"([a-z]{3})[a-z]*\s+(\d{1,2})\s+(\d{4})", s)
    if m and m[1] in MONTHS:
        return _mk(int(m[3]), MONTHS[m[1]], int(m[2]))
    return None


def _mk(y, mo, d):
    y = y + 2000 if y < 100 else y
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def months_between(a: date, b: date) -> int:
    return (a.year - b.year) * 12 + (a.month - b.month)


# ---------- layout ----------

def _lines(words, tol=3.0):
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(l, key=lambda w: w["x0"]) for l in lines]


def _value_tokens(line, label_right: float):
    """Numeric tokens right of the labels; a line of only years is a header, not data."""
    nums = [w for w in line if is_num_token(w["text"]) and w["x0"] > label_right]
    if nums and all(YEAR.match(w["text"].strip("()")) for w in nums):
        return []
    return nums


def _columns(lines, label_right: float):
    """Cluster right edges of numeric tokens into value columns (figures are right-aligned)."""
    edges = []
    for l in lines:
        nums = _value_tokens(l, label_right)
        if len(nums) >= 2:
            edges += [(w["x1"], w["text"], w["x0"]) for w in nums]
    edges.sort()
    clusters: list[list] = []
    for x, t, x0 in edges:
        if clusters and x - clusters[-1][-1][0] <= 12:
            clusters[-1].append((x, t, x0))
        else:
            clusters.append([(x, t, x0)])
    cols = []
    for c in clusters:
        if len(c) < 4:
            continue
        toks = [t for _, t, _ in c]
        small_ints = sum(1 for t in toks if re.fullmatch(r"\d{1,2}", t))
        if small_ints / len(toks) > 0.6:      # row-number / note-number column
            continue
        xs = [x for x, _, _ in c]
        x0s = sorted(x0 for _, _, x0 in c)
        cols.append((min(xs) - 4, max(xs) + 4, (x0s[len(x0s) // 2] + max(xs)) / 2))
    return cols


def parse_page(page) -> Parsed:
    words = page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False)
    text = " ".join(w["text"] for w in words)
    low = text.lower()
    head = low[:900]
    statement = "consolidated" if "consolidated" in head else ("standalone" if "standalone" in head else None)
    um = re.search(r"(crores?|lakhs?|lacs|millions?)", low[:2500])
    unit = None
    if um:
        unit = {"crore": "₹ crore", "lakh": "₹ lakh", "lacs": "₹ lakh", "milli": "₹ million"}[
            next(k for k in ("crore", "lakh", "lacs", "milli") if um[1].startswith(k))]

    lines = _lines(words)
    # where do labels end? use the revenue row as the anchor
    label_right = 0.0
    for l in lines:
        lbl = " ".join(w["text"] for w in l if not is_num_token(w["text"]))
        if re.search(r"revenue\s+from\s+operations", lbl, re.I):
            label_right = max((w["x1"] for w in l if not is_num_token(w["text"]) and
                               w["x0"] < page.width * 0.6), default=0.0)
            break
    cols = _columns(lines, label_right)
    notes: list[str] = []
    if len(cols) < 3:
        return Parsed(statement, unit, {}, {}, {}, ["Could not find at least three value columns."])

    # header text per column: words above the first data row inside the column's x-range
    first_row_top = min((l[0]["top"] for l in lines if sum(
        1 for w in _value_tokens(l, label_right) if any(a <= w["x1"] <= b for a, b, _ in cols)) >= 3), default=0)
    headers, dates = [], []
    zone = [w for w in words if first_row_top - 90 < w["top"] < first_row_top - 1]
    owner = {}
    for w in zone:   # each header word belongs to the one column it sits over
        cx = (w["x0"] + w["x1"]) / 2
        ci = min(range(len(cols)), key=lambda i: abs(cols[i][2] - cx))
        if abs(cols[ci][2] - cx) < 45:
            owner.setdefault(ci, []).append(w)
    for ci in range(len(cols)):
        ws = owner.get(ci, [])
        hl = [" ".join(w["text"] for w in l) for l in _lines(ws)]
        found = None
        for k in range(len(hl) - 1, -1, -1):          # nearest header line first
            for txt in ([hl[k] + " " + hl[k + 1]] if k + 1 < len(hl) else []) + [hl[k]]:
                found = parse_date(txt)
                if found:
                    break
            if found:
                break
        headers.append(" ".join(hl))
        dates.append(found)

    # current quarter = first column with the latest date; prev/yoy = 3 and 12 months before,
    # searched only before the latest date repeats (that repeat starts the half-year block)
    known = [d for d in dates if d]
    if not known:
        return Parsed(statement, unit, {}, {}, {}, ["No dates found in the column headers."])
    latest = max(known)
    cur = dates.index(latest)
    end = next((i for i in range(cur + 1, len(dates)) if dates[i] == latest), len(dates))
    pick = {"current": cur}
    for name, gap in (("prev_q", 3), ("yoy_q", 12)):
        for i in range(cur + 1, end):
            if dates[i] and months_between(latest, dates[i]) == gap:
                pick[name] = i
                break
        else:
            notes.append(f"No column found for {name.replace('_', ' ')}.")
    periods = {k: dates[i].strftime("%d %b %Y") for k, i in pick.items()}

    decimal_style = sum(1 for w in words if re.search(r"\.\d{2}\)?$", w["text"])) > 15
    rows: dict[str, dict[str, Cell | None]] = {}
    labels: dict[str, str] = {}
    prev_label = ""
    for l in lines:
        lbl_words = [w for w in l if w["x1"] <= cols[0][0] - 2 and not
                     (is_num_token(w["text"]) and w["x0"] > label_right - 5 and label_right)]
        label = " ".join(w["text"] for w in lbl_words).strip()
        label = LABEL_PREFIX.sub("", label).strip()
        cells: dict[int, Cell] = {}
        for w in l:
            for ci, (a, b, _) in enumerate(cols):
                if a <= w["x1"] <= b:
                    c = to_number(w["text"], decimal_style)
                    if c is not None:
                        cells[ci] = c
        if not cells:
            prev_label = label
            continue
        candidates = [label, (prev_label + " " + label).strip()]
        prev_label = ""
        for key, pat in ROWS.items():
            if key in rows and not (key == "pat" and re.search(r"including\s+share|after\s+share", " ".join(candidates), re.I)):
                continue
            hit = next((c for c in candidates if pat.search(c)), None)
            if not hit:
                continue
            if key == "pbt" and re.search(r"exceptional|share of", hit, re.I):
                continue
            if key == "pat" and re.search(r"attributable|comprehensive", hit, re.I):
                continue
            rows[key] = {k: cells.get(i) for k, i in pick.items()}
            labels[key] = hit
            break
    if any(c and c.ocr_fixed for r in rows.values() for c in r.values()):
        notes.append("Some figures were corrected for scan errors (e.g. a letter read as a digit).")
    return Parsed(statement, unit, periods, rows, labels, notes)


def find_and_parse(pdf_path, candidate_pages: list[int]) -> tuple[Parsed | None, int | None]:
    """Parse candidate pages; prefer a consolidated statement with a revenue row."""
    import pdfplumber
    best = None
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i in candidate_pages:
            if i >= len(pdf.pages):
                continue
            try:
                p = parse_page(pdf.pages[i])
            except Exception:          # one odd page (rotated, two-up) must not sink the whole card
                continue
            # A statement page has revenue and a profit line and most of the rest; a highlights page
            # with one stray "revenue" figure must not qualify (it produced a wrong number once).
            if "revenue" not in p.rows or not ({"pbt", "pat"} & set(p.rows)) or len(p.rows) < 4:
                continue
            score = len(p.rows) + (3 if p.statement == "consolidated" else 0)
            if best is None or score > best[0]:
                best = (score, p, i + 1)
    return (best[1], best[2]) if best else (None, None)
