"""Sanitized provider, source and session health for the local dashboard."""
from __future__ import annotations

from datetime import datetime, time, timezone
from contextlib import closing
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tomllib
from zoneinfo import ZoneInfo

from .common import read_json
from .market_live import configured as alpaca_configured


def _source(name, path, recovery):
    path = Path(path)
    return {"name": name, "status": "AVAILABLE" if path.exists() else "MISSING",
            "path": str(path), "recovery": recovery}


def _file_sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _kx_readiness(path):
    """Return evidence from the immutable KX reference now bundled in this Hub."""
    path = Path(path)
    candidate_path = path / "audit/release-candidate.json"
    manifest_path = path / "RELEASE_MANIFEST.json"
    missing = [name for name, item in (("release candidate", candidate_path),
                                        ("release manifest", manifest_path)) if not item.is_file()]
    if missing:
        return {"status": "MISSING_EVIDENCE", "releaseAuthorized": False,
                "sourceHashMatch": False, "blockerCount": len(missing),
                "blockers": ["MISSING_" + item.upper().replace(" ", "_") for item in missing],
                "recovery": "Restore the KX release candidate and manifest. The Hub will remain research-only."}
    try:
        candidate = read_json(candidate_path)
        manifest = read_json(manifest_path)
        manifest_candidate = ((manifest.get("artifacts") or {}).get("candidate") or {})
        relative_source = Path(str(candidate.get("source_path") or ""))
        if not relative_source.parts or relative_source.is_absolute() or ".." in relative_source.parts:
            raise ValueError("unsafe source path")
        base = path.resolve()
        source = (base / relative_source).resolve()
        if not source.is_relative_to(base) or not source.is_file() or source.stat().st_size > 8_000_000:
            raise ValueError("candidate source missing or too large")
        actual_hash = _file_sha256(source)
        candidate_hash = str(candidate.get("source_sha256") or "")
        manifest_hash = str(manifest_candidate.get("sha256") or "")
        build_contract = str(candidate.get("build_contract_sha256") or "")
        manifest_contract = str(manifest_candidate.get("build_contract_sha256") or "")
        blockers = []
        for value in candidate.get("blockers") or []:
            value = str(value)[:100]
            if re.fullmatch(r"[A-Z0-9_]+", value) and value not in blockers:
                blockers.append(value)
        checks = {
            "sourceHashMatch": bool(actual_hash and actual_hash == candidate_hash == manifest_hash),
            "buildContractMatch": bool(build_contract and build_contract == manifest_contract),
            "frozen": candidate.get("frozen_at_ms") is not None,
            "compiled": candidate.get("compiled") is True and manifest_candidate.get("compiled") is True,
            "compileEvidenceRegistered": bool(candidate.get("compile_evidence_refs")),
            "released": manifest.get("release_state") == "RELEASED",
            "acceptancePassed": manifest.get("acceptance_state") == "PASS",
        }
        authorized = all(checks.values()) and not blockers
        return {"status": "RELEASE_AUTHORIZED" if authorized else "BLOCKED",
                "releaseAuthorized": authorized, "candidateStatus": str(candidate.get("status") or "UNKNOWN")[:40],
                "releaseState": str(manifest.get("release_state") or "UNKNOWN")[:40],
                "acceptanceState": str(manifest.get("acceptance_state") or "UNKNOWN")[:40],
                "sourceSha256": actual_hash, "sourceBytes": source.stat().st_size,
                **checks, "blockerCount": len(blockers), "blockers": blockers,
                "recovery": ("The hash-pinned KX release is authorized for read-only research context."
                             if authorized else
                             "The integrated KX reference remains blocked until registered compile, transport, nine-chart, coverage, holdout, replay and package gates are completed with valid evidence.")}
    except (OSError, ValueError, TypeError):
        return {"status": "EVIDENCE_INVALID", "releaseAuthorized": False,
                "sourceHashMatch": False, "blockerCount": 1,
                "blockers": ["KX_EVIDENCE_COULD_NOT_BE_VALIDATED"],
                "recovery": "Repair the KX evidence files in their owning project. The Hub will remain research-only."}


def _saved_spx_counts(root):
    path = Path(root) / "data/hub.sqlite3"
    counts = {"savedSnapshots": 0, "syntheticSnapshots": 0, "importedSnapshots": 0}
    if not path.is_file():
        return counts
    database = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        if not database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_snapshots'").fetchone():
            return counts
        for kind, payload in database.execute("SELECT kind,payload_json FROM web_snapshots WHERE symbol='SPX'"):
            try:
                if not (json.loads(payload).get("options") or []):
                    continue
            except (TypeError, ValueError):
                continue
            counts["savedSnapshots"] += 1
            counts["syntheticSnapshots" if kind == "synthetic" else "importedSnapshots"] += 1
        return counts
    finally:
        database.close()


def _spx_readiness(root, news):
    """Inspect the configured local chain candidate without importing or relabelling it."""
    path = Path(news) / "data/options_chain.csv"
    candidates = []
    if path.is_file() and not path.is_symlink() and path.stat().st_size <= 2_000_000:
        try:
            identities, rows = set(), 0
            with path.open(newline="", encoding="utf-8-sig") as handle:
                for row in csv.DictReader(handle):
                    rows += 1
                    for field in ("symbol", "underlying", "root"):
                        value = str(row.get(field) or "").strip().upper()
                        if value:
                            identities.add(value[:40])
            native = bool(identities) and identities <= {"SPX", "SPXW", "^SPX", "^GSPC"}
            proxy = bool(identities & {"SPY", "QQQ", "XSP"})
            candidates.append({"name": path.name, "rows": rows, "identities": sorted(identities),
                               "sha256": _file_sha256(path), "eligibleForImportReview": native,
                               "status": ("NATIVE_IDENTITY_DECLARED_UNVERIFIED" if native else
                                          "PROXY_REJECTED_FOR_SPX_GEX" if proxy else "IDENTITY_UNVERIFIED"),
                               "recovery": ("Preview this file in SPX GEX and supply exact source, capture, spot, OI and lifecycle metadata."
                                            if native else
                                            "This local chain is not native SPX/SPXW and cannot enter SPX GEX.")})
        except (OSError, csv.Error, UnicodeError):
            candidates.append({"name": path.name, "rows": 0, "identities": [],
                               "eligibleForImportReview": False, "status": "UNREADABLE",
                               "recovery": "Export a readable UTF-8 CSV from an authorized native SPX/SPXW source."})
    saved = _saved_spx_counts(root)
    status = ("LOCAL_CHAIN_SAVED_REVIEW_REQUIRED" if saved["importedSnapshots"] else
              "SYNTHETIC_ONLY" if saved["syntheticSnapshots"] else "IMPORT_REQUIRED")
    recovery = ("Review the saved chain's identity, lifecycle, coverage and timestamps before using modeled GEX."
                if saved["importedSnapshots"] else
                "Synthetic GEX is a walkthrough only; import an authorized native SPX/SPXW chain."
                if saved["syntheticSnapshots"] else
                "Import an authorized native SPX/SPXW chain; no chain means GEX unavailable.")
    return {"status": status, "recovery": recovery, **saved, "localCandidates": candidates}


def _us_session(now):
    from .market_calendar import session_status
    return session_status(now)


def _webull(root):
    try:
        capability = read_json(Path(root) / "state/webull-capability-declaration.json")
    except (OSError, ValueError):
        capability = {}
    app_only = isinstance(capability, dict) and capability.get("purchaseScope") == "APP_ONLY"
    state_path = Path(root) / "state/webull-probe.json"
    state = {}
    try:
        if state_path.exists():
            state = read_json(state_path)
    except (OSError, ValueError):
        state = {}
    checked = state.get("checked_at")
    probe_ok = state.get("status") == "READ_ONLY_PROBE_COMPLETED"
    spx_state = {}
    try:
        spx_path = Path(root) / "state/webull-spx-probe.json"
        if spx_path.exists():
            spx_state = read_json(spx_path)
    except (OSError, ValueError):
        spx_state = {}
    products = {}
    for name, row in (state.get("products") or {}).items():
        products[name] = {"status": row.get("status", "UNVERIFIED"),
                          "snapshot": (row.get("market_data_checks") or {}).get("snapshot", {}).get("status", "UNVERIFIED"),
                          "bars": (row.get("market_data_checks") or {}).get("M5_bars", {}).get("status", "UNVERIFIED")}
    return {"region": "Hong Kong", "environment": "sandbox",
            "status": "READ_ONLY_SANDBOX_PROBE_OBSERVED" if probe_ok else "UNVERIFIED_OR_NOT_CONFIGURED",
            "lastCheckedAt": checked, "products": products,
            "spxOptionSandbox": {
                "status": str(spx_state.get("status") or "UNVERIFIED"),
                "checkedAt": spx_state.get("checked_at"),
                "contractsObserved": int(spx_state.get("contracts_observed") or 0),
                "snapshotsObserved": int(spx_state.get("snapshots_observed") or 0),
                "fieldsObserved": [str(value)[:40] for value in (spx_state.get("fields_observed") or [])[:20]],
                "currentMarketQualified": False,
                "gexImportReady": False,
                "note": str(spx_state.get("note") or
                            "Run the bounded sandbox coverage check. It will not import or qualify GEX.")[:300],
            },
            "ordersEnabled": False, "paperOrdersEnabled": True,
            "paperOrderScope": "SANDBOX · US STOCK/ETF · LIMIT/DAY/CORE · TYPED CONFIRMATION",
            "orderRequests": state.get("order_requests", 0) if state else 0,
            "productionAccount": "UNVERIFIED", "marketDataEntitlement": state.get("live_data_entitlement", "UNVERIFIED") if state else "UNVERIFIED",
            "purchaseScope": "APP_ONLY_USER_REPORTED" if app_only else "UNVERIFIED",
            "recovery": ("Your reported futures subscription is Webull app-only. Webull documents separate OpenAPI subscriptions. Confirm an existing OpenAPI futures entitlement with Webull before production data verification; do not buy anything through the Hub. Sandbox checks do not establish live access."
                         if app_only else "Use the read-only checks first. Paper submissions require a provider preview and a fresh typed confirmation; production remains disabled.")}


def _paper_ledger(root):
    """Read local paper lifecycle counts without exposing order/account IDs."""
    path = Path(root) / "data/hub.sqlite3"
    if not path.exists():
        return {"status": "NO_LOCAL_LEDGER"}
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
            rows = db.execute("SELECT state,payload_json FROM web_broker_intents WHERE state LIKE 'PAPER_%' ORDER BY created_at DESC LIMIT 200").fetchall()
            submitted = 0
            for (payload,) in db.execute("SELECT payload_json FROM web_broker_events"):
                submitted += int(json.loads(payload).get("ordersSubmitted", 0))
            last = json.loads(rows[0][1]) if rows else {}
            statuses = [str(x.get("status"))[:40] for x in last.get("providerOrder", []) if isinstance(x, dict)]
        return {"status": "LOCAL_LEDGER_OBSERVED", "paperIntents": len(rows),
                "recordedOrderWrites": submitted, "latestLocalState": rows[0][0] if rows else None,
                "lastRecordedProviderStates": statuses,
                "note": "Local audit, not a fresh broker reconciliation. No new order was sent by this health check."}
    except (sqlite3.Error, ValueError, TypeError):
        return {"status": "LOCAL_LEDGER_UNAVAILABLE"}


def snapshot(root):
    root = Path(root)
    try:
        with (root / "config.toml").open("rb") as handle:
            cfg = tomllib.load(handle)
        def source_path(name):
            path = Path(cfg["sources"][name]).expanduser()
            return path if path.is_absolute() else (root / path).resolve()
        kx, news = source_path("kx"), source_path("news")
    except Exception:
        kx = root / "integrated/kx-reference"
        news = root / "integrated/news-reference"
    alpaca = alpaca_configured(root)
    return {"checkedAt": datetime.now(timezone.utc).isoformat(),
            "offlineCore": {"status": "READY", "recovery": "No credentials are needed for saved/imported charts, research, GEX imports, journal and calculators."},
            "sources": [_source("KX structure", kx, "Restore or update config.toml [sources].kx; the Hub will remain RESEARCH_ONLY."),
                        _source("News+ macro", news, "Restore or update config.toml [sources].news; stale/missing macro coverage remains unknown.")],
            "alpaca": {"status": "CONFIGURED_NOT_CONNECTED" if alpaca else "OPTIONAL_KEYS_MISSING",
                       "configured": alpaca, "websocketDependency": importlib.util.find_spec("websocket") is not None,
                       "coverage": "IEX single-exchange U.S. stocks/ETFs only; not SIP/NBBO, SPX, options or CME futures",
                       "recovery": "Copy local-data.env.example to local-data.env, add free/paper Alpaca data keys, run the optional installer, then start the feed explicitly."},
            "webull": _webull(root), "kx": _kx_readiness(kx),
            "sessions": {"usEquities": _us_session(datetime.now(timezone.utc))},
            "spx": _spx_readiness(root, news),
            "broker": {"executionEnabled": False, "paperExecutionEnabled": True,
                       "environment": "sandbox", "perOrderConfirmation": True,
                       "scope": "US stock/ETF LIMIT DAY CORE; 10 shares and USD 2,000 local cap",
                       "notificationsEnabled": False, "status": "PAPER_CONFIRMATION_REQUIRED", "ledger": _paper_ledger(root),
                       "recovery": "Preview in Webull sandbox, type the order-specific phrase, then reconcile the broker status. Production is unreachable."}}


def _webull_python(root):
    """Prefer the Hub-owned optional environment; retain legacy fallback."""
    root = Path(root).resolve()
    for item in (root / ".venv-webull/bin/python", root / ".venv-webull/Scripts/python.exe"):
        if item.exists():
            return item
    health = snapshot(root)
    news = next(Path(row["path"]) for row in health["sources"] if row["name"] == "News+ macro")
    for item in (news / ".venv-webull/bin/python", news / ".venv/bin/python", news / ".venv/Scripts/python.exe"):
        if item.exists():
            return item
    raise ValueError("The optional Webull SDK environment is missing; run python3 scripts/enable_webull_paper.py")


def run_webull_probe(root):
    """Run the existing official SDK inside its isolated legacy environment."""
    root = Path(root).resolve()
    executable = _webull_python(root)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run([str(executable), "-m", "trading_hub", "webull-probe"],
                                cwd=root, env=env, capture_output=True, text=True, timeout=75)
    except subprocess.TimeoutExpired:
        raise ValueError("Webull sandbox check exceeded 75 seconds; no order request was made") from None
    if result.returncode:
        raise ValueError("Webull sandbox check could not start in the isolated SDK environment; no raw SDK diagnostics were retained")
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise ValueError("Webull sandbox check returned no sanitized result; no credentials were exposed") from None
    if data.get("order_requests") != 0:
        raise ValueError("Unexpected order-request count; result refused")
    if data.get("status") != "READ_ONLY_PROBE_COMPLETED":
        raise ValueError("Webull read-only sandbox check is blocked (" + str(data.get("error_type") or "unknown setup error") + "); no order request was made")
    return data


def run_webull_account_probe(root):
    """Run GET-only account/balance/position/open-order reads in the SDK venv."""
    root = Path(root).resolve()
    executable = _webull_python(root)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run([str(executable), "-m", "trading_hub", "webull-account-probe"],
                                cwd=root, env=env, capture_output=True, text=True, timeout=75)
    except subprocess.TimeoutExpired:
        raise ValueError("Webull sandbox account read exceeded 75 seconds; no order write was made") from None
    if result.returncode:
        raise ValueError("Webull sandbox account read could not start; raw SDK diagnostics were discarded")
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise ValueError("Webull account read returned no sanitized result") from None
    if data.get("order_write_requests") != 0 or data.get("execution_enabled") is not False:
        raise ValueError("Unexpected broker-write capability in account response; result refused")
    return data


def run_webull_spx_probe(root):
    """Check sandbox SPX-option field coverage without retaining contracts or prices."""
    root = Path(root).resolve()
    executable = _webull_python(root)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run([str(executable), "-m", "trading_hub", "webull-spx-probe"],
                                cwd=root, env=env, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        raise ValueError("Webull SPX sandbox check exceeded 45 seconds; no order or import was made") from None
    if result.returncode:
        raise ValueError("Webull SPX sandbox check could not start; raw SDK diagnostics were discarded")
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise ValueError("Webull SPX sandbox check returned no sanitized result") from None
    if data.get("order_requests") != 0 or data.get("current_market_qualified") is not False or data.get("gex_import_ready") is not False:
        raise ValueError("Unexpected qualification or broker capability in SPX sandbox response; result refused")
    return data


def run_webull_paper_action(root, action, order):
    """Run one sandbox paper action in the isolated, pinned official SDK."""
    root = Path(root).resolve()
    if action not in {"preview", "place", "status", "cancel"}:
        raise ValueError("Unsupported Webull paper action")
    executable = _webull_python(root)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    raw = json.dumps({"action": action, "order": order}, allow_nan=False,
                     separators=(",", ":"))
    try:
        result = subprocess.run([str(executable), "-m", "trading_hub", "webull-paper-worker"],
                                cwd=root, env=env, input=raw, capture_output=True,
                                text=True, timeout=45)
    except subprocess.TimeoutExpired:
        raise ValueError("Webull paper action timed out; reconcile by client order ID before retrying") from None
    if result.returncode:
        raise ValueError("Webull paper worker failed; raw SDK diagnostics were discarded")
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise ValueError("Webull paper worker returned no sanitized result") from None
    if data.get("environment") != "sandbox" or data.get("endpoint") != "api.sandbox.webull.hk" or data.get("productionAllowed") is not False:
        raise ValueError("Paper worker environment proof failed")
    if action in {"preview", "status"} and data.get("orderRequests") != 0:
        raise ValueError("Unexpected write count in read-only paper action")
    if action in {"place", "cancel"} and data.get("orderRequests") not in {0, 1}:
        raise ValueError("Unexpected paper write count")
    return data
