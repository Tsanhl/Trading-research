# SPX examples are synthetic

`spx-gex-demo.json`, `spx-options-template.csv` and `cboe-spx-manual-export-synthetic.csv` contain fictional values to demonstrate native SPX input shapes. Do not trade from these levels or relabel them as live data.

Import JSON from SPX GEX, or use the explicit synthetic walkthrough button. No price candles are required for a chain-only SPX calculation. For a real authorized export, supply the actual source, underlying spot/time, capture time, OI date, exact contract roots, multiplier and AM/PM expiry timestamps. Schema validation cannot authenticate your provider declarations.

CSV fields:

```text
underlying,root,type,strike,expiry,oi,multiplier,iv,gamma,delta,bid,ask,volume,quoteAsOf,greekAsOf,ivAsOf,oiAsOf,greekSource,ivSource,settlementType,lastTradingAt,payoffFixingAt,settlementAt,expiryVerified,lifecycleSource,lifecycleRuleVersion,lifecycleIndependentlyVerified
```

`iv` is decimal (0.20 = 20%), not percent units. `oi` is whole contracts or blank when unknown. Contract multiplier is 100 for native SPX/SPXW. Lifecycle times must be timezone-aware ISO timestamps. `expiryVerified=true` does not authenticate a declaration; independent verification also requires a non-user lifecycle source and the separate `lifecycleIndependentlyVerified=true` flag. Blank timestamps and open interest remain unknown, rather than becoming current/zero.

The paired-Cboe adapter accepts a manually downloaded CSV with call and put columns on the same strike row. It does not fetch or scrape Cboe. If the file carries only an expiry date, the adapter creates a visibly provisional time so the supplied gamma arithmetic can be inspected. Those rows remain excluded from qualified aggregates and gamma-flip curves until exact last-trading, payoff-fixing and AM/PM settlement metadata is supplied.

The full JSON example demonstrates the complete schema, including source/provenance. Do not simply rename a stock/ETF/futures chain to SPX; those are distinct products and explicit proxies are rejected.
