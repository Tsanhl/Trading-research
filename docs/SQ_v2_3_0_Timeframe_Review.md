# SQ+ v2.3.0 — timeframe decision and reproducible diagnostic

## Decision

**Product decision: two separately labelled Direction contexts.**

- STRATEGY DIR: explicit 5m / 15m / 30m / 1h input, independent of chart view.
- CHART DIR: same selected direction rule on the visible chart timeframe.
- Visible MOM3, MOM6, EMA20, ADX14, Delta and recent CVD change follow the chart.
- Default RVOL is the current chart interval versus historical comparable intervals.
- VWAP and total CVD remain session calculations; Daily Pivot and Daily TR are unchanged.
- Alerts belong only to Strategy Direction. Local Direction does not veto it.
- No 1h background gate, 5m entry engine, MOM BIAS or 5% neutral buffer was added.

**Research decision: NO TIMEFRAME PROMOTION.**

15m remains an initial compatibility setting, not a protected or statistically proven winner. The timeframe can now be selected explicitly. The small test below does not approve 5m, 15m, 30m or 1h as an entry system. It is not the earlier proposed 60-session evaluation plus 30-session warm-up.

## 1. Actual data and scope

Connected Alpaca SIP 1m data: SPY and QQQ, **8–10 September 2026**, inclusive. Three complete normal RTH sessions per symbol, 390 minutes each: **2,340 minute records**.

The source returned OHLCV, but the reproducible archive contains a manually normalised **open/close/volume subset**, with timestamps reconstructed from the returned continuous 09:30–15:59 ET minute grids. Highs/lows and the full raw 1m JSON responses were not archived. This limitation matters: no ATR stop/target, intrabar adverse-excursion or quote-aware fill test is claimed.

A separate Alpaca request returned 30-minute bars for all six symbol-days. All **78 interval opens, 78 closes and 78 volume totals** reconciled to the normalised minute data (234 scalar comparisons). This checks interval endpoints and volume aggregation; it is not independent verification of every interior minute price or aggressor classification. File hashes and counts are in `results/source_manifest.json`; the reconciliation is in `results/independent_30m_reconciliation.csv`.

No IEX/SIP blending. No native SPX, NDX, options, NVDA or TSLA performance inference. These dates have been seen in earlier related research; this is **not out-of-sample evidence**.

## 2. Exact replay method

1. Start from the common 1m data, building 5m/15m/30m/60m parents from 09:30 ET. The last hourly parent ends after 30 minutes at 16:00.
2. At each minute close, compute the developing parent's MOM3 using only the price then available and the third preceding completed parent close. No future parent close is read. MOM6 is recorded as context, not used as a compulsory filter.
3. Classify each minute's volume from close versus open; for ties use previous close, then retain previous nonzero polarity. Accumulate session CVD. This is a candle-direction estimate, not verified bid/ask aggressor flow.
4. CVD change3 subtracts session CVD at the close of the third preceding parent. Its history resets each session. Price-reference history may extend across sessions; CVD-reference history may not.
5. MOM-only and MOM+CVD are tested separately. BULL/BEAR uses raw positive/negative signs; otherwise WAIT. No EMA, ADX, MOM6, RVOL, VWAP or ATR-sign buffer in these tests.
6. Primary comparisons use common afternoon observation times, starting at **12:31 ET** (the close of the 12:30 minute). This gives every candidate enough same-session history. Morning availability is separately recorded; a 1h CVD-change3 candidate does not have an opening-session signal merely because its price MOM is available.
7. Enter at the next minute's open in the diagnostic, exit after 15, 30 or 60 minutes, never beyond the RTH close. Report signed basis-point price change. Flat 2bp and 5bp per-side assumptions subtract 4bp and 10bp per round trip. These are sensitivity assumptions, not verified executable costs.

The replay has minute, not tick, resolution. The first dataset day warms within the session before the common afternoon comparison; no claim of a separate 30-day warm-up is made. EMA/ADX/RVOL trading-performance tests are not included.

## 3. Thirty-minute event diagnostic

Take a newly nonzero Direction (including WAIT-to-direction); do not enter on every bar while it remains unchanged. Only one diagnostic position per symbol/TF at a time, held exactly 30 minutes. No forced entry simply because the common observation window began. Each row is a separate candidate, not one combined portfolio.

| Calculation TF | Events | Mean gross bps | Net: 2bp/side | Net: 5bp/side |
| --- | --- | --- | --- | --- |
| 5m | 34 | +0.645 | -3.355 | -9.355 |
| 15m | 26 | -0.781 | -4.781 | -10.781 |
| 30m | 19 | -2.588 | -6.588 | -12.588 |
| 60m | 21 | -2.981 | -6.981 | -12.981 |

**All four candidates are negative after the 2bp-per-side assumption in this small event sample.** The 5m candidate has the highest gross mean in this particular event construction; that is not grounds to promote it from three days.

These events are not executions of a completed entry/stop/target system. The averages are per-event underlying-price outcomes, not account returns, option returns or R multiples.

## 4. Thirty-minute overlapping direction observations

Here a new forward observation is taken every eligible minute with a non-WAIT signal. Many observations overlap and share the same subsequent price path. They are **not independent trades**, and the active timestamps differ because the candidates wait at different times. The full fixed-clock results, which also keep WAIT at zero, are available in `summary.csv`.

| TF | Active overlapping observations | Coverage | Mean gross bps | Net: 2bp/side |
| --- | --- | --- | --- | --- |
| 5m | 743 | 68.8% | -0.430 | -4.430 |
| 15m | 694 | 64.3% | -2.287 | -6.287 |
| 30m | 596 | 55.2% | -1.907 | -5.907 |
| 60m | 702 | 65.0% | -0.320 | -4.320 |

The 1h candidate has the least-negative gross mean in this particular snapshot construction, whereas 5m ranks highest in the event diagnostic. This sensitivity to evaluation design is another reason not to announce a winner. Pooled snapshot MOM+CVD outcomes at 2bp per side are negative for all four TFs at all three tested forward horizons.

### MOM-only control, same 30-minute observation horizon

| MOM-only TF | Mean gross bps | Net: 2bp/side |
| --- | --- | --- |
| 5m | -0.013 | -4.013 |
| 15m | -1.351 | -5.351 |
| 30m | -1.946 | -5.946 |
| 60m | -0.769 | -4.769 |

The full symbol/date breakdown, 15-/30-/60-minute results, coverage and events are included in CSV files. SPY and QQQ are not interchangeable: for example, the 1h MOM+CVD snapshot gross mean at 30 minutes is approximately +0.863bp for QQQ but −2.091bp for SPY. Both are negative after the 4bp round-trip assumption.

## 5. What the new script displays

Example settings: Strategy TF=15m, visible chart=5m.

```text
STRATEGY DIR [15m]   BULL / BEAR / WAIT
CHART DIR [5m]       BULL / BEAR / WAIT
LIVE MOM 3 [5m]      raw value | movement wording
LIVE MOM 6 [5m CTX]  raw value | movement wording
EMA 20 [5m]
VWAP SESSION
ADX 14 [5m]
Delta [5m] EST
CVD SESSION EST      total | change3 [5m]
Daily P / R1 / S1
Daily TR
RVOL BAR [5m] EST
```

The Strategy row's hover tooltip and Data Window show its own MOM, CVD change and EMA. The visible local MOM is never silently substituted into the Strategy calculation.

On a 1h chart, the visible MOM3/MOM6 are again based on three/six hourly bars. Strategy Direction still uses its explicit selected timeframe. Nominal bar-count spans are not always exact wall-clock periods: live partial bars, session gaps and shortened final bars matter.

The green/red descriptive wording compares current live local MOM to the previous completed local MOM. The existing 2% local ATR wording deadband remains; it never changes raw MOM, sign, Direction or alerts. The deadband input can be set to zero.

Both Direction contexts apply the same rule independently. A BULL/BEAR disagreement describes different horizons, not an automatic entry or a compulsory veto. When both bases and inputs match, the calculation definitions match; native live/reload equivalence remains a runtime acceptance check.

## 6. Relative volume: what changed

Default BAR RVOL:

```text
volume accumulated inside the current chart bar so far
/
median volume in the same elapsed historical chart-bar interval
```

The engine retains native 1m cumulative histories. For BAR mode it subtracts volume before the chart interval from both the current numerator and each historical denominator. Completed prior minutes are used in full; only the currently developing minute is proportionally interpolated. Therefore it does not simply compare a small partial current bar with an entire past 15m/1h bar.

A high session volume total cannot automatically force BAR RVOL high: the current interval is assessed separately. Conversely, an active 5m burst may have high BAR RVOL while whole-session RVOL is subdued.

`RVOL interval → Session cumulative` restores the previous cumulative interpretation without adding another table row. Median baseline is deliberate; TradingView's standard Relative Volume at Time documentation describes an average baseline, so exact equality to that built-in is not promised.

Both modes need the minimum number of usable prior complete sessions (default 10, history window20). Zero historical denominator, missing source bars and insufficient samples remain N/A. BAR RVOL can legitimately be 0 when valid current volume is zero and its historical denominator is positive. This is an interpretability change, not a demonstrated performance improvement.

VWAP stays native-session 1m HLC3×volume/volume. ETF flow proxies for cash indices never provide substituted index prices or volume for VWAP/RVOL. Index provider-volume analytics remain references rather than executable index order flow.

## 7. Implementation review and limitations

**14 Python/model/static tests passed; native Pine compilation and live TradingView execution were NOT performed.**

Checks include causal prefix replay, explicit reference-index arithmetic, same-session CVD reset, session-CVD invariance across aggregation bases, equal-duration MOM at shared boundaries, interval versus session RVOL, zero-volume handling, exact preservation of Daily Pivot/TR code sections, no hidden EMA/sign-buffer gate, strategy-only alert references and lexical delimiter integrity.

The Pine update evaluates each Direction inside its own timeframe context, including nested 1m flow requests. It does not pair a previously completed strategy price with a newer chart-timeframe CVD window. Bad source data immediately clears VWAP/RVOL rather than leaving a previous value available behind an error status. Flow parent lengths use their actual timeframe/session bounds rather than a hardcoded 15-minute count. VWAP plots are segmented to avoid connecting separate RTH sessions.

Historical higher-timeframe requests retain confirmed snapshots between higher-timeframe closes; the current realtime value develops. Lower-timeframe values shown on larger host bars are snapshots, not every intrabar event. The external 1m replay, not the chart's final historical line, was used for this diagnostic. None of this certifies underlying exchange-tick timestamps or removes a delayed subscription.

Daily Pivot and Daily TR calculation/validation/rendering sections were copied unchanged and checked byte-for-byte. Local EMA/ADX now follow the chart; strategy EMA is separate internally when selected. Optional legacy risk-distance preview remains fixed15m and OFF by default. No broker orders, positions, expiry-aware options, stops, targets, no-chase rule or automated entry engine were added.

## 8. Installation / acceptance

Save the supplied `.pine` as a new indicator and compile it in TradingView. Defaults: Strategy15m (baseline), local rows follow chart, RVOL=Chart bar, alertsOFF.

Native acceptance should check 5m/15m/30m/1h views, same-TF row agreement, matched-time strategy readings, session reset, morning CVD warm-up, RVOL against constituent1m volume, provider outages and chart reload. Existing TradingView alerts must be recreated to use the new script/settings. No Alpaca credentials belong in Pine.

No background monitoring or automatic further study was scheduled. A full unseen timeframe study, larger symbol/date sample and entry/stop/target validation remain outstanding. Keep every candidate experimental until that evidence exists.

## Reproduce this exact diagnostic

```bash
python -m pip install numpy pandas
python replay.py
python validate.py
```

`build_pine.py` rebuilds the new indicator from the included v2.2.9 baseline; run it from the package folder after placing the baseline in its parent, as in the ZIP layout. `replay.py` is scoped to the included pilot; it is not an automated 60-session study runner. The reporting script reproduces this document and the packaging.

## Official technical references

- TradingView, Other timeframes and data: https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/
- TradingView, Repainting: https://www.tradingview.com/pine-script-docs/concepts/repainting/
- TradingView, Cumulative Volume Delta: https://www.tradingview.com/support/solutions/43000725058-cumulative-volume-delta/
- TradingView, Relative Volume at Time: https://www.tradingview.com/support/solutions/43000705489-relative-volume-at-time/
- Alpaca, Historical bars: https://docs.alpaca.markets/us/reference/stockbars

These references support API/calculation behaviour, not the profitability of any timeframe. Numerical results above are computed from the included connected-account data subset.
