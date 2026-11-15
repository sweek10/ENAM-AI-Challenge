"""Warm the cache: screener.in page + the last N concall transcripts for some tickers.

    python -m scripts.fetch_transcripts KPITTECH TCS
    python -m scripts.fetch_transcripts --portfolio
"""
import argparse

from finn import pdfs, screener
from finn.portfolio import PORTFOLIO


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--portfolio", action="store_true")
    ap.add_argument("-n", type=int, default=5, help="transcripts per company")
    args = ap.parse_args()
    tickers = args.tickers + ([s.symbol for s in PORTFOLIO] if args.portfolio else [])
    for t in tickers:
        try:
            c = screener.fetch(t)
        except Exception as e:
            print(f"{t:<11} screener failed: {e}")
            continue
        got = 0
        for cc in c.concalls[: args.n]:
            try:
                pdfs.fetch(cc.transcript)
                got += 1
            except Exception as e:
                print(f"{t:<11} {cc.label}: {e}")
        print(f"{t:<11} {c.name[:30]:<30} {len(c.quarters)} quarters, {got}/{min(args.n, len(c.concalls))} transcripts")


if __name__ == "__main__":
    main()
