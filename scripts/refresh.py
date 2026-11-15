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
from finn import db, enrich, prices
from finn.announcements import Announcement
from finn.portfolio import PORTFOLIO


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--no-nse", action="store_true")
    ap.add_argument("--no-enrich", action="store_true", help="skip reading filing PDFs")
    ap.add_argument("--enrich-days", type=int, default=7)
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

    if args.no_enrich:
        return
    # Read filing PDFs once: page 1 of vague filings, results tables, appointment letters.
    lo = (date.today() - timedelta(days=args.enrich_days)).isoformat()
    with db.connect() as conn:
        items = [Announcement(**dict(r)) for r in conn.execute("SELECT * FROM announcements WHERE dt >= ?", (lo,))]
    st = enrich.run(items, retry_errors=True)   # a manual refresh retries earlier failures
    print(f"\nread PDFs for {len(items)} filings from the last {args.enrich_days} days: {st}")


if __name__ == "__main__":
    main()
