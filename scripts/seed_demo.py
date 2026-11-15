"""Seed FAKE data for layout work only. Writes to data_demo/ (gitignored), never data/.

    python -m scripts.seed_demo
    FINN_DATA_DIR=data_demo uvicorn app:app --reload

The UI shows a "Demo data" banner whenever this data dir is in use.
"""
import os
import random
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("FINN_DATA_DIR", str(ROOT / "data_demo"))

from finn import config, db  # noqa: E402
from finn.announcements import Announcement  # noqa: E402
from finn.portfolio import PORTFOLIO  # noqa: E402
from tests.corpus import CORPUS  # noqa: E402


def main():
    d = config.data_dir()
    assert d.name != "data", "refusing to write demo data into the real data dir"
    if config.db_path().exists():
        config.db_path().unlink()
    (d / "DEMO").write_text("fake data for layout\n")
    rnd = random.Random(7)
    now = datetime.now(config.IST).replace(tzinfo=None)

    anns = []
    for i, (cat, sub, head, _, _) in enumerate(CORPUS):
        s = PORTFOLIO[(i * 7) % len(PORTFOLIO)]
        dt = now - timedelta(minutes=rnd.randint(20, 70 * 60))
        anns.append(Announcement(f"demo:{i}", s.symbol, dt.isoformat(timespec="seconds"), head, cat, sub,
                                 "", "demo"))

    prices = []
    spikes = {"DIXON": (-6.4, 3.6), "HAL": (4.9, 2.4), "TRENT": (1.1, 2.7)}
    for s in PORTFOLIO:
        px, vol = rnd.uniform(400, 4000), rnd.uniform(5e5, 5e6)
        days = [date.today() - timedelta(days=k) for k in range(40, -1, -1)]
        days = [x for x in days if x.weekday() < 5]
        for j, dd in enumerate(days):
            chg, vm = rnd.gauss(0, 1.1), rnd.uniform(0.7, 1.3)
            if j == len(days) - 1 and s.symbol in spikes:
                chg, vm = spikes[s.symbol]
            px *= 1 + chg / 100
            prices.append((s.symbol, dd.isoformat(), px, px, px, px, px, int(vol * vm)))

    with db.connect() as conn:
        conn.executemany("INSERT OR REPLACE INTO announcements VALUES (:id,:symbol,:dt,:headline,:category,"
                         ":subcategory,:pdf_url,:source)", [asdict(a) for a in anns])
        conn.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?,?)", prices)
    print(f"seeded {len(anns)} fake filings and {len(prices)} fake bars into {d}")


if __name__ == "__main__":
    main()
