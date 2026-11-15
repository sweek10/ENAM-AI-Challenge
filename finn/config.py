"""Paths and constants. FINN_DATA_DIR overrides the data dir (used by tests)."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
IST = ZoneInfo("Asia/Kolkata")

# Indian market closes 15:30 IST; treat today's bar as final only after this.
EOD_FINAL_HOUR_IST = 16

# Announcement windows that include "now" are re-fetched at most this often.
OPEN_WINDOW_TTL_SEC = 30 * 60
# Price refresh is skipped if the same ticker was fetched this recently.
PRICE_REFETCH_SEC = 6 * 3600


def data_dir() -> Path:
    d = Path(os.environ.get("FINN_DATA_DIR", ROOT / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "finn.sqlite"


def cache_dir() -> Path:
    d = data_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot_dir() -> Path:
    d = data_dir() / "snapshots" / "announcements"
    d.mkdir(parents=True, exist_ok=True)
    return d
