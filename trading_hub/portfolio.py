"""Chronological portfolio overlay for independently generated strategy trades.

The single-instrument engines remain diagnostic.  This module applies shared
cash, position and loss controls without silently inventing broker margin.
"""
from __future__ import annotations

from collections import Counter

from .backtest import _fee, _product
from .common import finite, instant
from .paper import FUTURES


def _open_loss_check(root, trade, bars, *, preset, slippage_ticks, realized_today):
    """Return conservative observed open P&L and an optional forced close.

    Only bars strictly before the primary engine's exit bar are marked at the
    adverse OHLC extreme.  The primary exit bar already has a frozen path rule.
    """
    product = _product(root, preset)
    direction = int(trade["direction"])
    quantity = int(trade["quantity"])
    slippage = finite(product["tick"]) * int(slippage_ticks)
    point_value = finite(product["point_value_usd"])
    fees = 2 * _fee(product, preset, quantity)
    worst = 0.0
    forced = None
    start = int(trade["entry_index"])
    stop = int(trade["exit_index"])
    for index in range(start, stop):
        bar = bars[index]
        adverse = finite(bar["low"] if direction == 1 else bar["high"])
        liquidation = adverse - direction * slippage
        gross = direction * (liquidation - finite(trade["entry"])) * point_value * quantity
        open_net = round(gross - fees, 6)
        worst = min(worst, open_net)
        if realized_today + open_net <= -finite(preset["daily_loss_threshold_usd"]):
            forced = {**trade, "exit": liquidation, "exit_index": index,
                      "exit_time": bar["close_time"], "reason": "PORTFOLIO_OPEN_LOSS_LIMIT",
                      "gross_pnl_usd": round(gross, 6), "fees_usd": round(fees, 6),
                      "net_pnl_usd": open_net, "path_ambiguous": False,
                      "fill_model": "CONSERVATIVE_OHLC_PORTFOLIO_LIQUIDATION"}
            break
    return worst, forced


def run_portfolio(normalized, ledger, *, variant, cost, preset):
    roots = sorted(normalized)
    date_sets = [{session["date"] for session in normalized[root]["sessions"]} for root in roots]
    common_dates = sorted(set.intersection(*date_sets)) if date_sets else []
    session_lookup = {root:{session["date"]:session for session in normalized[root]["sessions"]}
                      for root in roots}
    candidates = []
    excluded_noncommon = 0
    for root in roots:
        for session in ledger[root][variant][cost]:
            for trade in session["trades"]:
                if session["date"] not in common_dates:
                    excluded_noncommon += 1
                    continue
                candidates.append({"root":root, "date":session["date"], **trade})
    candidates.sort(key=lambda item:(instant(item["entry_time"]),
                                     finite(item["planned_total_risk_usd"]), item["root"]))

    initial = finite(preset["initial_balance_usd"])
    balance = peak = initial
    max_drawdown = 0.0
    worst_open_pnl = 0.0
    accepted = []
    decisions = []
    rejection_reasons = Counter()
    daily_realized = {date:0.0 for date in common_dates}
    active = None

    def realize(item):
        nonlocal balance, peak, max_drawdown, active
        pnl = finite(item["net_pnl_usd"])
        balance += pnl
        daily_realized[item["date"]] = round(daily_realized[item["date"]] + pnl, 6)
        peak = max(peak, balance)
        max_drawdown = max(max_drawdown, peak - balance)
        accepted.append(item)
        active = None

    for candidate in candidates:
        if active and instant(active["exit_time"]) <= instant(candidate["entry_time"]):
            realize(active)
        reason = None
        if active:
            reason = "PORTFOLIO_POSITION_ALREADY_OPEN"
        elif daily_realized[candidate["date"]] <= -finite(preset["daily_loss_threshold_usd"]):
            reason = "PORTFOLIO_REALIZED_DAILY_LOSS_LOCK"
        elif candidate["root"] in FUTURES:
            reason = "FUTURES_MARGIN_UNVERIFIED"
        elif int(candidate["direction"]) == -1:
            reason = "ETF_SHORT_MARGIN_UNVERIFIED"
        else:
            required = finite(candidate["entry"]) * int(candidate["quantity"])
            if required > balance:
                reason = "INSUFFICIENT_CASH_BUYING_POWER"
        if reason:
            rejection_reasons[reason] += 1
            decisions.append({"status":"REJECTED", "reason":reason, "candidate":candidate})
            continue

        bars = session_lookup[candidate["root"]][candidate["date"]]["bars"]
        worst, forced = _open_loss_check(candidate["root"], candidate, bars, preset=preset,
                                         slippage_ticks=ledger[candidate["root"]][variant][cost][0]["slippage_ticks"],
                                         realized_today=daily_realized[candidate["date"]])
        worst_open_pnl = min(worst_open_pnl, worst)
        max_drawdown = max(max_drawdown, peak - (balance + worst))
        active = forced or candidate
        decisions.append({"status":"ACCEPTED", "reason":active["reason"] if forced else "PRESET_CONTROLS_PASS",
                          "candidate":candidate, "portfolio_trade":active,
                          "worst_observed_open_pnl_usd":round(worst, 6)})
    if active:
        realize(active)

    net = round(balance - initial, 6)
    summary = {"eligible_common_dates":common_dates, "eligible_sessions":len(common_dates),
               "candidate_trades":len(candidates), "excluded_noncommon_date_trades":excluded_noncommon,
               "accepted_trades":len(accepted), "rejected_trades":sum(rejection_reasons.values()),
               "rejection_reasons":dict(sorted(rejection_reasons.items())),
               "initial_balance_usd":round(initial, 6), "ending_equity_usd":round(balance, 6),
               "net_pnl_usd":net, "closed_and_open_max_drawdown_usd":round(max_drawdown, 6),
               "worst_observed_open_pnl_usd":round(worst_open_pnl, 6),
               "daily_realized_net_pnl_usd":daily_realized,
               "position_limit":1, "etf_long_buying_power_model":"FULL_NOTIONAL_CASH_FLOOR",
               "etf_short_margin_policy":"UNVERIFIED_FAIL_CLOSED",
               "futures_margin_policy":"UNVERIFIED_FAIL_CLOSED",
               "simultaneous_entry_priority":"LOWEST_PLANNED_RISK_THEN_SYMBOL"}
    return summary, {"decisions":decisions, "accepted_trades":accepted}


def run_portfolio_matrix(normalized, ledger, preset):
    matrix, portfolio_ledger = {}, {}
    for variant in preset["variants"]:
        matrix[variant], portfolio_ledger[variant] = {}, {}
        for cost in ("base", "stress"):
            summary, decisions = run_portfolio(normalized, ledger, variant=variant, cost=cost, preset=preset)
            matrix[variant][cost] = summary
            portfolio_ledger[variant][cost] = decisions
    return matrix, portfolio_ledger
