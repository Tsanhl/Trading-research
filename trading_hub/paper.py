"""Deterministic local OHLC simulation. Never a Webull fill or KX proof."""
from __future__ import annotations
from contextlib import closing

from decimal import Decimal
from pathlib import Path
import re
import sqlite3

from .common import canonical, digest, finite, instant, read_json
from .structure import validate_bars

FUTURES = {
    "MES": {"point_value_usd": 5, "tick": .25},
    "ES": {"point_value_usd": 50, "tick": .25},
    "NQ": {"point_value_usd": 20, "tick": .25},
}


def validate_dataset(data):
    meta, bars = data["metadata"], data["bars"]
    required = ("instrument", "root", "asset_class", "venue", "currency", "session", "timeframe", "adjustment", "source", "environment")
    if any(not meta.get(k) or meta[k] == "UNKNOWN" for k in required):
        raise ValueError("Explicit dataset identity/provenance required")
    if meta["currency"] != "USD" or meta["environment"] not in {"production", "sandbox", "synthetic"}:
        raise ValueError("Unsupported currency/environment")
    if meta["asset_class"] == "FUTURE":
        if meta["root"] not in FUTURES or meta["venue"] != "CME":
            raise ValueError("Unsupported futures product")
        if not re.fullmatch(re.escape(meta["root"])+r"[FGHJKMNQUVXZ]\d{1,4}", meta["instrument"]):
            raise ValueError("Exact expiry contract required; roots/continuous contracts are not fillable")
        if meta["adjustment"] != "unadjusted" or not meta.get("expiry"):
            raise ValueError("Futures require unadjusted bars and an explicit expiry")
        expiry = instant(meta["expiry"])
        if any(instant(b["close_time"]) >= expiry for b in bars):
            raise ValueError("Bars at/after expiry; roll contracts in separate datasets")
    elif meta["asset_class"] != "ETF" or meta["root"] not in {"SPY", "QQQ"} or meta["instrument"] != meta["root"]:
        raise ValueError("Simulation supports exact SPY/QQQ ETFs or registered futures, not options/index proxies")
    if not bars:
        raise ValueError("Empty dataset")
    validate_bars(bars)
    return meta, bars


def simulate(data, *, side="LONG", window=30, retest=True, slip_ticks=1, fee_per_side=1.0, quantity=1):
    meta, bars = validate_dataset(data)
    if side not in {"LONG", "SHORT"} or not isinstance(window,int) or window < 3:
        raise ValueError("Invalid side/window")
    if not isinstance(quantity,int) or isinstance(quantity,bool) or quantity < 1:
        raise ValueError("Positive whole-contract quantity required")
    if not isinstance(slip_ticks,int) or isinstance(slip_ticks,bool) or slip_ticks < 0 or finite(fee_per_side) < 0:
        raise ValueError("Nonnegative integer ticks and fees required")
    spec = FUTURES[meta["root"]] if meta["asset_class"] == "FUTURE" else {"point_value_usd": 1, "tick": .01}
    tick = Decimal(str(spec["tick"]))
    # Reject off-tick OHLC instead of quietly rounding the source data.
    if any(Decimal(str(b[f])) % tick for b in bars for f in ("open", "high", "low", "close")):
        raise ValueError("Dataset contains prices off the product tick grid")
    settings = {"version": "frozen-range-replay-0.1", "side": side, "window": window, "retest": retest,
                "slip_ticks": slip_ticks, "fee_per_side": fee_per_side, "quantity": quantity,
                "signal_expiry_bars": 12, "reward_risk": 2, "cost_scope": "assumed commissions/slippage; excludes margin, financing and tax"}
    dataset_hash = digest(data)
    run_id = digest({"dataset": dataset_hash, "settings": settings})
    events, trades = [], []
    chain = "0"*64
    direction = 1 if side == "LONG" else -1
    friction = slip_ticks*spec["tick"]

    def emit(kind, index, **payload):
        nonlocal chain
        event = {"run_id": run_id, "sequence": len(events), "kind": kind, "bar_index": index,
                 "observed_at": bars[index]["close_time"], "previous_sha256": chain, **payload}
        chain = digest(event)
        events.append({**event, "sha256": chain})

    armed = pending = position = None
    for i in range(window, len(bars)):
        b = bars[i]
        o,h,l,c = (finite(b[k]) for k in ("open","high","low","close"))
        closed_this_bar = False
        if pending:
            entry = o+direction*friction
            stop, target = pending["stop"], pending["target"]
            if direction*(entry-stop) <= 0 or direction*(target-entry) <= 0:
                emit("REJECTED_GAP", i, planned=pending, observed_open=o)
                closed_this_bar = True
            else:
                position = {"entry": entry, "stop": stop, "target": target, "entry_index": i,
                            "entry_time": b["open_time"], "signal_index": pending["signal_index"]}
                emit("SIMULATED_ENTRY", i, fill_time=b["open_time"], **position)
            pending = None
        if position:
            stop, target = position["stop"], position["target"]
            stop_hit = l <= stop if direction == 1 else h >= stop
            target_hit = h >= target if direction == 1 else l <= target
            gap_stop = o <= stop if direction == 1 else o >= stop
            gap_target = o >= target if direction == 1 else o <= target
            reason = None
            if gap_stop:
                exit_price, reason = o-direction*friction, "STOP_GAP"
            elif gap_target:
                exit_price, reason = target-direction*friction, "TARGET_AT_OPEN_CONSERVATIVE"
            elif stop_hit:
                exit_price, reason = stop-direction*friction, "BOTH_TOUCHED_STOP_FIRST" if target_hit else "STOP"
            elif target_hit:
                exit_price, reason = target-direction*friction, "TARGET"
            elif i == len(bars)-1:
                exit_price, reason = c-direction*friction, "END_OF_SAMPLE_MARK"
            if reason:
                gross = direction*(exit_price-position["entry"])*spec["point_value_usd"]*quantity
                pnl = round(gross-2*fee_per_side*quantity, 6)
                trade = {**position, "exit": exit_price, "exit_index": i, "reason": reason,
                         "net_pnl_usd": pnl, "quantity": quantity,
                         "path_ambiguous": stop_hit and target_hit and not gap_stop and not gap_target,
                         "fill_model": "OHLC_SIMULATION_NOT_BROKER_FILL"}
                emit("SIMULATED_EXIT", i, **trade)
                trades.append(trade)
                position = None
                closed_this_bar = True
        if position or closed_this_bar:
            continue
        if armed:
            boundary = armed["boundary"]
            if i-armed["index"] > 12:
                emit("EXPIRED", i, frozen_setup=armed)
                armed = None
            elif direction*(c-armed["stop"]) <= 0:
                emit("INVALIDATED", i, frozen_setup=armed)
                armed = None
            elif (l <= boundary <= h) and direction*(c-boundary) > 0:
                emit("LATER_BAR_RETEST_CONFIRMED", i, frozen_setup=armed)
                pending = {"stop": armed["stop"], "target": c+direction*2*abs(c-armed["stop"]), "signal_index": i}
                armed = None
            continue
        past = bars[i-window:i]
        upper, lower = max(finite(x["high"]) for x in past), min(finite(x["low"]) for x in past)
        boundary, stop = (upper,lower) if direction == 1 else (lower,upper)
        if direction*(c-boundary) > 0:
            armed = {"boundary": boundary, "stop": stop, "index": i, "known_at": b["close_time"],
                     "geometry_sha256": digest(past)}
            emit("EXPERIMENTAL_BREAKOUT", i, frozen_setup=armed)
            if not retest:
                pending = {"stop": stop, "target": c+direction*2*abs(c-stop), "signal_index": i}
                armed = None
    if pending:
        emit("NO_NEXT_BAR_NO_FILL", len(bars)-1)
    equity = peak = drawdown = 0.0
    for t in trades:
        equity += t["net_pnl_usd"]
        peak = max(peak,equity)
        drawdown = max(drawdown,peak-equity)
    return {"run_id": run_id, "mode": "LOCAL_SIMULATION", "dataset_sha256": dataset_hash,
            "metadata": meta, "settings": settings, "trades": trades, "events": events, "chain_head": chain,
            "score": {"trade_count": len(trades), "net_pnl_usd": round(equity,6),
                      "closed_trade_drawdown_usd": round(drawdown,6), "ambiguous_trades": sum(t["path_ambiguous"] for t in trades)},
            "independent_ledger": False, "kx_validation": False, "profitability_established": False}


def record_run(path, result):
    """Append-only SQLite log with deterministic IDs and a verifiable hash chain."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        for operation in ("UPDATE", "DELETE"):
            connection.execute(f"CREATE TRIGGER IF NOT EXISTS no_{operation.lower()} BEFORE {operation} ON runs BEGIN SELECT RAISE(ABORT, 'append only'); END")
        payload = canonical(result).decode()
        old = connection.execute("SELECT payload FROM runs WHERE run_id=?", (result["run_id"],)).fetchone()
        if old and old[0] != payload:
            raise ValueError("Deterministic run ID collision or altered ledger")
        connection.execute("INSERT OR IGNORE INTO runs VALUES (?,?)", (result["run_id"],payload))


def verify_chain(result):
    chain = "0"*64
    for i,event in enumerate(result["events"]):
        body = {k:v for k,v in event.items() if k != "sha256"}
        if body["sequence"] != i or body["previous_sha256"] != chain or body["run_id"] != result["run_id"] or digest(body) != event["sha256"]:
            return False
        chain = event["sha256"]
    return chain == result["chain_head"]
