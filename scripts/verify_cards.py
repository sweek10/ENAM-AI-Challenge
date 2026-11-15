"""Run the card parsers on real BSE filings and save the source pages for checking.

    python -m scripts.verify_cards

Writes samples/<name>/page_N.png (the pages the figures came from) and
samples/extracted.json, so every number can be checked against the filing.
"""
import argparse
import json
from pathlib import Path

from finn import cards, pdfs

ROOT = Path(__file__).resolve().parent.parent
B = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/"

# Real filings (BSE, Oct-Nov 2026) with three different statement layouts.
FILINGS = [
    ("tcs_results",    "TCS",      "results", B + "2b021530-bbc2-4590-8375-cefc205a7132.pdf"),
    ("itc_results",    "ITC",      "results", B + "6ad82347-ac2d-4385-ae80-3cd07c428f69.pdf"),   # OCR'd scan
    ("dixon_results",  "DIXON",    "results", B + "d7b8fffb-7a5f-4efa-9241-482c5d765f1d.pdf"),   # OCR'd scan
    ("dixon_mgmt",     "DIXON",    "mgmt",    B + "53529778-f611-4f21-b789-0bc1bf52e7e5.pdf"),
    ("hdfc_mgmt",      "HDFCBANK", "mgmt",    B + "b36ff756-1cf7-44a3-9b2c-4583e7e0e792.pdf"),
    ("maruti_mgmt",    "MARUTI",   "mgmt",    B + "1f588c41-9ebd-4665-b28c-da70d260cb7c.pdf"),
    ("dixon_outcome",  "DIXON",    "page1",   B + "f4cee6de-593c-4f4c-a0b0-368c8d0aa43e.pdf"),
]


def fmt(v):
    return "not found" if v is None else f"{v:,.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated names")
    args = ap.parse_args()
    out_dir = ROOT / "samples"
    out_dir.mkdir(exist_ok=True)
    report = {}

    for name, sym, kind, url in FILINGS:
        if args.only and name not in args.only.split(","):
            continue
        d = out_dir / name
        d.mkdir(exist_ok=True)
        try:
            pdf = pdfs.fetch(url)
        except Exception as e:
            print(f"[{name}] download failed: {e}")
            continue
        n = pdfs.num_pages(pdf)
        entry = {"symbol": sym, "kind": kind, "url": url, "pages": n}
        print(f"\n[{name}] {sym} {kind}  {n} pages  {url}")
        render = {0}
        if kind == "results":
            card, meta = cards.extract_results(pdf)
            entry.update(card=card.to_json(), meta=meta)
            if card.page:
                render.add(card.page - 1)
            print(f"  {card.statement} statement, {card.unit}, page {card.page}, status {card.status}, periods {card.periods}")
            print(f"  {'':8}{'current':>14}{'prev q':>14}{'yoy q':>14}{'QoQ %':>9}{'YoY %':>9}")
            for k in cards.METRICS:
                m = card.metrics[k]
                q = "" if m.qoq_pct is None else f"{m.qoq_pct:+.1f}"
                y = "" if m.yoy_pct is None else f"{m.yoy_pct:+.1f}"
                print(f"  {m.name:<8}{fmt(m.current):>14}{fmt(m.prev_q):>14}{fmt(m.yoy_q):>14}{q:>9}{y:>9}")
            for c in card.checks:
                print(f"  ! {c}")
        elif kind == "mgmt":
            card, meta = cards.extract_mgmt(pdf)
            entry.update(card=card.to_json(), meta=meta)
            for p in card.people:
                print(f"  {p.name} | {p.role} | {p.change} | effective {p.effective} | page {p.page}")
                print(f"     background: {p.background or 'not found in filing'}")
                if p.reason:
                    print(f"     reason: {p.reason}")
                if p.page:
                    render.add(p.page - 1)
            if not card.people:
                print(f"  {card.note}")
        elif kind == "page1":
            r = cards.classify_page1(pdf)
            entry["page1"] = r
            print(f"  page-1 category: {r['category']}  (all: {r['all']}, via {r['method']})")
        for p in sorted(x for x in render if 0 <= x < n):
            (d / f"page_{p + 1}.png").write_bytes(pdfs.render(pdf, p, dpi=150, max_side=2400))
        entry["rendered"] = sorted(p + 1 for p in render if 0 <= p < n)
        report[name] = entry

    (out_dir / "extracted.json").write_text(json.dumps(report, indent=1, default=str))
    print("\nPages saved under samples/<name>/ for checking.")


if __name__ == "__main__":
    main()
