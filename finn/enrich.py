"""Offline enrichment: reads filing PDFs once and stores the result. No LLM.

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
RETRY_ERRORS_AFTER = 6 * 3600
PARSER_VERSION = "p5"     # bump to re-parse stored filings after a parser change


def get(ids: list[str], kind: str) -> dict[str, dict]:
    if not ids:
        return {}
    with db.connect() as conn:
        conn.execute(classify.ENRICH_SCHEMA)
        q = ",".join("?" * len(ids))
        return {r["id"]: json.loads(r["data"]) for r in
                conn.execute(f"SELECT id, data FROM enrich WHERE kind=? AND id IN ({q})", [kind, *ids])}


def _have(aid: str, kind: str, retry_errors: bool = False) -> bool:
    with db.connect() as conn:
        conn.execute(classify.ENRICH_SCHEMA)
        r = conn.execute("SELECT data, ts FROM enrich WHERE id=? AND kind=?", (aid, kind)).fetchone()
    if not r:
        return False
    d = json.loads(r["data"])
    if d.get("parser") != PARSER_VERSION:
        return False
    if "error" in d:
        return not (retry_errors or time.time() - r["ts"] > RETRY_ERRORS_AFTER)
    return True


def _put(aid: str, kind: str, data: dict) -> None:
    with db.connect() as conn:
        conn.execute(classify.ENRICH_SCHEMA)
        conn.execute("INSERT OR REPLACE INTO enrich(id,kind,data,ts) VALUES (?,?,?,?)",
                     (aid, kind, json.dumps({**data, "parser": PARSER_VERSION}), time.time()))


def needs_results(c: classify.Classified) -> bool:
    return c.category == "results" and c.reason.split(" · ")[0] not in ("results ad", "results date", "results deck")


def needs_mgmt(c: classify.Classified) -> bool:
    if c.category == "management_change" and c.tier in ("top", "notable"):
        return True
    return c.method == "pdf:page1" and "management_change" in c.rule   # board outcome that also changes people


def run(items: list[Announcement], prefs=None, retry_errors: bool = False) -> dict:
    """Enrich what needs it. Returns counts per kind for the refresh log."""
    stats = {"page1": 0, "results": 0, "mgmt": 0, "errors": 0, "skipped_cached": 0}
    for a in items:
        if not (a.pdf_url and cards.is_vague(a)):
            continue
        if _have(a.id, "page1", retry_errors):
            stats["skipped_cached"] += 1
            continue
        try:
            _put(a.id, "page1", cards.classify_page1(pdfs.fetch(a.pdf_url)))
            stats["page1"] += 1
        except Exception as e:
            log.warning("page1 %s: %s", a.id, e)
            _put(a.id, "page1", {"error": str(e)})
            stats["errors"] += 1

    classified, _ = classify.classify(items, prefs=prefs)
    for c in classified:
        a = c.ann
        if not a.pdf_url:
            continue
        for kind, wanted, fn in (("results", needs_results(c), cards.extract_results),
                                 ("mgmt", needs_mgmt(c), cards.extract_mgmt)):
            if not wanted:
                continue
            if _have(a.id, kind, retry_errors):
                stats["skipped_cached"] += 1
                continue
            try:
                card, meta = fn(pdfs.fetch(a.pdf_url))
                _put(a.id, kind, {"card": card.to_json(), "meta": meta})
                stats[kind] += 1
            except Exception as e:
                log.warning("%s %s: %s", kind, a.id, e)
                _put(a.id, kind, {"error": str(e)})
                stats["errors"] += 1
    return stats
