# Trading Research Hub v0.2 — operating guide

The hub is now the main implementation workspace. KX owns the indicator and release evidence; News+ macro remains a specialist data/research source. This release builds the research and advisory foundation. It does not claim that the complete trading bot, market-data verification or paper execution is finished.

## What is implemented

| Component | Implemented behavior | Boundary |
|---|---|---|
| Macro worker | Private legacy corpus import, bounded EdgeRunner RSS and official Fed monetary-policy feed | Summaries are not full articles; no normalized event/surprise history yet |
| Structure worker | KevinX posts, quote context, media references, coverage and checkpoints | Reads changing local exports; does not autonomously finish signed-in X/Substack backlog |
| SQLite journal | Document revisions, provenance, decisions, positions, quotes, alerts, data manifests, reports and cycles | Imported claims do not create release/provider authority |
| Data intake | JSON/CSV OHLC validation, exact identity, timestamps, ticks, hash-bound raw/normalized files | Independent feed qualification, exchange calendars and daily/weekly normalization remain open |
| Candidate validator | Side, size, horizon, exact contract, entry/SL/TP, expiry, provenance, conservative risk and explicit vetoes | Every candidate remains RESEARCH_ONLY; no automatically promoted strategy |
| Position monitor | Local imported records, matching fresh quote inputs, TP/SL threshold events, dedup and acknowledgement | No broker discovery/reconciliation, continuous quote subscription or protective orders |
| Reports | Daily/weekly Markdown and JSON, macro context, conditional scenarios, intraday/swing research, blockers and positions | Cached source values are labelled; weekly report is not a completed performance evaluation |
| Scheduled task | Existing reporting heartbeat consolidated into hourly hub checks; morning report/catch-up, Sunday weekly review | Requires Mac/app running; first unattended delivery still needs observation |

The initial import held 826 source-specific research records. Repeated feed captures add provenance without duplicating unchanged document revisions. The three implementation workers also tested the components independently; they are not three permanently running operating-system processes.

## Everyday use

Run from your extracted project directory with Python 3.11 or newer:

```sh
python3 -m trading_hub init
python3 -m trading_hub hub-status
python3 -m trading_hub worker all --refresh
python3 -m trading_hub data-ingest
python3 -m trading_hub data-list
python3 -m trading_hub register-legacy-data
python3 -m trading_hub brief daily
python3 -m trading_hub brief weekly
python3 -m trading_hub cycle
```

`cycle` is a bounded pass protected against overlap. It refreshes research at most once per UTC hour, ingests local OHLC files, observes local positions and generates due briefs. Daily report dates use HKT; after 07:30 it catches up if today's report is missing. Sunday also creates the week's review. Repeated commands return the retained report; `brief daily --force` appends a new revision. The latest pointers are `state/briefings/daily-latest.json` and `weekly-latest.json`.

The task schedule is hourly while the quote provider is unconnected. It is not an intraday execution or guaranteed delivery clock. The scheduled morning/weekly task is instructed to check fresh primary macro sources and add a cited `macro-review.md`; the deterministic base brief itself merges retained data and does not pretend to generate fresh market facts. Scheduled output is delivered in the existing task; no Slack/Telegram destination or other external recipient was configured.

## Your data folder

Put OHLC files in `data/inbox/`. CSV needs a same-stem `.metadata.json`; JSON contains `metadata` and `bars`. Follow [the exact input schema](DATA_INPUT_GUIDE.md). Unknown source, timestamps, contract identity or session definitions are reasons to retain a gap, not guess a value. Originals are never moved or deleted.

Use `data/records/inbox/` for candidate, position and quote files. These are not OHLC files, so keep them outside the OHLC inbox. No passwords or API keys belong in either directory.

Existing studies have also been registered without network access: four retained captures, 32 datasets and 52,800 source row observations including overlap. They remain `RAW_ONLY_METADATA_GAPS`, not qualified independent data. Their 32 inspected-period envelopes are conservatively marked exposed pending review; this is not a holdout freeze. Raw bytes are in `data/raw/`, manifests in `data/legacy_manifests/` and records in SQLite. `data-list` separately lists normalized intake; an empty normalized inventory does not mean the raw historical archive is empty.

```sh
python3 -m trading_hub record-import decision data/records/inbox/candidate.json
python3 -m trading_hub record-import position data/records/inbox/position.json
python3 -m trading_hub record-import quote data/records/inbox/quote.json
python3 -m trading_hub observe-positions
python3 -m trading_hub alerts
python3 -m trading_hub journal decision
python3 -m trading_hub journal position
```

Importing a position means “monitor this local record”, not “open a trade”. `position-close POSITION_ID` stops local monitoring only; it does not close anything at a broker or infer exit P&L. Closed IDs cannot be reused. Stop/target updates append versions for the same immutable instrument, entry, side and opening time. `alerts --acknowledge EVENT_ID` records your acknowledgement, not a broker acknowledgement or external delivery receipt.

The files named `*-synthetic.json` under `examples/` are fictional test fixtures, not recommendations. They are never automatically imported into the real workspace. The test suite uses isolated temporary databases.

## Exact futures and options plans

A candidate carries: candidate ID; instrument; long/short; quantity; intraday/swing; entry, stop and ordered targets with explicit units; decision, first-known, market-data and expiration times; source/data IDs; rule version; release hash. Future plans need an exact contract (not a continuous chart) and ISO expiry. SPY/QQQ option plans need exact OCC identity, underlying, call/put, strike, expiry and multiplier. Options use premium levels; an underlying invalidation is a separate concept and cannot masquerade as a premium SL.

Current defaults preserve $250 risk per trade and $1,000 portfolio loss limits. ES/NQ are capped at one contract and MES at two; margin/buying power/portfolio state cannot be self-certified by an imported plan. A request such as “buy ten ES” is not an override. Long-option premium debit is reserved rather than pretending a stop guarantees maximum loss; option fees, deliverables, quote entitlement and liquidity still need verification. Intraday/swing labels are supported in records, but overnight risk and swing/options backtests are not yet qualified.

An alert means that a fresh, exact-instrument observation crossed a retained threshold. It never means a market was filled there. Local files remain `LOCAL_FILE_UNVERIFIED` even when their contents claim “production”. Stale, delayed, future and wrong-contract quotes suppress TP/SL. Diagnostic events are currently deduplicated once per position revision; a continuous-provider implementation must add outage/recovery episode handling before live delivery qualification.

## Verification and next development

1. Qualify independent SPY/QQQ history, longer exact futures contracts, exchange calendars, daily/weekly bars and historical option quotes. Register every inspected period before a holdout freeze.
2. Complete KX hash-bound visual compile evidence, FH1/FS1 transport, independently observed positive entry transition and the nine chart configurations. No KX source or gate was changed by this hub build.
3. Freeze eligible candidates and untouched holdouts, then complete six baseline and six final formal replays with independent ledgers and acceptance scoring. Existing rolling-range backtests are not these replays.
4. Normalize attributed KevinX/PA rules and macro event vintages. Test structure-only, incremental PA, macro and combined versions on identical data and adverse costs. Keep failed candidates in the journal.
5. Add a verified read-only quote/position adapter, persistent stream health, reliable delivery/outbox receipts and forward observation. Qualify exact options and swing monitoring separately.
6. Only after acceptance, bind the sealed release and add a sandbox order adapter with idempotency, acknowledgements, partial fills, cancellations, protective exits, reconciliation and a kill switch. Verify each product/account separately. No live broker trading is enabled here.

Before credentialed paper integration, privately rotate the earlier screenshot-exposed Webull key and secret in the existing Keychain setup. Do not repost them. [Webull futures documentation](https://developer.webull.hk/apis/docs/trade-api/futures/) currently excludes OCO/OTO/OTOCO combinations, so standalone stops require separately tested cancellation/partial-fill/protection handling. [TradingView alerts](https://www.tradingview.com/pine-script-docs/concepts/alerts/) retain their script/settings snapshot and need recreation after the approved release changes.

The first scheduled run, exact-provider entitlement, broker fills, full strategy performance and the KX seal remain unproven. Passing software tests is not profitability evidence.
