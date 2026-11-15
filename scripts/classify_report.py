"""Classify announcements and show what it cost: rules vs LLM, tokens, $.

    python -m scripts.classify_report                 # real filings in data/ (run scripts.refresh first)
    python -m scripts.classify_report --days 7
    python -m scripts.classify_report --corpus --fake-llm   # test headlines, no API key needed

Needs ANTHROPIC_API_KEY for real LLM calls; without it, leftovers are shown as
"unreviewed" and the counterfactual table is still computed.
"""
import argparse
import json
from collections import Counter
from datetime import date, timedelta

from finn import classify as C
from finn import config, db
from finn.announcements import Announcement, load_snapshot
from finn.portfolio import PORTFOLIO

OUT_TOKENS_PER_ITEM = 8      # '[12,"management_change"],' ~ 8 tokens
NAIVE_PROMPT_OVERHEAD = 120  # per-call framing when one item is sent at a time


def _tok(s: str) -> int:
    return max(1, len(s) // 4)   # rough chars/4 estimate, labelled as such


def load_items(days: int | None) -> list[Announcement]:
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM announcements")]
    items = [Announcement(**r) for r in rows]
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


def counterfactuals(items):
    sys_t = _tok(C.SYSTEM)
    line_t = sum(_tok(f"{i}|{a.category}|{a.subcategory}|{a.headline[:220]}") for i, a in enumerate(items))
    n = len(items)
    batches = -(-n // C.BATCH)
    rows = []
    for label, model, tin, tout in [
        ("everything -> Haiku 5.5, batched", "claude-haiku-5-5", sys_t * batches + line_t, OUT_TOKENS_PER_ITEM * n),
        ("everything -> Opus 5.5, batched", "claude-opus-5-5", sys_t * batches + line_t, OUT_TOKENS_PER_ITEM * n),
        ("everything -> Opus 5.5, 1 call/filing", "claude-opus-5-5",
         (sys_t + NAIVE_PROMPT_OVERHEAD) * n + line_t, 30 * n),
    ]:
        pin, pout = config.MODEL_PRICES_PER_MTOK[model]
        rows.append((label, n if "1 call" in label else batches, tin, tout, tin / 1e6 * pin + tout / 1e6 * pout))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int)
    ap.add_argument("--corpus", action="store_true", help="use tests/corpus.py headlines")
    ap.add_argument("--fake-llm", action="store_true", help="stub LLM (token counts are estimates)")
    ap.add_argument("--show", type=int, default=8, help="items to print per tier")
    args = ap.parse_args()

    items = corpus_items() if args.corpus else load_items(args.days)
    if not items:
        print("No announcements found. Run `python -m scripts.refresh` first (or use --corpus).")
        return
    client = "default"
    if args.fake_llm:
        from tests.test_classify import FakeClient
        client = FakeClient()

    res, st = C.classify(items, client=client)
    n = st["total"]
    rules = st["rule:headline"] + st["rule:exchange_cat"]
    pct = lambda k: f"{100 * k / n:5.1f}%"

    print(f"\n== Classification of {n} announcements ({'test corpus' if args.corpus else 'data/'}) ==")
    print(f"  rules (headline)         {st['rule:headline']:>5}  {pct(st['rule:headline'])}")
    print(f"  rules (exchange category){st['rule:exchange_cat']:>5}  {pct(st['rule:exchange_cat'])}")
    print(f"  LLM label from cache     {st['llm_cache']:>5}  {pct(st['llm_cache'])}")
    print(f"  LLM this run             {st['llm']:>5}  {pct(st['llm'])}")
    print(f"  unreviewed (no LLM)      {st['no_llm']:>5}  {pct(st['no_llm'])}")
    print(f"  -> handled without an LLM call this run: {rules + st['llm_cache']}/{n} ({pct(rules + st['llm_cache']).strip()})")

    print(f"\n== LLM spend this run ({config.CLASSIFY_MODEL}{', FAKE client: tokens estimated' if args.fake_llm else ''}) ==")
    print(f"  calls {st['llm_calls']}   input {st['input_tokens']:,} tok   output {st['output_tokens']:,} tok"
          f"   cost ${st['cost_usd']:.6f}")
    if st.get("llm_errors"):
        print(f"  errors: {st['llm_errors'][:2]}")
    with db.connect() as conn:
        tot = conn.execute("SELECT COUNT(*) c, COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(output_tokens),0) o, "
                           "COALESCE(SUM(cost_usd),0) usd FROM llm_usage").fetchone()
    print(f"  all-time ledger: {tot['c']} calls, {tot['i']:,} in / {tot['o']:,} out, ${tot['usd']:.6f}")

    print("\n== What the alternatives would cost for the same items (chars/4 estimate) ==")
    print(f"  {'approach':<40}{'calls':>6}{'in tok':>10}{'out tok':>9}{'cost $':>12}")
    for label, calls, tin, tout, usd in counterfactuals(items):
        print(f"  {label:<40}{calls:>6}{tin:>10,}{tout:>9,}{usd:>12.5f}")
    print(f"  {'THIS: rules + Haiku on leftovers':<40}{st['llm_calls']:>6}{st['input_tokens']:>10,}"
          f"{st['output_tokens']:>9,}{st['cost_usd']:>12.5f}")

    print("\n== Categories ==")
    print("  " + ", ".join(f"{k}:{v}" for k, v in Counter(r.category for r in res).most_common()))
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

    sent = [r for r in res if r.method in ("llm", "llm_cache", "no_llm")]
    if sent:
        print("\n== Sent to (or waiting for) the LLM: candidates for new rules ==")
        for r in sent[:15]:
            print(f"   {r.category:<17} [{r.ann.category} / {r.ann.subcategory}] {r.ann.headline[:70]}")


if __name__ == "__main__":
    main()
