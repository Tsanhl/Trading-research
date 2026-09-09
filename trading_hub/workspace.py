"""Local journal orchestration. No brokerage, credentials or notification transport."""
from __future__ import annotations

from datetime import datetime, timezone
from . import portable_lock as fcntl
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .common import ROOT, canonical, config, digest, instant, now_iso
from .decisions import evaluate_candidate, evaluate_position
from .storage import HubStore

VERSION = "hub-workspace-1"


def policy():
    cfg = config()
    return {key: value for section in ("paper", "backtest", "policy")
            for key, value in cfg[section].items()
            if key in {"risk_per_trade_usd", "daily_loss_threshold_usd", "max_futures_contracts",
                       "max_mes_contracts", "max_etf_shares", "quote_max_age_seconds"}}


def initialize(root=ROOT):
    root = Path(root)
    for name in ("inbox", "raw", "normalized", "quarantine", "manifests", "records/inbox"):
        path = root / "data" / name
        if path.is_symlink():
            raise ValueError("Workspace directory cannot be a symlink")
        path.mkdir(parents=True, exist_ok=True)
    with HubStore(root / "data/hub.sqlite3") as store:
        return {"database": str(store.path), "counts": store.stats(), "broker_execution": False,
                "quote_provider": "NOT_CONNECTED", "version": VERSION}


def load_record_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("Record input must be a regular file under 2 MB")
    raw = path.read_text()
    if re.search(r"(?i)(app[_ -]?secret|api[_ -]?key|access[_ -]?token|password|-----BEGIN )", raw):
        raise ValueError("Sensitive-looking record was not imported")
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("Duplicate JSON field")
            obj[key] = value
        return obj
    value = json.loads(raw, object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
    if not isinstance(value, dict):
        raise ValueError("Record must be an object")
    return value


def latest_positions(store, as_of=None):
    latest = {}
    rows = store.list_records("position", limit=10000)
    rows.sort(key=lambda row: instant(row["recorded_at"]), reverse=True)
    for row in rows:
        if as_of and instant(row["recorded_at"]) > instant(as_of):
            continue
        latest.setdefault(row["position"]["position_id"], row)
    return [row for row in latest.values() if row["state"] == "OPEN"]


def import_record(kind, value, *, root=ROOT, as_of=None):
    as_of = as_of or now_iso()
    instant(as_of)
    initialize(root)
    # Idempotency is based on the input, not when the command was repeated.
    key = digest([kind, value])
    with HubStore(Path(root) / "data/hub.sqlite3") as store:
        prior = store.get_record(kind, key)
        if prior:
            return {"status": "ALREADY_IMPORTED", "record_id": prior["store_record_id"]}
        if kind == "decision":
            payload = evaluate_candidate(value, as_of, policy())
            payload["provenance_resolution"] = "UNRESOLVED_IMPORTED_REFERENCES"
        elif kind == "position":
            fields = {"position_id", "origin", "instrument", "side", "quantity", "opened_at",
                      "entry", "stop", "targets", "horizon", "candidate_id"}
            position = {k: v for k, v in value.items() if k in fields}
            # This is an explicitly user-supplied local record, never a broker snapshot.
            position["origin"] = "user_imported"
            key = digest([kind, position])
            prior = store.get_record(kind, key)
            if prior:
                return {"status": "ALREADY_IMPORTED", "record_id": prior["store_record_id"]}
            check = evaluate_position(position, {}, as_of, policy())
            if any(x["kind"] in {"INVALID_POSITION", "INVALID_INPUT"} for x in check):
                return {"status": "REJECTED", "reasons": check}
            versions = [r for r in store.list_records("position", limit=10000)
                        if r["position"]["position_id"] == position["position_id"]]
            if versions:
                previous = max(versions, key=lambda r: instant(r["recorded_at"]))
                if previous["state"] != "OPEN":
                    raise ValueError("Closed position IDs cannot be reused")
                if instant(as_of) <= instant(previous["recorded_at"]):
                    raise ValueError("Position update must follow its previous revision")
                for field in ("instrument", "side", "opened_at", "entry"):
                    if position.get(field) != previous["position"].get(field):
                        raise ValueError("Position identity cannot be changed; use a new ID")
            payload = {"position": position, "state": "OPEN", "recorded_at": as_of,
                       "broker_reconciled": False, "input_hash": key}
        elif kind == "quote":
            fields = {"instrument", "price", "unit", "known_at", "source_id", "environment", "delayed"}
            quote = {k: v for k, v in value.items() if k in fields}
            if not isinstance(quote.get("instrument"), dict):
                raise ValueError("Exact quote instrument required")
            instant(quote.get("known_at"))
            if instant(quote["known_at"]) > instant(as_of):
                raise ValueError("Future quote not imported")
            canonical(quote)
            payload = {"quote": quote, "recorded_at": as_of, "transport": "LOCAL_FILE",
                       "provider_verified": False, "input_hash": key}
        else:
            raise ValueError("Only decision, position or quote imports are supported")
        identifier = store.save_record(kind, payload, dedup_key=key)
        return {"status": "IMPORTED", "kind": kind, "record_id": identifier,
                "schema_valid": payload.get("schema_valid"), "blockers": payload.get("blockers", []),
                "broker_execution": False}


def close_position(position_id, *, root=ROOT, as_of=None):
    """Close only the local monitoring record, without recording a broker fill."""
    as_of = as_of or now_iso()
    instant(as_of)
    with HubStore(Path(root) / "data/hub.sqlite3") as store:
        selected = [r for r in latest_positions(store, as_of) if r["position"]["position_id"] == position_id]
        if not selected:
            raise ValueError("No open local position with this ID")
        row = selected[0]
        if instant(as_of) <= instant(row["recorded_at"]):
            raise ValueError("Local close must follow position import")
        result = {"position": row["position"], "state": "CLOSED_LOCALLY", "recorded_at": as_of,
                  "broker_reconciled": False, "previous_record_id": row["store_record_id"]}
        store.save_record("position", result)
        return {"position_id": position_id, "state": "CLOSED_LOCALLY", "orders_sent": 0,
                "note": "Monitoring stopped; no broker position was closed and no fill/P&L was fabricated."}


def observe_positions(*, root=ROOT, as_of=None):
    as_of = as_of or now_iso()
    instant(as_of)
    with HubStore(Path(root) / "data/hub.sqlite3") as store:
        positions = latest_positions(store, as_of)
        quotes = store.list_records("quote", limit=10000)
        added = []
        for row in positions:
            position = row["position"]
            matches = [q for q in quotes if q["quote"]["instrument"] == position["instrument"]
                       and instant(q["recorded_at"]) <= instant(as_of)
                       and instant(q["quote"]["known_at"]) <= instant(as_of)]
            matches.sort(key=lambda q: instant(q["quote"]["known_at"]), reverse=True)
            quote = matches[0]["quote"] if matches else {}
            events = evaluate_position(position, quote, as_of, policy())
            for event in events:
                event.update(input_transport="LOCAL_FILE_UNVERIFIED", notification_status="PENDING_REVIEW",
                             position_record_id=row["store_record_id"], live_signal_verified=False)
                if not matches:
                    event["kind"] = "NO_QUOTE"
                    event["event_id"] = digest([row["store_record_id"], "NO_QUOTE"])
                if store.save_record_once("alert", event, dedup_key=event["event_id"]):
                    added.append(event)
        return {"observed_at": as_of, "open_local_positions": len(positions), "new_events": added,
                "continuous_feed": False, "notifications_sent": 0, "orders_sent": 0}


def alerts(*, root=ROOT, acknowledge=None, as_of=None):
    with HubStore(Path(root) / "data/hub.sqlite3") as store:
        if acknowledge:
            original = store.get_record("alert", acknowledge)
            if not original:
                raise ValueError("Unknown alert event ID")
            if not store.get_record("alert_receipt", acknowledge):
                store.save_record("alert_receipt", {"event_id": acknowledge, "acknowledged_at": as_of or now_iso(),
                                                     "status": "USER_ACKNOWLEDGED_NOT_BROKER_EXECUTED"}, acknowledge)
        receipts = {r["event_id"] for r in store.list_records("alert_receipt", limit=10000)}
        return [{**row, "acknowledged": row["event_id"] in receipts}
                for row in store.list_records("alert", limit=10000)]


def ingest_data(*, root=ROOT, as_of=None):
    from .data_intake import ingest_inbox
    result = ingest_inbox(root=root, as_of=as_of or now_iso())
    with HubStore(Path(root) / "data/hub.sqlite3") as store:
        for manifest in result.get("files", []):
            store.save_record("dataset", manifest)
    return result


def run_cycle(*, root=ROOT, as_of=None, refresh=False):
    """One bounded scheduled pass; flock prevents overlap, no hidden daemon."""
    from .briefing import build_brief
    from .workers import run_worker
    fixed_cutoff = as_of
    as_of = as_of or now_iso()
    stamp = datetime.fromtimestamp(instant(as_of), timezone.utc)
    initialize(root)
    lock_path = Path(root) / "data/cycle.lock"
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "ALREADY_RUNNING"}
        db = Path(root) / "data/hub.sqlite3"
        hour_key = "research:" + stamp.strftime("%Y-%m-%dT%H")
        with HubStore(db) as store:
            research_done = store.get_record("cycle", hour_key)
        workers = []
        if refresh or not research_done:
            workers = [run_worker(stream, db_path=db, refresh=True) for stream in ("macro", "structure")]
            with HubStore(db) as store:
                if not research_done:
                    store.save_record("cycle", {"kind": "research_pass", "as_of": as_of,
                                                 "workers": workers}, hour_key)
        evaluation_at = fixed_cutoff or now_iso()
        data = ingest_data(root=root, as_of=evaluation_at)
        observations = observe_positions(root=root, as_of=evaluation_at)
        hkt = datetime.fromtimestamp(instant(evaluation_at), timezone.utc).astimezone(ZoneInfo("Asia/Hong_Kong"))
        reports = []
        # Catch up after the scheduled morning time; never backfill unknown old news.
        if (hkt.hour, hkt.minute) >= (7, 30):
            reports.append(build_brief("daily", root=root, as_of=evaluation_at))
            if hkt.weekday() == 6:
                reports.append(build_brief("weekly", root=root, as_of=evaluation_at))
        status = "PARTIAL" if any(w["status"] != "COMPLETE" for w in workers) else "COMPLETE"
        return {"status": status, "as_of": evaluation_at, "workers": workers,
                "data_status": data.get("status"), "observations": observations, "reports": reports,
                "continuous_feed": False, "broker_orders": 0}
