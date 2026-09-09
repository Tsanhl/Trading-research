from __future__ import annotations

import csv
import tomllib
from pathlib import Path

from .backtest import run_matrix
from .backtest_data import normalize_capture
from .common import ROOT, config, digest, file_hash, read_json, save_json
from .portfolio import run_portfolio_matrix


def build_backtest(capture_path=None, *, root=ROOT):
    app_root = Path(root)
    cfg = tomllib.loads((app_root/"config.toml").read_text(encoding="utf-8"))
    if cfg["policy"]["broker_execution"] or cfg["paper"]["broker_orders"]:
        raise ValueError("This build has no broker execution capability")
    preset = dict(cfg["backtest"]) | {key:value for key,value in cfg["paper"].items() if key not in {"broker_orders","holdout_membership","account_risk_policy"}}
    if capture_path is None:
        capture_path = read_json(app_root/"state/backtests/latest-capture.json")["path"]
    capture = read_json(capture_path)
    normalized = normalize_capture(capture)
    matrix,ledger = run_matrix(normalized,preset)
    portfolio_matrix,portfolio_ledger = run_portfolio_matrix(normalized,ledger,preset)
    source_hashes = {name:file_hash(app_root/"trading_hub"/name) for name in ("backtest.py","backtest_data.py","portfolio.py")}
    source_hashes["config.toml"] = file_hash(app_root/"config.toml")
    engine = "intraday-development-0.3"
    identity = digest({"capture":digest(capture),"preset":preset,"engine":engine,"source_hashes":source_hashes})
    output = app_root/"reports/backtests"/identity[:16]
    output.mkdir(parents=True,exist_ok=True)
    result = {"schema":"trading-hub.backtest.v1","run_id":identity,"data_captured_at":capture["captured_at"],
              "engine":engine,"engine_source_hashes":source_hashes,"capture_path":str(capture_path),"capture_sha256":digest(capture),
              "data_environment":"sandbox","data_independently_verified":False,"broker_fills":False,
              "kx_parity":False,"kx_validation":False,"holdout":False,"profitability_established":False,
              "preset":preset,"data_quality":{root:{k:v for k,v in data.items() if k != "sessions"}
                                                | {"complete_session_dates":[s["date"] for s in data["sessions"]]}
                                                for root,data in normalized.items()},
              "matrix":matrix,"ledger":ledger,"portfolio_matrix":portfolio_matrix,
              "portfolio_ledger":portfolio_ledger,
              "limitations":["Development sample only; settings were not derived from a frozen untouched holdout.",
                             "Webull sandbox is the only market source and bar-time/session semantics are not independently verified.",
                             "The model uses completed OHLC bars, not bid/ask depth, queue position, partial fills or broker acknowledgements.",
                             "The portfolio overlay fails closed on futures and ETF shorts because verified broker margin requirements are not available; ETF longs use a full-notional cash floor.",
                             "Futures are exact observed expiries but only standard NY 09:30-16:00 sessions are evaluated; overnight Globex is excluded.",
                             "ES and NQ may produce sizing rejections under the US$250 preset; rejected signals are not trades.",
                             "The strategies are experimental rolling-range breakout/retest rules, not the KX Pine contract or full channel/triangle logic.",
                             "Options are excluded because no historical point-in-time option quotes were captured."]}
    save_json(output/"backtest.json",result)
    summary_rows=[]
    trade_rows=[]
    for root,variants in matrix.items():
        for variant,costs in variants.items():
            for cost,score in costs.items():
                summary_rows.append({"root":root,"variant":variant,"cost":cost,**{k:v for k,v in score.items() if k != "daily_net_pnl_usd"}})
                for session in ledger[root][variant][cost]:
                    for trade in session["trades"]:
                        trade_rows.append({"root":root,"variant":variant,"cost":cost,"date":session["date"],**trade})
    for name,rows in (("summary.csv",summary_rows),("trades.csv",trade_rows)):
        if rows:
            with (output/name).open("w",newline="") as handle:
                writer=csv.DictWriter(handle,fieldnames=list(rows[0]))
                writer.writeheader();writer.writerows(rows)
    (output/"backtest.md").write_text(render(result))
    save_json(app_root/"state/backtests/latest.json",{"run_id":identity,"report":str(output/"backtest.md"),
                                                   "json":str(output/"backtest.json"),"summary_csv":str(output/"summary.csv"),
                                                   "trades_csv":str(output/"trades.csv")})
    return read_json(app_root/"state/backtests/latest.json")


def render(result):
    p=result["preset"]
    lines=["# Preset intraday development backtest","",f"Run: {result['run_id']}",f"Data captured: {result['data_captured_at']}","",
           "**DEVELOPMENT SAMPLE · SANDBOX DATA · LOCAL OHLC FILLS · NOT KX VALIDATION · NOT A PROFITABILITY CLAIM**","",
           "## Frozen preset","",f"Simulated balance: US${p['initial_balance_usd']:,.0f}; risk budget: US${p['risk_per_trade_usd']:,.0f} per trade; daily loss threshold: US${p['daily_loss_threshold_usd']:,.0f}.","",
           f"One open position per instrument diagnostic and one total in the portfolio overlay; no overnight holding; 30-bar range; 12-bar retest expiry; 2R target; no new entries after {p['entry_cutoff_new_york']} New York time.","",
           "Each complete session after the first is warmed with prior completed bars, so morning setups are eligible without using future data. The first captured session remains naturally warm-up limited.","",
           "Base costs use one tick each way; stress costs use two. Assumed per-side fees: MES US$1.25/contract, ES/NQ US$2.50/contract, ETFs US$0.005/share with US$1 minimum. These are modeling assumptions, not a verified Webull fee schedule.","",
           "## Results","", "| Instrument | Sessions | Variant | Cost | Trades | Risk rejects | Win % | Net P&L | Expectancy | Max DD |", "|---|---:|---|---|---:|---:|---:|---:|---:|---:|"]
    for root,variants in result["matrix"].items():
        for variant,costs in variants.items():
            for cost,score in costs.items():
                def money(x): return "—" if x is None else f"${x:,.2f}"
                win="—" if score["win_rate_pct"] is None else f"{score['win_rate_pct']:.2f}%"
                lines.append(f"| {root} | {score['sessions']} | {variant} | {cost} | {score['trade_count']} | {score['risk_rejections']} | {win} | {money(score['net_pnl_usd'])} | {money(score['expectancy_usd'])} | {money(score['closed_trade_max_drawdown_usd'])} |")
    lines += ["", "## Shared portfolio overlay", "",
              "This chronological overlay applies a single account balance, one total open position, portfolio-wide realized/open-loss monitoring and buying-power gates. Unknown broker margin fails closed.", "",
              "| Variant | Cost | Common days | Candidates | Accepted | Rejected | Net P&L | Ending equity | Max DD incl. open loss |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for variant,costs in result["portfolio_matrix"].items():
        for cost,score in costs.items():
            lines.append(f"| {variant} | {cost} | {score['eligible_sessions']} | {score['candidate_trades']} | {score['accepted_trades']} | {score['rejected_trades']} | ${score['net_pnl_usd']:,.2f} | ${score['ending_equity_usd']:,.2f} | ${score['closed_and_open_max_drawdown_usd']:,.2f} |")
    lines += ["","## Interpretation",""]
    ranked=[]
    for root,variants in result["matrix"].items():
        for variant,costs in variants.items():
            score=costs["base"]
            if score["trade_count"]:
                ranked.append((score["net_pnl_usd"],root,variant,score))
    if ranked:
        best=max(ranked)
        descriptor = "largest" if best[0] > 0 else "least negative"
        lines.append(f"The {descriptor} in-sample base-cost net result was {best[1]} {best[2]} at ${best[0]:,.2f} across {best[3]['trade_count']} trades. This is a descriptive maximum among tested variants, not a selected production rule.")
    else:
        lines.append("No variant produced a preset-compliant simulated trade.")
    lines += ["", "A positive cell does not validate an edge: the sample is short, sandbox-only and used for development. Compare base versus stress costs for fragility; zero-trade ES/NQ cells can be a correct risk-budget result rather than a failed engine.","",
              "## Data coverage", "", "| Root | Exact instrument | Complete sessions | Raw rows | Excluded extended rows | Excluded dates |", "|---|---|---:|---:|---:|---:|"]
    for root,data in result["data_quality"].items():
        lines.append(f"| {root} | {data['instrument']} | {len(data['complete_session_dates'])} | {data['raw_rows']} | {data['excluded_extended_rows']} | {len(data['excluded_dates'])} |")
    lines += ["","## Limits",""]+["- "+item for item in result["limitations"]]
    lines += ["","See `backtest.json` for the full event and trade ledgers. `summary.csv` and `trades.csv` are provided for review. No broker order was submitted.",""]
    return "\n".join(lines)
