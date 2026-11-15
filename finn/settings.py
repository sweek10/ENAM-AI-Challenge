"""User-tunable thresholds and category preferences (single-user, stored in SQLite)."""
import json
from dataclasses import asdict, dataclass, field

from . import db
from .classify import Prefs

DEFAULT_WATCH = ["results", "M&A", "management_change", "pledging"]


@dataclass
class Settings:
    price_move_pct: float = 4.0       # |day move| at or above this -> alert
    volume_multiple: float = 2.0      # volume >= N x average -> alert
    volume_lookback: int = 20         # trading days in the average
    watch: list[str] = field(default_factory=lambda: list(DEFAULT_WATCH))
    mute: list[str] = field(default_factory=list)

    def prefs(self) -> Prefs:
        return Prefs(care=frozenset(self.watch), mute=frozenset(self.mute))


def _ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT NOT NULL)")


def load() -> Settings:
    with db.connect() as conn:
        _ensure(conn)
        row = conn.execute("SELECT v FROM settings WHERE k='user'").fetchone()
    if not row:
        return Settings()
    data = json.loads(row["v"])
    known = Settings.__dataclass_fields__
    return Settings(**{k: v for k, v in data.items() if k in known})


def save(s: Settings) -> None:
    s.price_move_pct = max(0.5, min(20.0, float(s.price_move_pct)))
    s.volume_multiple = max(1.1, min(10.0, float(s.volume_multiple)))
    s.volume_lookback = max(5, min(60, int(s.volume_lookback)))
    s.mute = [c for c in s.mute if c not in s.watch]
    with db.connect() as conn:
        _ensure(conn)
        conn.execute("INSERT OR REPLACE INTO settings(k,v) VALUES ('user',?)", (json.dumps(asdict(s)),))
