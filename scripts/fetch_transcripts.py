"""Warm the cache and save research pages: screener.in + the last N transcripts, then build
data/research/<TICKER>.json (committed, so the deployed app has them even if screener is slow).

    python -m scripts.fetch_transcripts KPITTECH TCS
    python -m scripts.fetch_transcripts --portfolio
"""
import argparse

from finn import pdfs, research, screener
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
        try:
            r = research.build(t)
            verdict = f"{r['judged']} checked, {r['unclear']} unclear"
        except Exception as e:
            verdict = f"research failed: {e}"
        print(f"{t:<11} {c.name[:30]:<30} {len(c.quarters)} quarters, {got}/{min(args.n, len(c.concalls))} transcripts, {verdict}")


if __name__ == "__main__":
    main()
