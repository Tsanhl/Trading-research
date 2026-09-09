# Verification roadmap

Status at initial integration: **development / unsealed / no broker execution**.

## Different kinds of proof

1. Software correctness: deterministic tests, causal features, invalid inputs rejected, no authority bypass. Unit tests are necessary but do not prove investment returns.
2. Data correctness: exact instrument and expiry; timestamp and session semantics; adjustment policy; entitlement; independent upstream comparisons. A Webull-derived signal compared with the same Webull feed is not independent verification.
3. Strategy evidence: rules frozen before holdouts, all opportunities recorded, no retrospective selection, realistic costs, drawdowns and uncertainty. Sandbox fills cannot establish a real-market edge.
4. Operational correctness: real broker-paper account capability, acknowledgements, rejection/cancel/partial-fill reconciliation, deduplication, restart recovery and kill switch. No such order path exists in v0.1.

## Existing KX gates must remain intact

The original `audit/release-candidate.json` remains the source of truth. At integration its Pine SHA-256 is `5e37acb534df708be91efa562fbbe9204097de33811853f8fb48145826a195ae` and status is BLOCKED. The hub confirms the file hash; it does not substitute that check for compile evidence.

| Gate | Required remaining evidence |
|---|---|
| Positive structure path | Retained SPX 5m multi-ARM telemetry with independently observed entry-eligible to ACTIVE path |
| Compile and alerts | Exact hash-pinned compile evidence plus FH1/FS1 transport proof |
| Coverage | SPY/SPX/QQQ × 5m/1H/1D; canonical NASDAQ QQQ feed |
| Kevin source audit | Finish accessible windows; explicitly resolve or bound inaccessible routes, quote context and media hashes |
| Holdouts | Untouched membership frozen before tuning; membership hashes and access/selection attestations |
| Replays | Six formal baseline and six final runs, exported OHLC, checkpoint chains, independent ledgers, adjudication and non-regression |
| Release | Seal only after all gates pass; bind the agent to the exact sealed contract |
| Delivery | Read-only providers, advisory delivery, recreated alerts tied to the sealed version |

The hub's integration source pins are **not holdout membership**. The two synthetic demonstrations are **not formal KX replays**. The hub's SQLite log is self-generated, not an independent adjudicator.

## New asset classes need separate validation

- MES, ES and NQ: resolve exact expiry/instrument IDs, verify CME contract economics, obtain exchange calendars and session definitions, handle daylight-saving changes, maintenance breaks, early closes, expiry and roll. Never fill a continuous/back-adjusted contract or silently substitute cash-index/ETF prices.
- Options: resolve the exact contract and deliverable, licensed bid/ask timestamps, spread/size, Greeks freshness, expiry, exercise/assignment and corporate actions. Underlying OHLC does not establish option P&L. Initial research supports long premium only; spreads and short options need separate models.
- Intraday: jointly completed 1D/1H context and 5m decision bars, known-at event timing and stale-data vetoes. A daily data timestamp is not a current quote.
- Swing: completed daily/weekly context, position inventory, earnings/macro events, overnight gaps, exposure aggregation and carrying costs. Weekly ingestion/position reconciliation is not implemented yet.

## Paper execution stage

The user authorized provisional local-backtest defaults: US$100,000 simulated balance, US$250 risk budget per trade, US$1,000 daily loss threshold, one open position per instrument test and no overnight holding. They are frozen for the first development run and are not broker-paper authority. Portfolio-wide correlated S&P/Nasdaq exposure, permitted live-paper products and broker margin behavior still require explicit confirmation before an order adapter is added. These are user choices, not defaults copied from a writer's portfolio.

Then prove sandbox account product permissions and margin requirements using official read-only endpoints. Add a **separate sandbox-only** adapter with endpoint/account identity pinning, an explicit order allowlist, idempotency, persisted intents, reconciliation and a kill switch. Start with minimum-size simulated tests after these gates—not with live orders. No submission is authorized merely by the presence of credentials.

The simulated stop distance is not a guaranteed maximum futures loss: gaps, limits, liquidity and slippage can make realized loss larger. Portfolio exposure limits must combine SPY/SPX-related options with MES/ES, and QQQ-related options with NQ; counting tickets separately understates common risk.

## Research evaluation protocol

- Name each hypothesis and freeze its parameters, candidate-selection rules, code hash, data hash and costs.
- Separate development, validation and untouched holdout datasets by time and embargo overlapping trades/events.
- Keep publication time, capture/known-at time and revision vintage separate. Historical articles discovered today are not automatically available to an earlier backtest.
- Compare a price-only baseline, structural version, structural plus EMA filters, and structural plus macro/event filters. Score all rejected and expired candidates as well as fills.
- Predeclare endpoints, minimum evidence and acceptable degradation; count tested alternatives to expose multiple-testing/selection bias.
- Measure net expectancy, drawdown, losing streaks, exposure, turnover, gap risk, cost sensitivity and uncertainty by symbol, session and regime. Avoid claiming proof from a small positive sample or arbitrary session count.
- Preserve losing, ambiguous, missing-data and mismatched-feed cases. A blocked check is not a passing check or evidence that no opportunity existed.

## Source and capability references

Webull's detailed futures documentation describes products, contract discovery and data/trading interfaces, but documentation alone does not establish this account's capabilities. The sandbox observations in the new project are narrower evidence. See [Webull futures guide](https://developer.webull.hk/apis/docs/trade-api/futures/), [historical futures bars](https://developer.webull.hk/apis/docs/reference/futures-historical-bars/) and [market-data entitlements](https://developer.webull.hk/apis/docs/market-data-api/overview/).

Futures point values in the local simulator are research product constants, also checked against returned sandbox metadata; they are not broker margin rates. See [CME S&P 500 products](https://www.cmegroup.com/education/articles-and-reports/trading-the-sp-500) and [CME micro versus E-mini sizing](https://www.cmegroup.com/education/articles-and-reports/trading-micro-e-mini-options).
