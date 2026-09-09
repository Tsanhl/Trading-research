"""Point-in-time daily/weekly research briefs from the private journal."""
from __future__ import annotations

from datetime import datetime, timezone
import copy
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import ROOT, digest, instant, now_iso, save_json
from .reporting import clean
from .sources import snapshot
from .storage import HubStore


def _text(value):
    return clean(value).replace("[", "\\[").replace("]", "\\]").replace("`", "'")[:1500]


def render_brief(result):
    source = result["source_snapshot"]
    macro = source.get("macro", {})
    lines = [f"# Trading Research Hub — {result['cadence']} brief", "",
             f"As of {result['as_of']} · HKT report period {result['period']}", "",
             "RESEARCH / ADVISORY ONLY — no broker orders, verified entry recommendations or continuous live feed.", ""]
    current = result.get("session_review") or {}
    lines += ["## What matters now", "",
              _text(current.get("summary", "No current dated market review is available. Event risk remains unknown.")), "",
              f"Reviewed: {_text(current.get('reviewedAt', 'unknown'))}; coverage: {_text(current.get('coverage', 'unknown'))}.", ""]
    for row in current.get("events", []):
        lines.append(f"- {_text(row.get('when'))}: {_text(row.get('title'))} — {_text(row.get('implication'))}")
    for row in current.get("news", []):
        lines.append(f"- {_text(row.get('title'))}: {_text(row.get('summary'))}")
    lines += ["", "### Retained model assessment", "",
             f"Older imported assessment: {_text(macro.get('regime', 'unknown'))}; confidence: {_text(macro.get('confidence', 'unknown'))}.", "",
             f"Original report time: {_text(macro.get('generated_at', 'unknown'))}; artifact age: {_text(source.get('freshness', {}).get('macro_artifact', 'unknown'))}.", "",
             "Publication time, first-known time and source coverage are retained separately. A freshly generated brief does not make old market quotes current.", ""]
    lines += ["Cutoff views use retained source availability claims and are retrospective research, not certified historical holdout snapshots.", ""]
    for name in ("market_summary", "cross_asset_notes"):
        values = macro.get(name, [])
        lines += ["- " + _text(v) for v in (values if isinstance(values, list) else [values])]
    lines += ["", "### Conditional scenario framework", "",
              "These are research hypotheses, not newly observed conditions or trade instructions.", "",
              "| Condition to verify | Possible transmission | Required response |", "|---|---|---|",
              "| Inflation/real yields rise together | Discount-rate pressure may weigh on long-duration equities | Test sector exposure; require confirmed structure before a trade |",
              "| Growth weakens and credit deteriorates | Earnings/risk appetite may weaken despite falling yields | Distinguish growth shock from benign disinflation |",
              "| Oil shock with firmer USD | Inflation and financial-condition pressure may diverge by sector | Reassess event and overnight gap risk |",
              "| Growth resilient, inflation easing | Risk appetite may improve | Confirm breadth and exact-contract data; no automatic long bias |", "",
              "Official event dates, consensus/actual surprises and earnings calendars are not yet normalized here. Event-risk clearance remains UNKNOWN, not clear.", "",
              "## Newest retained research", "",
              "Full text stays private. Feed summaries and incomplete browser exports are not complete-article evidence.", "",
              "| Stream/source | Title | Published | First known | Coverage |", "|---|---|---|---|---|"]
    for row in result["documents"]:
        lines.append("| " + " | ".join(_text(row.get(k, "unknown")) for k in
                     ("source", "title", "published_at", "known_at", "coverage")) + " |")
    lines += ["", "## Intraday and swing plans", "",
              "Structure defines the setup; incremental PA tests the trigger; macro may veto risk; portfolio rules determine eligibility. These components have not yet demonstrated an out-of-sample combined edge.", ""]
    if not result["decisions"]:
        lines += ["No qualified exact-contract recommendation is available. No buy/sell price, option strike or expiry has been invented.", ""]
    for decision in result["decisions"]:
        rec = decision.get("recommendation", {})
        instrument = rec.get("instrument", {})
        lines += [f"### {_text(rec.get('horizon', 'unknown'))}: {_text(instrument.get('symbol', 'unknown'))}", "",
                  f"{_text(rec.get('side'))} × {_text(rec.get('quantity'))}; entry {_text(rec.get('entry'))}; stop {_text(rec.get('stop'))}; targets {_text(rec.get('targets'))}.", "",
                  f"Contract: {_text(instrument)}. Risk estimate: {_text(decision.get('risk_usd'))} USD.", "",
                  "Current gates: " + _text(", ".join(decision.get("blockers", []))) + ".", ""]
    lines += ["### Legacy watch context", "",
              "Retained technical observations only; not newly confirmed setups.", ""]
    for row in macro.get("technicals", [])[:12]:
        lines.append(f"- {_text(row.get('symbol'))}: {_text(row.get('pattern'))}; EMA20 {_text(row.get('ema20'))}; source bar {_text(row.get('as_of'))}.")
    lines += ["", "## Positions and TP/SL observations", "",
              f"Open user-imported/local positions: {len(result['positions'])}. Journal alerts: {len(result['alerts'])}.", "",
              "Imported quotes are not independently verified live prices. Threshold events are not fills, protective orders or guaranteed execution prices. No external delivery has been proven.", ""]
    for row in result["positions"]:
        p = row["position"]
        lines.append(f"- {_text(p['position_id'])}: {_text(p['instrument'])}; {_text(p['side'])} × {_text(p['quantity'])}; stop {_text(p['stop'])}; targets {_text(p['targets'])}.")
    for row in result["alerts"][:20]:
        lines.append(f"- {_text(row['kind'])}: {_text(row['position_id'])}; observed {_text(row['observed_at'])}; local input only.")
    if result["cadence"] == "weekly":
        lines += ["", "## Weekly review and next-week work", "",
                  "This review uses retained evidence available by the report cutoff. It is not a backdated simulation of what was known in a prior week.", "",
                  "- Reconcile macro scenarios with primary releases; record revisions and misses rather than rewriting earlier theses.",
                  "- Review intraday versus swing outcomes separately, including costs, MAE/MFE, event exposure and unfilled recommendations.",
                  "- Compare structure-only, structure+PA, structure+macro and combined candidates on identical untouched data.",
                  "- Confirm the coming week's official economic/earnings calendar before permitting event-risk clearance.",
                  "- No strategy promotion is supported until holdout, KX parity and independent data gates pass.", ""]
    lines += ["", "## Verification and release gates", "",
              f"KX release: {_text(source.get('kx_gate', {}).get('release_status', 'UNKNOWN'))}; entry authority: false.", ""]
    lines += ["- " + _text(x) for x in source.get("kx_gate", {}).get("reasons", [])]
    lines += ["", "Additional gates: independent SPY/QQQ data, exact-contract futures histories, holdout freeze and twelve formal replays; Webull credential rotation, permissions, margin, protective exits and reconciliation remain unverified.", "",
              "## Evidence links", "", f"Database: {_text(result['database'])}.", ""]
    for row in result["documents"]:
        url = row.get("url", "")
        if isinstance(url, str) and url.startswith("https://"):
            lines.append(f"- {_text(row.get('title', row['source']))}: {_text(url)}")
    return "\n".join(lines) + "\n"


def build_brief(cadence, *, root=ROOT, as_of=None, force=False, source_snapshot=None):
    from .workspace import latest_positions, policy
    from .decisions import evaluate_candidate
    if cadence not in {"daily", "weekly"}:
        raise ValueError("Unknown report cadence")
    as_of = as_of or now_iso()
    now = instant(as_of)
    hkt = datetime.fromtimestamp(now, timezone.utc).astimezone(ZoneInfo("Asia/Hong_Kong"))
    period = hkt.date().isoformat() if cadence == "daily" else f"{hkt.isocalendar().year}-W{hkt.isocalendar().week:02d}"
    key = f"{cadence}:{period}"
    db = Path(root) / "data/hub.sqlite3"
    with HubStore(db) as store:
        previous_reports = [row for row in store.list_records("report", limit=10000)
                            if row.get("cadence") == cadence and row.get("period") == period
                            and instant(row["as_of"]) <= now]
        previous = max(previous_reports, key=lambda row: instant(row["as_of"])) if previous_reports else None
        if previous and not force:
            return {"status": "ALREADY_GENERATED", **previous}
        documents = []
        hidden_sources = {"al_brooks_local_inventory", "hub_source_rule_audit", "kevinx_coverage"}
        # Explicit cutoff excludes documents first fetched after this report.
        for row in store.list_documents(limit=10000, include_text=False, as_of=as_of):
            try:
                if row.get("source") not in hidden_sources and row.get("known_at") and instant(row["known_at"]) <= now:
                    documents.append(row)
            except (ValueError, TypeError):
                continue
        documents.sort(key=lambda row: instant(row.get("published_at") or row["known_at"]), reverse=True)
        selected = []
        for source_name in sorted({row["source"] for row in documents}):
            selected += [row for row in documents if row["source"] == source_name][:3]
        selected = selected[:24]
        decisions = [evaluate_candidate(row.get("recommendation", {}), as_of, policy())
                     for row in store.list_records("decision", limit=50)
                     if instant(row.get("as_of", as_of)) <= now]
        inputs = copy.deepcopy(source_snapshot) if source_snapshot is not None else snapshot(now=as_of)
        if inputs.get("generated_at") and instant(inputs["generated_at"]) > now:
            raise ValueError("Source snapshot is later than the report cutoff")
        for source_key in ("macro", "legacy_monitor"):
            stamp = inputs.get(source_key, {}).get("generated_at")
            if not stamp or instant(stamp) > now:
                inputs[source_key] = {}
                inputs.setdefault("errors", {})[source_key] = "UNAVAILABLE_AT_CUTOFF"
                inputs.setdefault("freshness", {})[source_key + "_artifact"] = "unknown"
        session_review = {}
        session_path = Path(root) / "state/briefings/session-review.json"
        if session_path.is_file():
            try:
                candidate = json.loads(session_path.read_text())
                if candidate.get("reviewedAt") and instant(candidate["reviewedAt"]) <= now:
                    session_review = candidate
            except (OSError, ValueError, TypeError):
                session_review = {}
        result = {"schema": "hub-brief-1", "cadence": cadence, "period": period, "as_of": as_of,
                  "database": str(db), "documents": selected, "decisions": decisions,
                  "positions": latest_positions(store, as_of),
                  "alerts": [row for row in store.list_records("alert", limit=100)
                             if instant(row["observed_at"]) <= now],
                  "source_snapshot": inputs, "session_review": session_review,
                  "broker_authority": False, "content_is_untrusted_data": True}
        identifier = digest(result)[:20]
        folder = Path(root) / "reports/briefings" / identifier
        folder.mkdir(parents=True, exist_ok=True)
        save_json(folder / "brief.json", result)
        (folder / "brief.md").write_text(render_brief(result))
        record = {"cadence": cadence, "period": period, "as_of": as_of,
                  "markdown": str(folder / "brief.md"), "json": str(folder / "brief.json"),
                  "broker_authority": False}
        store.save_record("report", record, key + ":" + identifier)
        save_json(Path(root) / "state/briefings" / (cadence + "-latest.json"), record)
        return {"status": "GENERATED", **record}
