# Simple trade home — operator guide

Verified 9 September 2026. Open `http://127.0.0.1:8787/#home`.

The page has four working areas:

1. **Leading setups** shows at most three intraday or swing reviews. MES/MNQ are ranked first. A BUY/SELL setup needs the shared completed-bar breakout, a later retest, aligned context and participation. WAIT is the correct result when inputs conflict or data is stale.
2. **Options, GEX and expected range** keeps three different calculations separate. Options need a matching timestamped stock/ETF chain. SPX GEX needs native SPX/SPXW OI plus gamma/IV and exact lifecycle data. The expected-range projection needs timestamped SPX and VIX1D values and uses `SPX × VIX1D / 100 / √252 × K` for K 0.9 and 1.4.
3. **Live alerts** watches later completed observations during an explicitly started, bounded browser session. Setup-ready, withdrawn confirmation, correction, flush-watch and trim-watch transitions are deduplicated. Browser notifications require explicit permission and the Hub/server must remain open.
4. **Daily & weekly macro/news** displays current private briefs. Starting the monitor creates a missing period from saved evidence. External source refresh happens only through **Refresh sources & rebuild**.

The swing label uses confirmed width-2 pivots and reports higher-high/higher-low, lower-high/lower-low or mixed structure. It describes a trend-leg, pullback or trading-range candidate. It does not invent an Elliott-wave number or claim that the full Kevin/Brooks methods are implemented.

`FLUSH WATCH` requires a completed bearish break, lower-high/lower-low structure, aligned momentum, participation and, when a current range exists, price below the inner range. `TRIM / DO NOT CHASE` requires price at a frozen first target or outer expected-range boundary. Neither state places an order.

Webull production, futures and options orders remain disabled. The only submission route is the separately confirmed Webull HK sandbox U.S. stock/ETF limit-order workflow. A Webull app market-data subscription is not treated as proof of OpenAPI entitlement.
