"""Fetch EOD prices + announcements for the whole portfolio into data/.

    python -m scripts.refresh            # last 30 days of filings, prices up to date
    python -m scripts.refresh --days 90

Run this locally (or in a cron job) and commit data/finn.sqlite and
data/snapshots/ so the deployed app never depends on live exchange access.
"""
import argparse
import logging
import time
from datetime import date, timedelta

from finn import announcements as ann
from finn import prices
from finn.portfolio import PORTFOLIO


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--no-nse", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    end, start = date.today(), date.today() - timedelta(days=args.days)
    print(f"{'symbol':<11} {'prices':<22} {'filings':<8} source")
    for s in PORTFOLIO:
        try:
            p = prices.update_prices(s.symbol)
            pstr = f"{p['status']} +{p['new_rows']}"
        except Exception as e:
            pstr = f"ERR {type(e).__name__}"
        r = ann.get_announcements(s, start, end, use_nse=not args.no_nse)
        print(f"{s.symbol:<11} {pstr:<22} {len(r.items):<8} {r.source}"
              + (f"   ({'; '.join(r.errors)})" if r.errors else ""))
        if r.source in ("bse", "nse"):
            time.sleep(0.5)  # be polite to the exchanges


if __name__ == "__main__":
    main()
