"""How well do the rules cover real filings? Shows what's placed by which rule, and what isn't.

    python -m scripts.classify_report              # real filings in data/ (run scripts.refresh first)
    python -m scripts.classify_report --days 7
    python -m scripts.classify_report --corpus     # hand-written test headlines

The "unreviewed" list at the end is the to-do list for new rules.
"""
import argparse
from collections import Counter
from datetime import date, timedelta

from finn import classify as C
from finn import db
from finn.announcements import Announcement, load_snapshot
from finn.portfolio import PORTFOLIO


def load_items(days: int | None) -> list[Announcement]:
    with db.connect() as conn:
        items = [Announcement(**dict(r)) for r in conn.execute("SELECT * FROM announcements")]
    if not items:  # fall back to committed snapshots
        for s in PORTFOLIO:
            items.extend(load_snapshot(s.symbol))
    if days:
        lo = (date.today() - timedelta(days=days)).isoformat()
        items = [a for a in items if a.dt >= lo]
    return list({a.id: a for a in items}.values())


def corpus_items() -> list[Announcement]:
    from tests.corpus import CORPUS
    return [Announcement(f"corpus:{i}", "TEST", f"2026-11-1{i % 5}T10:00:00", h, c, s, "", "bse")
            for i, (c, s, h, _, _) in enumerate(CORPUS)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int)
    ap.add_argument("--corpus", action="store_true", help="use tests/corpus.py headlines")
    ap.add_argument("--show", type=int, default=8, help="items to print per tier")
    args = ap.parse_args()

    items = corpus_items() if args.corpus else load_items(args.days)
    if not items:
        print("No announcements found. Run `python -m scripts.refresh` first (or use --corpus).")
        return
    res, st = C.classify(items)
    n = st["total"]
    pct = lambda k: f"{100 * k / n:5.1f}%"

    print(f"\n== Classification of {n} announcements ({'test corpus' if args.corpus else 'data/'}) ==")
    print(f"  rules on the headline          {st['rule:headline']:>5}  {pct(st['rule:headline'])}")
    print(f"  rules on the exchange category {st['rule:exchange_cat']:>5}  {pct(st['rule:exchange_cat'])}")
    print(f"  rules on the PDF's first page  {st['pdf:page1']:>5}  {pct(st['pdf:page1'])}")
    print(f"  unreviewed                     {st['unreviewed']:>5}  {pct(st['unreviewed'])}")

    print("\n== Categories ==")
    print("  " + ", ".join(f"{k}:{v}" for k, v in Counter(r.category for r in res).most_common()))
    print("\n== Rules that fired ==")
    print("  " + ", ".join(f"{k}:{v}" for k, v in Counter(r.rule for r in res if r.rule).most_common(12)))
    tiers = Counter(r.tier for r in res)
    print(f"\n== Priority tiers ==  top:{tiers['top']}  notable:{tiers['notable']}  "
          f"routine (collapsed):{tiers['routine']}  hidden:{tiers['hidden']}")
    for t in ("top", "notable", "routine", "hidden"):
        group = [r for r in res if r.tier == t]
        if not group:
            continue
        print(f"\n  [{t}]")
        for r in group[:args.show]:
            print(f"   {r.score:>3} {r.ann.symbol:<10} {r.category:<17} {r.method:<17} {r.ann.headline[:70]}")
        if len(group) > args.show:
            print(f"   ... +{len(group) - args.show} more")

    left = [r for r in res if r.method == "unreviewed"]
    if left:
        print("\n== Unreviewed: candidates for new rules ==")
        for r in left[:20]:
            print(f"   [{r.ann.category} / {r.ann.subcategory}] {r.ann.headline[:80]}")


if __name__ == "__main__":
    main()
