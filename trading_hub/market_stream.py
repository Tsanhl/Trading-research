"""Optional shared Alpaca IEX WebSocket manager.

One bounded upstream connection serves the local process. The dependency and
credentials are optional; imports, polling, research and calculations continue
without them. This module never calls broker account or order endpoints.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import random
import ssl
import threading
import time
import uuid

from .market_live import keys
from .symbols import resolve

URL = "wss://stream.data.alpaca.markets/v2/iex"
MAX_SYMBOLS = 30
MAX_EVENTS = 2000
MAX_CLIENTS = 8
MAX_REVISIONS = 4000
LEASE_SECONDS = 45


def _iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def dependency_available():
    return importlib.util.find_spec("websocket") is not None


class AlpacaIEXStream:
    def __init__(self, root, *, ws_factory=None, sleeper=time.sleep, jitter=None):
        self.root = root
        self._factory = ws_factory
        self._sleep = sleeper
        self._jitter = jitter or (lambda base: random.uniform(0, min(1.0, base * .25)))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._ws = None
        self._symbols = set()
        self._leases = {}
        self._events = deque(maxlen=MAX_EVENTS)
        self._seen = deque(maxlen=MAX_EVENTS * 2)
        self._seen_set = set()
        self._revisions = OrderedDict()
        self._last_source = {}
        self._sequence = 0
        self._status = "OFF"
        self._error = None
        self._connected_at = None
        self._last_message_at = None
        self._last_observation_at = None
        self._reconnects = 0
        self._gaps = 0
        self._duplicates = 0
        self._late = 0
        self._overflow = 0

    def _credentials(self):
        credentials = keys(self.root)
        if not all(credentials.values()):
            raise ValueError("Alpaca IEX streaming needs APCA_API_KEY_ID and APCA_API_SECRET_KEY in local-data.env; polling/imports remain available")
        return credentials

    def _normalize_symbols(self, symbols):
        if not isinstance(symbols, list) or not symbols:
            raise ValueError("Choose at least one U.S. stock or ETF")
        normalized = []
        for raw in symbols:
            info = resolve(raw)
            if not info["alpaca"]:
                raise ValueError(f"{info['symbol']} is not eligible for the Alpaca IEX U.S. stock stream")
            normalized.append(info["alpaca"])
        normalized = list(dict.fromkeys(normalized))
        if len(normalized) > MAX_SYMBOLS:
            raise ValueError(f"The free stream manager is capped at {MAX_SYMBOLS} symbols")
        return set(normalized)

    @staticmethod
    def _client_id(value=None):
        if value in (None, ""):
            return str(uuid.uuid4())
        value = str(value)
        try:
            return str(uuid.UUID(value))
        except ValueError:
            raise ValueError("Invalid stream client identifier") from None

    def _union_symbols(self):
        return set().union(*(lease["symbols"] for lease in self._leases.values())) if self._leases else set()

    def _prune_leases(self):
        now = time.monotonic()
        expired = [key for key, lease in self._leases.items() if now - lease["touched"] > LEASE_SECONDS]
        if not expired:
            return
        old = set(self._symbols)
        for key in expired:
            self._leases.pop(key, None)
        self._symbols = self._union_symbols()
        self._update_subscription(old, self._symbols)
        self._prune_symbol_state()

    def _prune_symbol_state(self):
        for symbol in list(self._last_source):
            if symbol not in self._symbols:
                self._last_source.pop(symbol, None)

    def start(self, symbols, client_id=None):
        wanted = self._normalize_symbols(symbols)
        if self._factory is None and not dependency_available():
            raise ValueError("Optional package websocket-client==1.9.2 is missing. Run the free-feed installer, restart, then try streaming again")
        self._credentials()
        with self._lock:
            self._prune_leases()
            client_id = self._client_id(client_id)
            if client_id not in self._leases and len(self._leases) >= MAX_CLIENTS:
                raise ValueError(f"The local stream relay is capped at {MAX_CLIENTS} active browser clients")
            old = set(self._symbols)
            self._leases[client_id] = {"symbols": wanted, "touched": time.monotonic()}
            self._symbols = self._union_symbols()
            if self._thread and self._thread.is_alive():
                self._update_subscription(old, self._symbols)
                return {**self.public_status(), "clientId": client_id}
            self._stop.clear()
            self._status = "CONNECTING"
            self._error = None
            self._thread = threading.Thread(target=self._run, name="alpaca-iex-stream", daemon=True)
            self._thread.start()
            return {**self.public_status(), "clientId": client_id}

    def stop(self, client_id=None, *, all_clients=False):
        with self._lock:
            if client_id and not all_clients:
                client_id = self._client_id(client_id)
                old = set(self._symbols)
                self._leases.pop(client_id, None)
                self._symbols = self._union_symbols()
                if self._symbols:
                    self._update_subscription(old, self._symbols)
                    self._prune_symbol_state()
                    return {**self.public_status(), "clientId": client_id}
            self._leases.clear()
        self._stop.set()
        with self._lock:
            ws = self._ws
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3)
        with self._lock:
            self._status = "OFF"
            self._ws = None
            self._symbols.clear()
            self._prune_symbol_state()
        return self.public_status()

    def _send(self, value):
        raw = json.dumps(value, separators=(",", ":"))
        if self._ws:
            self._ws.send(raw)

    def _update_subscription(self, old, new):
        if not self._ws or self._status not in {"AUTHENTICATED", "STREAMING"}:
            return
        remove, add = sorted(old - new), sorted(new - old)
        if remove:
            self._send({"action": "unsubscribe", "trades": remove, "quotes": remove,
                        "bars": remove, "updatedBars": remove})
        if add:
            self._send({"action": "subscribe", "trades": add, "quotes": add,
                        "bars": add, "updatedBars": add})

    def _build_ws(self, credentials):
        if self._factory:
            return self._factory(self._on_open, self._on_message, self._on_error, self._on_close)
        import websocket
        return websocket.WebSocketApp(URL, on_open=self._on_open, on_message=self._on_message,
                                      on_error=self._on_error, on_close=self._on_close,
                                      header=["User-Agent: TradingResearchHub/0.6 read-only"])

    def _run(self):
        backoff = 1.0
        while not self._stop.is_set():
            credentials = self._credentials()
            ws = self._build_ws(credentials)
            with self._lock:
                self._ws = ws
                if self._reconnects:
                    self._status = "RECONNECTING"
                    self._gaps += 1
            try:
                ws.run_forever(ping_interval=20, ping_timeout=10,
                               sslopt={"cert_reqs": ssl.CERT_REQUIRED})
            except Exception as exc:
                self._on_error(ws, exc)
            finally:
                with self._lock:
                    self._ws = None
            if self._stop.is_set():
                break
            self._reconnects += 1
            with self._lock:
                self._status = "RECONNECTING"
            delay = min(backoff, 30.0) + self._jitter(backoff)
            if self._stop.wait(delay):
                break
            backoff = min(backoff * 2, 30.0)
        with self._lock:
            self._status = "OFF"

    def _on_open(self, ws):
        credentials = self._credentials()
        self._send({"action": "auth", "key": credentials["APCA_API_KEY_ID"],
                    "secret": credentials["APCA_API_SECRET_KEY"]})
        with self._lock:
            self._status = "CONNECTED_AUTH_PENDING"
            self._connected_at = _iso_now()

    def _on_error(self, ws, error):
        with self._lock:
            self._error = "Authentication, entitlement or connection error; check free IEX keys and provider status"
            self._status = "ERROR"

    def _on_close(self, ws, code=None, message=None):
        if not self._stop.is_set():
            with self._lock:
                self._status = "DISCONNECTED"

    def _on_message(self, ws, raw):
        try:
            messages = json.loads(raw)
            if not isinstance(messages, list):
                messages = [messages]
        except (ValueError, TypeError):
            with self._lock:
                self._error = "Provider sent malformed stream JSON"
            return
        for message in messages:
            if not isinstance(message, dict):
                continue
            kind = message.get("T")
            if kind == "success" and message.get("msg") == "authenticated":
                with self._lock:
                    self._status = "AUTHENTICATED"
                    wanted = sorted(self._symbols)
                self._send({"action": "subscribe", "trades": wanted, "quotes": wanted,
                            "bars": wanted, "updatedBars": wanted})
                continue
            if kind == "subscription":
                with self._lock:
                    self._status = "STREAMING"
                    self._error = None
                continue
            if kind == "error":
                with self._lock:
                    self._status = "ENTITLEMENT_ERROR" if message.get("code") in {402, 403, 406} else "AUTH_ERROR"
                    self._error = "Provider rejected authentication, entitlement or subscription; keys were not exposed"
                continue
            if kind in {"t", "q", "b", "u", "c", "x"}:
                self._record(message)

    def _record(self, message):
        symbol = str(message.get("S") or "")
        with self._lock:
            if symbol not in self._symbols:
                return
        source_at = str(message.get("t") or "") or None
        identity = [message.get("T"), symbol, message.get("i"), source_at,
                    message.get("p"), message.get("bp"), message.get("ap"),
                    message.get("o"), message.get("h"), message.get("l"), message.get("c"), message.get("v")]
        fingerprint = hashlib.sha256(json.dumps(identity, separators=(",", ":"), default=str).encode()).hexdigest()
        logical = json.dumps([message.get("T"), symbol, message.get("i"), source_at], separators=(",", ":"))
        with self._lock:
            if fingerprint in self._seen_set:
                self._duplicates += 1
                return
            if len(self._seen) == self._seen.maxlen:
                self._seen_set.discard(self._seen[0])
            self._seen.append(fingerprint)
            self._seen_set.add(fingerprint)
            try:
                source_time = datetime.fromisoformat(source_at.replace("Z", "+00:00")) if source_at else None
            except ValueError:
                source_time = None
            prior_source = self._last_source.get(symbol)
            late = bool(source_time and prior_source and source_time < prior_source)
            if late:
                self._late += 1
            if source_time and (not prior_source or source_time > prior_source):
                self._last_source[symbol] = source_time
            revision = self._revisions.get(logical, 0) + 1
            self._revisions[logical] = revision
            self._revisions.move_to_end(logical)
            while len(self._revisions) > MAX_REVISIONS:
                self._revisions.popitem(last=False)
            self._sequence += 1
            event = {"sequence": self._sequence, "type": message.get("T"), "symbol": symbol,
                     "sourceAt": source_at, "receivedAt": _iso_now(), "feed": "alpaca-iex",
                     "coverage": "IEX_SINGLE_EXCHANGE", "late": late, "revision": revision,
                     "correction": message.get("T") in {"u", "c", "x"}}
            for source, target in (("p", "price"), ("s", "size"), ("bp", "bid"),
                                   ("ap", "ask"), ("bs", "bidSize"), ("as", "askSize"),
                                   ("o", "open"), ("h", "high"), ("l", "low"),
                                   ("c", "close"), ("v", "volume"), ("oi", "originalTradeId"),
                                   ("ci", "correctedTradeId"), ("cp", "correctedPrice"),
                                   ("cs", "correctedSize")):
                if source in message:
                    event[target] = message[source]
            if len(self._events) == self._events.maxlen:
                self._overflow += 1
            self._events.append(event)
            self._last_message_at = event["receivedAt"]
            if message.get("T") in {"t", "q", "b", "u"}:
                self._last_observation_at = source_at

    def events_since(self, sequence, client_id=None):
        try:
            sequence = max(0, int(sequence))
        except (TypeError, ValueError):
            raise ValueError("since must be a non-negative event sequence") from None
        with self._lock:
            self._prune_leases()
            if client_id:
                client_id = self._client_id(client_id)
                if client_id not in self._leases:
                    raise ValueError("Stream client lease expired or was stopped; press Start stream again")
                self._leases[client_id]["touched"] = time.monotonic()
            events = [dict(event) for event in self._events if event["sequence"] > sequence]
            return {"events": events, "status": self.public_status(),
                    "gap": bool(events and events[0]["sequence"] > sequence + 1)}

    def public_status(self):
        with self._lock:
            self._prune_leases()
            return {"mode": "STREAMING — IEX single exchange" if self._status == "STREAMING" else "OFF/PENDING",
                    "status": self._status, "configured": all(keys(self.root).values()),
                    "dependencyInstalled": dependency_available() or self._factory is not None,
                    "symbols": sorted(self._symbols), "connectedAt": self._connected_at,
                    "lastReceivedAt": self._last_message_at,
                    "lastSuccessfulObservationAt": self._last_observation_at,
                    "error": self._error, "connectionLimit": 1, "symbolLimit": MAX_SYMBOLS,
                    "clientLimit": MAX_CLIENTS, "activeClients": len(self._leases),
                    "leaseSeconds": LEASE_SECONDS,
                    "queueLimit": MAX_EVENTS, "queued": len(self._events),
                    "reconnects": self._reconnects, "markedGaps": self._gaps,
                    "duplicatesDropped": self._duplicates, "lateEvents": self._late,
                    "overflowDrops": self._overflow,
                    "coverage": "Eligible U.S. stocks/ETFs on Alpaca IEX; not SIP/NBBO, SPX, options or CME futures"}
