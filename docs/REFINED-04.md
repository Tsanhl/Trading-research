# Refined 0.4 — implementation and data contracts

This addendum supersedes chart/GEX routing in the v0.3 browser merge. Original research files and engine behavior remain unless specifically noted.

## Boundaries

`web/charts.js` manages `C` (symbol, provider, interval, transient dataset, refresh generation). Existing analysis uses `S.s` and an explicit Save action. SPX uses independent `S.gexSnapshot` and `S.gexId`. No price or option chain flows from a TradingView widget into the calculations. The widget loads only after opt-in, in an opaque-origin iframe with no `allow-same-origin`; content policy allows only expected external chart hosts and blocks local API connections from the child. This is not an independent security audit; do not deploy the server publicly.

The server remains standard-library-only and loopback-bound. Authenticated writes require a session CSRF token and valid Host/Origin. Market HTTP requests use bounded timeouts and response-size limits. Alpaca redirects are refused; only IEX stock data paths are used. The API returns a configured boolean, never credentials.

## New routes

| Route | Contract |
|---|---|
| `GET /api/chart/resolve?symbol=NVDA` | Canonical symbol and explicit provider mappings/session metadata. Does not check exchange entitlement. |
| `POST /api/chart/fetch` | `{symbol, interval: "1m"|"5m"|"15m"|"1h"|"1d", provider:"yahoo"|"alpaca-iex"}`. Returns snapshot, cache flag and minimum refresh seconds. Does not persist every poll. |
| `POST /api/gex/import` | `{snapshot: {...}}` or `{format:"csv",text,metadata:{spot,spotAsOf,asOf,source,oiAsOf,dataKind}}`. Validates native SPX/SPXW chain and stores separately. |
| `GET /api/template?kind=spx` | Full synthetic chain-only JSON example. |
| `GET /api/template?kind=spx-options` | Normalized synthetic SPX CSV example. |
| `GET /api/backup` | Consistent main SQLite backup, reports and non-database state; excludes keys and legacy paper SQLite. |

The transient chart cache is bounded to 64 keys and 15-second IEX / 60-second Yahoo minimums. Errors are cached briefly as errors; no fabricated success. The browser rejects late results after a symbol/provider/interval change. This is request polling, not WebSocket streaming. Different browser windows can select different data without automatically replacing one another's datasets; local settings and the database are shared by the same app process.

## Snapshot additions

`chainOnly`, `feed`, `latencyClass`, `currency`, `exchangeTimezone`, `sessionModel` and option `root`/`underlying` are preserved. Untrusted imports cannot set `executionEligible=true` or upgrade their data kind to an authenticated live source. SPX chain-only inputs require explicit underlying source time. Imported provenance remains user-supplied. Legacy snapshots continue to load.

The schema handles UTC/offset-aware timestamps. OI and option quote times can be unknown. Native root/multiplier checks reject explicit proxy chains; a manually forged SPX declaration cannot be independently authenticated by a local schema validator. Expiry verification is a user declaration. Do not report this as exchange-certified data.

## Analysis scope

Known U.S. RTH/session logic is retained; unknown/international/crypto sessions use native-bar indicators only and suppress U.S.-specific trade plans and opening-range/VWAP references. They do not silently reaggregate into an invented U.S. calendar. Index/futures-reference data is not a tradable contract. Futures risk sizing still requires native entry/stop/margin input, not stock-to-future rescaling.

The original backtest now accepts an explicit `root` argument and writes outputs into that selected app copy. A regression test prevents the wrong-root report-writing bug. Existing simulation logic, risk policy, source hashes and limitations are retained. It does not backtest the new stock-polling or GEX signal engine.

## Out of scope

No full-market live-feed entitlement, paid provider integration, tick replay, historical point-in-time SPX chain backtest, broker execution, unattended notifications, user authentication or public server deployment. No model's profitable trading edge is asserted. Missing data and feed errors remain visible.
