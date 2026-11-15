"""Deterministic parser for director / KMP change disclosures. No LLM.

Two shapes cover most filings:
  1. SEBI's annexure table: Name / Reason for change / Date / Brief profile
  2. A letter sentence: "Mr. X has resigned from the post of Y w.e.f. Z"
Background is a verbatim excerpt of the filing's brief profile, never a summary.
"""
import re
from dataclasses import dataclass, field

HON = r"(?:Mr|Ms|Mrs|Dr|Shri|Smt)\.?"
NAME = r"[A-Z][\w’'.-]*(?:[ \t]+[A-Z][\w’'.-]*){0,4}"
BOILER = re.compile(r"viz\.?\s*appointment,?\s*re-?\s*appointment,?\s*resignation,?\s*removal,?\s*death\s*or\s*otherwise", re.I)
STOP = r"(?=\s+for a period|\s+w\.?e\.?f|\s+with effect|\s+of the (?:Company|Bank)|[.;]\s|[.;]$|\s+and\s+(?:a|an|the|has|he|she|shall)\b|$)"
DATE = (r"(\d{1,2}(?:st|nd|rd|th)?\s*[A-Za-z]{3,9},?\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}|"
        r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4})")


@dataclass
class Person:
    name: str
    role: str | None = None
    change: str | None = None
    effective: str | None = None
    background: str | None = None
    prior_roles: list[str] = field(default_factory=list)
    reason: str | None = None
    page: int | None = None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip(" ,.;:-–")


def _change(text: str) -> str | None:
    t = text.lower()
    for word, label in (("re-appoint", "re-appointed"), ("reappoint", "re-appointed"), ("appoint", "appointed"),
                        ("resign", "resigned"), ("superannuat", "retired"), ("retire", "retired"),
                        ("cessation", "ceased"), ("ceased", "ceased"), ("stepped down", "resigned"),
                        ("demise", "ceased"), ("death", "ceased")):
        if word in t:
            return label
    return None


def _role(text: str) -> str | None:
    t = _norm(text)
    m = re.search(rf"in the capacity of\s+(?:an?\s+|the\s+)?(.+?){STOP}", t, re.I)
    if not m:
        m = re.search(rf"(?:designated as|post of|to be the|\bas)\s+(?:an?\s+|the\s+)?(.+?){STOP}", t, re.I)
    if not m:
        return None
    role = _norm(m[1])
    role = re.sub(r"\s+(?:and\s+)?a\s+Senior Management Personnel.*$", "", role, flags=re.I)
    return role[:110]


INFORMATIVE = re.compile(r"experience|years|graduat|alumn|degree|mba|b\.?tech|chartered|engineer|iit|iim|"
                         r"university|served|previously|prior to|before joining|joined|worked|head(ed)?|led|career", re.I)


def _excerpt(profile: str, max_chars: int = 340) -> str | None:
    """Two verbatim sentences from the profile, preferring experience/education/prior roles."""
    p = _norm(re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", profile))
    if len(p) < 20:
        return None
    guarded = re.sub(r"\b(Mr|Ms|Mrs|Dr|Shri|Smt)\.", r"\1§", p)
    sentences = [x.replace("§", ".") for x in re.split(r"(?<=[.!?])\s+(?=[A-Z])", guarded) if len(x) > 15]
    ranked = sorted(range(len(sentences)), key=lambda i: (-len(INFORMATIVE.findall(sentences[i])), i))
    keep = sorted(ranked[:2])
    out = " ".join(sentences[i] for i in keep)
    return out if len(out) <= max_chars else out[:max_chars].rsplit(" ", 1)[0] + "…"


def _tables(pages: list[str]) -> list[Person]:
    people = []
    for pi, text in enumerate(pages):
        if not re.search(r"reason\s+for\s+change|brief\s+profile", text, re.I):
            continue
        text = BOILER.sub(" ", text)
        starts = [m for m in re.finditer(rf"\bName(?:\s+of\s+(?:the\s+)?[A-Za-z /]+?)?\s+({HON}\s*{NAME})", text)]
        for k, m in enumerate(starts):
            block = text[m.start(): starts[k + 1].start() if k + 1 < len(starts) else len(text)]
            name = _norm(re.sub(rf"^{HON}\s*", "", re.sub(r"\(DIN.*", "", m[1])))
            reason_m = re.search(r"Reason\s+for\s+change(?:\s+viz\.?[^\n]*?(?:otherwise|removal)[^\n]*)?\s+(.+?)"
                                 r"(?=\n?\s*\d?\.?\s*Date\s+of|Brief\s+Profile|$)", block, re.I | re.S)
            reason = _norm(reason_m[1]) if reason_m else ""
            date_seg = re.search(r"Date\s+of(.+?)(?=Brief\s+Profile|Disclosure|$)", block, re.I | re.S)
            eff = re.search(DATE, date_seg[1]) if date_seg else None
            prof = re.search(r"Brief\s+Profile(?:\s*\(in case of appointment\))?\s*(.+?)"
                             r"(?=Disclosure\s+of\s+relationship|\n\s*\d{1,2}\.\s+[A-Z][a-z]+\s+of|$)", block, re.I | re.S)
            people.append(Person(
                name=name, role=_role(reason), change=_change(reason), effective=_norm(eff[1]) if eff else None,
                background=_excerpt(prof[1]) if prof else None, page=pi + 1))
    return people


def _letters(pages: list[str]) -> list[Person]:
    people = []
    verbs = r"(resigned|superannuated|retired|ceased|stepped\s+down|been\s+re-?appointed|been\s+appointed)"
    for pi, raw in enumerate(pages[:3]):
        text = _norm(raw)
        for m in re.finditer(rf"{HON}\s*({NAME})\s+(?:\(DIN[^)]*\)\s+)?has\s+{verbs}(.{{0,260}})", text):
            tail = m[3]
            eff = re.search(rf"(?:w\.?e\.?f\.?|with effect from)\s+(?:the\s+)?(?:close of business hours\s+(?:on|of)\s+)?{DATE}", tail, re.I)
            why = re.search(r"(?:pursuant to|due to|owing to|on account of)\s+([^.;]{5,120})", text[max(0, m.start() - 160): m.end()], re.I)
            people.append(Person(name=_norm(m[1]), role=_role(tail), change=_change(m[2]),
                                 effective=eff[1] if eff else None,
                                 reason=re.sub(rf",?\s*{HON}$", "", _norm(why[1])) if why and _change(m[2]) in ("resigned", "ceased") else None,
                                 page=pi + 1))
        for m in re.finditer(rf"(?:appointed|appointment of)\s+{HON}\s*({NAME})\s+(?:\(DIN[^)]*\)\s+)?(?:as|to)\s+(.{{0,160}})", text):
            people.append(Person(name=_norm(m[1]), role=_role("as " + m[2]), change="appointed", page=pi + 1))
    return people


def _key(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower().split()[-1]) if name else ""


def parse(pages: list[str]) -> list[Person]:
    """All people whose status changes in the filing, table data taking precedence over letter text."""
    merged: dict[str, Person] = {}
    for p in _letters(pages) + _tables(pages):    # tables second: they overwrite letter guesses
        k = _key(p.name)
        if not k:
            continue
        if k in merged:
            old = merged[k]
            for f in ("role", "change", "effective", "background", "reason"):
                if getattr(p, f):
                    setattr(old, f, getattr(p, f))
            if len(p.name) > len(old.name):
                old.name = p.name
            old.page = p.page if p.background else old.page
        else:
            merged[k] = p
    return list(merged.values())
