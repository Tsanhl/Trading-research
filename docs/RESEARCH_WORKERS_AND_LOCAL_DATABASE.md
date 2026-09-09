# Research workers and local database

The integrated store defaults to `data/hub.sqlite3` in Trading Research Hub. It uses Python's standard SQLite library, WAL journaling, a 30-second busy timeout, foreign keys, and mode 0600 for the database. This is private local storage: imported subscribed research stays on this machine. Backups should include a SQLite-consistent snapshot, not a copy of only the database while WAL writes are active.

## Two bounded workers

- `macro`: imports the existing 194-document EdgeRunner corpus through a read-only SQLite connection. With `refresh=True`, also captures the bounded public EdgeRunner RSS feed and the official Federal Reserve monetary-policy RSS feed. RSS text is labelled feed content/summary, never a verified full article.
- `structure`: imports KevinX normalized posts, additional click-audit posts, quote relationships, media archive metadata, click routes, coverage manifest and crawl checkpoint. It preserves unresolved media hashes, conflicting legacy completion claims and inaccessible windows. `refresh=True` rechecks these local exports; it does not claim to crawl new authenticated X pages.

The Fed feed is linked from its [official RSS directory](https://www.federalreserve.gov/feeds/feeds.htm) at `https://www.federalreserve.gov/feeds/press_monetary.xml`, verified on 2026-09-05. Its public fetch is limited to 15 seconds and 2 MB, uses TLS verification, refuses redirects and accepts document links only on the official host. EdgeRunner uses the existing bounded RSS implementation and robots check. There is no credential access, broker call, external notification, daemon or schedule in these workers.

Each worker call is one resumable pass. Local import fingerprints avoid duplicate ingestion after restart. A batch's documents, revisions, provenance and checkpoint commit in one transaction; malformed input rolls back that batch and leaves the previous checkpoint intact. Errors expose a source label and exception class, never arbitrary error bodies. Successful feeds can commit even if another source is unavailable; the run then reports PARTIAL.

`published_at` records claimed publication time. `known_at` records observation availability and can be unknown. A post carrying later reviewed context uses the latest available evidence timestamp; original capture timestamps remain in its metadata. `ingested_at` separately records when the hub learned the revision. None of these imports imply the material was available to a historical strategy before collection. All source text is untrusted data and does not modify worker instructions or trade authority.

Initial local import on 2026-09-05: 194 macro documents and 597 structure records (596 unique post IDs plus one coverage record), totalling 791 documents and 4,970 provenance links. Coverage remains incomplete.

The first live bounded refresh also succeeded: 20 EdgeRunner RSS items and 15 official Fed monetary-policy items. This brought the store to 826 documents/revisions and 5,005 provenance links. These are source-specific records; an RSS article may overlap a legacy corpus article and is not counted as independent corroboration merely because it was captured twice.

## Python interface

```python
from trading_hub.workers import run_worker
from trading_hub.storage import HubStore, DEFAULT_DB

result = run_worker("macro", db_path=DEFAULT_DB, refresh=False)
result = run_worker("structure", db_path=DEFAULT_DB, refresh=False)
with HubStore(DEFAULT_DB) as store:
    metadata = store.list_documents("macro", limit=100, include_text=False)
    local_research = store.list_documents("structure", limit=100)
    cursor = store.checkpoint("structure")
    counts = store.stats()
    identifier = store.save_record("decision", {"decision_id": "example", "action": "NO_TRADE"}, dedup_key="example:v1")
    prior = store.get_record("decision", "example:v1")
    decisions = store.list_records("decision", limit=100)
```

`save_record` returns a deterministic ID string. Repeating an identical payload/key is idempotent; changing content under an existing key raises an error. Position changes append events using keys such as `position_id:version`. Journal retrieval returns each original payload with `store_record_id` added, newest first. Missing `get_record` returns `None`.

`save_record_once("alert", payload, dedup_key)` is an atomic first-write-wins operation returning a boolean: true only for the process that inserted the original event. It is restricted to alert records so two observation processes cannot race on their different observation timestamps. Other journal kinds and normal `save_record` preserve strict conflict detection. Threshold observations are deduplicated once per position revision. Diagnostic alerts such as STALE/NO_QUOTE currently also stay suppressed after their first event in that revision; later distinct outage episodes require a connection-health state machine before continuous-feed notification is qualified.

`list_documents(as_of=timestamp)` selects the newest stored revision whose original evidence `known_at` is at or before the cutoff. This is a retrospective evidence query, not proof of a historical hub state. Optional `ingested_by=timestamp` additionally excludes revisions that the hub ingested later. Results include `hub_ingested_at` so the distinction is visible. A report claiming an exact historical state must use its retained frozen snapshot; current local source files and retrospective metadata do not establish that state.

Supported journal kinds accept singular/plural forms: decision(s), alert(s), position(s), report(s), dataset(s), inspected_period(s), cycle(s), quote(s), and alert_receipt(s). `list_documents` returns full local payloads plus `document_id` and `revision_id`; `include_text=False` restricts output to a small metadata allowlist. Raw RSS capture artifacts live below `data/raw/research/` and are hash referenced. Legacy source files are read in place and are not changed.

The store has `documents`, `document_revisions`, `provenance`, `checkpoints`, `worker_runs`, and append-only `records` tables. Provenance for the legacy SQLite corpus uses the hash of the selected committed rows, explicitly labelled `selected_committed_rows`; it is not represented as the physical database hash. Interrupted worker-run rows remain RUNNING as evidence of an unfinished attempt; a new invocation safely resumes from the last committed checkpoint.

These workers collect evidence. Strategy extraction, macro interpretation, data qualification, the decision journal, alert delivery acknowledgement and release gates remain separate consumers. No document alone authorizes an order.
