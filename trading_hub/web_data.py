"""Local-first browser data layer. Standard library; no broker or paid data client.

User snapshots are research inputs, never authenticated quotes. Existing Hub tables
are untouched; browser records live in new, prefixed tables in the same database.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import ROOT, digest, now_iso, read_json

from . import __version__ as VERSION
from .symbols import PATTERN as SYMBOL, normalize, resolve
KINDS = {"sandbox", "synthetic", "imported", "public-unverified"}
INTERVALS = {1, 5, 15, 60, 240, 1440, 10080}


def number(value, name="value", *, minimum=None, nullable=False):
    if nullable and (value is None or value == ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name}: boolean is not a number")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: numeric value required") from None
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"{name}: invalid numeric value")
    return result


def timestamp(value, name="timestamp"):
    if not isinstance(value, str) or "T" not in value:
        raise ValueError(f"{name}: use an ISO timestamp including time and timezone")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{name}: invalid ISO timestamp") from None
    if dt.tzinfo is None:
        raise ValueError(f"{name}: timezone is required")
    return dt.astimezone(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def validate_snapshot(value, *, origin="imported"):
    if not isinstance(value, dict):
        raise ValueError("Snapshot must be a JSON object")
    symbol = normalize(str(value.get("symbol", "")))
    if not SYMBOL.fullmatch(symbol):
        raise ValueError("Use a stock/root symbol such as SPY, GOOGL or MES")
    minutes = value.get("barMinutes", 5)
    if isinstance(minutes, bool) or minutes not in INTERVALS:
        raise ValueError("barMinutes must be 1, 5, 15, 60, 240, 1440 or 10080")
    asof = timestamp(value.get("asOf"), "asOf")
    if asof > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("Snapshot is future-dated relative to the computer clock")
    spot = number(value.get("spot"), "spot", minimum=0.00000001)
    raw_bars = value.get("bars", [])
    chain_only = symbol == "SPX" and raw_bars == [] and bool(value.get("options"))
    if not isinstance(raw_bars, list) or (not chain_only and not 2 <= len(raw_bars) <= 50000):
        raise ValueError("Supply 2–50,000 bars, or an SPX chain-only snapshot with no fabricated candles")
    if chain_only and not value.get("spotAsOf"):
        raise ValueError("SPX chain-only imports require an explicit underlying spotAsOf timestamp")
    bars, previous = [], -1
    for i, raw in enumerate(raw_bars):
        if not isinstance(raw, dict):
            raise ValueError(f"Bar {i+1} is not an object")
        b = {k: number(raw.get(k), f"Bar {i+1} {k}", minimum=0) for k in ("t", "o", "h", "l", "c", "v")}
        if b["t"] <= previous or b["t"] > asof.timestamp()*1000 or b["l"] <= 0:
            raise ValueError(f"Bar {i+1}: timestamps must increase, not exceed asOf, and low must be positive")
        if not b["l"] <= min(b["o"], b["c"]) <= max(b["o"], b["c"]) <= b["h"]:
            raise ValueError(f"Bar {i+1}: inconsistent OHLC")
        if raw.get("session"):
            try:
                datetime.strptime(str(raw["session"]), "%Y-%m-%d")
            except ValueError:
                raise ValueError(f"Bar {i+1}: invalid session date") from None
            b["session"] = raw["session"]
        if raw.get("durationMinutes") is not None:
            duration = number(raw.get("durationMinutes"), f"Bar {i+1} durationMinutes", minimum=1)
            if duration > minutes:
                raise ValueError(f"Bar {i+1}: duration cannot exceed the selected timeframe")
            b["durationMinutes"] = int(duration)
        b["finalized"] = raw.get("finalized") is not False
        bars.append(b)
        previous = b["t"]
    spot_asof = timestamp(value.get("spotAsOf") or iso(datetime.fromtimestamp(min(asof.timestamp(), previous/1000 + minutes*60), timezone.utc)), "spotAsOf")
    if spot_asof > asof + timedelta(seconds=5):
        raise ValueError("spotAsOf cannot be newer than the capture timestamp")
    options, identities = [], set()
    rows = value.get("options") or []
    if not isinstance(rows, list) or len(rows) > 15000:
        raise ValueError("Supply at most 15,000 option rows")
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"Option {i+1}: not an object")
        kind = str(raw.get("type", "")).lower()
        if kind not in {"call", "put"}:
            raise ValueError(f"Option {i+1}: type must be call or put")
        strike = number(raw.get("strike"), "strike", minimum=0.00000001)
        expiry = iso(timestamp(raw.get("expiry"), "expiry"))
        oi = number(raw.get("oi"), "open interest", minimum=0, nullable=True)
        if oi is not None and not oi.is_integer():
            raise ValueError("Open interest must be a whole number, or null if unknown")
        multiplier = number(raw.get("multiplier"), "multiplier", minimum=0.00000001)
        root = str(raw.get("root") or "").upper()
        underlying = str(raw.get("underlying") or symbol).upper()
        occ_match = None
        if symbol == "SPX":
            if underlying not in {"SPX", "SPXW", "^SPX", "^GSPC"} or (root and root not in {"SPX", "SPXW"}) or multiplier != 100:
                raise ValueError("SPX GEX accepts native SPX/SPXW contracts with multiplier 100 only; no ETF or futures proxies")
            occ_match = re.fullmatch(r"([A-Z]+)(\d{6})([CP])(\d{8})", str(raw.get("id") or ""))
            if occ_match and occ_match.group(1) not in {"SPX", "SPXW"}:
                raise ValueError("Contract identifier is not native SPX/SPXW")
            if occ_match:
                occ_root, occ_date, occ_side, occ_strike = occ_match.groups()
                if root and root != occ_root:
                    raise ValueError("Contract root and identifier disagree")
                if kind != ("call" if occ_side == "C" else "put"):
                    raise ValueError("Contract identifier and option type disagree")
                if not math.isclose(strike, int(occ_strike) / 1000, rel_tol=0, abs_tol=1e-9):
                    raise ValueError("Contract identifier and strike disagree")
                root = occ_root
        identity = (kind, strike, expiry, multiplier, root)
        if identity in identities:
            raise ValueError("Duplicate option type/strike/expiry/multiplier")
        identities.add(identity)
        settlement = str(raw.get("settlementType") or "UNKNOWN").upper()
        if settlement not in {"AM", "PM", "UNKNOWN"}:
            raise ValueError("settlementType must be AM, PM or UNKNOWN")
        last_trading = iso(timestamp(raw["lastTradingAt"], "lastTradingAt")) if raw.get("lastTradingAt") else None
        settlement_at = iso(timestamp(raw["settlementAt"], "settlementAt")) if raw.get("settlementAt") else None
        payoff_fixing = iso(timestamp(raw["payoffFixingAt"], "payoffFixingAt")) if raw.get("payoffFixingAt") else settlement_at
        settlement_published = iso(timestamp(raw["settlementValuePublishedAt"], "settlementValuePublishedAt")) if raw.get("settlementValuePublishedAt") else None
        if last_trading and timestamp(last_trading) > timestamp(expiry):
            raise ValueError("lastTradingAt cannot be after expiry")
        if last_trading and payoff_fixing and timestamp(last_trading) > timestamp(payoff_fixing):
            raise ValueError("lastTradingAt cannot be after payoffFixingAt")
        if settlement_published and payoff_fixing and timestamp(settlement_published) < timestamp(payoff_fixing):
            raise ValueError("settlementValuePublishedAt cannot precede payoffFixingAt")
        if occ_match:
            expected_date = datetime.strptime(occ_match.group(2), "%y%m%d").date()
            effective_time = timestamp(payoff_fixing or expiry)
            actual_date = effective_time.astimezone(ZoneInfo("America/New_York")).date()
            if expected_date != actual_date:
                raise ValueError("Contract identifier date disagrees with payoff/expiry date")
        lifecycle_source = str(raw.get("lifecycleSource") or "USER_DECLARED")[:180]
        lifecycle_complete = bool(raw.get("expiryVerified") is True and settlement != "UNKNOWN" and last_trading and payoff_fixing)
        lifecycle_independent = bool(raw.get("lifecycleIndependentlyVerified") is True and lifecycle_complete and lifecycle_source != "USER_DECLARED")
        option = {"type": kind, "strike": strike, "expiry": expiry, "oi": None if oi is None else int(oi), "multiplier": multiplier,
                  "id": str(raw.get("id") or f"{symbol}:{kind}:{strike}:{expiry}")[:140], "expiryVerified": raw.get("expiryVerified") is True, "root": root, "underlying": underlying}
        option.update({"settlementType": settlement, "lastTradingAt": last_trading,
                       "settlementAt": settlement_at, "payoffFixingAt": payoff_fixing,
                       "settlementValuePublishedAt": settlement_published,
                       "modelExpiryAt": payoff_fixing, "lifecycleSource": lifecycle_source,
                       "lifecycleRuleVersion": str(raw.get("lifecycleRuleVersion") or "UNVERSIONED")[:80],
                       "lifecycleVerified": lifecycle_complete,
                       "lifecycleIndependentlyVerified": lifecycle_independent})
        for field in ("gamma", "iv", "bid", "ask", "volume"):
            option[field] = number(raw.get(field), field, minimum=0, nullable=True)
        option["delta"] = number(raw.get("delta"), "delta", nullable=True)
        if option["delta"] is not None and not -1 <= option["delta"] <= 1:
            raise ValueError("Delta must be in [-1,1]")
        if option["bid"] is not None and option["ask"] is not None and option["bid"] > option["ask"]:
            raise ValueError("Crossed bid/ask rejected")
        for field, label in (("quoteAsOf", "Option quote"), ("greekAsOf", "Greek"), ("ivAsOf", "IV")):
            option[field] = None
            if raw.get(field):
                observed = timestamp(raw[field], field)
                if observed > asof + timedelta(seconds=5):
                    raise ValueError(f"{label} timestamp exceeds capture time")
                option[field] = iso(observed)
        option["greekSource"] = str(raw.get("greekSource") or ("SUPPLIED" if option["gamma"] is not None else "MISSING"))[:80]
        option["ivSource"] = str(raw.get("ivSource") or ("SUPPLIED" if option["iv"] is not None else "MISSING"))[:80]
        option["oiAsOf"] = str(raw.get("oiAsOf") or value.get("oiAsOf") or "UNKNOWN")[:180]
        option["position"] = number(raw.get("position"), "signed position", nullable=True)
        if option["position"] is not None and not option["position"].is_integer():
            raise ValueError("Signed position must be a whole contract quantity")
        options.append(option)
    kind = origin
    if value.get("dataKind") == "synthetic" or value.get("mode") == "demo":
        kind = "synthetic"
    elif value.get("dataKind") == "sandbox":
        kind = "sandbox"
    if kind not in KINDS:
        raise ValueError("Invalid data provenance")
    warnings = value.get("warnings", [])
    if not isinstance(warnings, list):
        raise ValueError("warnings must be an array")
    result = {"schema": "trading-hub.web-snapshot.v1", "symbol": symbol, "title": str(value.get("title") or symbol)[:160],
              "spot": spot, "asOf": iso(asof), "spotAsOf": iso(spot_asof), "bars": bars, "barMinutes": minutes,
              "options": options, "source": str(value.get("source") or "User import; independently unverified")[:240],
              "dataKind": kind, "mode": "demo" if kind == "synthetic" else "imported", "executionEligible": False,
              "oiAsOf": str(value.get("oiAsOf") or "UNKNOWN — provider did not publish an OI date")[:180],
              "chainComplete": value.get("chainComplete") is True, "rate": number(value.get("rate", .04), "model rate"),
              "dividend": number(value.get("dividend", 0), "dividend yield", minimum=0),
              "contract": str(value["contract"])[:40] if value.get("contract") else None,
              "continuous": value.get("continuous") is True, "expiryConvention": str(value.get("expiryConvention") or "Supplied dates; externally unverified")[:240],
              "warnings": [str(w)[:350] for w in warnings[:25]], "barTimeSemantics": str(value.get("barTimeSemantics") or "User-declared bar start time")[:160],
              "adjustment": str(value.get("adjustment") or "UNKNOWN")[:100]}
    info = resolve(symbol)
    if info["assetClass"] == "future":
        if value.get("instrumentId") and value["instrumentId"] != info["instrumentId"]:
            raise ValueError("Futures canonical identity must match its exact symbol")
        if value.get("currency") and value["currency"] != "USD":
            raise ValueError("These CME equity-index contracts are denominated in USD")
    result.update({"chainOnly": chain_only, "feed": str(value.get("feed") or "import")[:40],
                   "latencyClass": str(value.get("latencyClass") or "UNVERIFIED")[:60],
                   "currency": str(value.get("currency") or info["currency"])[:12],
                   "exchangeTimezone": str(value.get("exchangeTimezone") or info["exchangeTimezone"])[:64],
                   "sessionModel": str(value.get("sessionModel") or info["sessionModel"])[:40],
                   "instrumentId": str(value.get("instrumentId") or info["instrumentId"])[:120],
                   "assetClass": str(value.get("assetClass") or info["assetClass"])[:32],
                   "exchange": str(value.get("exchange") or info["exchange"])[:32],
                   "referenceOnly": value.get("referenceOnly") is True or info["referenceOnly"],
                   "formingBar": value.get("formingBar") if isinstance(value.get("formingBar"), dict) else None,
                   "fetchAsOf": str(value.get("fetchAsOf") or iso(asof))[:40]})
    if result["sessionModel"] not in {"us-rth", "continuous", "futures-reference", "cme-equity-index", "unknown"}:
        result["sessionModel"] = "unknown"
    if chain_only:
        result["warnings"] = list(dict.fromkeys(result["warnings"] + ["SPX chain-only snapshot: no historical candles were supplied or fabricated."]))
    if value.get("sourceHash"):
        result["sourceHash"] = str(value["sourceHash"])[:64]
    from .futures_market import enrich
    return enrich(result,datetime.fromisoformat(result["asOf"].replace("Z","+00:00")))


def parse_csv_time(raw, *, zone, daily=False):
    text = str(raw).strip()
    try:
        val = float(text)
        if math.isfinite(val):
            return datetime.fromtimestamp(val / (1000 if val > 1e11 else 1), timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass
    if len(text) == 10 and daily:
        text += "T09:30:00"
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        if not zone:
            raise ValueError("CSV has timezone-free timestamps; select their source timezone")
        dt = dt.replace(tzinfo=ZoneInfo(zone))
    return dt.astimezone(timezone.utc)


def csv_snapshot(text, meta):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ValueError("CSV is empty")
    minutes = int(meta.get("barMinutes", 5))
    bars = []
    for row in reader:
        r = {str(k).strip().lower(): v for k, v in row.items() if k}
        raw_time = next((r[k] for k in ("time", "timestamp", "datetime", "date", "open_time", "t") if r.get(k)), None)
        if raw_time is None:
            raise ValueError("CSV needs time/date/timestamp and open/high/low/close/volume columns")
        dt = parse_csv_time(raw_time, zone=meta.get("timezone"), daily=minutes == 1440)
        b = {"t": dt.timestamp()*1000}
        for short, full in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume")):
            raw = r.get(full, r.get(short))
            b[short] = 0 if short == "v" and raw in (None, "") else number(raw, full)
        bars.append(b)
        if len(bars) > 50000:
            raise ValueError("CSV is limited to 50,000 bars")
    bars.sort(key=lambda b: b["t"])
    if len(bars) < 2:
        raise ValueError("CSV needs at least two observations")
    asof = meta.get("asOf") or now_iso()
    end = bars[-1]["t"]/1000 + minutes*60
    if minutes == 1440 and meta.get("symbol") not in {"ES","MES","NQ","MNQ"}:
        local_day = datetime.fromtimestamp(bars[-1]["t"]/1000, ZoneInfo("America/New_York"))
        end = local_day.replace(hour=16, minute=0, second=0, microsecond=0).timestamp()
    return validate_snapshot({**meta, "asOf": asof, "bars": bars, "barMinutes": minutes, "spot": bars[-1]["c"],
                              "spotAsOf": iso(datetime.fromtimestamp(min(timestamp(asof).timestamp(), end), timezone.utc)),
                              "title": f"{meta.get('symbol','')} • CSV import", "source": meta.get("source") or "User CSV import",
                              "warnings": ["CSV timezone and bar-start convention are user declarations, not external verification.", "Missing volume is recorded as zero and disables volume confirmation; prices are never filled forward."]})


def _cboe_expiry(value, root):
    """Return a provisional exact timestamp for a date-only manual Cboe export.

    This permits row-level arithmetic preview but never marks lifecycle metadata
    verified. The user must repair half-day and series-specific lifecycle details
    before qualified expiry-sensitive modeling.
    """
    raw = str(value or "").strip()
    parsed = None
    for pattern in ("%a %b %d %Y", "%b %d %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(raw, pattern)
            break
        except ValueError:
            pass
    if parsed is None:
        raise ValueError("Cboe ExpirationDate is missing or ambiguous; map it to YYYY-MM-DD")
    # Provisional only: SPX monthly contracts are generally AM-settled and SPXW
    # generally PM-settled. Exact series/holiday metadata remains unverified.
    hour, minute = ((9, 30) if root == "SPX" else (16, 0))
    local = parsed.replace(hour=hour, minute=minute, tzinfo=ZoneInfo("America/New_York"))
    return iso(local.astimezone(timezone.utc))


def _cboe_number(row, prefix, name):
    aliases = {
        "strike": (prefix + "Strike", "Strike"),
        "oi": (prefix + "OpenInt", prefix + "OpenInterest", prefix + "Int"),
        "iv": (prefix + "IV",), "gamma": (prefix + "Gamma",),
        "delta": (prefix + "Delta",), "bid": (prefix + "Bid",),
        "ask": (prefix + "Ask",), "volume": (prefix + "Vol", prefix + "Volume"),
    }
    lower = {str(k).strip().lower(): v for k, v in row.items() if k}
    for key in aliases[name]:
        value = lower.get(key.lower())
        if value not in (None, "", "-"):
            return str(value).replace(",", "").strip()
    return None


def _cboe_rows(raw_rows, *, metadata=None):
    metadata = metadata or {}
    output = []
    capture = metadata.get("asOf")
    for source in raw_rows:
        expiration = next((v for k, v in source.items() if str(k).strip().lower() in {"expirationdate", "expiration date", "expiry"}), None)
        for prefix, kind in (("Call", "call"), ("Put", "put")):
            label = next((v for k, v in source.items() if str(k).strip().lower() == ("calls" if kind == "call" else "puts")), "")
            label = str(label or "").strip()
            root_match = re.match(r"([A-Za-z]+)", label)
            root = (root_match.group(1).upper() if root_match else str(metadata.get("defaultRoot") or "").upper())
            if root not in {"SPX", "SPXW"}:
                if not any(_cboe_number(source, prefix, field) for field in ("strike", "oi", "gamma", "iv")):
                    continue
                raise ValueError("Cboe paired CSV row needs an SPX or SPXW contract/root label")
            strike = _cboe_number(source, prefix, "strike")
            if strike is None:
                strike_match = re.search(r"(-?\d+(?:\.\d+)?)", label)
                strike = strike_match.group(1) if strike_match else None
            if strike is None:
                continue
            expiry = _cboe_expiry(expiration, root)
            option = {"id": f"CBOE-MANUAL:{root}:{expiration}:{kind}:{strike}",
                      "underlying": "SPX", "root": root, "type": kind, "strike": strike,
                      "expiry": expiry, "oi": _cboe_number(source, prefix, "oi"),
                      "multiplier": 100, "iv": _cboe_number(source, prefix, "iv"),
                      "gamma": _cboe_number(source, prefix, "gamma"),
                      "delta": _cboe_number(source, prefix, "delta"),
                      "bid": _cboe_number(source, prefix, "bid"),
                      "ask": _cboe_number(source, prefix, "ask"),
                      "volume": _cboe_number(source, prefix, "volume"),
                      "expiryVerified": False, "settlementType": "UNKNOWN",
                      "lifecycleSource": "CBOE_MANUAL_EXPORT_DATE_ONLY_PROVISIONAL",
                      "lifecycleRuleVersion": "cboe-date-only-v1",
                      "greekSource": "CBOE_MANUAL_EXPORT_SUPPLIED",
                      "ivSource": "CBOE_MANUAL_EXPORT_SUPPLIED"}
            if capture:
                option.update({"quoteAsOf": capture, "greekAsOf": capture, "ivAsOf": capture})
            output.append(option)
    return output


def options_csv(text, metadata=None):
    rows = []
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    fieldnames = [str(name or "").strip().lower() for name in (reader.fieldnames or [])]
    raw_rows = list(reader)
    if {"expirationdate", "callgamma", "putgamma"}.issubset(set(fieldnames)) or ({"calls", "puts"}.issubset(set(fieldnames)) and any("openint" in name for name in fieldnames)):
        rows = _cboe_rows(raw_rows, metadata=metadata)
        if not rows:
            raise ValueError("No native SPX/SPXW rows found in the Cboe paired CSV")
        return rows
    for row in raw_rows:
        r = {str(k).strip(): v for k, v in row.items() if k}
        if len(rows) >= 15000:
            raise ValueError("Option CSV limit is 15,000 rows")
        if "oi" not in r and "openInterest" in r:
            r["oi"] = r["openInterest"]
        r["expiryVerified"] = r.get("expiryVerified", "").lower() == "true"
        r["lifecycleIndependentlyVerified"] = r.get("lifecycleIndependentlyVerified", "").lower() == "true"
        rows.append(r)
    if not rows:
        raise ValueError("No option rows found")
    return rows


class BrowserStore:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        self.path = self.root / "data/hub.sqlite3"
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS web_snapshots(id TEXT PRIMARY KEY, symbol TEXT NOT NULL, kind TEXT NOT NULL,
                as_of TEXT NOT NULL, imported_at TEXT NOT NULL, payload_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS web_notes(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS web_settings(key TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS web_events(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload_json TEXT NOT NULL);
        ''')
        self._migrate()

    def _migrate(self):
        """Apply explicit, idempotent migrations without replacing user rows."""
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > 4:
            raise ValueError(f"Database schema {version} is newer than this application supports")
        with self.conn:
            self.conn.executescript('''
                CREATE TABLE IF NOT EXISTS hub_schema_migrations(
                    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS web_import_sources(
                    source_sha256 TEXT PRIMARY KEY, imported_at TEXT NOT NULL,
                    media_type TEXT NOT NULL, byte_count INTEGER NOT NULL,
                    original_name TEXT, stored_path TEXT NOT NULL,
                    snapshot_id TEXT REFERENCES web_snapshots(id));
                CREATE INDEX IF NOT EXISTS idx_web_import_snapshot ON web_import_sources(snapshot_id);
            ''')
            if version < 2:
                self.conn.execute(
                    "INSERT OR IGNORE INTO hub_schema_migrations VALUES(?,?,?)",
                    (2, now_iso(), "browser imports, favorites and migration receipts"),
                )
                self.conn.execute("PRAGMA user_version=2")
            if version < 3:
                self.conn.executescript('''
                    CREATE TABLE IF NOT EXISTS web_broker_intents(
                        intent_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                        state TEXT NOT NULL, payload_json TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS web_broker_events(
                        event_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL REFERENCES web_broker_intents(intent_id),
                        created_at TEXT NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS idx_web_broker_events_intent ON web_broker_events(intent_id, created_at);
                ''')
                self.conn.execute(
                    "INSERT OR IGNORE INTO hub_schema_migrations VALUES(?,?,?)",
                    (3, now_iso(), "blocked broker review intent ledger; no submission routes"),
                )
                self.conn.execute("PRAGMA user_version=3")
            if version < 4:
                self.conn.execute(
                    "INSERT OR IGNORE INTO hub_schema_migrations VALUES(?,?,?)",
                    (4, now_iso(), "confirmed Webull HK sandbox paper-order lifecycle ledger"),
                )
                self.conn.execute("PRAGMA user_version=4")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.conn.close()

    def put_snapshot(self, snapshot, *, origin="imported"):
        s = validate_snapshot(snapshot, origin=origin)
        identifier = digest(s)[:32]
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO web_snapshots VALUES(?,?,?,?,?,?)", (identifier, s["symbol"], s["dataKind"], s["asOf"], now_iso(), json.dumps(s, allow_nan=False)))
        return {"id": identifier, "snapshot": s}

    def snapshots(self):
        out = []
        for r in self.conn.execute("SELECT * FROM web_snapshots ORDER BY imported_at DESC LIMIT 300"):
            s = json.loads(r["payload_json"])
            out.append({k: s.get(k) for k in ("symbol", "title", "source", "dataKind", "asOf", "spotAsOf", "spot", "barMinutes", "contract", "continuous")}
                       | {"id": r["id"], "barCount": len(s["bars"]), "optionCount": len(s["options"]), "importedAt": r["imported_at"]})
        return out

    def broker_intent(self, ticket):
        """Persist a blocked local review artifact without creating an order."""
        if not isinstance(ticket, dict) or ticket.get("executionEnabled") is not False or ticket.get("state") != "BLOCKED_REVIEW_ONLY":
            raise ValueError("Only blocked, non-executable review tickets may enter the local ledger")
        intent_id = str(ticket.get("ticketId") or "")
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", intent_id):
            raise ValueError("Review ticket identifier is invalid")
        payload = json.dumps(ticket, allow_nan=False, sort_keys=True)
        event_id = digest({"intent": intent_id, "state": ticket["state"], "payload": payload})[:32]
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO web_broker_intents VALUES(?,?,?,?)",
                              (intent_id, ticket["createdAt"], ticket["state"], payload))
            self.conn.execute("INSERT OR IGNORE INTO web_broker_events VALUES(?,?,?,?,?)",
                              (event_id, intent_id, now_iso(), "LOCAL_REVIEW_CREATED",
                               json.dumps({"ordersSubmitted": 0, "brokerAcknowledged": False})))
        return {"intentId": intent_id, "state": ticket["state"], "ordersSubmitted": 0}

    def broker_intents(self):
        return [dict(row) for row in self.conn.execute(
            "SELECT intent_id,created_at,state FROM web_broker_intents ORDER BY created_at DESC LIMIT 200")]

    def paper_broker_intent(self, preview):
        """Persist a sanitized provider preview; challenge secrets stay in memory."""
        if not isinstance(preview, dict) or preview.get("state") != "PAPER_PREVIEWED":
            raise ValueError("A successful paper preview is required")
        order = preview.get("order") or {}
        intent_id = str(order.get("clientOrderId") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", intent_id):
            raise ValueError("Paper client order identifier is invalid")
        payload = {k: v for k, v in preview.items() if k not in {"challengeId", "expectedConfirmation"}}
        encoded = json.dumps(payload, allow_nan=False, sort_keys=True)
        event_id = digest({"intent": intent_id, "event": "PAPER_PROVIDER_PREVIEW", "payload": encoded})[:32]
        with self.conn:
            self.conn.execute("INSERT INTO web_broker_intents VALUES(?,?,?,?)",
                              (intent_id, now_iso(), "PAPER_PREVIEWED", encoded))
            self.conn.execute("INSERT INTO web_broker_events VALUES(?,?,?,?,?)",
                              (event_id, intent_id, now_iso(), "PAPER_PROVIDER_PREVIEW",
                               json.dumps({"ordersSubmitted": 0, "environment": "sandbox"})))
        return {"intentId": intent_id, "state": "PAPER_PREVIEWED", "ordersSubmitted": 0}

    def paper_broker_update(self, intent_id, state, receipt, *, event_type, orders_submitted):
        allowed = {"PAPER_SUBMITTED", "PAPER_REJECTED", "PAPER_SUBMISSION_UNCERTAIN",
                   "PAPER_STATUS_OBSERVED", "PAPER_STATUS_UNAVAILABLE",
                   "PAPER_CANCEL_REQUESTED", "PAPER_CANCEL_REJECTED"}
        if state not in allowed or not re.fullmatch(r"[0-9a-f]{32}", str(intent_id or "")):
            raise ValueError("Invalid paper-order ledger transition")
        if not isinstance(receipt, dict) or receipt.get("productionAllowed") is not False:
            raise ValueError("Paper receipt lacks sandbox boundary proof")
        encoded = json.dumps(receipt, allow_nan=False, sort_keys=True)
        event_id = digest({"intent": intent_id, "event": event_type, "payload": encoded})[:32]
        with self.conn:
            updated = self.conn.execute("UPDATE web_broker_intents SET state=?,payload_json=? WHERE intent_id=?",
                                        (state, encoded, intent_id)).rowcount
            if updated != 1:
                raise ValueError("Paper-order intent was not found")
            self.conn.execute("INSERT OR IGNORE INTO web_broker_events VALUES(?,?,?,?,?)",
                              (event_id, intent_id, now_iso(), event_type,
                               json.dumps({"ordersSubmitted": int(orders_submitted),
                                           "environment": "sandbox"})))
        return {"intentId": intent_id, "state": state, "ordersSubmitted": int(orders_submitted)}

    def snapshot(self, identifier):
        row = self.conn.execute("SELECT payload_json FROM web_snapshots WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("Snapshot not found")
        from .futures_market import enrich
        return enrich(json.loads(row[0]))

    def settings(self):
        row = self.conn.execute("SELECT payload_json FROM web_settings WHERE key='preferences'").fetchone()
        defaults = {"equity": 100000, "riskPct": .25, "eventFreeze": False,
                    "watchlist": ["SPY", "QQQ", "NVDA", "MU", "GOOGL", "TSLA", "PLTR", "V", "MCD"],
                    "favorites": ["SPY", "QQQ", "AAPL", "NVDA"]}
        if not row:
            return defaults
        saved = json.loads(row[0])
        return defaults | saved if isinstance(saved, dict) else defaults

    def save_settings(self, value):
        if not isinstance(value, dict):
            raise ValueError("Settings must be an object")
        equity = number(value.get("equity"), "Paper account", minimum=1)
        risk = number(value.get("riskPct"), "Risk percentage", minimum=.001)
        if risk > 5 or equity > 1e10:
            raise ValueError("Research risk is capped at 5%; invalid paper balance")
        current = self.settings()
        def symbols(field, minimum, maximum):
            supplied = value.get(field, current[field])
            if not isinstance(supplied, list) or not minimum <= len(supplied) <= maximum:
                raise ValueError(f"{field.title()} must contain {minimum}–{maximum} symbols")
            try:
                cleaned = [normalize(s) for s in supplied]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field.title()}: {exc}") from None
            return list(dict.fromkeys(cleaned))
        result = {"equity": equity, "riskPct": risk, "eventFreeze": value.get("eventFreeze") is True,
                  "watchlist": symbols("watchlist", 1, 30), "favorites": symbols("favorites", 0, 30)}
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO web_settings VALUES('preferences',?)", (json.dumps(result),))
        return result

    def record_import(self, text, *, media_type, original_name, snapshot_id):
        """Persist the exact user-supplied text privately and attach its hash."""
        if not isinstance(text, str):
            raise ValueError("Imported source must be text")
        raw = text.encode("utf-8")
        if not raw or len(raw) > 16_000_000:
            raise ValueError("Imported source must be 1 byte to 16 MB")
        import hashlib
        source_hash = hashlib.sha256(raw).hexdigest()
        suffix = ".csv" if media_type == "text/csv" else ".json"
        folder = self.root / "data/raw/web-imports"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (source_hash + suffix)
        if not target.exists():
            target.write_bytes(raw)
            target.chmod(0o600)
        safe_name = Path(str(original_name or "user-import")) .name[:160]
        relative = str(target.relative_to(self.root))
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO web_import_sources VALUES(?,?,?,?,?,?,?)",
                (source_hash, now_iso(), media_type, len(raw), safe_name, relative, snapshot_id),
            )
        return {"sha256": source_hash, "bytes": len(raw), "storedPath": relative,
                "originalName": safe_name}

    def migration_status(self):
        return {"userVersion": self.conn.execute("PRAGMA user_version").fetchone()[0],
                "applied": [dict(row) for row in self.conn.execute(
                    "SELECT version,applied_at,description FROM hub_schema_migrations ORDER BY version")]}

    def note(self, value):
        if not isinstance(value, dict):
            raise ValueError("Note must be an object")
        text = str(value.get("text", "")).strip()
        if not text or len(text) > 10000:
            raise ValueError("Write a note of 1–10,000 characters")
        symbol = normalize(str(value.get("symbol", "SPY")))
        if not SYMBOL.fullmatch(symbol):
            raise ValueError("Invalid note symbol")
        note = {"text": text, "symbol": symbol, "category": str(value.get("category") or "research")[:40], "createdAt": now_iso(), "brokerFill": False}
        sid = value.get("snapshotId")
        if sid:
            s = self.snapshot(sid)
            note.update({"snapshotId": sid, "dataKind": s["dataKind"], "dataAsOf": s["asOf"]})
        identifier = digest(note)[:32]
        with self.conn:
            self.conn.execute("INSERT INTO web_notes VALUES(?,?,?)", (identifier, note["createdAt"], json.dumps(note)))
        return {"id": identifier, **note}

    def notes(self):
        return [{"id": r[0], **json.loads(r[1])} for r in self.conn.execute("SELECT id,payload_json FROM web_notes ORDER BY created_at DESC LIMIT 500")]

    def event(self, kind, message, **extra):
        e = {"kind": kind, "message": str(message)[:500], "at": now_iso(), **extra}
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO web_events VALUES(?,?,?)", (digest(e)[:32], e["at"], json.dumps(e, allow_nan=False)))
        return e

    def events(self):
        return [json.loads(r[0]) for r in self.conn.execute("SELECT payload_json FROM web_events ORDER BY created_at DESC LIMIT 30")]


def seed_existing(root=ROOT):
    """Read the uploaded captures; never contact the old SDK or handle credentials."""
    root = Path(root)
    with BrowserStore(root) as store:
        if store.conn.execute("SELECT 1 FROM web_settings WHERE key='seed_v1'").fetchone():
            return
        captures = []
        for p in (root / "state/historical/captures").glob("*.json"):
            try:
                d = read_json(p)
                if d.get("environment") == "sandbox":
                    captures.append((d.get("captured_at", ""), p, d))
            except (ValueError, OSError):
                pass
        if captures:
            _, path, cap = max(captures, key=lambda x: x[0])
            datasets = []
            for symbol, data in cap.get("etfs", {}).items():
                datasets.append((symbol, None, data))
            for symbol, data in cap.get("futures", {}).items():
                contracts = data.get("contracts", {})
                available = [(k,v) for k,v in contracts.items() if v.get("metadata") and v.get("rows")]
                if available:
                    contract, record = max(available, key=lambda x: x[1].get("newest_epoch", 0) or 0)
                    datasets.append((symbol, contract, record))
            for symbol, contract, dataset in datasets:
                bars = []
                rejected = 0
                for row in dataset.get("rows", []):
                    try:
                        t = datetime.fromisoformat(row["time"].replace("Z", "+00:00")).timestamp()*1000
                        b = {"t": t, **{a: float(row[b]) for a,b in (("o","open"),("h","high"),("l","low"),("c","close"),("v","volume"))}}
                        if b["l"] <= 0 or not b["l"] <= min(b["o"],b["c"]) <= max(b["o"],b["c"]) <= b["h"]:
                            raise ValueError("OHLC")
                        bars.append(b)
                    except (ValueError, TypeError, KeyError):
                        rejected += 1
                bars = sorted({b["t"]: b for b in bars}.values(), key=lambda b: b["t"])
                if len(bars) >= 2:
                    try:
                        store.put_snapshot({"symbol": symbol, "title": f"{contract or symbol} • your saved Webull sandbox", "asOf": cap["captured_at"],
                                            "spot": bars[-1]["c"], "bars": bars, "barMinutes": 5, "options": [], "contract": contract,
                                            "source": "Uploaded Trading Research Hub / Webull HK sandbox capture", "sourceHash": path.stem,
                                            "barTimeSemantics": "Assumed start time; original Hub marks semantics unverified", "adjustment": "Original capture: unverified",
                                            "warnings": ["SAVED SANDBOX OBSERVATIONS — not a current production quote.", "No options chain in this capture. GEX is unavailable, not zero.", f"Malformed OHLC rows excluded: {rejected}; duplicate timestamps deduplicated in the archive view."]}, origin="sandbox")
                    except ValueError as exc:
                        store.event("SEED_REJECTED", str(exc), symbol=symbol)
        demo = root / "examples/web/demo-gex.json"
        if demo.exists():
            store.put_snapshot(read_json(demo), origin="synthetic")
        with store.conn:
            store.conn.execute("INSERT OR REPLACE INTO web_settings VALUES('seed_v1','true')")
