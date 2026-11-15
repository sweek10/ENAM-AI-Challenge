"""Offline enrichment: reads filing PDFs once and stores the result.

Runs in scripts.refresh (or a cron job), never during a page view, so the app
stays fast and the deployed copy never needs to reach the exchanges.

Order matters: page-1 classification can change a filing's category, which
decides whether it gets a results or management card.
"""
import json
import logging
import time

from . import cards, classify, db, pdfs
from .announcements import Announcement

log = logging.getLogger(__name__)
SCHEMA = """CREATE TABLE IF NOT EXISTS enrich (
    id   TEXT NOT NULL,       -- announcement id
    kind TEXT NOT NULL,       -- page1 | results | mgmt
    data TEXT NOT NULL,       -- JSON; {"error": ...} on failure
    ts   REAL NOT NULL,
    PRIMARY KEY (id, kind))"""
RETRY_ERRORS_AFTER = 6 * 3600


def get(ids: list[str], kind: str) -> dict[str, dict]:
    if not ids:
        return {}
    with db.connect() as conn:
        conn.execute(SCHEMA)
        q = ",".join("?" * len(ids))
        return {r["id"]: json.loads(r["data"]) for r in
                conn.execute(f"SELECT id, data FROM enrich WHERE kind=? AND id IN ({q})", [kind, *ids])}


def _have(conn, aid: str, kind: str) -> bool:
    r = conn.execute("SELECT data, ts FROM enrich WHERE id=? AND kind=?", (aid, kind)).fetchone()
    if not r:
        return False
    return not ('"error"' in r["data"] and time.time() - r["ts"] > RETRY_ERRORS_AFTER)


def _put(aid: str, kind: str, data: dict) -> None:
    with db.connect() as conn:
        conn.execute(SCHEMA)
        conn.execute("INSERT OR REPLACE INTO enrich(id,kind,data,ts) VALUES (?,?,?,?)",
                     (aid, kind, json.dumps(data), time.time()))


def needs_results(c: classify.Classified) -> bool:
    return c.category == "results" and c.reason.split(" · ")[0] not in ("results ad", "results date", "results deck")


def needs_mgmt(c: classify.Classified) -> bool:
    return c.category == "management_change" and c.tier in ("top", "notable")


def run(items: list[Announcement], client, prefs=None) -> dict:
    """Enrich what needs it. Returns counts per kind for the refresh log."""
    stats = {"page1": 0, "results": 0, "mgmt": 0, "errors": 0, "skipped_cached": 0}
    with db.connect() as conn:
        conn.execute(SCHEMA)
        todo_p1 = [a for a in items if a.pdf_url and cards.is_vague(a) and not _have(conn, a.id, "page1")]
    for a in todo_p1:
        try:
            _put(a.id, "page1", cards.classify_page1(pdfs.fetch(a.pdf_url), client))
            stats["page1"] += 1
        except Exception as e:
            log.warning("page1 %s: %s", a.id, e)
            _put(a.id, "page1", {"error": str(e)})
            stats["errors"] += 1

    classified, _ = classify.classify(items, prefs=prefs, client=client)
    for c in classified:
        a = c.ann
        if not a.pdf_url:
            continue
        for kind, wanted, fn in (("results", needs_results(c), cards.extract_results),
                                 ("mgmt", needs_mgmt(c), cards.extract_mgmt)):
            if not wanted:
                continue
            with db.connect() as conn:
                conn.execute(SCHEMA)
                if _have(conn, a.id, kind):
                    stats["skipped_cached"] += 1
                    continue
            if client is None:
                continue
            try:
                card, meta = fn(pdfs.fetch(a.pdf_url), client)
                _put(a.id, kind, {"card": card.to_json(), "meta": meta})
                stats[kind] += 1
            except Exception as e:
                log.warning("%s %s: %s", kind, a.id, e)
                _put(a.id, kind, {"error": str(e)})
                stats["errors"] += 1
    return stats
