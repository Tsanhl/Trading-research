# Local market-data inbox

Put OHLC exports directly into `<project-root>/data/inbox`.
Keep API credentials in the existing private credential store, never in this folder.
The importer reads at most 100 direct `.json`/`.csv` files per call, at most 16 MiB and
200,000 bars per file. It does not recurse into folders or follow input symlinks.
Original files remain in the inbox. Remove processed files from the inbox yourself
if you want fewer files scanned on subsequent runs; retained data remains available.

| Folder | Contents |
|---|---|
| `data/inbox` | User-supplied exports and CSV metadata sidecars |
| `data/raw` | Exact source bytes addressed by SHA-256, plus safe metadata sidecars |
| `data/normalized` | Validated, ordered UTC bars and explicit instrument metadata |
| `data/manifests` | Accepted ingestion records, row counts, quality limits and hashes |
| `data/quarantine` | Rejection records with fixed reason codes; originals are preserved |

An accepted file has status `ACCEPTED_RESEARCH_ONLY`. This establishes structural
validity, not market accuracy, exchange-feed provenance, historical option fill
quality, an untouched holdout, or authority to trade. Conflicting/duplicate,
overlapping, unsorted, malformed, off-tick futures, future and unclosed bars reject
the entire file. No bar is silently sorted, removed, rounded, or imputed.

## JSON format

Supply exactly `metadata` and `bars`, compatible with the existing local replay
dataset format. The importer requires the additional explicit provenance and time
fields below. It does not infer them from the filename or chart symbol.

```json
{
  "metadata": {
    "instrument": "SPY",
    "root": "SPY",
    "asset_class": "ETF",
    "venue": "NYSE_ARCA",
    "currency": "USD",
    "session": "RTH",
    "timeframe": "5m",
    "adjustment": "unadjusted",
    "source": "your-export-provider",
    "upstream_origin": "documented-upstream-feed",
    "environment": "production",
    "timezone": "UTC",
    "timestamp_convention": "explicit_intervals",
    "calendar_policy": "observed_only_unverified"
  },
  "bars": [{
    "open_time": "2026-09-01T13:30:00Z",
    "close_time": "2026-09-01T13:35:00Z",
    "open": "640.00",
    "high": "641.00",
    "low": "639.00",
    "close": "640.50",
    "volume": "100000"
  }]
}
```

The above numbers demonstrate the schema and are not market observations. Set
`environment` to `synthetic` for fixture data. Valid values are `production`,
`sandbox`, and `synthetic`. Supplying a production label does not verify it.

Use `timezone: "UTC"` when timestamps use `Z`/`+00:00`. If retaining original New
York timestamps, use `America/New_York` and the correct `-04:00`/`-05:00` offset for
each timestamp. Naive timestamps, epoch numbers, offsets inconsistent with the
declared timezone, and ambiguous local times are not guessed.

Fixed intraday intervals are supported as positive integer seconds/minutes/hours,
such as `30s`, `5m`, or `1h`. Each bar must have exactly that duration. Daily and
calendar-length bars need a separate exchange-calendar normalization route and
are currently rejected. Shortened final hourly bars must be handled explicitly
in that future route, not padded. Gap summaries report observations only: a gap
may be an exchange closure. Session completeness is explicitly unverified.

## CSV format

Place `spy.csv` with `spy.metadata.json`. The sidecar contains the metadata object
alone, not the outer `metadata`/`bars` wrapper. For `bar_start` or `bar_end`, use:

```csv
timestamp,open,high,low,close,volume
2026-09-01T13:30:00Z,640.00,641.00,639.00,640.50,100000
```

Set `timestamp_convention` to the provider's documented `bar_start` or `bar_end`.
The importer derives the other endpoint using the declared fixed timeframe.
For `explicit_intervals`, use `open_time,close_time,open,high,low,close,volume`.
Volume is optional, finite and nonnegative. Decimal prices are retained without
rounding. Other columns and duplicate CSV headings are rejected.

## Exact instruments

- ETFs: exact SPY and QQQ currently match the replay engine's supported universe.
- Futures: exact MES/ES/NQ expiry symbol, e.g. `MESU6`, `venue: "CME"`,
  `asset_class: "FUTURE"`, explicit ISO `expiry` instant, `adjustment: "unadjusted"`.
  Continuous/root symbols and bars at/after expiry are rejected. Different expiry
  contracts belong in different datasets; a roll is never silently applied.
- Options: `asset_class: "OPTION"` plus exact `instrument`, `underlying`, `strike`,
  `right` (`CALL`/`PUT`), ISO `expiry` instant and `multiplier`. These imports support
  research inventory only. The local futures/ETF simulator does not simulate
  options, and OHLC does not supply historical spreads, quotes, Greeks or fills.

`provenance_ref` can identify a locally retained source/export evidence record.
It does not by itself establish reviewed provenance or source independence.

## Programmatic interface and comparison

```python
from trading_hub.data_intake import ingest_inbox, inventory, compare_datasets

result = ingest_inbox(root="/path/to/Trading Research Hub",
                      as_of="2026-09-05T00:00:00Z")
available = inventory(root="/path/to/Trading Research Hub")
# Supply two retained data/normalized/<hash>.json paths:
# comparison = compare_datasets(left_path, right_path)
```

Use the actual collection/inspection time for `as_of`; a date deliberately in
the future would defeat the unclosed-bar check. The same file, metadata, cutoff
and importer version produce the same manifest. Repeating with a later cutoff
retains another assessment without changing the normalized content identity.

Comparison requires matching exact instrument, session, interval, timezone,
adjustment, environment and contract terms. The same provider or same upstream
is rejected as a putatively independent pair. Distinct declared sources produce
only `DIAGNOSTIC_ONLY`, reporting exact-interval overlap and OHLC discrepancies.
Independent provenance still requires review; there is no automatic “verified”
promotion and no trade authority. Volume comparison is not implemented.

Before a formal backtest, review timestamps and exchange calendars, classify
missing periods, investigate cross-source differences, and register periods
already inspected so they cannot later be presented as untouched holdouts.
