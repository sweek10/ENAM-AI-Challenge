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

# Model routing, one place. Override with env vars without touching code.
#   classify: bulk, short prompts -> cheapest capable model
#   extract:  reading results tables / appointment letters from page images -> strongest model
CLASSIFY_MODEL = os.environ.get("FINN_CLASSIFY_MODEL", "claude-haiku-5-5")
EXTRACT_MODEL = os.environ.get("FINN_EXTRACT_MODEL", "claude-opus-5-5")

MODEL_NAMES = {"claude-haiku-5-5": "Haiku 5.5", "claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5"}


def model_name(model_id: str) -> str:
    return MODEL_NAMES.get(model_id, model_id)


# USD per million tokens (input, output), Claude API list prices, Nov 2026.
# Haiku 5.5 rate is for prompts <=100K tokens (our classify batches are ~2-3K).
MODEL_PRICES_PER_MTOK = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
