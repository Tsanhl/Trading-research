# Trade home · local 0.8.0

Verified and updated 8 September 2026. Open http://127.0.0.1:8787/#home.

## Use it

1. Trade opens by default. Saved MES/MNQ observations appear first; up to three leading cards are shown. Select Intraday or Swing. A missing or conflicting setup says WAIT, with no invented entry.
2. Expand **Instruments & data source**. Saved exact-contract observations work offline. Enter an explicit contract (for example MESU6 or MNQU6) to choose saved or sandbox data. ES/NQ are optional. There is no automatic roll. Yahoo uses visibly continuous references, never a dated contract’s levels.
3. **Refresh now** is one sweep. **Start monitoring** starts one bounded shared monitor (120 seconds by default, maximum four hours). **Stop monitoring** stops it. Reload stays off. Daily stock history refreshes at most every 15 minutes; failures back off. At most eight instrument slots are monitored across stocks and futures.
4. Each card gives data age and blockers, an available conditional entry range, stop/no-chase, two hypothetical objectives and R:R after disclosed costs. Expand **Why this trade?** for the rule evidence. BUY/SELL setup describes a conditional direction, never a fill. Frozen confirmed/pending levels come from the existing breakout → later completed retest lifecycle. Confirmation requires uninterrupted observations; corrections, gaps, stale data, session changes and invalidation can withdraw it.
5. **Chart** opens that contract’s observations with chart polling off. **Save plan** saves the source snapshot, rule version, known-at time, costs, data hash and unapproved quantity to the existing journal. Options below use their own exact saved chain and conservative quote-side costs; missing quote/expiry evidence keeps them theoretical. The full options view remains accessible.
6. Expand **Alerts & browser notifications** to opt in. Chrome permission and OS permission are separate. The Hub and browser must remain open. Dashboard alerts work without OS permission. No external messaging service, scheduled agent or automatic order was added.

The sidebar has Trade, Research and Settings. Expand **Charts, GEX & research tools** for all older chart, GEX, options, scanner, journal, backtest and trade-lab links. Existing hash URLs and preferences still work. All original research and KX restrictions remain.

## Futures data and limits

| Source | What works | Boundary |
|---|---|---|
| Saved exact-contract imports | Offline completed-bar analysis, costs and conditional levels | User-declared data; cannot prove live timing |
| Yahoo MES/MNQ/ES/NQ references | Bounded free public retrieval and reference analysis | Continuous series, unverified delay/adjustment; not an exact contract |
| Existing Webull HK sandbox | Three GET-only SDK reads: contract metadata, M5 history, timestamped price | Permanently sandbox; no production prices or futures orders |
| Production CME data | Not enabled by this release | Requires an independently verified permitted entitlement; none purchased |

MES: $5/point, 0.25 tick = $1.25. MNQ: $2/point, 0.25 tick = $0.50. ES: $50/point, 0.25 tick = $12.50. NQ: $20/point, 0.25 tick = $5. The monitor assumes $1.25/side for micros or $2.50/side for minis plus one tick of slippage per side. Costs are assumptions, not a verified fee schedule. Stops do not cap loss through gaps. Quantity is unapproved and margin is unknown; the existing Trade lab retains explicit risk/margin sizing.

Quarterly codes H/M/U/Z use the CME third-Friday 08:30 Chicago expiration rule, labelled as such. The sandbox adapter additionally checks provider root, symbol, USD, XCME, multiplier, tick and expiry date. The numeric snapshot `last_trade_time` is preserved as Unix milliseconds; a newer HTTP response cannot refresh an older trade time.

Regular sessions use America/Chicago, Sunday–Friday 17:00–16:00 CT, with the documented 15:15–15:30 CT recess. DST is timezone-based. Published holiday windows without verified product-level segments are UNKNOWN, including September 6–8, 2026; dates outside the supported 2026 calendar also fail closed. This is **not a complete CME holiday calendar**. No U.S. equity calendar is substituted. Unknown sessions are omitted, gaps never fill themselves, and shortened buckets stay explicitly partial and are excluded from strategy indicators. Expiry clips remaining contract time. Daily aggregation requires every bar across a complete known session; the sandbox adapter offers M5 only. Futures daily bars cannot be reconstructed from partial M5 history or silently use adjusted provider daily bars.

Current boundaries are intentionally visible. These free/sandbox sources can support research and paper practice; they do not qualify a delay-free futures purchase signal.

## SPX snapshot versus repriced scenario

Open GEX. The original snapshot calculation remains unchanged. **Reprice an older SPX chain** requires a newer positive SPX price, its observation timestamp (with timezone), and a source. **Get public SPX price** makes one explicit Yahoo request; it never downloads a chain or changes stock chart state.

Repricing requires every surviving contract’s native SPX/SPXW identity, multiplier 100, exact qualified lifecycle, OI and IV. It can use IV solved from the original timestamped bid/ask where the existing solver qualifies it. Missing OI is not zero; missing IV cannot be replaced by scaling fixed aggregate gamma. Contracts already expired at the newer observation are counted and excluded. Gamma is recomputed at the newer price/time, using original strike IV and declared rates/dividend assumptions. The original chain, OI date and observation time remain immutable.

The resulting curve and crossings are explicitly **modelled using older chain observations**. Export includes both dates, source chain ID/hash when available, rate/dividend, positioning convention, exclusions and synthetic/sandbox provenance. It is not a newly observed live chain. Changing assumptions clears the previous scenario so it cannot silently claim new settings.

Exposure units remain:

```
assumed signed contracts × gamma × multiplier × spot² × 0.01
```

This is USD delta-notional sensitivity to a 1% move, not expected profit or observed dealer holdings. The current saved Webull SPX chain is sandbox/lifecycle-unqualified; it does not produce qualified snapshot GEX or repricing. Manually download/import a permitted native SPX/SPXW chain with exact lifecycle metadata. Price alone is insufficient.

## Sources checked 8 September 2026

- [CME micro equity futures fact card](https://www.cmegroup.com/trading/equity-index/files/cme-micro-e-mini-futures-fact-card.pdf): micro point/tick economics and quarterly expiration.
- [CME micro futures FAQs](https://www.cmegroup.com/articles/faqs/micro-e-mini-equity-index-futures-frequently-asked-questions.html): regular session/recess and micro/mini relationships.
- [CME trading hours](https://www.cmegroup.com/trading-hours.html): 2026 holiday windows; detailed product holiday segments remain unverified here.
- [CME delayed quote limitations](https://www.cmegroup.com/trading/about-all-delayed-quotes.html): public quotes delayed at least ten minutes.
- [Webull HK futures data](https://developer.webull.hk/apis/docs/reference/futures-market-data/): production futures subscription requirement. Sandbox success is not proof of production entitlement.
- [Webull futures history](https://developer.webull.hk/apis/docs/reference/futures-historical-bars/): M5/minute observations and adjusted daily-or-higher limitations.
- [Cboe extraction restriction](https://www.cboe.com/delayed_quotes/API/quote_table/): no automated table extraction. This application retains user-operated download/import only.

No ZIP, KX deletion, purchases, deployment, credential changes or orders were part of this update.
