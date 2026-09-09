# Historical data capability and qualification matrix

Updated: 2026-09-08 (Europe/London)

This is a research-data inventory, not a representation that Webull sandbox data is production, exchange-official, or independently verified.

## 0.8.0 Trade-home verification

On 2026-09-08 at 16:20 UTC, Yahoo MES and MNQ continuous-reference M5 requests succeeded, with source observations at 16:15 UTC. They remain public/unverified and reference-only. The new isolated Webull sandbox adapter returned 1,200 M5 rows each for exact MESU6 and MNQU6 after checking contract metadata; source observation time remained older than fetch time. Two sandbox snapshots were saved for offline practice. No production entitlement or order was tested in this update.

Regular 2026 CME windows/DST are supported, while product-specific holiday windows are explicitly UNKNOWN. Native daily futures bars must align to complete known sessions; the bounded sandbox adapter offers M5 only. The shared monitor vetoes live entry timing for all current futures sources. SPX older-chain repricing is implemented but requires lifecycle-qualified OI/IV inputs; the saved unqualified sandbox SPX chain cannot supply them.

Fresh receipts: [public futures](../qa/trade-home-2026-09-08/public-futures.json), [sandbox futures](../qa/trade-home-2026-09-08/sandbox-futures.json), [verification](../qa/trade-home-2026-09-08/TEST-REPORT.md).

## Current access modes

| Mode/source | Instruments and cadence | Calculations | Credentials | Verified in this build |
|---|---|---|---|---|
| Offline/imported | User CSV/JSON and saved observations; source-defined cadence | Yes, subject to identity/session/warm-up gates | None | Yes |
| Synthetic walkthrough | Explicitly fictional stock and SPX examples | Yes, permanently labelled synthetic | None | Yes |
| Yahoo research | Provider-resolvable equities, indices, crypto and supported references; 1m to 1W subject to upstream limits | Yes; delay/entitlement remains unverified | None; optional `yfinance` | AAPL 15m HTTP retrieval observed 2026-09-08; the latest source bar remained 2026-09-04 and was labelled stale |
| Alpaca Basic IEX polling | Eligible U.S. stocks/ETFs; minimum app poll 15 seconds | Yes, with IEX-only coverage label | User-supplied free/paper keys | Fixture-tested; external credentials/entitlement unverified |
| Alpaca Basic IEX WebSocket | Trades and corrections for up to 30 combined U.S. stock/ETF symbols, explicitly started; eight local browser leases | Intrabar display only; completed-bar decisions do not repaint | User-supplied free/paper keys; optional pinned WebSocket client | Protocol, leases, bounded queues/revisions, reconnect and cleanup fixture-tested; authenticated external stream unverified |
| TradingView widget | Display coverage declared by TradingView and selected symbol | No; display isolated in sandboxed iframe | None | Consent/isolation fixture-tested; remote render not used as market evidence |
| Webull HK sandbox | Exact read-only instruments/snapshots/M5 history available to existing entitlement | Corroboration/research only | Existing private Keychain setup | MES/ES/NQ metadata, snapshot and 60 M5 rows observed 2026-09-08 |
| Webull HK sandbox account | Account list, balances, positions and open orders | Paper risk context and reconciliation; no production inference | Existing private Keychain/local environment | Account reads observed; identifiers are masked and detailed state stays private |
| Webull HK sandbox paper orders | U.S. stock/ETF LIMIT/DAY/CORE; 1–10 shares; USD 2,000 local cap | Provider preview, one-use typed confirmation, submit/status/cancel | Existing private sandbox Quotes/Trading scope plus pinned SDK | AAPL 1-share USD 1.00 order submitted unfilled and canceled; final status CANCELED on 2026-09-08 |
| Webull HK sandbox SPX option export | Five bounded contract pages plus batched option snapshots | Supplied-gamma arithmetic only; lifecycle-qualified GEX/flip blocked | Existing private Keychain/local environment | 368 standard native contracts exported 2026-09-08; partial, sandbox, mixed-spot and all lifecycle-unqualified |
| Native SPX/SPXW chain import | User-authorized point-in-time JSON/CSV or manually downloaded paired Cboe CSV | SPX GEX after lifecycle/completeness validation | None | Import/OCC/rejection/modeling tested; bundled Webull sandbox file is unqualified, so a current exact-lifecycle chain is still required |
| Configured News+ `options_chain.csv` | 199 SPY option rows observed 2026-09-08 | Stock/ETF options research only; never SPX GEX | Existing local file | SHA-256 recorded in the private readiness receipt; dashboard classifies it `PROXY_REJECTED_FOR_SPX_GEX` |

The app keeps exchange/source time, receive time and fetch time distinct. A reconnect or HTTP response does not refresh an old observation timestamp. No provider failure switches to synthetic or another provider.

## Current captured evidence

| Dataset | Captured coverage | Identity evidence | Pagination | Qualification status |
|---|---|---|---|---|
| SPY 5m RTH | 3,600 unique rows, 2026-07-01 18:50 UTC through 2026-09-04 19:55 UTC; 44 complete 09:30–16:00 New York sessions | Requested and response symbol observed as SPY | Three documented `end_time` pages of 1,200 rows | `WEBULL_SANDBOX_ONLY` |
| QQQ 5m RTH | 3,600 unique rows, same bounds; 44 complete sessions | Requested and response symbol observed as QQQ | Three documented `end_time` pages of 1,200 rows | `WEBULL_SANDBOX_ONLY` |
| MES/ES/NQ September 2026 | 1,200 rows per exact U6 contract; five complete standard US sessions each | Exact U6 metadata, CME venue code, tick and point value observed; response symbol observed | No usable historical pagination in the pinned futures SDK | `WEBULL_SANDBOX_ONLY` |
| MES/ES/NQ March and June 2026 | 1,200 rows per exact H6/M6 request, concentrated near each expiry | Response symbol observed, but the current instrument endpoint returned no expired-contract metadata | No usable historical pagination | `IDENTITY_INCOMPLETE` |
| Independent comparison source | Not yet captured | None | N/A | `BLOCKED` |

Hash-pinned extended capture: `215034e91040c166374c7e02a4d25352e0c97fd086974eb9afdf9a13c4da98f0`.

Backtest-compatible subset: `7db975ac4999a408cee4a4174bdce708a84d2c3418c03428cc55d25b11e40d13`.

## Observed discrepancies and constraints

- SPY and QQQ each contain two 77-bar dates, 2026-07-24 and 2026-08-14. They are excluded instead of imputed. An independent source must establish whether the missing bars are provider omissions, session differences, or another cause.
- The leading 2026-07-01 ETF page is partial with 14 regular-session bars and is excluded.
- The installed Webull futures history interface has no `start_time` or `end_time` option. A bounded read-only probe added `end_time` directly to the request; the server returned the same latest page with zero older rows, so this is not treated as pagination.
- Historical H6/M6 responses identify the requested contract in the response, but their metadata is no longer returned by the current instrument endpoint. These rows cannot enter a roll-aware qualified dataset until contract identity and expiry are corroborated independently.
- Webull documents minute bars as unadjusted, but provider documentation is not independent evidence of the delivered row semantics.
- No exchange calendar has yet been bound to the capture. The normalizer admits only observed weekday sessions with exactly 78 five-minute bars from 09:30 through 16:00 New York time.
- A 5 September TradingView status check requested `AMEX:SPY`, but the effective chart accessibility identity was `BATS:SPY` with `NYSE Arca by Cboe One`. It was not accepted as the canonical independent comparison and no comparison rows were merged.

## Required before dataset qualification

1. Export matching SPY and QQQ windows from an independent, visibly identified feed and compare timestamp, OHLC and volume fields over overlaps.
2. Resolve the two 77-bar dates without filling or silently dropping discrepancies.
3. Obtain exchange-calendar evidence, including holidays and early closes.
4. Obtain authoritative metadata for every expired futures contract and define a precommitted roll rule.
5. Acquire sufficiently long exact-contract futures history from a source that supports historical boundaries; do not splice continuous contracts into exact-contract results.
6. Freeze development, validation and untouched holdout membership only after the qualified dataset is complete.

## Provenance

- Extended raw artifact: `state/historical/captures/215034e91040c166374c7e02a4d25352e0c97fd086974eb9afdf9a13c4da98f0.json`
- Extended-data pointer: `state/historical/latest.json`
- Expanded development backtest: `reports/backtests/22fd89cdf6f38cf1/`
- Webull market-data documentation: <https://developer.webull.hk/apis/docs/market-data-api/overview/>
- Webull futures documentation: <https://developer.webull.hk/apis/docs/trade-api/futures/>
