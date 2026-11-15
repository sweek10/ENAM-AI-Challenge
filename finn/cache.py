"""Disk cache for raw API responses: one JSON file per key.

ttl=None means the entry never expires (closed time windows can't change).
Expired entries are kept on disk so they can serve as a last-resort fallback.
"""
import hashlib
import json
import time
from pathlib import Path

from . import config


def _path(namespace: str, key: str) -> Path:
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    d = config.cache_dir() / namespace
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{h}.json"


def read(namespace: str, key: str, ttl: float | None):
    """Return (body, is_fresh) or (None, False) if absent."""
    p = _path(namespace, key)
    if not p.exists():
        return None, False
    entry = json.loads(p.read_text())
    fresh = ttl is None or (time.time() - entry["fetched_at"]) < ttl
    return entry["body"], fresh


def write(namespace: str, key: str, body) -> None:
    p = _path(namespace, key)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"key": key, "fetched_at": time.time(), "body": body}))
    tmp.replace(p)
