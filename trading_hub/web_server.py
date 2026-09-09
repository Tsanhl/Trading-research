"""Loopback-only research website with confirmed sandbox paper routes."""
from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import mimetypes
import io
import tempfile
import zipfile
import secrets
import sqlite3
import threading
import time
import webbrowser
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .common import ROOT, digest, now_iso, read_json
from .web_data import BrowserStore, VERSION, csv_snapshot, options_csv, seed_existing
from .market_stream import AlpacaIEXStream
from .webull_paper import PaperConfirmationManager, public_order, validate_order

MAX_BODY = 16_000_000


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, *, root=ROOT):
        if address[0] != "127.0.0.1":
            raise ValueError("This private research build may bind only to 127.0.0.1")
        self.root = Path(root)
        self.csrf = secrets.token_urlsafe(32)
        self.network_lock = threading.Lock()
        self.work_lock = threading.Lock()
        self.fetch_cache = {}
        self.chart_cache = {}
        self.webull_account = None
        self.paper_orders = PaperConfirmationManager()
        self.market_stream = AlpacaIEXStream(self.root)
        seed_existing(self.root)
        super().__init__(address, Handler)

    def server_close(self):
        self.market_stream.stop(all_clients=True)
        super().server_close()

    @property
    def origins(self):
        port = self.server_address[1]
        return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


def research_docs(root, search="", stream="", limit=60, include_internal=False):
    with BrowserStore(root) as store:
        exists = store.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'").fetchone()
        if not exists:
            return []
        sql = "SELECT d.document_id,r.payload_json FROM documents d JOIN document_revisions r ON r.revision_id=d.current_revision"
        args, clauses = [], []
        if not include_internal:
            clauses.append("d.source NOT IN ('al_brooks_local_inventory','hub_source_rule_audit')")
        if stream in {"macro", "structure"}:
            clauses.append("d.stream=?")
            args.append(stream)
        if search:
            clauses.append("r.payload_json LIKE ?")
            args.append("%" + search[:150] + "%")
        sql += (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY r.ingested_at DESC, r.rowid DESC LIMIT ?"
        args.append(limit)
        out = []
        for row in store.conn.execute(sql, args):
            d = json.loads(row[1])
            out.append({"id": row[0], **{k: d.get(k) for k in ("title", "source", "stream", "url", "known_at", "published_at", "coverage")}, "excerpt": str(d.get("text", ""))[:700]})
        return out


def hub_summary(root):
    with BrowserStore(root) as store:
        names = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {name: store.conn.execute("SELECT count(*) FROM " + name).fetchone()[0] if name in names else 0 for name in ("documents", "document_revisions", "provenance", "worker_runs", "records")}
        counts.update({"web_snapshots": len(store.snapshots()), "web_notes": len(store.notes())})
        sources = []
        if "documents" in names:
            sources = [dict(r) for r in store.conn.execute("SELECT stream,source,count(*) AS count FROM documents GROUP BY stream,source ORDER BY count DESC")]
        macro = {"documents": 0, "sources": [], "latestKnownAt": None, "latestPublishedAt": None,
                 "coverage": [], "calendarCoverage": "UNKNOWN",
                 "eventRisk": "UNKNOWN_UNLESS_MANUALLY_VERIFIED"}
        if "documents" in names and "document_revisions" in names:
            rows = store.conn.execute("SELECT r.payload_json FROM documents d JOIN document_revisions r ON r.revision_id=d.current_revision WHERE d.stream='macro'")
            known, published, coverage, macro_sources = [], [], set(), set()
            for row in rows:
                try:
                    payload = json.loads(row[0])
                except (TypeError, ValueError):
                    continue
                if payload.get("known_at"): known.append(str(payload["known_at"]))
                if payload.get("published_at"): published.append(str(payload["published_at"]))
                if payload.get("coverage"): coverage.add(str(payload["coverage"])[:100])
                if payload.get("source"): macro_sources.add(str(payload["source"])[:100])
                macro["documents"] += 1
            macro.update({"sources": sorted(macro_sources), "latestKnownAt": max(known, default=None),
                          "latestPublishedAt": max(published, default=None), "coverage": sorted(coverage)})
        return {"counts": counts, "sources": sources, "kxRelease": "UNSEALED / NOT CERTIFIED", "brokerOrders": 0,
                "continuousFeed": False, "executionEligible": False, "archivePreserved": True,
                "macroEvidence": macro}


def backtests(root):
    output = []
    for path in (Path(root)/"reports/backtests").glob("*/backtest.json"):
        try:
            d = read_json(path)
            output.append({"id": path.parent.name, **{k: d.get(k) for k in ("run_id", "engine", "data_environment", "data_captured_at", "generated_at", "data_independently_verified", "holdout", "profitability_established", "preset", "matrix", "portfolio_matrix", "limitations")}})
        except (OSError, ValueError):
            continue
    output.sort(key=lambda r: str(r.get("data_captured_at") or r.get("generated_at") or ""), reverse=True)
    return output


def latest_brief(root, cadence):
    if cadence not in {"daily", "weekly"}:
        raise ValueError("Choose daily or weekly")
    state = Path(root) / "state/briefings" / f"{cadence}-latest.json"
    if not state.is_file():
        return {"available": False, "cadence": cadence,
                "reason": "No report has been generated for this cadence."}
    record = read_json(state)
    report_root = (Path(root) / "reports/briefings").resolve()
    markdown = Path(str(record.get("markdown") or ""))
    if "/Trading Research Hub/" in str(markdown) and (not markdown.is_file() or not markdown.resolve().is_relative_to(report_root)):
        markdown = Path(root) / str(markdown).split("/Trading Research Hub/", 1)[1]
    markdown = markdown.resolve()
    if not markdown.is_relative_to(report_root) or not markdown.is_file():
        raise ValueError("Report pointer is outside the local report directory")
    if markdown.stat().st_size > 2_000_000:
        raise ValueError("Report exceeds the local display limit")
    return {"available": True, "cadence": cadence,
            **{k: record.get(k) for k in ("period", "as_of", "broker_authority")},
            "markdown": markdown.read_text(encoding="utf-8")}


class Handler(BaseHTTPRequestHandler):
    server_version = "TradingResearchHub/0.7.0"

    def log_message(self, format, *args):
        # Never print imported payloads, private corpus text or provider diagnostics.
        pass

    @property
    def root(self):
        return self.server.root

    def send_bytes(self, status, data, content_type="application/json; charset=utf-8", filename=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        widget = urlsplit(self.path).path == "/chart-widget.html"
        self.send_header("X-Frame-Options", "SAMEORIGIN" if widget else "DENY")
        if widget:
            # Sandboxed opaque origin. Third-party script cannot read the private
            # parent DOM or call local APIs. Only display-provider connections.
            local = " ".join(self.server.origins)
            policy = ("default-src 'none'; script-src " + local + " https://s3.tradingview.com; "
                      "style-src 'unsafe-inline' https://*.tradingview.com https://*.tradingview-widget.com; img-src https://*.tradingview.com https://*.tradingview-widget.com data:; "
                      "frame-src https://*.tradingview.com https://*.tradingview-widget.com; connect-src https://*.tradingview.com https://*.tradingview-widget.com wss://*.tradingview.com; "
                      "frame-ancestors 'self'; base-uri 'none'; object-src 'none'; "
                      "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox")
        else:
            policy = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'"
        self.send_header("Content-Security-Policy", policy)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json(self, value, status=200, filename=None):
        self.send_bytes(status, json.dumps(value, allow_nan=False, ensure_ascii=False).encode(), filename=filename)

    def guard(self, write=False):
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        valid_hosts = {urlsplit(o).netloc for o in self.server.origins}
        if host not in valid_hosts:
            raise PermissionError("Untrusted Host header; use the local launch URL")
        if origin and origin not in self.server.origins:
            raise PermissionError("Cross-origin access is disabled")
        if write and not secrets.compare_digest(self.headers.get("X-Hub-CSRF", ""), self.server.csrf):
            raise PermissionError("Local write token missing; reload this page")

    def parse(self):
        url = urlsplit(self.path)
        return url.path, {k:v[-1] for k,v in parse_qs(url.query).items()}

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            raise ValueError("Request body must be 1 byte to 16 MB")
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise ValueError("Use application/json")
        def unique(pairs):
            out = {}
            for k,v in pairs:
                if k in out:
                    raise ValueError("Duplicate JSON field: " + k)
                out[k] = v
            return out
        return json.loads(self.rfile.read(length), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON rejected")))

    def do_GET(self):
        try:
            self.guard()
            path, q = self.parse()
            if path == "/api/session-briefing":
                target = self.root / "state/briefings/session-review.json"
                if not target.exists():
                    return self.json({"date": None, "coverage": "UNAVAILABLE", "events": []})
                if target.stat().st_size > 200_000:
                    raise ValueError("Briefing exceeds the local size limit")
                return self.json(read_json(target))
            if path == "/api/reports/latest":
                report = latest_brief(self.root, q.get("cadence", "daily"))
                if q.get("download") == "1" and report.get("available"):
                    name = f"Hub-{report['cadence']}-{report.get('period') or 'latest'}.md"
                    return self.send_bytes(200, report["markdown"].encode(),
                                           "text/markdown; charset=utf-8", name)
                return self.json(report)
            if path == "/api/status":
                with BrowserStore(self.root) as store:
                    from .connection_health import snapshot as connection_health
                    return self.json({"ok": True,"version": VERSION,"csrf": self.server.csrf,"time": now_iso(),
                                      "settings": store.settings(),"snapshots": store.snapshots(),"events": store.events(),
                                      "hub": hub_summary(self.root),"yfinanceInstalled": importlib.util.find_spec("yfinance") is not None,
                                      "networkVerifiedHere": False,"brokerExecution": False,
                                      "alpacaConfigured": __import__("trading_hub.market_live",fromlist=["configured"]).configured(self.root),
                                      "spxGexOnly": True, "autoRefreshIsBrowserScoped": True,
                                      "migration": store.migration_status(), "health": connection_health(self.root),
                                      "stream": self.server.market_stream.public_status()})
            if path == "/api/health":
                from .connection_health import snapshot as connection_health
                return self.json({"health": connection_health(self.root),
                                  "stream": self.server.market_stream.public_status()})
            if path == "/api/stream/status":
                return self.json(self.server.market_stream.public_status())
            if path == "/api/stream/events":
                return self.json(self.server.market_stream.events_since(q.get("since", "0"), q.get("clientId")))
            if path == "/api/chart/resolve":
                from .symbols import resolve
                return self.json(resolve(q.get("symbol", "SPY")))
            if path == "/api/backup":
                from .private_backup import create_backup
                return self.send_bytes(200,create_backup(self.root),"application/zip","Hub-private-research-backup.zip")
            if path == "/api/snapshot":
                with BrowserStore(self.root) as store:
                    return self.json(store.snapshot(q.get("id", "")), filename="research-snapshot.json" if q.get("download") else None)
            if path == "/api/notes":
                with BrowserStore(self.root) as store:
                    return self.json(store.notes(), filename="research-journal.json" if q.get("download") else None)
            if path == "/api/research":
                return self.json(research_docs(self.root, q.get("q", ""), q.get("stream", ""), include_internal=q.get("internal") == "1"))
            if path == "/api/document":
                with BrowserStore(self.root) as store:
                    row = store.conn.execute("SELECT r.payload_json FROM documents d JOIN document_revisions r ON r.revision_id=d.current_revision WHERE d.document_id=?", (q.get("id", ""),)).fetchone()
                    if not row:
                        raise ValueError("Document not found")
                    return self.json(json.loads(row[0]))
            if path == "/api/backtests":
                return self.json(backtests(self.root))
            if path == "/api/backtest-export":
                allowed = {d["id"] for d in backtests(self.root)}
                formats = {"json": "backtest.json", "summary": "summary.csv", "trades": "trades.csv", "markdown": "backtest.md"}
                identifier, fmt = q.get("id"), q.get("format", "summary")
                if identifier not in allowed or fmt not in formats:
                    raise ValueError("Unknown backtest export")
                p = self.root / "reports/backtests" / identifier / formats[fmt]
                return self.send_bytes(200, p.read_bytes(), "application/octet-stream", p.name)
            if path == "/api/records":
                kind = q.get("kind", "positions")
                if kind not in {"positions","decisions","quotes","alerts","reports","datasets","cycles"}:
                    raise ValueError("Invalid record kind")
                with BrowserStore(self.root) as store:
                    rows = store.conn.execute("SELECT record_id,created_at,payload_json FROM records WHERE kind=? ORDER BY created_at DESC LIMIT 40",(kind,)).fetchall()
                    return self.json([{"id":r[0],"createdAt":r[1],"record":json.loads(r[2])} for r in rows])
            if path == "/api/structure":
                from .structure import features
                with BrowserStore(self.root) as store:
                    s = store.snapshot(q.get("id", ""))
                from .web_data import timestamp, iso
                bars = [{"open_time": iso(datetime.fromtimestamp(b["t"]/1000, timezone.utc)),
                         "close_time": iso(datetime.fromtimestamp(b["t"]/1000 + s["barMinutes"]*60, timezone.utc)),
                         **{long:b[short] for short,long in (("o","open"),("h","high"),("l","low"),("c","close"))}} for b in s["bars"]]
                return self.json(features(bars, as_of=s["asOf"]))
            if path == "/api/guide":
                p = self.root / "README.md"
                return self.send_bytes(200, p.read_bytes(), "text/plain; charset=utf-8", "START-HERE.md")
            if path == "/api/source-coverage":
                p = self.root / "docs/SOURCE-TO-RULE-COVERAGE-2026-09-08.md"
                return self.send_bytes(200, p.read_bytes(), "text/plain; charset=utf-8")
            if path == "/api/template":
                files = {"ohlcv":"ohlcv-template.csv","options":"options-template.csv","snapshot":"demo-gex.json", "spx":"spx-gex-demo.json", "spx-options":"spx-options-template.csv"}
                if q.get("kind") not in files:
                    raise ValueError("Unknown template")
                p = self.root/"examples/web"/files[q["kind"]]
                return self.send_bytes(200, p.read_bytes(), "application/octet-stream", p.name)
            if path == "/api/spx/sandbox-export":
                p = self.root / "data/raw/web-imports/Webull-HK-SANDBOX-SPX-partial-2026-09-08.json"
                if not p.is_file() or p.stat().st_size > MAX_BODY:
                    raise ValueError("The bounded Webull sandbox SPX export is unavailable")
                return self.send_bytes(200, p.read_bytes(), "application/json; charset=utf-8", p.name)
            static = {"/futures-tools.js":"futures-tools.js", "/trade-home.js":"trade-home.js", "/": "index.html", "/index.html":"index.html", "/app.js":"app.js", "/styles.css":"styles.css", "/engine.js":"engine.js", "/research-engine.js":"research-engine.js", "/market-context.js":"market-context.js", "/favicon.svg":"favicon.svg", "/charts.js":"charts.js", "/chart-widget.html":"chart-widget.html", "/chart-widget.js":"chart-widget.js", "/symbols.js":"symbols.js", "/monitor.js":"monitor.js", "/monitor-tools.js":"monitor-tools.js", "/alert-rules.js":"alert-rules.js", "/monitor-alerts.js":"monitor-alerts.js"}
            if path in static:
                p = self.root/"web"/static[path]
                return self.send_bytes(200, p.read_bytes(), mimetypes.guess_type(str(p))[0] or "application/octet-stream")
            return self.json({"error":"Not found"}, 404)
        except PermissionError as exc:
            self.json({"error": str(exc)},403)
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
            self.json({"error": str(exc)[:500]},400)
        except Exception:
            self.json({"error":"Local read failed; check the package and restart the app. No data was replaced."},500)

    def do_POST(self):
        try:
            self.guard(write=True)
            path, _ = self.parse()
            value = self.body()
            if not isinstance(value,dict):
                raise ValueError("Request must be a JSON object")
            if path == "/api/reports/generate":
                if not self.server.work_lock.acquire(blocking=False):
                    return self.json({"error":"Another local research/report job is running"},409)
                try:
                    from .briefing import build_brief
                    cadence = str(value.get("cadence") or "daily")
                    result = build_brief(cadence, root=self.root,
                                         force=value.get("force") is True)
                    with BrowserStore(self.root) as store:
                        store.event("REPORT_GENERATED", f"{cadence.title()} private research brief prepared; no order action",
                                    cadence=cadence, brokerAuthority=False)
                    return self.json({"result": result,
                                      "report": latest_brief(self.root, cadence)})
                finally:
                    self.server.work_lock.release()
            if path == "/api/chart/fetch":
                from .market_live import chart_fetch
                from .symbols import resolve
                symbol=resolve(str(value.get("symbol", "SPY")))["symbol"]
                interval=value.get("interval", "15m"); provider=value.get("provider", "yahoo")
                if interval not in {"1m","5m","15m","1h","4h","1d","1w"} or provider not in {"yahoo","alpaca-iex","webull-sandbox"}:
                    raise ValueError("Invalid chart interval/provider")
                info=resolve(symbol)
                key=(info["instrumentId"], interval, provider, info["sessionModel"], "raw"); ttl=15 if provider=="alpaca-iex" else 60
                entry=self.server.chart_cache.get(key)
                if entry and time.monotonic()-entry[0]<ttl:
                    return self.json({**entry[1],"cached":True},502 if entry[1].get("error") else 200)
                if not self.server.network_lock.acquire(blocking=False):
                    return self.json({"error":"Another bounded feed request is running; retry after it completes", "oldDataPreserved":True},409)
                try:
                    try:
                        snap=chart_fetch(symbol,interval,provider,self.root)
                        if snap.get("symbol") != symbol: raise ValueError("Provider returned a different symbol; previous observations were retained")
                        result={"snapshot":snap,"cached":False,"refreshFloorSeconds":ttl,"persisted":False}
                        status=200
                    except ValueError as e:
                        result={"error":str(e),"oldDataPreserved":True};status=502
                    if len(self.server.chart_cache)>=64:
                        oldest=min(self.server.chart_cache,key=lambda k:self.server.chart_cache[k][0])
                        del self.server.chart_cache[oldest]
                    self.server.chart_cache[key]=(time.monotonic(),result)
                    return self.json(result,status)
                finally:
                    self.server.network_lock.release()
            if path == "/api/gex/preview":
                from .spx_gex import preview
                return self.json(preview(value))
            if path == "/api/gex/import":
                from .spx_gex import prepare
                raw_text = value.get("text") if value.get("format") == "csv" else (value.get("rawText") or json.dumps(value.get("snapshot"),sort_keys=True,separators=(",",":"),allow_nan=False))
                source_hash = hashlib.sha256(raw_text.encode()).hexdigest()
                if value.get("format") == "csv":
                    value = {**value,"metadata":{**(value.get("metadata") or {}),"sourceHash":source_hash}}
                else:
                    value = {**value,"snapshot":{**(value.get("snapshot") or {}),"sourceHash":source_hash}}
                snapshot=prepare(value)
                with BrowserStore(self.root) as store:
                    result=store.put_snapshot(snapshot)
                    result["provenance"] = store.record_import(raw_text,media_type="text/csv" if value.get("format")=="csv" else "application/json",original_name=value.get("originalName"),snapshot_id=result["id"])
                    store.event("SPX_GEX_IMPORT","Native SPX chain imported; dealer position and source not authenticated",symbol="SPX",snapshotId=result["id"])
                    return self.json(result)
            if path == "/api/import":
                with BrowserStore(self.root) as store:
                    if value.get("format") == "csv":
                        raw_text=value.get("text","")
                        source_hash=hashlib.sha256(raw_text.encode()).hexdigest()
                        snap = csv_snapshot(raw_text, {**(value.get("metadata") or {}),"sourceHash":source_hash})
                    else:
                        snap = value.get("snapshot")
                    result = store.put_snapshot(snap)
                    if value.get("rawText") or value.get("format") == "csv":
                        raw_text=value.get("rawText") or value.get("text","")
                        result["provenance"] = store.record_import(raw_text,media_type="text/csv" if value.get("format")=="csv" else "application/json",original_name=value.get("originalName"),snapshot_id=result["id"])
                    store.event("IMPORTED", "Imported research snapshot", symbol=result["snapshot"]["symbol"], snapshotId=result["id"])
                    return self.json(result)
            if path == "/api/import-options":
                with BrowserStore(self.root) as store:
                    s = store.snapshot(value.get("snapshotId", ""))
                    s["options"] = options_csv(value.get("text", "")) if value.get("format") == "csv" else value.get("options", [])
                    if not s["options"]:
                        raise ValueError("No options supplied")
                    s["oiAsOf"] = value.get("oiAsOf") or "UNKNOWN — user did not supply an OI date"
                    s["chainComplete"] = False
                    s["title"] += " + imported options"
                    s["warnings"].append("Option chain joined by user; validate same underlying, time, currency, and deliverable. Snapshot is not certified.")
                    # Joining cannot promote a demo/sandbox to a real feed.
                    return self.json(store.put_snapshot(s, origin=s["dataKind"] if s["dataKind"] in {"sandbox","synthetic"} else "imported"))
            if path == "/api/settings":
                with BrowserStore(self.root) as store:
                    return self.json(store.save_settings(value))
            if path == "/api/notes":
                with BrowserStore(self.root) as store:
                    return self.json(store.note(value))
            if path == "/api/stream/start":
                return self.json(self.server.market_stream.start(value.get("symbols"), value.get("clientId")))
            if path == "/api/stream/stop":
                return self.json(self.server.market_stream.stop(value.get("clientId")))
            if path == "/api/webull/probe":
                if not self.server.network_lock.acquire(blocking=False):
                    return self.json({"error":"Another bounded provider request is active"},409)
                try:
                    from .connection_health import run_webull_probe
                    result=run_webull_probe(self.root)
                    with BrowserStore(self.root) as store:
                        store.event("WEBULL_READ_ONLY_PROBE","Webull HK sandbox data check completed; no order request",environment="sandbox",orders=0)
                    return self.json(result)
                finally:
                    self.server.network_lock.release()
            if path == "/api/webull/account":
                if not self.server.network_lock.acquire(blocking=False):
                    return self.json({"error":"Another bounded provider request is active"},409)
                try:
                    from .connection_health import run_webull_account_probe
                    result = run_webull_account_probe(self.root)
                    self.server.webull_account = result
                    with BrowserStore(self.root) as store:
                        store.event("WEBULL_ACCOUNT_READ", "Sanitized Webull HK sandbox account state read; no order write",
                                    environment="sandbox", accounts=len(result.get("accounts", [])),
                                    orderWrites=0)
                    return self.json(result)
                finally:
                    self.server.network_lock.release()
            if path == "/api/webull/spx":
                if not self.server.network_lock.acquire(blocking=False):
                    return self.json({"error":"Another bounded provider request is active"},409)
                try:
                    from .connection_health import run_webull_spx_probe
                    result = run_webull_spx_probe(self.root)
                    with BrowserStore(self.root) as store:
                        store.event("WEBULL_SPX_OPTION_PROBE",
                                    "Webull HK sandbox SPX option-field coverage checked; no values imported or orders requested",
                                    environment="sandbox", contracts=result.get("contracts_observed", 0),
                                    snapshots=result.get("snapshots_observed", 0), orders=0)
                    return self.json(result)
                finally:
                    self.server.network_lock.release()
            if path == "/api/broker/ticket":
                from .broker_webull import review_ticket
                ticket=review_ticket(value, self.server.webull_account)
                with BrowserStore(self.root) as store:
                    ticket["ledger"] = store.broker_intent(ticket)
                    store.event("BROKER_REVIEW_TICKET","Blocked manual Webull review ticket created; no order submitted",symbol=ticket["instrument"]["symbol"],ticketId=ticket["ticketId"])
                return self.json(ticket)
            if path == "/api/broker/paper/preview":
                if not self.server.network_lock.acquire(blocking=False):
                    return self.json({"error":"Another bounded provider request is active"},409)
                try:
                    from .connection_health import run_webull_paper_action
                    order = validate_order(value)
                    provider = run_webull_paper_action(self.root, "preview", order)
                    challenge = self.server.paper_orders.issue(order, provider)
                    with BrowserStore(self.root) as store:
                        challenge["ledger"] = store.paper_broker_intent(challenge)
                        store.event("PAPER_ORDER_PREVIEWED",
                                    "Webull HK sandbox order preview accepted; typed confirmation still required",
                                    symbol=order["symbol"], clientOrderId=order["clientOrderId"], orders=0)
                    return self.json(challenge)
                finally:
                    self.server.network_lock.release()
            if path == "/api/broker/paper/confirm":
                if not self.server.network_lock.acquire(blocking=False):
                    return self.json({"error":"Another bounded provider request is active"},409)
                try:
                    from .connection_health import run_webull_paper_action
                    row = self.server.paper_orders.consume(value.get("challengeId"), value.get("confirmation"))
                    order = row["order"]
                    try:
                        result = run_webull_paper_action(self.root, "place", order)
                    except ValueError as exc:
                        result = {"schema":"trading-hub.webull-paper-provider.v1",
                                  "checkedAt":now_iso(), "environment":"sandbox",
                                  "endpoint":"api.sandbox.webull.hk", "action":"place",
                                  "order":public_order(order), "orderRequests":0,
                                  "productionAllowed":False, "state":"PAPER_SUBMISSION_UNCERTAIN",
                                  "note":str(exc)[:300]}
                    state = result.get("state")
                    if state not in {"PAPER_SUBMITTED", "PAPER_REJECTED"}:
                        state = "PAPER_SUBMISSION_UNCERTAIN"
                        result["state"] = state
                    with BrowserStore(self.root) as store:
                        result["ledger"] = store.paper_broker_update(
                            order["clientOrderId"], state, result,
                            event_type=state, orders_submitted=result.get("orderRequests", 0))
                        store.event(state, "Webull HK sandbox paper-order submission completed",
                                    symbol=order["symbol"], clientOrderId=order["clientOrderId"],
                                    orders=result.get("orderRequests", 0))
                    return self.json(result, 200 if state == "PAPER_SUBMITTED" else 502)
                finally:
                    self.server.network_lock.release()
            if path in {"/api/broker/paper/status", "/api/broker/paper/cancel"}:
                if not self.server.network_lock.acquire(blocking=False):
                    return self.json({"error":"Another bounded provider request is active"},409)
                try:
                    from .connection_health import run_webull_paper_action
                    order = validate_order(value.get("order") or {},
                                           client_order_id=(value.get("order") or {}).get("clientOrderId"))
                    action = "cancel" if path.endswith("/cancel") else "status"
                    if action == "cancel":
                        expected = "CANCEL PAPER " + order["clientOrderId"][-8:]
                        if not secrets.compare_digest(str(value.get("confirmation") or ""), expected):
                            return self.json({"error":"Type the exact cancellation confirmation: " + expected},400)
                    result = run_webull_paper_action(self.root, action, order)
                    state = str(result.get("state") or "PAPER_STATUS_UNAVAILABLE")
                    with BrowserStore(self.root) as store:
                        result["ledger"] = store.paper_broker_update(
                            order["clientOrderId"], state, result,
                            event_type=state, orders_submitted=result.get("orderRequests", 0))
                        store.event(state, "Webull HK sandbox paper order reconciled",
                                    symbol=order["symbol"], clientOrderId=order["clientOrderId"],
                                    orders=result.get("orderRequests", 0))
                    return self.json(result, 200 if result.get("brokerAcknowledged", True) is not False else 502)
                finally:
                    self.server.network_lock.release()
            if path == "/api/fetch":
                from .free_data import fetch_public
                symbol, interval = str(value.get("symbol", "SPY")).upper().strip(), value.get("interval", "15m")
                options = value.get("options") is True
                key = (symbol, interval, options)
                cache = self.server.fetch_cache.get(key)
                if cache and time.monotonic() - cache[0] < 60:
                    if cache[1].get("error"):
                        return self.json(cache[1],502)
                    return self.json({**cache[1],"cached":True})
                if not self.server.network_lock.acquire(blocking=False):
                    return self.json({"error":"Another public-feed request is running. Requests are deliberately bounded; try after it completes."},409)
                try:
                    try:
                        s = fetch_public(symbol,interval,options)
                        with BrowserStore(self.root) as store:
                            result = store.put_snapshot(s,origin="public-unverified")
                            store.event("PUBLIC_FETCH", "Public research data captured; not execution-grade", symbol=symbol, snapshotId=result["id"])
                        self.server.fetch_cache[key] = (time.monotonic(),result)
                        return self.json(result)
                    except ValueError as exc:
                        error = {"error":str(exc),"oldDataPreserved":True}
                        self.server.fetch_cache[key] = (time.monotonic(),error)
                        with BrowserStore(self.root) as store:
                            store.event("FEED_UNAVAILABLE",str(exc),symbol=symbol)
                        return self.json(error,502)
                finally:
                    self.server.network_lock.release()
            if path == "/api/backtests/run":
                if not self.server.work_lock.acquire(blocking=False):
                    return self.json({"error":"A local Hub backtest is already running"},409)
                try:
                    from .backtest_report import build_backtest
                    # Read only bundled sandbox captures, never request a broker feed.
                    captures = sorted((self.root/"state/backtests/captures").glob("*.json"),key=lambda p:p.stat().st_size)
                    if not captures:
                        raise ValueError("No saved Hub backtest capture was bundled")
                    result = build_backtest(capture_path=captures[-1],root=self.root)
                    with BrowserStore(self.root) as store:
                        store.event("BACKTEST", "Recomputed original Hub preset on saved sandbox capture, not a live-strategy qualification")
                    return self.json({"ok":True,"runId":result["run_id"],"runs":backtests(self.root)})
                finally:
                    self.server.work_lock.release()
            if path == "/api/record-import":
                from .workspace import import_record
                kind = value.get("kind")
                if kind not in {"decision","position","quote"}:
                    raise ValueError("Choose decision, position or quote")
                return self.json(import_record(kind,value.get("record"),root=self.root))
            if path == "/api/research/refresh":
                if not self.server.work_lock.acquire(blocking=False):
                    return self.json({"error":"A bounded research refresh is already running"},409)
                try:
                    from .workers import run_worker
                    result=run_worker("macro",self.root/"data/hub.sqlite3",refresh=True)
                    structure=run_worker("structure",self.root/"data/hub.sqlite3",refresh=True)
                    return self.json({"ok":result["status"] in {"COMPLETE","PARTIAL"},"result":result,
                                      "structure":structure,
                                      "note":"One bounded public EdgeRunner/Federal Reserve refresh plus local Kevin exports. X requires a separate visible-browser review; Brooks inventory is offline. Missing calendar coverage remains unknown."})
                finally:
                    self.server.work_lock.release()
            return self.json({"error":"Not found or unavailable route"},404)
        except PermissionError as exc:
            self.json({"error":str(exc)},403)
        except (ValueError,KeyError,TypeError,OSError,sqlite3.Error) as exc:
            self.json({"error":str(exc)[:500]},400)
        except Exception:
            self.json({"error":"Local action failed. Existing records were not intentionally overwritten; see the terminal for installation issues."},500)

    def refresh_fed(self):
        if not self.server.network_lock.acquire(blocking=False):
            return self.json({"error":"Another network request is active"},409)
        try:
            url = "https://www.federalreserve.gov/feeds/press_monetary.xml"
            try:
                with urlopen(Request(url,headers={"User-Agent":"TradingResearchHub/0.7.0 personal local RSS reader"}),timeout=10) as response:
                    raw=response.read(2_000_001)
                if len(raw)>2_000_000:
                    raise ValueError("Feed too large")
                tree=ET.fromstring(raw)
            except (OSError,ValueError,ET.ParseError):
                return self.json({"error":"Federal Reserve RSS is currently unavailable. Your saved research library is unchanged."},502)
            docs=[]
            for item in tree.findall(".//item")[:30]:
                title=item.findtext("title") or "Federal Reserve update"
                link=item.findtext("link") or ""
                description=html.unescape(item.findtext("description") or "")
                docs.append({"source":"federal_reserve_monetary","source_id":link or digest(title),"title":title,"url":link,
                             "text":title+"\n\n"+description,"known_at":now_iso(),"published_at":None,"coverage":"PUBLIC_RSS_EXCERPT_ONLY"})
            from .storage import HubStore
            with HubStore(self.root/"data/hub.sqlite3") as store:
                result=store.ingest_documents("macro",docs)
            return self.json({"ok":True,"counts":result,"note":"RSS excerpts only; not a verified live economic calendar"})
        finally:
            self.server.network_lock.release()


def main(argv=None):
    parser=argparse.ArgumentParser(description="Local Trading Research Hub — free-first, confirmed sandbox paper execution only")
    parser.add_argument("--port",type=int,default=8787)
    parser.add_argument("--no-browser",action="store_true")
    args=parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a local port from 1024 to 65535")
    try:
        server=LocalServer(("127.0.0.1",args.port))
    except OSError as exc:
        print(f"Could not open port {args.port}: {exc}\nAnother app may be running. Try: python3 -m trading_hub.web_server --port {args.port+1}")
        return 1
    url=f"http://127.0.0.1:{args.port}"
    print(f"\nTrading Research Hub {VERSION}\n{url}\nPrivate local research. Webull sandbox paper orders require per-order confirmation; production is disabled.\nKeep this terminal open; press Ctrl+C to stop.\n",flush=True)
    if not args.no_browser:
        threading.Timer(.7,lambda:webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=.3)
    except KeyboardInterrupt:
        print("\nStopped. Your local research is saved.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
