"""Independent audit and fill reconstruction for a development backtest artifact."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from trading_hub.common import digest,file_hash,finite,instant,read_json,save_json

NY=ZoneInfo("America/New_York")
PRODUCTS={
    "MES":{"tick":.25,"point_value":5,"fee":1.25},
    "ES":{"tick":.25,"point_value":50,"fee":2.5},
    "NQ":{"tick":.25,"point_value":20,"fee":2.5},
    "SPY":{"tick":.01,"point_value":1,"fee":None},
    "QQQ":{"tick":.01,"point_value":1,"fee":None},
}


def close_enough(left,right,tolerance=1e-6):
    return abs(finite(left)-finite(right)) <= tolerance


def raw_sessions(capture):
    """Rebuild only the timestamp/OHLC mapping needed for fill verification."""
    result={}
    for root,item in capture["datasets"].items():
        days=defaultdict(list)
        for row in item["rows"]:
            start=datetime.fromtimestamp(instant(row["time"]),timezone.utc)
            local=start.astimezone(NY)
            minute=local.hour*60+local.minute
            if local.weekday()<5 and 570 <= minute < 960:
                days[local.date().isoformat()].append({"open_time":start.isoformat(),
                    "close_time":(start+timedelta(minutes=5)).isoformat(),
                    **{key:finite(row[key]) for key in ("open","high","low","close")}})
        result[root]={date:sorted(bars,key=lambda bar:instant(bar["open_time"])) for date,bars in days.items()}
    return result


def fee(root,preset,quantity):
    product=PRODUCTS[root]
    if product["fee"] is not None:
        return product["fee"]*quantity
    return max(finite(preset["etf_min_fee_side"]),finite(preset["etf_fee_per_share_side"])*quantity)


def verify_fill(root,session,trade,bars,preset,failures,label):
    try:
        entry_index=int(trade["entry_index"]); exit_index=int(trade["exit_index"])
        if not (0 <= int(trade["signal_index"]) < entry_index <= exit_index < len(bars)):
            raise ValueError("index")
        entry_bar,exit_bar=bars[entry_index],bars[exit_index]
        direction=int(trade["direction"]); quantity=int(trade["quantity"])
        product=PRODUCTS[root]
        slippage=product["tick"]*int(session["slippage_ticks"])
        expected_entry=entry_bar["open"]+direction*slippage
        if not close_enough(trade["entry"],expected_entry) or instant(trade["entry_time"])!=instant(entry_bar["open_time"]):
            failures.append("ENTRY_RECONSTRUCTION:"+label)
        stop=finite(trade["stop"]); target=finite(trade["target"])
        expected_target=expected_entry+direction*finite(preset["reward_risk_target"])*direction*(expected_entry-stop)
        if not close_enough(target,expected_target):
            failures.append("TARGET_RECONSTRUCTION:"+label)
        o,h,l,c=(exit_bar[key] for key in ("open","high","low","close"))
        stop_hit=l<=stop if direction==1 else h>=stop
        target_hit=h>=target if direction==1 else l<=target
        gap_stop=o<=stop if direction==1 else o>=stop
        gap_target=o>=target if direction==1 else o<=target
        reason=trade["reason"]
        if gap_stop:
            expected_exit,expected_reason,expected_time=o-direction*slippage,"STOP_GAP",exit_bar["open_time"]
        elif gap_target:
            expected_exit,expected_reason,expected_time=target-direction*slippage,"TARGET_AT_OPEN_CONSERVATIVE",exit_bar["open_time"]
        elif stop_hit:
            expected_exit=stop-direction*slippage
            expected_reason="BOTH_TOUCHED_STOP_FIRST" if target_hit else "STOP"
            expected_time=exit_bar["close_time"]
        elif target_hit:
            expected_exit,expected_reason,expected_time=target-direction*slippage,"TARGET",exit_bar["close_time"]
        elif exit_index==len(bars)-1:
            expected_exit,expected_reason,expected_time=c-direction*slippage,"SESSION_FLATTEN",exit_bar["close_time"]
        else:
            raise ValueError("exit path")
        if reason!=expected_reason or not close_enough(trade["exit"],expected_exit) or instant(trade["exit_time"])!=instant(expected_time):
            failures.append("EXIT_RECONSTRUCTION:"+label)
        gross=direction*(expected_exit-expected_entry)*product["point_value"]*quantity
        fees=2*fee(root,preset,quantity)
        if not close_enough(trade["gross_pnl_usd"],round(gross,6)) or not close_enough(trade["fees_usd"],round(fees,6)) or not close_enough(trade["net_pnl_usd"],round(gross-fees,6)):
            failures.append("PNL_RECONSTRUCTION:"+label)
        if bool(trade["path_ambiguous"]) != bool(stop_hit and target_hit and not gap_stop and not gap_target):
            failures.append("AMBIGUITY_RECONSTRUCTION:"+label)
    except (KeyError,TypeError,ValueError,IndexError,OverflowError):
        failures.append("FILL_RECONSTRUCTION_ERROR:"+label)


def verify_portfolios(result,failures):
    initial=finite(result["preset"]["initial_balance_usd"])
    for variant,costs in result.get("portfolio_matrix",{}).items():
        for cost,score in costs.items():
            detail=result["portfolio_ledger"][variant][cost]
            decisions=detail["decisions"]
            accepted=detail["accepted_trades"]
            label=f"{variant}:{cost}"
            if sum(item["status"]=="ACCEPTED" for item in decisions)!=score["accepted_trades"]:
                failures.append("PORTFOLIO_ACCEPT_COUNT:"+label)
            if sum(item["status"]=="REJECTED" for item in decisions)!=score["rejected_trades"]:
                failures.append("PORTFOLIO_REJECT_COUNT:"+label)
            net=round(sum(finite(item["net_pnl_usd"]) for item in accepted),6)
            if not close_enough(net,score["net_pnl_usd"]) or not close_enough(initial+net,score["ending_equity_usd"]):
                failures.append("PORTFOLIO_EQUITY:"+label)
            ordered=sorted(accepted,key=lambda item:instant(item["entry_time"]))
            for prior,current in zip(ordered,ordered[1:]):
                if instant(current["entry_time"]) < instant(prior["exit_time"]):
                    failures.append("PORTFOLIO_OVERLAP:"+label)
            for item in accepted:
                if item["root"] in {"MES","ES","NQ"} or int(item["direction"])!=1:
                    failures.append("PORTFOLIO_UNVERIFIED_MARGIN_ACCEPTED:"+label)


def main():
    pointer=read_json(ROOT/"state/backtests/latest.json")
    result=read_json(pointer["json"])
    failures=[]
    capture_path=Path(result["capture_path"]).resolve()
    capture=read_json(capture_path)
    if ROOT.resolve() not in capture_path.parents or digest(capture) != result["capture_sha256"]:
        failures.append("CAPTURE_HASH_OR_PATH_MISMATCH")
    for name,expected in result["engine_source_hashes"].items():
        path=ROOT/(name if name=="config.toml" else "trading_hub/"+name)
        if not path.exists() or file_hash(path)!=expected:
            failures.append("ENGINE_SOURCE_MISMATCH:"+name)
    sessions_by_root=raw_sessions(capture)
    reconstructed=0
    for root,variants in result["ledger"].items():
        for variant,costs in variants.items():
            for cost,sessions in costs.items():
                all_trades=[]; daily={}
                for session in sessions:
                    chain="0"*64; open_position=False
                    label=f"{root}:{variant}:{cost}:{session['date']}"
                    bars=sessions_by_root.get(root,{}).get(session["date"],[])
                    if len(bars)!=78:
                        failures.append("RAW_SESSION_MAPPING:"+label)
                    for sequence,event in enumerate(session["events"]):
                        body={key:value for key,value in event.items() if key!="sha256"}
                        if event["sequence"]!=sequence or event["previous_sha256"]!=chain or digest(body)!=event["sha256"]:
                            failures.append("EVENT_CHAIN:"+label); break
                        chain=event["sha256"]
                        if not (0 <= int(event["bar_index"]) < 78): failures.append("EVENT_INDEX:"+label)
                        if event["kind"]=="SIMULATED_ENTRY":
                            if open_position or event["signal_index"]>=event["bar_index"]: failures.append("CAUSAL_OR_OVERLAP:"+label)
                            open_position=True
                        elif event["kind"]=="SIMULATED_EXIT":
                            if not open_position: failures.append("EXIT_WITHOUT_ENTRY:"+label)
                            open_position=False
                    if chain!=session["chain_head"] or open_position: failures.append("CHAIN_HEAD_OR_OPEN_POSITION:"+label)
                    for index,trade in enumerate(session["trades"]):
                        verify_fill(root,session,trade,bars,result["preset"],failures,label+f":{index}")
                        reconstructed+=1
                        if finite(trade["planned_total_risk_usd"])>finite(result["preset"]["risk_per_trade_usd"]):
                            failures.append("PRESET_RISK_EXCEEDED:"+label)
                    all_trades.extend(session["trades"])
                    daily[session["date"]]=round(sum(finite(t["net_pnl_usd"]) for t in session["trades"]),6)
                score=result["matrix"][root][variant][cost]
                if score["trade_count"]!=len(all_trades) or not close_enough(sum(finite(t["net_pnl_usd"]) for t in all_trades),score["net_pnl_usd"]) or daily!=score["daily_net_pnl_usd"]:
                    failures.append(f"SCORE_MISMATCH:{root}:{variant}:{cost}")
    verify_portfolios(result,failures)
    audit={"schema":"trading-hub.backtest-audit.v2","run_id":result["run_id"],
           "status":"PASS_RECOMPUTED_FILLS" if not failures else "FAIL","failures":sorted(set(failures)),
           "checked_capture_sha256":result["capture_sha256"],"recomputed_trade_fills":reconstructed,
           "fills_recomputed_from_raw_capture":True,"order_evidence_reviewed":False,
           "independent_market_data_reviewed":False,"kx_parity_reviewed":False,"profitability_established":False}
    save_json(Path(pointer["json"]).parent/"audit.json",audit)
    print(audit["status"])
    if failures: print("\n".join(audit["failures"]))
    return 0 if not failures else 1


if __name__=="__main__":
    raise SystemExit(main())
