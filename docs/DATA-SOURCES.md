# Provider references and terms — checked 7 September 2026

These are primary provider references used for this build. Access plans and terms may change; verify eligibility and permitted personal use before connecting or sharing data. A subscription on a provider website does not automatically grant website redistribution rights.

## TradingView (official)

- Advanced Chart widget and official embedding code: https://www.tradingview.com/widget-docs/widgets/charts/advanced-chart/
- Free widget North American availability table: https://www.tradingview.com/widget-docs/markets/north-america/
- Widget data FAQ: https://www.tradingview.com/widget-docs/faq/data/
- General/privacy and embedding FAQ: https://www.tradingview.com/widget-docs/faq/general/
- Container sizing: https://www.tradingview.com/widget-docs/tutorials/build-page/widget-integration/

The current table labels NASDAQ/NYSE/Arca stock widget feeds as delayed. Certain symbols are unavailable in widgets. A paid individual TradingView subscription does not upgrade the free embedded widget feed. The app retains attribution, uses the official script and does not extract or republish the widget's data into its API.

## Alpaca (official)

- Plans and authentication: https://docs.alpaca.markets/us/docs/about-market-data-api
- Historical feed definitions: https://docs.alpaca.markets/us/docs/historical-stock-data-1
- Historical-data guide: https://alpaca.markets/learn/fetch-historical-data
- Stock bars: https://docs.alpaca.markets/us/v1.4.2/reference/stockbarsingle-1
- Stock snapshot: https://docs.alpaca.markets/us/reference/stocksnapshotsingle
- Streaming versus polling: https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data

Basic is a free plan for eligible accounts with real-time IEX-only equity coverage, not complete all-exchange/SIP data. The app explicitly requests `feed=iex`. It does not implement streaming or use paid SIP/OPRA endpoints. Bars are capped at a bounded recent window; pagination is disclosed rather than represented as complete backtest history. Sparse/old IEX observations do not become fresh merely because the endpoint was polled.

## Yahoo / optional yfinance

- yfinance project documentation: https://ranaroussi.github.io/yfinance/
- Download interval documentation: https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html

This is an unofficial public personal-research path, not an exchange-certified feed. Requests can be blocked, limited or changed. The built-in implementation tries the chart path and can use the optional installed package. No delay-free, global symbol-availability or data-redistribution guarantee is provided.

## Native SPX chain policy

- Cboe SPX quote page and automated-extraction notice: https://www.cboe.com/delayed_quotes/spx/realtime_quotes

The app does not scrape this page or build a workaround for its automated-extraction prohibition. Import an authorized native chain supplied by the user. SPX options and OI are different datasets from a free stock chart. Their absence is reported as unavailable. The SPX examples are fictional schema demonstrations, not downloaded market observations.

## Verification here

HTTP request/schema behavior was tested with controlled responses. Outbound attempts to Yahoo and TradingView from the build environment failed DNS resolution; authenticated Alpaca keys were not supplied. Browser data was synthetic QA material. Documentation research is not equivalent to a successful live end-to-end feed test.
