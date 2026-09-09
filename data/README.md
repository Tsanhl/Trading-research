# Private local data

This folder is the hub's local data workspace. Do not put keys, secrets or account passwords here.

| Location | Contents |
|---|---|
| `hub.sqlite3` | Versioned research, provenance, decisions, positions, quotes, alerts, reports and worker checkpoints |
| `inbox/` | OHLC JSON exports or CSV plus matching `.metadata.json` |
| `raw/` | Hash-bound original input bytes and public research captures |
| `normalized/` | Validated research-only OHLC; not independent feed certification |
| `manifests/` | Intake results and quality checks |
| `quarantine/` | Rejection evidence; originals remain untouched |
| `records/inbox/` | Your explicit candidate/position/quote JSON files; import with `record-import` |
| `legacy_manifests/` | Existing backtest capture inventory; missing metadata is not invented |

See [Data input guide](../docs/DATA_INPUT_GUIDE.md) and [Hub operating guide](../docs/HUB_OPERATING_GUIDE.md).

SQLite uses WAL. Back up with SQLite's backup API or `.backup`, not by copying only the main DB while writes are active. Research may include private subscribed text; keep backups private. Source counts include overlapping captures and do not count independent sources. No live quotes or broker positions appear automatically.
