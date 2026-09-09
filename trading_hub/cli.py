from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .common import ROOT, now_iso, read_json, save_json


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private trading research with confirmed Webull sandbox paper execution; production disabled.")
    commands = parser.add_subparsers(dest="command",required=True)
    for name in ("status","report","monitor-once","research-refresh","webull-probe","webull-spx-probe","webull-spx-export","webull-account-probe","webull-paper-worker","capture-backtest-data","capture-extended-data","backtest"):
        commands.add_parser(name)
    analyze = commands.add_parser("analyze")
    analyze.add_argument("dataset",type=Path)
    analyze.add_argument("--as-of",required=True,help="Explicit timezone-aware point-in-time cutoff")
    replay = commands.add_parser("replay")
    replay.add_argument("dataset",type=Path)
    replay.add_argument("--side",choices=["LONG","SHORT"],default="LONG")
    replay.add_argument("--window",type=int,default=30)
    replay.add_argument("--slip-ticks",type=int,default=1)
    replay.add_argument("--fee-per-side",type=float,required=True,help="Explicit simulated fee in USD, not broker margin")
    replay.add_argument("--quantity",type=int,default=1)
    compare = commands.add_parser("verify-quotes")
    compare.add_argument("pair",type=Path,help="JSON with left and right normalized quote objects")
    compare.add_argument("--as-of",required=True)
    commands.add_parser("init")
    commands.add_parser("hub-status")
    worker = commands.add_parser("worker")
    worker.add_argument("stream", choices=["macro", "structure", "all"])
    worker.add_argument("--refresh", action="store_true", help="Bounded public-feed refresh; structure reads local exports")
    intake = commands.add_parser("data-ingest")
    intake.add_argument("--as-of")
    commands.add_parser("data-list")
    commands.add_parser("register-legacy-data")
    data_compare = commands.add_parser("data-compare")
    data_compare.add_argument("left", type=Path)
    data_compare.add_argument("right", type=Path)
    records = commands.add_parser("record-import")
    records.add_argument("kind", choices=["decision", "position", "quote"])
    records.add_argument("file", type=Path)
    records.add_argument("--as-of")
    journal = commands.add_parser("journal")
    journal.add_argument("kind", choices=["decision", "position", "quote", "alert", "report", "dataset", "cycle"])
    journal.add_argument("--limit", type=int, default=20)
    observations = commands.add_parser("observe-positions")
    observations.add_argument("--as-of")
    close = commands.add_parser("position-close")
    close.add_argument("position_id", help="Stop LOCAL monitoring only; does not close a broker position")
    alert = commands.add_parser("alerts")
    alert.add_argument("--acknowledge", help="Acknowledge local event ID; does not claim external delivery")
    brief = commands.add_parser("brief")
    brief.add_argument("cadence", choices=["daily", "weekly"])
    brief.add_argument("--as-of")
    brief.add_argument("--force", action="store_true", help="Append another report revision without overwriting")
    cycle = commands.add_parser("cycle")
    cycle.add_argument("--refresh", action="store_true", help="Run research workers even if this hour already ran")
    args = parser.parse_args(argv)
    if args.command in {"init", "hub-status"}:
        from .workspace import initialize, latest_positions
        from .storage import HubStore
        result = initialize()
        with HubStore() as store:
            result["open_local_positions"] = len(latest_positions(store))
            result["streams"] = {stream: store.checkpoint(stream) for stream in ("macro", "structure")}
        result["unverified"] = ["KX_RELEASE", "INDEPENDENT_MARKET_DATA", "LIVE_QUOTE_PROVIDER",
                                "PAPER_BROKER_RECONCILIATION", "EXTERNAL_ALERT_DELIVERY"]
    elif args.command == "worker":
        from .workers import run_worker
        result = [run_worker(stream, refresh=args.refresh) for stream in
                  (("macro", "structure") if args.stream == "all" else (args.stream,))]
    elif args.command == "data-ingest":
        from .workspace import ingest_data
        result = ingest_data(as_of=args.as_of)
    elif args.command == "data-list":
        from .data_intake import inventory
        result = inventory()
    elif args.command == "register-legacy-data":
        from .legacy_data import register_legacy_data
        result = register_legacy_data()
    elif args.command == "data-compare":
        from .data_intake import compare_datasets
        result = compare_datasets(args.left, args.right)
    elif args.command == "record-import":
        from .workspace import import_record, load_record_file
        result = import_record(args.kind, load_record_file(args.file), as_of=args.as_of)
    elif args.command == "journal":
        from .storage import HubStore
        with HubStore() as store:
            result = store.list_records(args.kind, limit=args.limit)
    elif args.command == "observe-positions":
        from .workspace import observe_positions
        result = observe_positions(as_of=args.as_of)
    elif args.command == "position-close":
        from .workspace import close_position
        result = close_position(args.position_id)
    elif args.command == "alerts":
        from .workspace import alerts
        result = alerts(acknowledge=args.acknowledge)
    elif args.command == "brief":
        from .briefing import build_brief
        result = build_brief(args.cadence, as_of=args.as_of, force=args.force)
    elif args.command == "cycle":
        from .workspace import run_cycle
        result = run_cycle(refresh=args.refresh)
    elif args.command == "status":
        from .sources import snapshot
        data = snapshot()
        result = {k:data[k] for k in ("mode","decision","kx_gate","freshness","kevin_window_counts","options","futures")}
        result["edgerunner_documents"] = data["edgerunner"].get("document_count",0)
    elif args.command == "report":
        from .reporting import build_report
        result = build_report()
    elif args.command == "monitor-once":
        from .sources import snapshot
        data = snapshot()
        result = {"checked_at":data["generated_at"],"action":data["decision"]["action"],"background_running":False,
                  "freshness":data["freshness"],"legacy_watchlist_count":len(data["legacy_monitor"].get("setups",[])),
                  "notifications_sent":0,"orders_sent":0,"reason":data["decision"]["reason"]}
        save_json(ROOT/"state/monitor/latest.json",result)
    elif args.command == "research-refresh":
        from .research import crawl_edgerunner,save_crawl
        data = crawl_edgerunner()
        path = save_crawl(data)
        result = {k:v for k,v in data.items() if k != "documents"} | {"document_count":len(data["documents"]),"artifact":str(path)}
    elif args.command == "webull-probe":
        from .webull_probe import probe
        result = probe()
        save_json(ROOT/"state/webull-probe.json",result)
        result = {k:v for k,v in result.items() if k != "products"} | {"products":{
            root:{"status":row["status"],"http_status":row.get("http_status"),"contract_count":len(row.get("records",[])),
                  "market_data_checks":{key:({k:v for k,v in value.items() if k != "rows"} if isinstance(value,dict) else value)
                                        for key,value in row.get("market_data_checks",{}).items()}}
            for root,row in result["products"].items()}}
    elif args.command == "webull-account-probe":
        from .webull_probe import account_probe
        result = account_probe()
    elif args.command == "webull-spx-probe":
        from .webull_probe import spx_option_probe
        result = spx_option_probe()
        save_json(ROOT/"state/webull-spx-probe.json", result)
    elif args.command == "webull-spx-export":
        from .webull_spx_export import export_sandbox_spx
        result = export_sandbox_spx(ROOT)
    elif args.command == "webull-paper-worker":
        from .webull_paper import provider_action
        raw = sys.stdin.buffer.read(32_769)
        if len(raw) > 32_768:
            raise ValueError("Paper worker payload exceeds 32 KB")
        result = provider_action(json.loads(raw))
    elif args.command == "capture-backtest-data":
        from .backtest_data import capture_history
        result = capture_history()
    elif args.command == "capture-extended-data":
        from .historical_data import capture_extended_history
        result = capture_extended_history()
    elif args.command == "backtest":
        from .backtest_report import build_backtest
        result = build_backtest()
    elif args.command == "analyze":
        from .paper import validate_dataset
        from .structure import features
        data = read_json(args.dataset)
        validate_dataset(data)
        result = features(data["bars"],as_of=args.as_of)
    elif args.command == "verify-quotes":
        from .verification import compare_quotes
        data = read_json(args.pair)
        result = compare_quotes(data["left"],data["right"],now=args.as_of)
    else:
        from .paper import simulate,record_run,verify_chain
        data = read_json(args.dataset)
        outputs = {}
        for retest,label in ((False,"breakout_baseline"),(True,"later_retest_candidate")):
            run = simulate(data,side=args.side,window=args.window,retest=retest,slip_ticks=args.slip_ticks,
                           fee_per_side=args.fee_per_side,quantity=args.quantity)
            if not verify_chain(run):
                raise ValueError("Internal checkpoint-chain failure")
            record_run(ROOT/"state/local-paper.sqlite3",run)
            path = ROOT/"state/replays"/(run["run_id"]+".json")
            save_json(path,run)
            outputs[label] = {"path":str(path),"score":run["score"],"chain_head":run["chain_head"]}
        result = {"mode":"LOCAL_SIMULATION_NOT_BROKER_PAPER", "dataset_environment":data["metadata"]["environment"],
                  "kx_validated":False, "outputs":outputs}
    print(json.dumps(result,indent=2,allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
