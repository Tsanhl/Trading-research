"""Causal intraday development backtests with fixed preset risk and costs."""
from __future__ import annotations

from datetime import datetime
import math
from zoneinfo import ZoneInfo

from .common import digest, finite
from .paper import FUTURES

NY = ZoneInfo("America/New_York")


def _minute(timestamp):
    local = datetime.fromisoformat(timestamp).astimezone(NY)
    return local.hour * 60 + local.minute


def _ema_series(values, period=20):
    out = [None] * len(values)
    if len(values) < period:
        return out
    current = sum(values[:period]) / period
    out[period - 1] = current
    alpha = 2 / (period + 1)
    for index in range(period, len(values)):
        current += alpha * (values[index] - current)
        out[index] = current
    return out


def _product(root, preset):
    if root in FUTURES:
        fee = preset["mes_fee_per_contract_side"] if root == "MES" else preset["es_nq_fee_per_contract_side"]
        cap = preset["max_mes_contracts"] if root == "MES" else preset["max_futures_contracts"]
        return {**FUTURES[root], "fee_per_side": fee, "quantity_cap": cap, "quantity_name": "contracts"}
    return {"point_value_usd": 1, "tick": .01, "fee_per_side": None,
            "quantity_cap": preset["max_etf_shares"], "quantity_name": "shares"}


def _fee(product, preset, quantity):
    if product["fee_per_side"] is not None:
        return finite(product["fee_per_side"]) * quantity
    return max(finite(preset["etf_min_fee_side"]), finite(preset["etf_fee_per_share_side"]) * quantity)


def _quantity(entry, stop, direction, product, preset, slippage):
    adverse_stop = stop - direction * slippage
    point_risk = direction * (entry - adverse_stop)
    if point_risk <= 0:
        return 0, None
    unit_price_risk = point_risk * product["point_value_usd"]
    for quantity in range(int(product["quantity_cap"]), 0, -1):
        total_risk = unit_price_risk * quantity + 2 * _fee(product, preset, quantity)
        if total_risk <= preset["risk_per_trade_usd"]:
            return quantity, total_risk
    return 0, unit_price_risk + 2 * _fee(product, preset, 1)


def run_session(dataset, session, *, variant, preset, slippage_ticks, warmup_bars=None):
    """Run one symbol/day; only information from completed bars is used."""
    if variant not in {"breakout", "retest", "retest_ema20"}:
        raise ValueError("Unknown frozen variant")
    session_bars = session["bars"]
    root = dataset["root"]
    product = _product(root, preset)
    slippage = product["tick"] * slippage_ticks
    window = int(preset["window_bars"])
    context = list(warmup_bars or [])[-max(window, 25):]
    bars = context + session_bars
    session_start = len(context)
    expires = int(preset["setup_expiry_bars"])
    reward_risk = finite(preset["reward_risk_target"])
    if reward_risk < finite(preset["minimum_entry_reward_risk"]):
        raise ValueError("Target is below the frozen minimum reward/risk")
    entry_cutoff = int(preset["entry_cutoff_new_york"].split(":")[0]) * 60 + int(preset["entry_cutoff_new_york"].split(":")[1])
    closes = [finite(bar["close"]) for bar in bars]
    expected_open = int(preset["session_open_new_york"].split(":")[0]) * 60 + int(preset["session_open_new_york"].split(":")[1])
    expected_flatten = int(preset["flatten_new_york"].split(":")[0]) * 60 + int(preset["flatten_new_york"].split(":")[1])
    if not session_bars or _minute(session_bars[0]["open_time"]) != expected_open or _minute(session_bars[-1]["close_time"]) != expected_flatten:
        raise ValueError("Engine requires one complete preset session")
    ema20 = _ema_series(closes)
    events, trades = [], []
    chain = "0" * 64
    armed = pending = position = None
    day_pnl = 0.0
    daily_lock_emitted = False

    def emit(kind, index, *, event_time=None, **payload):
        nonlocal chain
        body = {"sequence": len(events), "kind": kind, "bar_index": index - session_start,
                "known_at": event_time or bars[index]["close_time"], "previous_sha256": chain, **payload}
        chain = digest(body)
        events.append({**body, "sha256": chain})

    def ema_pass(index, direction):
        if index < 24 or ema20[index] is None or ema20[index - 5] is None:
            return False
        return direction * (closes[index] - ema20[index]) > 0 and direction * (ema20[index] - ema20[index - 5]) > 0

    for index in range(max(window, session_start), len(bars)):
        session_index = index - session_start
        bar = bars[index]
        o, high, low, close = (finite(bar[key]) for key in ("open", "high", "low", "close"))
        exited = False
        if day_pnl <= -finite(preset["daily_loss_threshold_usd"]):
            if not daily_lock_emitted:
                emit("DAILY_LOSS_LOCK", index, realized_net_pnl_usd=round(day_pnl, 6))
                daily_lock_emitted = True
            armed = pending = None
            continue
        if pending:
            direction = pending["direction"]
            entry = o + direction * slippage
            quantity, planned_risk_per_unit = _quantity(entry, pending["stop"], direction, product, preset, slippage)
            if direction * (entry - pending["stop"]) <= 0:
                emit("REJECTED_ENTRY_GAP", index, event_time=bar["open_time"], pending=pending, observed_open=o)
                exited = True
            elif quantity < 1:
                emit("REJECTED_BY_RISK_PRESET", index, event_time=bar["open_time"], pending=pending,
                     risk_budget_usd=preset["risk_per_trade_usd"], planned_total_risk_usd=planned_risk_per_unit)
                exited = True
            else:
                risk_points = direction * (entry - pending["stop"])
                target = entry + direction * reward_risk * risk_points
                position = {"direction": direction, "side": "LONG" if direction == 1 else "SHORT",
                            "entry": entry, "stop": pending["stop"], "target": target,
                            "quantity": quantity, "quantity_name": product["quantity_name"],
                            "entry_index": session_index, "entry_time": bar["open_time"],
                            "signal_index": pending["signal_index"], "signal_time": pending["signal_time"],
                            "planned_total_risk_usd": planned_risk_per_unit}
                emit("SIMULATED_ENTRY", index, event_time=bar["open_time"], **position)
            pending = None
        if position:
            direction = position["direction"]
            stop, target = position["stop"], position["target"]
            stop_hit = low <= stop if direction == 1 else high >= stop
            target_hit = high >= target if direction == 1 else low <= target
            gap_stop = o <= stop if direction == 1 else o >= stop
            gap_target = o >= target if direction == 1 else o <= target
            reason = None
            if gap_stop:
                exit_price, reason = o - direction * slippage, "STOP_GAP"
            elif gap_target:
                exit_price, reason = target - direction * slippage, "TARGET_AT_OPEN_CONSERVATIVE"
            elif stop_hit:
                exit_price, reason = stop - direction * slippage, "BOTH_TOUCHED_STOP_FIRST" if target_hit else "STOP"
            elif target_hit:
                exit_price, reason = target - direction * slippage, "TARGET"
            elif index == len(bars) - 1:
                exit_price, reason = close - direction * slippage, "SESSION_FLATTEN"
            if reason:
                fees = 2 * _fee(product, preset, position["quantity"])
                gross = direction * (exit_price - position["entry"]) * product["point_value_usd"] * position["quantity"]
                at_open = reason in {"STOP_GAP", "TARGET_AT_OPEN_CONSERVATIVE"}
                trade = {**position, "exit": exit_price, "exit_index": session_index,
                         "exit_time": bar["open_time"] if at_open else bar["close_time"],
                         "reason": reason, "gross_pnl_usd": round(gross, 6), "fees_usd": round(fees, 6),
                         "net_pnl_usd": round(gross - fees, 6),
                         "path_ambiguous": stop_hit and target_hit and not gap_stop and not gap_target,
                         "fill_model": "OHLC_SIMULATION_NOT_WEBULL_FILL"}
                emit("SIMULATED_EXIT", index, event_time=trade["exit_time"], trade=trade)
                trades.append(trade)
                day_pnl += trade["net_pnl_usd"]
                position = None
                exited = True
        if position or exited or index == len(bars) - 1:
            continue
        if armed:
            direction = armed["direction"]
            if session_index - armed["index"] > expires:
                emit("SETUP_EXPIRED", index, setup=armed)
                armed = None
            elif direction * (close - armed["stop"]) <= 0:
                emit("SETUP_INVALIDATED", index, setup=armed)
                armed = None
            elif low <= armed["boundary"] <= high and direction * (close - armed["boundary"]) > 0:
                if variant == "retest_ema20" and not ema_pass(index, direction):
                    emit("EMA20_FILTER_REJECTED_RETEST", index, setup=armed, ema20=ema20[index])
                    armed = None
                elif _minute(bar["close_time"]) <= entry_cutoff:
                    emit("LATER_BAR_RETEST_CONFIRMED", index, setup=armed, ema20=ema20[index])
                    pending = {"direction": direction, "stop": armed["stop"], "signal_index": session_index,
                               "signal_time": bar["close_time"]}
                    armed = None
                else:
                    emit("ENTRY_CUTOFF_REJECTED", index, setup=armed)
                    armed = None
            continue
        if _minute(bar["close_time"]) > entry_cutoff:
            continue
        past = bars[index - window:index]
        upper = max(finite(item["high"]) for item in past)
        lower = min(finite(item["low"]) for item in past)
        direction = 1 if close > upper else -1 if close < lower else 0
        if not direction:
            continue
        setup = {"direction": direction, "side": "LONG" if direction == 1 else "SHORT",
                 "boundary": upper if direction == 1 else lower,
                 "stop": lower if direction == 1 else upper, "index": session_index,
                 "known_at": bar["close_time"], "geometry_sha256": digest(past)}
        if variant == "retest_ema20" and not ema_pass(index, direction):
            emit("EMA20_FILTER_REJECTED_BREAKOUT", index, setup=setup, ema20=ema20[index])
        elif variant == "breakout":
            emit("BREAKOUT_CONFIRMED", index, setup=setup, ema20=ema20[index])
            pending = {"direction": direction, "stop": setup["stop"], "signal_index": session_index,
                       "signal_time": bar["close_time"]}
        else:
            emit("BREAKOUT_ARMED_FOR_RETEST", index, setup=setup, ema20=ema20[index])
            armed = setup
    if pending:
        emit("NO_NEXT_BAR_NO_FILL", len(bars) - 1, pending=pending)
    return {"date": session["date"], "variant": variant, "slippage_ticks": slippage_ticks,
            "warmup_bar_count": session_start, "events": events, "trades": trades, "chain_head": chain}


def score_sessions(results, preset):
    trades = [trade for result in results for trade in result["trades"]]
    events = [event for result in results for event in result["events"]]
    equity = peak = max_drawdown = 0.0
    daily = {}
    for result in results:
        day_pnl = sum(trade["net_pnl_usd"] for trade in result["trades"])
        daily[result["date"]] = round(day_pnl, 6)
        for trade in result["trades"]:
            equity += trade["net_pnl_usd"]
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
    gains = sum(max(0, trade["net_pnl_usd"]) for trade in trades)
    losses = -sum(min(0, trade["net_pnl_usd"]) for trade in trades)
    wins = sum(trade["net_pnl_usd"] > 0 for trade in trades)
    loss_limit_breaches = sum(pnl <= -preset["daily_loss_threshold_usd"] for pnl in daily.values())
    return {"sessions": len(results), "trade_count": len(trades), "wins": wins, "losses": len(trades) - wins,
            "win_rate_pct": round(100 * wins / len(trades), 2) if trades else None,
            "net_pnl_usd": round(equity, 6), "expectancy_usd": round(equity / len(trades), 6) if trades else None,
            "profit_factor": round(gains / losses, 4) if losses else None,
            "closed_trade_max_drawdown_usd": round(max_drawdown, 6),
            "signals": sum(event["kind"] in {"BREAKOUT_CONFIRMED", "BREAKOUT_ARMED_FOR_RETEST"} for event in events),
            "risk_rejections": sum(event["kind"] == "REJECTED_BY_RISK_PRESET" for event in events),
            "ema_rejections": sum(event["kind"].startswith("EMA20_FILTER_REJECTED") for event in events),
            "ambiguous_trades": sum(trade["path_ambiguous"] for trade in trades),
            "daily_loss_locks": sum(event["kind"] == "DAILY_LOSS_LOCK" for event in events),
            "daily_loss_threshold_breaches": loss_limit_breaches, "daily_net_pnl_usd": daily}


def run_matrix(normalized, preset):
    matrix, ledger = {}, {}
    for root,dataset in normalized.items():
        matrix[root], ledger[root] = {}, {}
        for variant in preset["variants"]:
            matrix[root][variant], ledger[root][variant] = {}, {}
            for label,ticks in (("base", preset["base_slippage_ticks"]), ("stress", preset["stress_slippage_ticks"])):
                sessions = []
                prior_bars = []
                for session in dataset["sessions"]:
                    sessions.append(run_session(dataset, session, variant=variant, preset=preset,
                                                slippage_ticks=ticks, warmup_bars=prior_bars))
                    prior_bars.extend(session["bars"])
                    prior_bars = prior_bars[-max(int(preset["window_bars"]), 25):]
                matrix[root][variant][label] = score_sessions(sessions, preset)
                ledger[root][variant][label] = sessions
    return matrix, ledger
