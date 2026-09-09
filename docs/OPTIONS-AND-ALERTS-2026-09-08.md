# Options candidates and local setup alerts — 8 September 2026

The local Hub is the deliverable. This update adds a direct options view and bounded dashboard/browser alerts. It does not submit orders, qualify the KX strategy, or create a schedule. No new ZIP was made.

## Options workflow

1. Open `http://127.0.0.1:8787/#options` and choose a US stock/ETF.
2. Fetch a public underlying/chain snapshot, or import your own exact standard contracts through Data & health. A price chart alone cannot supply option premiums. SPX GEX selection stays independent.
3. Choose calls/puts and long option/debit vertical. The existing deterministic candidate engine applies its 7–21 DTE, delta and liquidity filters. The wrapper verifies underlying/OCC date/type/strike/multiplier linkage before presenting candidates.
4. Read the exact buy/sell contracts, bid/ask observation times, expiry, OI/date, volume and Greek source. Review the underlying conditional scenarios and macro/event gaps underneath.
5. Assess expiry payoff, maximum premium loss/gain and maximum payoff R:R. These are arithmetic scenarios, not expected returns or win probabilities. Entry uses ask for purchases and bid for the sold spread leg, plus assumed $0.02 slippage per leg and $0.65 commission per contract per side at entry and exit. Actual broker fees and executable prices remain separate inputs. Expiry payoff does not forecast pre-expiry value; exercise/assignment, dividend, pin and gap risks remain.
6. Leave premium-risk budget blank to compare R:R without assuming an account balance. An entered budget gives only a rounded-down risk cap; insufficient budget gives zero. Manual payoff arithmetic also defaults to no size. No quantity is approved.
7. Save an idea to retain the exact legs, quote/data gates, snapshot identity, source/bar hashes, costs, rule version, known-at timestamp and no-order status in the journal.

Unknown or older-than-60-second quote times, unsynchronized spread legs, stale underlying references, unverified expiry metadata, missing OI dates, expired/current-DTE exclusions and synthetic/sandbox origins prevent a current quoted candidate. Some arithmetic remains useful and is permanently labelled **THEORETICAL / HISTORICAL ONLY**. Modelled delta is labelled separately. Exact contract metadata declarations are not independent authentication. Options order submission and 0DTE recommendations remain disabled.

A real public SPY request at approximately 15:50 UTC on 8 September returned 588 option rows. It supplied no bid/ask timestamps or verified lifecycle metadata; the underlying reference was the 4 September completed daily close. The UI displayed gated historical/theoretical scenarios. This proves adapter connectivity and the empty/qualified-state distinction, not real-time option quotes or a current purchase opportunity. See the sanitized public-provider receipt in the QA folder.

## Alert workflow and limits

Open `http://127.0.0.1:8787/#monitor`. Dashboard alerts are enabled by default, but polling always starts off. Choose symbols/source/cost assumptions, then **Start monitoring**. **Scan now** alone is a one-shot review and does not arm alert monitoring.

For native messages, open the URL in Chrome, click **Enable browser notifications**, grant permission for this loopback origin, then **Send test notification**. Site settings → Notifications can recover a prior denial. macOS notification permissions and Focus settings also affect display. The embedded Codex browser returned `denied` in this verification; Chrome's real permission/OS delivery step remains unverified. Test automation simulates granted/denied permission only in a disposable profile and is not evidence of an OS banner.

The alert engine wraps the existing Hub scenarios:

- The initial scan establishes a baseline; past signals are never replayed as new opportunities.
- A newly observed completed breakout requires the existing higher-timeframe context and participation checks. It freezes entry, stop, targets, no-chase, boundary, source hash and signal bar.
- A **later** completed retest must touch and hold that frozen boundary, close in the intended direction, remain inside stop/no-chase and retain context/participation. Then `SETUP_READY_FOR_REVIEW` is recorded and, if permitted, a browser notification is requested.
- Stop breach takes priority over favorable prices in the same candle. Pending setups expire after their existing six intraday or ten swing completed-bar window. A passed target/no-chase, lost context, stale data, session boundary, correction or observation gap withdraws confirmation as applicable.
- Duplicate bars do not repeat alerts. Corrections retain prior records and add a correction event. Paused/missed-bar transitions cannot retrospectively confirm a retest. A dated alert in the banner is not a fresh entry signal.
- Events retain frozen levels/hashes and source times. Up to 100 remain in browser storage; event snapshots and lifecycle records are also saved to SQLite. Journal errors are shown explicitly. No credential enters browser storage.

**Review-ready does not mean purchase-approved.** Current executable quotes, complete event coverage, strategy evidence and account suitability remain separate gates. An equity alert cannot authorize an option with missing quote/contract data. KX still has no entry authority. Any supported Webull stock/ETF paper order retains its own provider preview and exact typed confirmation. Production and option orders remain unreachable.

Polling defaults to 120 seconds, with daily history refreshed at most every 15 minutes; this is not every-tick streaming. Each session stops after four hours or the current session close plus five minutes, whichever is sooner. Without granted browser notifications, hiding or leaving the monitor view pauses it. With permission and the preference enabled, it can continue across Hub views or another tab. Closing the page, Stop, going offline or the deadline ends it. Sleep/browser throttling may delay checks; no service worker, cron, launchd, messaging service or recurring Codex task is installed. Restarting the page/server does not restart polling.

## KX folder decision (superseded by the later source refresh)

Later on 8 September 2026, KX was backed up consistently, verified and archived intact outside Desktop. See [current archive receipt and source coverage](SOURCE-TO-RULE-COVERAGE-2026-09-08.md). The older inventory below used a different scope and is retained as historical context.

The Hub reads `integrated/kx-reference`, not the Desktop KX directory. All 16 selected lineage hashes match. The separate `KX_Structure_Trade_Planner_v11_1_PACKAGE` contains 8,392 non-cache/non-environment files (835,011,834 bytes) and three links. That includes work outside the selected reference copy. No matching process or inspected LaunchAgent/Codex automation/crontab reference was found at audit time; this is a point-in-time inspection, not a guarantee about all future usage.

**Keep KX until a complete private archive is verified.** Removing it would not be the same as removing a redundant copy. The smallest next cleanup action is an intact archive outside Desktop, with SQLite backup of every donor database and a checksum/restore receipt, followed by a Hub startup check without the old paths. This update did not move or delete KX. Its nine release gates and holdout restrictions remain unchanged.

## Verification and rollback

Before changes, source/config/docs and both active SQLite databases were backed up consistently in `backups/local-pre-options-alerts-20260908T153506Z`. Tests run against disposable databases; current research remains authoritative. See `qa/options-alerts-2026-09-08/TEST-REPORT.md`, logs, preservation comparison and build receipt for current results, attempts and limitations.

Rollback source only from that private backup if necessary. To inspect old database state, restore into a separate destination using the existing backup/restore workflow; do not replace current user records blindly.

Official references checked on 8 September 2026: [OIC bull call spread](https://www.optionseducation.org/strategies/all-strategies/bull-call-spread-debit-call-spread) for expiry payoff/assignment risks, and [MDN Notifications API](https://developer.mozilla.org/en-US/docs/Web/API/Notifications_API/Using_the_Notifications_API) for user-gesture permission and browser support. These references do not qualify the experimental strategy.
