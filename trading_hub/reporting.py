from __future__ import annotations

from pathlib import Path

from .common import ROOT, digest, save_json
from .sources import freeze_inputs, snapshot


def clean(value):
    # Imported prose remains inert Markdown text, not raw HTML/directives.
    return str(value).replace("\n"," ").replace("|","/").replace("<","&lt;").replace(">","&gt;")


def render(result):
    macro = result["macro"]
    gate = result["kx_gate"]
    lines = ["# Trading Research Hub — combined report", "",
             f"Generated: {result['generated_at']}", "",
             "**RESEARCH ONLY · NO TRADE AUTHORITY · NO BROKER ORDERS · NO CONTINUOUS LIVE QUOTE FEED**", "",
             "## Decision", "", f"{result['decision']['action']}: {result['decision']['reason']}.", "",
             "This is a combined view of retained source artifacts, not a fresh market-wide scan. Cached technical levels and source opinions below are not verified trade recommendations.", "",
             "## News and macro context", "",
             f"Source report time: {clean(macro.get('generated_at','unknown'))}. Artifact status: {result['freshness']['macro_artifact']}.", "",
             f"Imported regime: {clean(macro.get('regime','unknown'))}; source confidence: {clean(macro.get('confidence','unknown'))}. Neither is an entry signal.", ""]
    summary = macro.get("market_summary",[])
    if isinstance(summary,str):
        summary = [summary]
    lines += ["- "+clean(s) for s in summary]
    lines += ["", "### Cross-asset context", ""]
    notes = macro.get("cross_asset_notes",[])
    lines += ["- "+clean(s) for s in (notes if isinstance(notes,list) else [notes])]
    lines += ["", "### Recent source headlines (retained, not freshly fetched)", ""]
    for item in macro.get("news",[])[:8]:
        url = item.get("link","")
        title = clean(item.get("title",""))
        lines.append(f"- {title} — {clean(item.get('published','unknown'))}. Source: {url if url.startswith('https://') else 'unavailable'}")
    lines += ["", "## Intraday monitor", "",
              f"Imported monitor: {result['freshness']['monitor_artifact']}; environment: {clean(result['legacy_monitor'].get('environment','unknown'))}.", "",
              "The old monitor's lifecycle is comparison evidence only. It cannot trigger KX or paper orders. Run `python3 -m trading_hub monitor-once` to refresh this legacy snapshot; the separate hub cycle and local position journal are described in docs/HUB_OPERATING_GUIDE.md.", "",
              "| Symbol | Legacy state | Structural hypothesis | Hub status |", "|---|---|---|---|"]
    for row in result["legacy_monitor"].get("setups",[]):
        lines.append(f"| {clean(row.get('symbol'))} | {clean(row.get('lifecycle'))} | {clean(row.get('pattern'))} | WATCHLIST ONLY |")
    lines += ["", "## Swing research", "", "Daily context below is separate from intraday timing. Weekly bars and actual open positions have not been connected to this hub.", "",
              "| Symbol | Source bar timestamp | Pattern | EMA20 | Source |", "|---|---|---|---|---|"]
    for row in macro.get("technicals",[]):
        lines.append(f"| {clean(row.get('symbol'))} | {clean(row.get('as_of'))} | {clean(row.get('pattern'))} | {clean(row.get('ema20'))} | {clean(row.get('source_url'))} |")
    lines += ["", "## Options and futures", "",
              f"Options examined: {result['options']['contract_rows']}; qualifying research candidates: {result['options']['eligible_research_candidates']}.", "",
              "Legacy option rows lack independently verified OPRA entitlement and contract-deliverable provenance. No specific option is promoted into a trade. Screening filters are hypotheses, not personalized risk limits.", ""]
    lines += [f"- {reason}: {count}" for reason,count in result["options"]["blocker_counts"].items()]
    lines += ["", "| Futures root | USD per index point | Outright tick / USD | State |", "|---|---:|---:|---|",
              "| MES | 5 | 0.25 / 1.25 | Research / local replay only |", "| ES | 50 | 0.25 / 12.50 | Research / local replay only |",
              "| NQ | 20 | 0.25 / 5 | Research / local replay only |", "",
              "These are product economics, not resolved tradable expiries or margin requirements. Continuous charts and cash indices cannot substitute for contract-specific fills.", ""]
    probe = result.get("webull_probe",{})
    lines += [f"Latest Webull sandbox metadata probe: {clean(probe.get('status','NOT_RUN'))}.", ""]
    for root,row in probe.get("products",{}).items():
        lines.append(f"- {root}: {clean(row.get('status'))}; HTTP {clean(row.get('http_status','unknown'))}; returned instruments: {len(row.get('records',[]))}. Order support remains unverified.")
        checks = row.get("market_data_checks",{})
        if checks:
            lines.append(f"  Probe contract: {clean(checks.get('instrument'))}; snapshot: {clean(checks.get('snapshot',{}).get('status'))}; M5 bars: {clean(checks.get('M5_bars',{}).get('status'))}. These are sandbox observations, not independent verification or live entitlement proof.")
    lines += ["", "## Research corpus", "",
              f"EdgeRunner: {result['edgerunner'].get('document_count',0)} indexed documents; {result['edgerunner'].get('hash_mismatch_count',0)} content-hash mismatches. Indexing is not proof that each article is complete or its thesis is valid.", "",
              f"KevinX window states: {clean(result['kevin_window_counts'])}.", "",
              "Kevin's inaccessible windows, unresolved quote context and media hashes remain open. Public RSS refresh captures only the returned feed, not three years of X history.", "",
              f"Latest public crawl: {clean(result.get('public_crawl',{}).get('status','NOT_RUN'))}; feed items: {result.get('public_crawl',{}).get('document_count',0)}.", "",
              "## Indicator and release verification", "",
              f"KX candidate: {clean(gate['release_status'])}; actual Pine hash matches candidate: {gate['candidate_hash_matches']}; registered compile evidence: {gate['compiled_evidence_registered']}.", ""]
    lines += ["- "+clean(reason) for reason in gate["reasons"]]
    lines += ["", "## Paper evaluation", "",
              "The hub can run deterministic local OHLC replay with next-bar entries, frozen breakout/retest levels, adverse costs, ambiguity marking and a hash-chained log. This is a new research experiment, not Pine parity testing, an independent ledger, a Webull broker fill, or evidence of a profitable edge.", "",
              "Provisional local-backtest preset: US$100,000 simulated balance, US$250 risk budget per trade, US$1,000 daily loss threshold, one position per instrument test and no overnight holding. These are research defaults, not approved broker sizing.", "",
              "Still unconfigured: portfolio-wide correlated exposure limits, broker margin checks, verified option quotes, external delivery and broker execution.", "",
              f"Latest development backtest: {clean(result.get('backtest',{}).get('run_id','NOT_RUN'))}; sandbox data: {clean(result.get('backtest',{}).get('data_environment','unknown'))}; KX validation: {clean(result.get('backtest',{}).get('kx_validation',False))}; profitability established: {clean(result.get('backtest',{}).get('profitability_established',False))}.", ""]
    if result.get("backtest"):
        lines += ["| Instrument | Variant | Trades | Net P&L (base costs) |", "|---|---|---:|---:|"]
        for root,variants in result["backtest"]["matrix"].items():
            for variant,costs in variants.items():
                score=costs["base"]
                lines.append(f"| {clean(root)} | {clean(variant)} | {score['trade_count']} | ${score['net_pnl_usd']:,.2f} |")
        lines += ["",f"Full development report: {clean(result['backtest']['report'])}.",""]
    lines += [
              "## Provenance", "", f"Integration source pins: {result.get('source_pin_path','not saved')}.", "",
              "Original projects are read-only inputs. No credentials are embedded. Current source changes require new evidence; old results cannot silently certify new code.", ""]
    return "\n".join(lines)


def build_report():
    result = snapshot()
    result["source_pin_path"] = str(freeze_inputs(result))
    run_dir = ROOT/"reports"/digest(result)[:16]
    run_dir.mkdir(parents=True,exist_ok=True)
    save_json(run_dir/"report.json",result)
    (run_dir/"report.md").write_text(render(result))
    save_json(ROOT/"state/latest.json",{ "generated_at":result["generated_at"], "report_json":str(run_dir/"report.json"), "report_markdown":str(run_dir/"report.md")})
    return {"markdown":str(run_dir/"report.md"),"json":str(run_dir/"report.json"),"action":result["decision"]["action"]}
