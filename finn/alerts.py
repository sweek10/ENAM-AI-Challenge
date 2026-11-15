"""End-of-day threshold alerts: unusual price moves and volume spikes."""
from dataclasses import dataclass

from . import db
from .portfolio import PORTFOLIO
from .settings import Settings


@dataclass
class Move:
    symbol: str
    date: str
    close: float
    change_pct: float
    vol_multiple: float | None    # today's volume / lookback average
    spark: list[float]            # recent closes, oldest first


@dataclass
class Alert:
    move: Move
    kinds: list[str]              # "price" and/or "volume"
    severity: float               # how far past the threshold, for ordering


def latest_moves(lookback: int = 20, symbols=None) -> dict[str, Move]:
    symbols = symbols or [s.symbol for s in PORTFOLIO]
    out = {}
    with db.connect() as conn:
        for sym in symbols:
            rows = conn.execute(
                "SELECT date, close, volume FROM prices WHERE symbol=? ORDER BY date DESC LIMIT ?",
                (sym, lookback + 1)).fetchall()
            if len(rows) < 2:
                continue
            today, prev = rows[0], rows[1]
            hist = [r["volume"] for r in rows[1:] if r["volume"]]
            avg = sum(hist) / len(hist) if hist else 0
            out[sym] = Move(
                symbol=sym, date=today["date"], close=today["close"],
                change_pct=(today["close"] / prev["close"] - 1) * 100 if prev["close"] else 0.0,
                vol_multiple=(today["volume"] / avg) if avg and today["volume"] else None,
                spark=[r["close"] for r in reversed(rows)],
            )
    return out


def check(settings: Settings, moves: dict[str, Move] | None = None) -> list[Alert]:
    moves = moves if moves is not None else latest_moves(settings.volume_lookback)
    alerts = []
    for m in moves.values():
        kinds, sev = [], 0.0
        if abs(m.change_pct) >= settings.price_move_pct:
            kinds.append("price")
            sev = max(sev, abs(m.change_pct) / settings.price_move_pct)
        if m.vol_multiple and m.vol_multiple >= settings.volume_multiple:
            kinds.append("volume")
            sev = max(sev, m.vol_multiple / settings.volume_multiple)
        if kinds:
            alerts.append(Alert(m, kinds, sev))
    return sorted(alerts, key=lambda a: a.severity, reverse=True)
