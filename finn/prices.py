"""End-of-day price/volume via yfinance, stored incrementally in SQLite.

Never refetches: only asks Yahoo for dates after the last stored bar, and skips
a ticker entirely if it was fetched within PRICE_REFETCH_SEC.
"""
import time
from datetime import date, datetime, timedelta

from . import config, db


def _today_ist() -> datetime:
    return datetime.now(config.IST)


def _last_final_date() -> date:
    """Latest date whose bar is final (today only after market close)."""
    now = _today_ist()
    return now.date() if now.hour >= config.EOD_FINAL_HOUR_IST else now.date() - timedelta(days=1)


def _yf_fetch(yahoo_symbol: str, start: date, end_inclusive: date) -> list[dict]:
    import yfinance as yf

    df = yf.Ticker(yahoo_symbol).history(
        start=start.isoformat(),
        end=(end_inclusive + timedelta(days=1)).isoformat(),  # yfinance end is exclusive
        auto_adjust=False,
        actions=False,
    )
    rows = []
    df = df.dropna(subset=["Close"])
    for idx, r in df.iterrows():
        rows.append({
            "date": idx.date().isoformat(),
            "open": float(r["Open"]), "high": float(r["High"]),
            "low": float(r["Low"]), "close": float(r["Close"]),
            "adj_close": float(r.get("Adj Close", r["Close"])),
            "volume": int(r["Volume"]) if r["Volume"] == r["Volume"] else 0,  # NaN-safe
        })
    return rows


# Tests swap this out.
FETCHER = _yf_fetch


def update_prices(symbol: str, lookback_days: int = 400, force: bool = False) -> dict:
    """Bring `symbol` up to date. Returns {"symbol", "new_rows", "status"}."""
    symbol = symbol.upper()
    end = _last_final_date()
    with db.connect() as conn:
        if not force:
            row = conn.execute(
                "SELECT fetched_at FROM fetch_log WHERE source='yahoo' AND key=?", (symbol,)
            ).fetchone()
            if row and time.time() - row["fetched_at"] < config.PRICE_REFETCH_SEC:
                return {"symbol": symbol, "new_rows": 0, "status": "cached"}

        last = conn.execute("SELECT MAX(date) d FROM prices WHERE symbol=?", (symbol,)).fetchone()["d"]
        start = (date.fromisoformat(last) + timedelta(days=1)) if last else end - timedelta(days=lookback_days)
        if start > end:
            return {"symbol": symbol, "new_rows": 0, "status": "up_to_date"}

        rows = FETCHER(f"{symbol}.NS", start, end)
        if not rows:  # some symbols only resolve on BSE
            rows = FETCHER(f"{symbol}.BO", start, end)

        conn.executemany(
            "INSERT OR IGNORE INTO prices(symbol,date,open,high,low,close,adj_close,volume) "
            "VALUES (:symbol,:date,:open,:high,:low,:close,:adj_close,:volume)",
            [{**r, "symbol": symbol} for r in rows if r["date"] <= end.isoformat()],
        )
        conn.execute(
            "INSERT OR REPLACE INTO fetch_log(source,key,fetched_at) VALUES ('yahoo',?,?)",
            (symbol, time.time()),
        )
        return {"symbol": symbol, "new_rows": len(rows), "status": "fetched"}


def get_prices(symbol: str, since: str | None = None) -> list[dict]:
    q = "SELECT * FROM prices WHERE symbol=?"
    args: list = [symbol.upper()]
    if since:
        q += " AND date>=?"
        args.append(since)
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(q + " ORDER BY date", args)]
