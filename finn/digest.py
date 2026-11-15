"""Windowed digest: every filing in the window, classified, ranked, grouped and folded."""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import alerts, cards, classify, config, db, enrich
from .announcements import Announcement, load_snapshot
from .portfolio import BY_SYMBOL, PORTFOLIO
from .settings import Settings

WINDOWS = {"24h": 24, "3d": 72, "7d": 168, "30d": 720}

LABELS = {
    "results": "Results", "M&A": "M&A", "fund_raise": "Fund raise", "management_change": "Management",
    "litigation": "Legal / regulatory", "insider_trading": "Insider deal", "earnings_call": "Earnings call",
    "investor_meetings": "Investor meet", "credit_rating": "Credit rating", "dividend": "Dividend",
    "bonus_split": "Bonus / split", "capex": "Capex", "press_release": "News", "postal_ballot": "Postal ballot",
    "agm_egm": "AGM / EGM", "pledging": "Pledge", "unclassified": "Paperwork",
}

# Exchange headlines are padded with boilerplate; strip it so the substance leads.
_BOILER = [
    r"^.{0,80}?\b(has|have)\s+informed\s+(the\s+)?(bse|exchange|nse)\s+(that|about|regarding)?\s*",
    r"^(intimation|disclosure|announcement)\s+(under|pursuant to)\s+reg(ulation)?\.?\s*30[^-–:]*[-–:]\s*",
    r"\s*under\s+reg(ulation)?\.?\s*30\s+of\s+(the\s+)?sebi\s*\(lodr\)\s*(regulations)?,?\s*(2015)?",
    r"^(the\s+)?(company|board)\s+(has|have)\s+",
    r"^outcome\s+of\s+(the\s+)?board\s+meeting(\s+held\s+on\s+[^-–:]+)?\s*[-–:]\s*(?=\S)",
]

# Some ranking reasons are a more precise label than the category.
REASON_LABELS = {"results date": "Results date", "results ad": "Results ad", "results deck": "Results deck",
                 "call scheduled": "Call scheduled", "routine debt issue": "Debt issue",
                 "rating reaffirmed": "Rating reaffirmed", "rating cut/watch": "Rating cut",
                 "internal restructuring": "Subsidiary"}


def clean_headline(h: str, symbol: str) -> str:
    s = (h or "").strip()
    name = BY_SYMBOL[symbol].name if symbol in BY_SYMBOL else ""
    if name:
        s = re.sub(rf"^{re.escape(name.split(' (')[0])}\s*(ltd|limited)?\.?\s*[-–:]?\s*(\d{{6}}\s*[-–]\s*)?", "", s, flags=re.I)
    for p in _BOILER:
        s = re.sub(p, "", s, flags=re.I)
    s = re.sub(r"\s{2,}", " ", s).strip(" -–:.")
    return (s[:1].upper() + s[1:]) if s else (h or "")


@dataclass
class Item:
    c: classify.Classified
    title: str
    label: str
    watched: bool
    related: list["Item"] = field(default_factory=list)
    first: bool = False
    results: "cards.ResultsCard | None" = None
    mgmt: "cards.MgmtCard | None" = None
    card_error: str = ""
    p1_summary: str = ""

    @property
    def why(self) -> str:
        """The ranking reason, only when it says more than the category tag."""
        r = self.c.reason.replace(" · on watchlist", "")
        if r in (self.c.category, self.label) or r in REASON_LABELS or r.replace("_", " ") == self.label.lower():
            return ""
        return r

    @property
    def a(self) -> Announcement:
        return self.c.ann


@dataclass
class Digest:
    window: str
    start: datetime
    end: datetime
    need_you: list[Item]
    notable: list[Item]
    routine: dict[str, list[Item]]   # symbol -> items
    hidden: list[Item]
    alerts: list[alerts.Alert]
    moves: dict[str, alerts.Move]
    total: int
    stats: dict
    source: str                      # "db" | "snapshot" | "empty"
    demo: bool


def _load_window(start: datetime, end: datetime) -> tuple[list[Announcement], str]:
    lo, hi = start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S")
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM announcements WHERE dt BETWEEN ? AND ? ORDER BY dt DESC", (lo, hi))]
        any_rows = conn.execute("SELECT 1 FROM announcements LIMIT 1").fetchone()
    if any_rows:
        return [Announcement(**r) for r in rows], "db"
    items = [a for s in PORTFOLIO for a in load_snapshot(s.symbol) if lo <= a.dt <= hi]
    return items, ("snapshot" if items else "empty")


def _group(items: list[Item]) -> list[Item]:
    """Same company, same day -> one card led by its most important filing."""
    buckets: dict[tuple, list[Item]] = defaultdict(list)
    for it in items:
        buckets[(it.a.symbol, it.a.dt[:10])].append(it)
    out = []
    for group in buckets.values():
        group.sort(key=lambda i: (i.c.score, i.results is not None or i.mgmt is not None), reverse=True)
        lead = group[0]
        lead.related = group[1:]
        out.append(lead)
    return sorted(out, key=lambda i: (i.c.score, i.a.dt), reverse=True)


def build(window: str = "24h", settings: Settings | None = None, now: datetime | None = None,
          llm_client="default") -> Digest:
    settings = settings or Settings()
    hours = WINDOWS.get(window, 24)
    end = (now or datetime.now(config.IST)).replace(tzinfo=None)
    start = end - timedelta(hours=hours)

    anns, source = _load_window(start, end)
    res, stats = classify.classify(anns, prefs=settings.prefs(), client=llm_client)
    items = [Item(c, clean_headline(c.ann.headline, c.ann.symbol),
                  REASON_LABELS.get(c.reason.replace(" · on watchlist", ""), LABELS.get(c.category, c.category)),
                  c.category in settings.watch) for c in res]

    ids = [i.a.id for i in items]
    res_cards, mgmt_cards = enrich.get(ids, "results"), enrich.get(ids, "mgmt")
    p1 = enrich.get(ids, "page1")
    for i in items:
        r, m = res_cards.get(i.a.id), mgmt_cards.get(i.a.id)
        i.p1_summary = (p1.get(i.a.id) or {}).get("summary") or ""
        if r:
            i.results = cards.ResultsCard.from_json(r["card"]) if "card" in r else None
            i.card_error = r.get("error", "")
        if m:
            i.mgmt = cards.MgmtCard.from_json(m["card"]) if "card" in m else None
            i.card_error = i.card_error or m.get("error", "")

    need = _group([i for i in items if i.c.tier == "top"])
    for i in need[:3]:   # the highlighter marks what to read first, nothing else
        i.first = True
    notable = _group([i for i in items if i.c.tier == "notable"])
    routine: dict[str, list[Item]] = defaultdict(list)
    for i in items:
        if i.c.tier == "routine":
            routine[i.a.symbol].append(i)
    hidden = [i for i in items if i.c.tier == "hidden"]

    moves = alerts.latest_moves(settings.volume_lookback)
    return Digest(window, start, end, need, notable, dict(sorted(routine.items(), key=lambda kv: -len(kv[1]))),
                  hidden, alerts.check(settings, moves), moves, len(items), stats, source,
                  demo=(config.data_dir() / "DEMO").exists())
