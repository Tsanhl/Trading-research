"""Allowlisted, read-only legacy inputs. Source text is data, never instructions."""
from __future__ import annotations
from contextlib import closing

from collections import Counter
import csv
import hashlib
from pathlib import Path
import sqlite3
from urllib.parse import quote

from .common import ROOT, age_status, config, digest, now_iso, read_json, save_json
from .options import screen_option


def read_pinned(path):
    import json
    content = Path(path).read_bytes()
    return json.loads(content), {"path": str(path), "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}


def corpus_inventory(kx):
    database = Path(kx)/"agent/state/edgerunner-corpus.sqlite3"
    if not database.exists():
        return {"status": "MISSING", "documents": []}
    with closing(sqlite3.connect("file:"+quote(str(database))+"?mode=ro", uri=True)) as connection:
        rows = connection.execute("SELECT document_id,source,canonical_url,title,published_at_ms,fetched_at_ms,audience,completeness,content_sha256,text FROM documents ORDER BY document_id").fetchall()
    records = []
    for identifier,source,url,title,published,fetched,audience,complete,claimed,body in rows:
        actual = hashlib.sha256(body.encode()).hexdigest()
        records.append({"document_id": identifier, "source": source, "url": url, "title": title,
                        "published_at_ms": published, "known_at_ms": fetched, "audience": audience,
                        "legacy_completeness_claim": complete, "content_sha256": actual,
                        "legacy_hash_matches": actual == claimed, "full_article_independently_verified": False})
    return {"status": "INDEXED_NOT_FULLY_VERIFIED", "database_path": str(database),
            "document_count": len(records), "by_source": dict(Counter(r["source"] for r in records)),
            "hash_mismatch_count": sum(not r["legacy_hash_matches"] for r in records),
            "index_sha256": digest(records), "documents": records,
            "note": "Metadata-only index; paid bodies remain in the original private corpus. FULL is a legacy claim, not a new audit."}


def snapshot(*, now=None):
    cfg = config()
    kx, news = Path(cfg["sources"]["kx"]), Path(cfg["sources"]["news"])
    now = now or now_iso()
    output = {"schema": "trading-hub.snapshot.v1", "generated_at": now, "mode": "RESEARCH_ONLY",
              "broker_execution": False, "background_monitor_running": False, "pins": {}, "errors": {}}
    names = {"macro": news/"state/latest.json", "legacy_monitor": news/"state/monitor/latest.json",
             "kx_candidate": kx/"audit/release-candidate.json", "kevin_coverage": kx/"research/kevinx/coverage.manifest.json",
             "kevin_checkpoint": kx/"research/kevinx/click-audit/checkpoint.json"}
    for name,path in names.items():
        try:
            output[name],output["pins"][name] = read_pinned(path)
        except (OSError,ValueError):
            output[name] = {}
            output["errors"][name] = "MISSING_OR_MALFORMED"
    macro = output["macro"]
    monitor = output["legacy_monitor"]
    output["freshness"] = {
        "macro_artifact": age_status(macro.get("generated_at"),now,cfg["policy"]["macro_max_age_hours"]*3600),
        "monitor_artifact": age_status(monitor.get("generated_at"),now,cfg["policy"]["monitor_max_age_seconds"]),
        "note": "Artifact age is not quote age. Imported market values retain their own timestamps and have not been independently corroborated."}
    candidate = output["kx_candidate"]
    pine = kx/"KX_Structure_Trade_Planner_v12_STRUCTURE_FIRST.pine"
    actual_hash = hashlib.sha256(pine.read_bytes()).hexdigest() if pine.exists() else None
    output["kx_gate"] = {"release_status": candidate.get("status","MISSING"),
                         "actual_source_sha256": actual_hash,
                         "candidate_hash_matches": bool(actual_hash and actual_hash == candidate.get("source_sha256")),
                         "compiled_evidence_registered": candidate.get("compiled") is True,
                         "entry_authority": False,
                         "reasons": list(candidate.get("blockers",[]))+["HUB_SEALED_RELEASE_BINDING_NOT_IMPLEMENTED"]}
    output["kevin_window_counts"] = dict(Counter(w.get("state","UNKNOWN") for w in output["kevin_checkpoint"].get("windows",[])))
    output["edgerunner"] = corpus_inventory(kx)
    option_path = news/"data/options_chain.csv"
    option_rows = []
    if option_path.exists():
        with option_path.open(newline="") as handle:
            option_rows = list(csv.DictReader(handle))
        output["pins"]["option_chain"] = {"path": str(option_path), "sha256": hashlib.sha256(option_path.read_bytes()).hexdigest()}
    # The legacy CSV omits entitlement, deliverable, environment and upstream identity.
    # Do not infer those from the machine's current config or timestamp of the CSV.
    screened = [screen_option(row,{},now=now) for row in option_rows]
    output["options"] = {"contract_rows": len(option_rows), "eligible_research_candidates": sum(r["status"]=="RESEARCH_CANDIDATE" for r in screened),
                         "blocker_counts": dict(Counter(reason for row in screened for reason in row["reasons"])),
                         "entry_authority": False, "policy": "Long-premium research only; no naked short options or assumed standard deliverables"}
    output["futures"] = {root:{"status":"RESEARCH_ONLY", "resolved_contract":None,
                                  "live_entitlement":"UNVERIFIED", "paper_order_support":"UNVERIFIED"} for root in ("MES","ES","NQ")}
    probe = ROOT/"state/webull-probe.json"
    if probe.exists():
        output["webull_probe"] = read_json(probe)
    crawl = ROOT/"state/research/latest-crawl.json"
    if crawl.exists():
        output["public_crawl"] = read_json(crawl)
    backtest_pointer = ROOT/"state/backtests/latest.json"
    if backtest_pointer.exists():
        pointer = read_json(backtest_pointer)
        backtest_path = Path(pointer.get("json","")).resolve()
        allowed = (ROOT/"reports/backtests").resolve()
        if allowed in backtest_path.parents and backtest_path.exists():
            backtest = read_json(backtest_path)
            output["backtest"] = {"run_id":backtest["run_id"],"report":pointer.get("report"),
                                  "data_environment":backtest["data_environment"],"holdout":backtest["holdout"],
                                  "kx_validation":backtest["kx_validation"],"profitability_established":backtest["profitability_established"],
                                  "matrix":backtest["matrix"]}
    output["decision"] = {"action":"NO_TRADE", "reason":"Unsealed structural release; market-data and external execution gates remain unproven",
                          "macro_role":"CONTEXT_OR_VETO_ONLY", "legacy_monitor_role":"COMPARISON_ONLY_NOT_ENTRY_AUTHORITY"}
    return output


def freeze_inputs(result):
    """Content-addressed integration snapshot, explicitly NOT a holdout freeze."""
    value = {"pins":result["pins"], "kx_source_sha256":result["kx_gate"]["actual_source_sha256"],
             "edgerunner_index_sha256":result["edgerunner"].get("index_sha256"), "holdout_membership_frozen":False}
    path = ROOT/"state/source-pins"/(digest(value)+".json")
    if not path.exists():
        save_json(path,value)
    return path
