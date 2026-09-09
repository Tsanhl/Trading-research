# Migration guide — 0.6.0

The active `Trading Research Hub` database remains authoritative. Never replace `data/hub.sqlite3` with the archived refined donor database.

Before this release was changed, a source/state backup and SQLite online backups were written to:

`~/Library/Application Support/Trading Research Hub/Backups/pre-gex-enactment-20260908T113900Z`

On first 0.6.0 start, the app runs an idempotent transaction. It preserves every existing table and row, retains the 0.5 import-provenance schema, creates `web_broker_intents` and `web_broker_events`, records migration 3 in `hub_schema_migrations`, and raises `PRAGMA user_version` to 3. Only blocked, non-executable review tickets can enter those tables; the migration adds no order-submission code.

Validate both databases with:

```sh
python3 - <<'PY'
import sqlite3
for name in ('data/hub.sqlite3', 'state/local-paper.sqlite3'):
    with sqlite3.connect(name) as db:
        print(name, db.execute('PRAGMA quick_check').fetchone()[0], db.execute('PRAGMA user_version').fetchone()[0])
PY
```

Create a safe backup from **Data & health → Full private backup**. Restore only into a separate empty directory:

```sh
python3 -m trading_hub.restore_backup BACKUP.zip 'Separate Restore Folder'
```

The restore path rejects traversal, unsafe links, duplicate members, bad hashes and non-empty destinations. It never overwrites the active workspace.
