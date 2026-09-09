# Trading Research Hub — Free-First Local 0.8.1

This is the merged private research workspace. The current `Trading Research Hub` database, journal, notes, backtests and configuration remained authoritative. The refined donor, KX release evidence, Kevin structural context, Edgerunner reference material and dated News+ macro state have been integrated into one application with recorded hashes. The original donor and sibling projects were not overwritten.

The public source repository intentionally excludes local databases, credentials, reports, backups, generated archives, QA artifacts and licensed/private research corpora. A fresh checkout creates its own empty local state. Keep provider keys in `local-data.env`, which is ignored by Git.

The Hub is a research and sandbox practice tool. It cannot promise profits or certify a strategy from software tests. Production broker execution and external messaging remain disabled. Dashboard alerts and optional local browser notifications are available only during user-started sessions. Webull order submission is restricted to confirmed Hong Kong OpenAPI **sandbox paper orders** under the narrow policy described below.

## Trade home (0.8.1 · 9 September 2026)

Open [Trade](http://127.0.0.1:8787/#home), the default page. MES/MNQ exact-contract reviews come first, with up to three leading cards and an options review directly below. Intraday/Swing, entry/stop/no-chase, targets, costs, observation age and data blockers share the existing deterministic engine. The visible swing phase uses confirmed pivots; **Why this judgment?** shows the evidence and explicitly avoids a certified Elliott-wave count. The sidebar is **Trade / Research / Settings**; **Charts, GEX & research tools** retains every older link.

**Instruments & data source** selects saved exact-contract observations (offline), Yahoo continuous references, or the existing optional Webull sandbox adapter. Select exact MES/MNQ/ES/NQ contract codes explicitly; no roll transfers levels. **Refresh now** performs one bounded sweep; **Start monitoring** runs up to four hours, with Stop and the existing notification controls. Reload never starts polling. Futures orders remain disabled. Saved/sandbox plans are practice hypotheses; no current account quantity is approved.

CME regular overnight sessions use America/Chicago with DST and maintenance/recess boundaries. Product holiday windows without verified detailed hours remain UNKNOWN, including the 2026 Labor Day window through September 8. Incomplete periods never become complete strategy bars. Native daily futures history must align to complete qualified sessions; the bounded sandbox adapter offers M5 only. Fees/slippage are disclosed assumptions, not verified broker charges.

SPX GEX retains snapshot calculations and the explicitly separate **repriced older-chain scenario**: newer timestamped SPX spot, original OI/IV dates, qualified native lifecycles, sticky-IV modeling, crossings and provenance export. Next to it, **SPX expected range / “TR” projection** calculates `SPX × VIX1D / 100 / √252 × K` at K 0.9 and 1.4 from timestamped observations. It is an expected-move scenario, not GEX, support/resistance certainty or probability. VIX is never substituted for VIX1D. Synthetic/sandbox labels persist. There is no authorized automated Cboe scraper or established free live native SPX chain.

The same home page exposes dashboard/browser alerts and dated daily/weekly private macro briefs. Starting monitoring only fills a missing report period from saved evidence; **Refresh sources & rebuild** is the explicit bounded EdgeRunner/Federal Reserve refresh. No cron, background service or external message schedule is installed. `FLUSH WATCH` requires completed downside structure, momentum and participation; `TRIM / DO NOT CHASE` requires a frozen target or outer expected-range boundary. These are rule states, never probabilities or order authority.

[Current guide](docs/SIMPLE-TRADE-HOME-2026-09-09.md). This release updates the local project only; no ZIP is produced.

## Local session monitoring (8 September 2026)

The authoritative deliverable is this local project. No new release ZIP is needed. Open `http://127.0.0.1:8787/#monitor` for the intraday and swing comparison. Choose up to eight US stock/ETF symbols and a per-share cost assumption; account quantity remains unapproved. Start performs serial bounded public-data requests every 120 seconds by default, with daily history at most every 15 minutes. By default, hiding the page or leaving this view pauses monitoring. After you explicitly enable browser notifications and grant permission, a started session may continue across Hub views or another tab. Closing the page, Stop, going offline or the deadline still stops it. Sessions are bounded to four hours or the current market close plus five minutes, whichever is sooner; no schedule or background service is installed. The stream and polling modes remain separate.

The R:R table deducts a round-trip cost assumption from reward and adds it to loss. Targets are hypothetical 2R/3R objectives, not a measured edge. The dated macro briefing cites official event schedules. A 2026 NYSE/Nasdaq schedule identifies holidays and early closes; instrument halts and unscheduled closures remain unverified.

## Options and setup alerts (0.7.2)

Open [Options recommendations](http://127.0.0.1:8787/#options), select a US stock/ETF and fetch a public chain or import exact timestamped standard contracts. The existing engine supplies long-call/put and debit-spread scenarios, conservative quote-side costs, expiry payoff, maximum loss/gain and R:R. Budget is optional and blank by default. Missing current quotes, OI dates or exact expiry metadata produce theoretical/historical scenarios; they never produce a purchase recommendation. SPX GEX remains independent.

[Today & monitor](http://127.0.0.1:8787/#monitor) now provides a completed-breakout → later completed-retest alert lifecycle. The first scan establishes a baseline. Alerts freeze entry, invalidation, targets, no-chase boundary and source hashes; corrections/gaps/stale data withdraw confirmation. An alert means **review a conditional paper-research setup**, with strategy, event, quotes and account suitability still to verify. No order is sent. Click **Enable browser notifications**, allow this site in Chrome, then **Send test notification** and **Start monitoring**. The embedded browser may deny permission; use Chrome. OS notification permission and Focus mode may also affect delivery. Sleep and browser throttling can delay polling.

Private installations may add a local `integrated/kx-reference` corpus. That directory is excluded from the public repository. Without it, KX-derived evidence stays unavailable and its release gates remain blocked.

See [operator guide](docs/OPTIONS-AND-ALERTS-2026-09-08.md) and [verification report](qa/options-alerts-2026-09-08/TEST-REPORT.md).

## Start the website

Python 3.11 or newer is required. Node, npm, Docker and paid services are not required.

```sh
cd '/path/to/Trading Research Hub'
python3 launch.py
```

Open <http://127.0.0.1:8787>. Keep the terminal open; Ctrl+C stops the app.

- macOS: open `START_MAC.command`
- Windows: open `START_WINDOWS.cmd`
- Linux: `bash start.sh`
- Busy port: `python3 launch.py --port 8788`

Startup is offline-capable, binds to loopback and makes no market request until you ask.

## What works without keys

- User-selected charts from saved/imported observations with canonical instrument identity, recent symbols, favorites and scanner navigation.
- 1m, 5m, 15m, 1H, 4H, 1D and 1W controls where source/session rules permit; incomplete periods never become completed-bar decisions.
- Zoom, pan, reset, crosshair OHLC/time/volume, EMA20/50, RSI14, ATR14 and qualified session VWAP.
- Independent SPX-only GEX imports and saved datasets. SPY, QQQ, futures, CFDs and scaled proxies are rejected.
- Supplied-gamma arithmetic and lifecycle-qualified spot-grid modeling kept separate. Missing OI is unknown; dealer signs are assumptions.
- One decision engine with completed-bar Kevin structural and price-action context. It cannot authorize an entry or clear KX gates.
- Macro/news library search, notes, journal, saved snapshots, stock scanner, options/futures risk arithmetic, original backtest reruns, private backup and restore.

## Optional free data

Yahoo public research data requires no account but can be delayed, stale or blocked. Optional Alpaca Basic supplies eligible U.S. stocks/ETFs from IEX through polling or an explicitly started shared WebSocket. IEX is single-exchange coverage, not consolidated SIP/NBBO, SPX options or CME futures.

```sh
python3 scripts/enable_free_feeds.py
cp local-data.env.example local-data.env
```

Add keys only to `local-data.env`; do not paste them into chat. TradingView remains an opt-in sandboxed display and never feeds Hub calculations.

## Webull HK sandbox paper orders

Install the official pinned optional SDK:

```sh
python3 scripts/enable_webull_paper.py
```

The existing macOS Keychain entries are recognized. On another machine, copy `local-data.env.example` to `local-data.env`, add the sandbox app key/secret locally, and restart. The file and `.venv-webull` are excluded from releases and backups.

Open **Trade lab → Webull HK paper order**. Each order follows this sequence:

1. Enter an exact U.S. stock/ETF symbol, BUY or SELL, 1–10 whole shares and a limit price.
2. The server validates the sandbox-only policy and requests an official Webull preview.
3. Type the order-specific phrase exactly within 180 seconds. The challenge is one-use and memory-only.
4. The Hub submits through `api.sandbox.webull.hk`, records a sanitized lifecycle receipt, and lets you refresh status.
5. Cancellation requires a separate exact phrase.

The initial scope is U.S. stocks/ETFs, `LIMIT`, `DAY`, `CORE`, at most 10 shares and USD 2,000 local notional. Market orders, short-sale instructions, HK shares/BCAN, options, futures and all production endpoints are rejected. `broker_execution=false` still means production execution is unavailable.

On 8 September 2026, an actual Hub smoke test previewed and submitted one AAPL sandbox paper BUY limit for one share at USD 1.00, observed zero filled shares, requested cancellation and then observed provider status `CANCELED`. See `qa/webull-paper-order-smoke-2026-09-08.json`. This proves only the tested sandbox lifecycle, not production permission, current quote entitlement, fill quality, strategy quality or profitability.

## SPX GEX

The private release includes `data/raw/web-imports/Webull-HK-SANDBOX-SPX-partial-2026-09-08.json`, a bounded export of 368 native standard SPX/SPXW sandbox contracts. It is already saved as a separate chain-only SPX dataset. It is permanently labelled `sandbox`, partial and mixed-source. All rows lack exact independently verified last-trading/payoff-fixing and AM/PM lifecycle metadata, so the Hub excludes them from qualified GEX curves and gamma-flip estimates. It may display **unqualified supplied-gamma arithmetic** for checking units.

For qualified modeling, import a native SPX/SPXW chain with exact spot/time, capture time, OI reference date, contract root, multiplier, quote/Greek times, last-trading time, payoff-fixing time and settlement type. No chain means GEX unavailable, never zero.

The Hub does not scrape Cboe. If you use Cboe’s delayed table, enter SPX and download it manually through the provider’s interface, then preview the file locally. Date-only rows remain gated. Exposure per one-percent index move is:

```text
signed_assumed_contracts × gamma × multiplier × spot² × 0.01
```

The sign convention is an assumption about positioning, not observed dealer inventory.

## Integrated KX, Kevin and Edgerunner context

`integrated/kx-reference/` contains the exact hash-pinned Pine candidate, release manifest, acceptance/holdout policies, rulebook, Kevin coverage/checkpoint and selected Edgerunner/technical-context lineage. `integrated/news-reference/` contains the dated macro/monitor references needed by the Hub. `LINEAGE.json` records every copied file. The browser uses the existing Hub engine and a dependency-free port of completed-bar structural/price-action context; it does not run a competing KX signal engine.

The KX candidate remains `BLOCKED` with nine gates. Compile/transport evidence, all nine SPY/SPX/QQQ chart configurations, Kevin coverage, frozen holdout membership, baseline/final replay and package acceptance require their original evidence. See `docs/KX-INTEGRATION-0.7.0.md`.

## Backup, restore and verification

Download a private backup from **Data & health** or run the existing backup command. SQLite uses its backup mechanism; WAL files, credentials, reusable tokens, caches and virtual environments are excluded.

```sh
python3 -m trading_hub.restore_backup '/path/to/private-backup.zip' '/path/to/New Empty Restore Folder'
python3 -m unittest discover -s tests -q
python3 -m unittest discover -s tests_web -p 'test_*.py' -q
node --test tests_web/gamma-original.test.js tests_web/research-engine.test.js tests_web/chart-state.test.js
python3 tests_web/browser_refined.py
```

Node and browser drivers are developer-only. Windows/Linux launchers are code-tested in this macOS build; native Windows/Linux verification remains external.

Keep this local project private because it contains the user’s research database. The server enforces loopback, Host/Origin/CSRF validation, bounded input, safe static routing, redacted broker responses and CSV formula neutralization.

Current verification: [local dashboard plan](docs/LOCAL-DASHBOARD-VERIFICATION-2026-09-08.md), [fresh test report](qa/local-monitor-2026-09-08/TEST-REPORT.md), and [today’s conditional RR review](reports/Today-Trade-Review-2026-09-08.pdf).

## Bounded source refresh — 8 September 2026

Use **Research → Run one bounded source refresh** for one public macro-feed check and local Kevin export import. No scheduled crawler is installed. Kevin profile reviews require visible browser access; Brooks file inventory is offline and is not full content learning. The Research view links the [source-to-rule coverage table](docs/SOURCE-TO-RULE-COVERAGE-2026-09-08.md). Webull futures were reported as app-only; production OpenAPI entitlement remains unverified and no additional subscription was purchased. This update stays in the local project, with no new ZIP.
