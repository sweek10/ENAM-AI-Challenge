"""Pull side: what management promised on the last four calls vs what the results show.

Sources are primary only: concall transcripts the company filed, and the quarterly
results table on screener.in. Every verdict is computed in code and says how.
"""
import json
import logging
import re
from datetime import datetime

from . import cache, config, guidance, pdfs, screener
from .guidance import Guidance

log = logging.getLogger(__name__)
GUIDANCE_VERSION = "g2"   # bump whenever finn/guidance.py rules change
CALLS = 4
TOL = 0.5                 # percentage points of slack before calling a miss
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
METRIC_LABEL = {"revenue_growth": "Revenue growth", "margin": "Operating margin", "profit_growth": "Profit growth",
                "capex": "Capex", "other": "Other"}
VERDICT_ORDER = {"missed": 0, "behind": 1, "delivered": 2, "on track": 3, "too early": 4, "unclear": 5}


# ---------- quarters ----------

def fy_of_quarter(label: str) -> tuple[int, int] | None:
    """'Sep 2026' -> (2027, 2): FY27 Q2."""
    m = re.match(r"([A-Za-z]{3})\w*\s+(\d{4})", label)
    if not m or m[1].title() not in ("Mar", "Jun", "Sep", "Dec"):
        return None
    mon, y = m[1].title(), int(m[2])
    q = {"Jun": 1, "Sep": 2, "Dec": 3, "Mar": 4}[mon]
    return (y if mon == "Mar" else y + 1), q


def period_quarters(period: str) -> list[tuple[int, int]]:
    m = re.match(r"(Q[1-4]|H[12])?FY(\d{2})$", period or "")
    if not m:
        return []
    fy = 2000 + int(m[2])
    if not m[1]:
        return [(fy, q) for q in (1, 2, 3, 4)]
    if m[1].startswith("Q"):
        return [(fy, int(m[1][1]))]
    return [(fy, 1), (fy, 2)] if m[1] == "H1" else [(fy, 3), (fy, 4)]


class Actuals:
    def __init__(self, c: screener.Company):
        self.c = c
        sales = c.rows.get("Sales") or c.rows.get("Revenue") or []
        self.idx = {}
        for i, lab in enumerate(c.quarters):
            k = fy_of_quarter(lab)
            if k:
                self.idx[k] = i
        self.sales = sales
        self.op = c.rows.get("Operating Profit") or []
        self.pat = c.rows.get("Net Profit") or []
        self.is_bank = "Sales" not in c.rows

    def _get(self, row, k):
        i = self.idx.get(k)
        return row[i] if i is not None and i < len(row) else None

    def label(self, k):
        i = self.idx.get(k)
        return self.c.quarters[i] if i is not None else f"Q{k[1]}FY{k[0] % 100:02d}"

    def measure(self, metric: str, qs: list[tuple[int, int]]):
        """(value %, quarters used, how) for growth vs the same quarters a year earlier, or margin."""
        have = [k for k in qs if self._get(self.sales, k) is not None]
        if not have:
            return None, [], ""
        prev = [(fy - 1, q) for fy, q in have]
        names = ", ".join(self.label(k) for k in have)
        if metric == "margin":
            if self.is_bank or not self.op:
                return None, have, "Operating margin isn't reported in this format"
            s, o = (sum(self._get(r, k) or 0 for k in have) for r in (self.sales, self.op))
            return (o / s * 100 if s else None), have, f"operating profit ÷ sales, {names}"
        row = self.pat if metric == "profit_growth" else self.sales
        cur = [self._get(row, k) for k in have]
        base = [self._get(row, k) for k in prev]
        if None in cur or None in base or not sum(base):
            return None, have, "year-ago quarters missing"
        what = "net profit" if metric == "profit_growth" else "sales"
        return (sum(cur) / sum(base) - 1) * 100, have, f"{what}, {names} vs a year earlier"

    def table(self, n: int = 6) -> list[dict]:
        out = []
        for i in range(max(0, len(self.c.quarters) - n), len(self.c.quarters)):
            lab = self.c.quarters[i]
            k = fy_of_quarter(lab)
            prev = self.idx.get((k[0] - 1, k[1])) if k else None
            s = self.sales[i] if i < len(self.sales) else None
            ps = self.sales[prev] if prev is not None and prev < len(self.sales) else None
            o = self.op[i] if i < len(self.op) else None
            p = self.pat[i] if i < len(self.pat) else None
            out.append({"q": lab, "sales": s, "yoy": ((s / ps - 1) * 100) if s and ps else None,
                        "opm": (o / s * 100) if o is not None and s else None, "pat": p})
        return out


# ---------- verdicts ----------

def judge(g: Guidance, act: Actuals) -> dict:
    """Returns {verdict, actual, how, note}. 'unclear' whenever the code can't judge honestly."""
    base = {"actual": None, "how": "", "quarters": 0, "of": 0}
    if g.metric in ("capex", "other"):
        return {**base, "verdict": "unclear", "note": f"{METRIC_LABEL[g.metric]} isn't in the quarterly results."}
    if g.unit != "%":
        return {**base, "verdict": "unclear", "note": "Guided as an amount, not a rate; nothing to compare."}
    if not g.period:
        return {**base, "verdict": "unclear", "note": "No period stated."}
    qs = period_quarters(g.period)
    if not qs:
        return {**base, "verdict": "unclear", "note": f"Can't map “{g.period}” to quarters."}
    val, have, how = act.measure(g.metric, qs)
    res = {**base, "actual": val, "how": how, "quarters": len(have), "of": len(qs)}
    if not have:
        last = max(act.idx) if act.idx else None
        if last and min(qs) > last:
            return {**res, "verdict": "too early", "note": "No results reported for this period yet."}
        return {**res, "verdict": "unclear", "note": "Results for this period aren't in the table."}
    if val is None:
        return {**res, "verdict": "unclear", "note": how or "Couldn't compute the actual."}
    if g.basis == "ebit":
        return {**res, "verdict": "unclear",
                "note": "Guided on EBIT margin; the results table shows operating margin before depreciation, so they don't compare."}
    if g.basis == "cc":
        return {**res, "verdict": "unclear",
                "note": "Guided in constant currency or dollars; filings report rupees, so the rupee figure is shown for context only."}
    lo, hi = g.low, g.high
    slack = 1.0 if (g.metric == "margin" and lo == hi) else TOL
    ok = val >= lo - slack
    final = len(have) == len(qs)
    verdict = ("delivered" if ok else "missed") if final else ("on track" if ok else "behind")
    note = ""
    if ok and hi is not None and val > hi + slack:
        note = "Above the guided range."
    return {**res, "verdict": verdict, "note": note}


def _mid(g: Guidance) -> float | None:
    if g.low is None:
        return None
    return g.low if g.high is None else (g.low + g.high) / 2


def target_text(g: Guidance) -> str:
    if g.low is None:
        return "–"
    fmt = lambda v: f"{v:g}"
    if g.unit == "%":
        if g.high is None:
            return f"≥{fmt(g.low)}%"
        return f"{fmt(g.low)}%" if g.low == g.high else f"{fmt(g.low)}–{fmt(g.high)}%"
    return f"{fmt(g.low)} {g.unit}"


# ---------- assembly ----------

def _call_guidance(cc: screener.Concall) -> tuple[list[Guidance], str | None]:
    key = f"{GUIDANCE_VERSION}|{cc.transcript}"
    body, _ = cache.read("guidance", key, None)
    if body is not None:
        return [Guidance(**g) for g in body["items"]], body.get("error")
    try:
        pdf = pdfs.fetch(cc.transcript)
        text = "\n".join(pdfs.page_texts(pdf, 60))
        if len(text.strip()) < 500:
            raise RuntimeError("transcript has no readable text")
        items, err = guidance.extract(text, cc.label, cc.transcript), None
    except Exception as e:
        items, err = [], str(e)[:160]
    if err is None or "has no readable text" in err:      # don't cache network failures
        cache.write("guidance", key, {"items": [g.to_json() for g in items], "error": err})
    return items, err


def _snapshot_path(t: str):
    d = config.data_dir() / "research"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{t}.json"


def build(ticker: str, calls: int = CALLS) -> dict:
    """Raises screener.NotFound for unknown tickers. Falls back to the saved snapshot if offline."""
    t = screener.normalise_ticker(ticker)
    try:
        company = screener.fetch(t)
    except screener.NotFound:
        raise
    except Exception as e:
        snap = _snapshot_path(t)
        if snap.exists():
            data = json.loads(snap.read_text())
            data["stale"] = f"screener.in unreachable ({str(e)[:80]}); showing the copy saved {data['built_at'][:16]}."
            return data
        raise
    act = Actuals(company)
    used = [cc for cc in company.concalls if cc.transcript.lower().endswith(".pdf")][:calls]
    statements, problems = [], []
    for cc in used:
        items, err = _call_guidance(cc)
        if err:
            problems.append(f"{cc.label}: {err}")
        statements += items

    groups: dict[tuple, list[Guidance]] = {}
    for g in statements:
        groups.setdefault((g.metric, g.period, g.basis), []).append(g)
    call_rank = {cc.label: i for i, cc in enumerate(reversed(used))}     # oldest first
    out_groups = []
    for (metric, period, basis), gs in groups.items():
        best: dict[str, Guidance] = {}
        for g in gs:           # one statement per call: the most specific one
            spec = (g.score, g.high is not None and g.low != g.high)
            if g.call not in best or spec > (best[g.call].score, best[g.call].high is not None and best[g.call].low != best[g.call].high):
                best[g.call] = g
        gs = sorted(best.values(), key=lambda g: call_rank.get(g.call, 0))
        trail, prev = [], None
        for g in gs:
            m = _mid(g)
            change = None
            if prev is not None and m is not None:
                change = "raised" if m > prev + 0.25 else ("cut" if m < prev - 0.25 else "kept")
            trail.append({**g.to_json(), "target": target_text(g), "change": change})
            prev = m if m is not None else prev
        latest = gs[-1]
        verdict = judge(latest, act)
        out_groups.append({"metric": metric, "label": METRIC_LABEL[metric], "period": period, "basis": basis,
                           "target": target_text(latest), "trail": trail, **verdict})
    metric_order = {"revenue_growth": 0, "margin": 1, "profit_growth": 2, "capex": 3, "other": 4}
    out_groups.sort(key=lambda g: (g["verdict"] == "unclear", metric_order[g["metric"]], -(g["of"] or 0),
                                   VERDICT_ORDER[g["verdict"]]))
    judged = [g for g in out_groups if g["verdict"] != "unclear"]
    data = {
        "ticker": t, "name": company.name, "screener_url": company.url,
        "built_at": datetime.now(config.IST).isoformat(timespec="minutes"),
        "calls": [{"label": cc.label, "url": cc.transcript} for cc in used],
        "groups": out_groups, "judged": len(judged), "unclear": len(out_groups) - len(judged),
        "headline": headline([g for g in judged if g["verdict"] != "too early"]), "quarters": act.table(), "problems": problems, "stale": None,
        "no_transcripts": not used,
    }
    try:
        _snapshot_path(t).write_text(json.dumps(data, indent=1, default=str))
    except OSError:
        pass
    return data


def headline(judged: list[dict]) -> str:
    """One line an investor can act on, built from the most important judged promise."""
    if not judged:
        return ""
    g = judged[0]
    so_far = "" if g["quarters"] == g["of"] else f" {g['quarters']} of {g['of']} quarters in,"
    verb = {"delivered": "delivered", "missed": "missed", "on track": "on track", "behind": "behind",
            "too early": "too early to tell"}[g["verdict"]]
    act = "" if g["actual"] is None else f" it's at {g['actual']:.1f}%."
    return f"Promised {g['target']} {g['label'].lower()} for {g['period']}.{so_far}{act} {verb.capitalize()}."
