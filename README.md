# FINN: a filings filter for a 15-stock portfolio

Mobile-first web app for ENAM's AI Engineer build challenge. Two workflows, built thin but working:

- **Push: the digest.** Every exchange filing for the portfolio in a time window, ranked and folded so the
  few that matter come first. Results filings show headline numbers with QoQ and YoY; management changes show
  who is coming in and their background. Price and volume moves beyond your limits are flagged next to the news.
- **Pull: promised vs delivered.** For any NSE ticker, read the last four earnings-call transcripts, find the
  numeric guidance management gave, and hold it against the reported quarters: delivered, missed, on track,
  behind, too early, or *unclear* when the code can't judge honestly.

**Live demo:** https://<your-url>/?window=30d · **Build history:** _add the conversation link_

## The point of view

This portfolio's 15 stocks filed 295 announcements in six weeks of results season. Most are paperwork. The product's value is the filtering,
so the first thing on screen is the result of the filtering ("3 filings need you, 2 stocks moved"), and the rest
is folded by importance, not deleted. From any card, "dig deeper" asks the question an analyst asks next: has
this management done what it said?

## How it works

```
BSE / NSE announcements ─┐                      ┌─ digest  (/)
Yahoo end-of-day prices ─┼─ scripts.refresh ──▶ │  data/finn.sqlite + snapshots ─▶ FastAPI + Jinja (server-rendered, no JS build)
filing PDFs ─────────────┘   (runs on a laptop)  └─ research (/research/<TICKER>)
screener.in results table + concall transcripts ─▶ finn.research (cached; saved copy in data/research/)
```

| Step | How | Why |
|---|---|---|
| Classify filings | Ordered rules on the headline, then the exchange's own category, then rules on **page 1 of the PDF** for vague ones ("Outcome of Board Meeting") | On a 58-headline test set, 88% are placed by the headline and category rules; vague ones by reading the filing, never guessed. Leftovers show as "unreviewed". |
| Prioritise | Base score per type plus modifiers (results date vs results; CFO vs independent director; rating cut vs reaffirm); watch/mute per type | Puts the decision-relevant 5% first. |
| Results card | Rebuild statement rows from **word positions** (pdfplumber), map columns to quarters by the **dates printed above them**, correct and flag OCR slips | Scanned filings emit text column by column; TCS and ITC order their quarter columns differently. Position + header dates handle both. |
| EBITDA | Derived in code: PBT + finance costs + depreciation − other income | Not a line in the statutory statement; marked with an asterisk. |
| Management card | SEBI's disclosure table (name, reason, date, brief profile) or the letter sentence ("has superannuated … w.e.f.") | Background is two verbatim sentences from the filing's own profile, picked for experience and education. |
| Guidance | Management turns only (moderator and analysts dropped); forward-looking cue + a % / range / "mid-teens"; tagged with metric, period (FY/Q/H, relative to the call date) and basis | Constant-currency and EBIT-margin guidance are marked unclear rather than compared with rupee revenue or EBITDA-level margins. |
| Verdicts | Sales growth vs the same quarters a year earlier, operating margin, profit growth, from screener's reported quarters; 0.5-point slack | Each verdict says which quarters it used. |

**No LLM API anywhere.** Every number on screen is either copied from a filing or computed in code from copied
numbers, so it can be checked, and the app costs nothing to run. The trade-off is recall on the pull side (below).

**Caching.** Closed time windows are cached forever; open ones for 30 minutes. Each filing PDF is downloaded once
and its parsed card stored. Transcript guidance is cached per transcript and rule version. The app never calls an
exchange during a page view.

## Checked against real filings

`tests/test_real_filings.py` pins 63 figures (revenue, PBT, PAT, EBITDA inputs × 3 quarters) from the
September-quarter results of TCS, ITC and Dixon, and three management changes (HDFC Bank, Maruti, Dixon). Each was
read off the PDF page by eye. These three filings cover the three layouts the parser had to handle: clean text,
an OCR'd scan with a different column order, and a scan whose text runs column by column.

## Known limits (honest list)

- **Pull-side recall is low; precision came first.** Across the portfolio plus KPIT (16 companies, last four
  calls each) the rules produce **two** checked verdicts, both on L&T and both right: FY26 revenue growth guided at
  15%, delivered 11.8% (*missed*; L&T's next call says "12% compared to our guidance of 15%"), and FY27 guided at
  10–12%, 11.4% so far (*on track*). Everything else is *unclear*. Earlier rule versions produced confident wrong
  verdicts (loan-book growth, order inflow and one segment's margin compared with company revenue); those cases are
  now regression tests. Most guidance is qualitative, segment-level or in constant currency. This is where a model
  would help, with the code still doing the comparison.
- **Results cards** are complete for 7 of 15 companies (TCS, ITC, Dixon, L&T, Bajaj Finance, Persistent, Tata Steel),
  partial for Trent, and "not found" for the rest (Reliance's and Eternal's layouts, HDFC Bank's bank format, HAL's
  image-only PDF). A page only counts as the statement if it has revenue, a profit line and most other rows: an
  early version read Maruti's revenue as 55 from a highlights page.
- Image-only PDFs with no text layer show "not found" with a link to the filing.
- Banks: no "revenue from operations" line, so results cards show "not found" for revenue; NIM guidance is unclear.
- Derived EBITDA can differ from the EBITDA a company quotes (Dixon: 599.63 derived vs 666 in its press release).
- Some transcripts live on company sites that refuse scripted downloads (e.g. one TCS call); those are listed as skipped.
- On the free host the data is as fresh as the last commit; settings changes reset when the service restarts.
- Alerts are computed when you open the page, not sent as push notifications.

## Deliberately left out

Chat and voice interfaces, the other six research sections (snapshot, balance sheet, cash quality, bull/bear),
LinkedIn scraping (a search link instead), user accounts, real push notifications.

## Run it

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m scripts.refresh --days 45 --enrich-days 45     # prices, filings, results/management cards
.venv/bin/python -m scripts.fetch_transcripts KPITTECH --portfolio # transcripts + saved research pages
.venv/bin/uvicorn app:app --reload                                  # http://127.0.0.1:8000
.venv/bin/python -m pytest                                          # 176 tests; add --live to hit the real sources
```

Other scripts: `scripts.verify_cards` (re-runs the parsers on the real filings and saves the pages read),
`scripts.classify_report` (rule coverage and the unreviewed list), `scripts.seed_demo` (fake data for layout work,
written to `data_demo/` and bannered as such).

## Deploy (Render, free)

1. Commit `data/` after a refresh and push to GitHub.
2. In Render: **New → Blueprint**, pick this repo. `render.yaml` sets up a free Python web service.
3. Wait for the build, open the URL, check `/healthz`. The free tier sleeps after 15 idle minutes; the first
   request after that takes about a minute.
4. To update the data: rerun the two refresh commands locally, commit `data/`, push. Render redeploys.

## Layout

```
app.py                 routes: /, /settings, /research, /research/<TICKER>, /api/digest, /healthz
finn/announcements.py  BSE → NSE → cache → committed snapshot
finn/prices.py         Yahoo end-of-day bars, incremental, into SQLite
finn/classify.py       rules + priority tiers
finn/statement.py      results-statement parser (positions + header dates)
finn/mgmt.py           director / KMP change parser
finn/cards.py          results card, management card, page-1 classification
finn/enrich.py         reads each filing PDF once, stores the card
finn/digest.py         window, grouping, tiers;  finn/alerts.py  price/volume limits
finn/screener.py       quarterly table + transcript links
finn/guidance.py       guidance extraction;  finn/research.py  verdicts and the page data
templates/             digest, research, settings (hand-written CSS, light and dark)
```

Built with Claude (Anthropic) writing most of the code under my direction; I set scope, made the product calls,
ran it against live data and reviewed the output. The full conversation is linked above.
