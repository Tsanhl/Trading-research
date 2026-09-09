# Migration guide — 0.5.0

The current project is the base. Do not copy an older bundled database over `data/hub.sqlite3`.

On first start, the application performs an idempotent schema migration inside a SQLite transaction:

1. Preserve every existing table and row.
2. Create `hub_schema_migrations` for applied migration receipts.
3. Create `web_import_sources` and its hash index for exact raw-file provenance.
4. Raise `PRAGMA user_version` from 1 to 2. The storage layer will never lower a newer version.

Before upgrading, use the 0.5.0 private backup tool or stop the app and make a verified SQLite backup. A release backup was made before this merge at:

`~/Library/Application Support/Trading Research Hub/Backups/premerge-20260908T103616Z`

To validate:

```sh
python3 - <<'PY'
import sqlite3
for name in ('data/hub.sqlite3', 'state/local-paper.sqlite3'):
    with sqlite3.connect(name) as db:
        print(name, db.execute('PRAGMA quick_check').fetchone()[0], db.execute('PRAGMA user_version').fetchone()[0])
PY
```

Restore never writes into an active or non-empty project. Use:

```sh
python3 -m trading_hub.restore_backup BACKUP.zip 'Separate Restore Folder'
```

Inspect that folder before choosing any manual data replacement.
