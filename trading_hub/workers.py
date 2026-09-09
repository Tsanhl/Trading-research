"""Two bounded, restartable research workers. Nothing schedules itself."""
from __future__ import annotations
from contextlib import closing

from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree as ET

from .common import config, digest, instant, now_iso, save_json
from .research import AGENT, PlainText, crawl_edgerunner
from .storage import DEFAULT_DB, HubStore

FED_FEED = "https://www.federalreserve.gov/feeds/press_monetary.xml"


def _iso(value, milliseconds=False):
    if value is None or value == "":
        return None
    if milliseconds:
        return datetime.fromtimestamp(float(value) / 1000, timezone.utc).isoformat()
    try:
        instant(value)
        return str(value)
    except (ValueError, TypeError):
        try:
            parsed = parsedate_to_datetime(str(value))
            return parsed.isoformat() if parsed.tzinfo else None
        except (ValueError, TypeError):
            return None


def _pin(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _jsonl(path):
    with path.open() as handle:
        for line in handle:
            if line.strip():
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError("Invalid source record")
                yield item


def _evidence_known_at(post, evidence, coverage):
    """Later reviews must never appear available at the original post capture."""
    timestamps = [_iso(post.get("source", {}).get("first_captured_at"))]
    for rows in evidence.values():
        for item in rows:
            for key in ("verified_at", "retrieved_at", "captured_at", "reviewed_at"):
                timestamps.append(_iso(item.get(key)))
            timestamps.append(_iso(item.get("review", {}).get("reviewed_at")))
    if evidence.get("click_posts") or evidence.get("media_clicks"):
        timestamps.append(_iso(coverage.get("click_checkpoint", {}).get("updated_at")))
    available = [value for value in timestamps if value]
    return max(available, key=instant) if available else None


def _edge_documents(root):
    path = root / "agent/state/edgerunner-corpus.sqlite3"
    if not path.exists():
        raise FileNotFoundError("Legacy corpus missing")
    with closing(sqlite3.connect("file:" + quote(str(path)) + "?mode=ro", uri=True)) as connection:
        rows = connection.execute("SELECT document_id,source,canonical_url,title,published_at_ms,fetched_at_ms,audience,completeness,content_sha256,text FROM documents ORDER BY document_id").fetchall()
    # The logical row-set hash includes committed WAL-visible content. It is not
    # mislabelled as the hash of the physical database file.
    logical_sha = digest(rows)
    ref = {"path": str(path), "sha256": logical_sha, "hash_scope": "selected_committed_rows"}
    documents = []
    for identifier, source, url, title, published, fetched, audience, completeness, claimed, body in rows:
        documents.append({"source": "edgerunner_legacy", "source_id": str(identifier), "url": url,
                          "title": title, "text": body, "published_at": _iso(published, True),
                          "known_at": _iso(fetched, True), "coverage": "LEGACY_ARTICLE_NOT_INDEPENDENTLY_VERIFIED",
                          "metadata": {"legacy_source": source, "audience": audience, "legacy_completeness_claim": completeness,
                                       "legacy_hash_matches": hashlib.sha256(body.encode()).hexdigest() == claimed,
                                       "full_article_independently_verified": False}, "artifact_refs": [ref]})
    return documents, {"legacy_rows_sha256": logical_sha, "legacy_documents": len(rows)}


def _structure_documents(root):
    base = root / "research/kevinx"
    posts_path = base / "corpus.current.jsonl"
    if not posts_path.exists():
        raise FileNotFoundError("KevinX corpus missing")
    sidecars = {
        "media_manifest": base / "media.current.jsonl",
        "media_archive": base / "media/archive.jsonl",
        "relationship_context": base / "media/relationship-context.payloads.jsonl",
        "click_posts": base / "click-audit/posts.jsonl",
        "media_clicks": base / "click-audit/media-clicks.jsonl",
    }
    refs = [_pin(posts_path)]
    related = defaultdict(lambda: defaultdict(list))
    missing = []
    for label, path in sidecars.items():
        if not path.exists():
            missing.append(label)
            continue
        refs.append(_pin(path))
        for row in _jsonl(path):
            identifier = row.get("status_id") or row.get("source", {}).get("status_id")
            if identifier:
                related[str(identifier)][label].append(row)
    coverage = {}
    for label, path in {"coverage_manifest": base / "coverage.manifest.json", "click_checkpoint": base / "click-audit/checkpoint.json"}.items():
        if path.exists():
            coverage[label] = json.loads(path.read_text())
            refs.append(_pin(path))
        else:
            missing.append(label)
    documents = []
    for row in _jsonl(posts_path):
        identifier = str(row["status_id"])
        metadata = dict(row)
        metadata.pop("text", None)
        metadata["supplemental_evidence"] = dict(related.get(identifier, {}))
        metadata["full_history_verified"] = False
        metadata["missing_sidecars"] = missing
        documents.append({"source": "kevinx", "source_id": identifier, "url": row.get("url"),
                          "title": "KevinX " + identifier, "text": row.get("text", ""),
                          "published_at": _iso(row.get("created_at")),
                          "known_at": _evidence_known_at(row, related.get(identifier, {}), coverage),
                          "coverage": "BOUNDED_WITH_UNRESOLVED_CONTEXT_AND_MEDIA", "metadata": metadata,
                          "artifact_refs": refs})
    # Keep newer click-audit posts which are not yet in the normalized corpus.
    existing = {doc["source_id"] for doc in documents}
    for identifier, evidence in related.items():
        if identifier in existing or not evidence.get("click_posts"):
            continue
        row = evidence["click_posts"][-1]
        documents.append({"source": "kevinx", "source_id": identifier, "url": row.get("url"),
                          "title": "KevinX " + identifier, "text": row.get("text", ""),
                          "published_at": _iso(row.get("created_at")), "known_at": None,
                          "coverage": "CLICK_AUDIT_ONLY_KNOWN_AT_UNRESOLVED",
                          "metadata": {"supplemental_evidence": dict(evidence), "full_history_verified": False}, "artifact_refs": refs})
    # Coverage is its own immutable document, preserving access boundaries and
    # contradictory legacy completion claims rather than silently resolving them.
    documents.append({"source": "kevinx_coverage", "source_id": "coverage", "text": "",
                      "published_at": None, "known_at": _iso(coverage.get("click_checkpoint", {}).get("updated_at")),
                      "coverage": "INCOMPLETE", "metadata": coverage, "artifact_refs": refs})
    checkpoint = {"source_pins": refs, "corpus_documents": len(documents) - 1,
                  "coverage_complete": False, "missing_sidecars": missing,
                  "window_states": dict(Counter(w.get("state", "UNKNOWN") for w in coverage.get("click_checkpoint", {}).get("windows", [])))}
    return documents, checkpoint


def fetch_fed_feed():
    """Official feed listed at federalreserve.gov/feeds/feeds.htm; HTTPS, no redirects."""
    result = subprocess.run(["/usr/bin/curl", "--silent", "--show-error", "--max-time", "15", "--max-filesize", "2000000",
                             "--proto", "=https", "--max-redirs", "0", "--user-agent", AGENT,
                             "--write-out", "\n%{http_code}", FED_FEED], capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError("Official feed fetch failed")
    raw, status = result.stdout.rsplit(b"\n", 1)
    if status != b"200" or len(raw) > 2_000_000:
        raise ValueError("Official feed response refused")
    return raw


def _fed_capture(fetcher=fetch_fed_feed):
    raw = fetcher()
    known = now_iso()
    root = ET.fromstring(raw)
    documents = []
    for item in root.findall("./channel/item")[:50]:
        url = item.findtext("link", "")
        parsed = urlsplit(url)
        if parsed.hostname != "www.federalreserve.gov" or parsed.scheme != "https":
            continue
        plain = PlainText()
        plain.feed(item.findtext("description", ""))
        documents.append({"source": "federal_reserve_monetary", "source_id": url, "url": url,
                          "title": item.findtext("title", ""), "text": "".join(plain.parts).strip(),
                          "published_at": _iso(item.findtext("pubDate")), "known_at": known,
                          "coverage": "OFFICIAL_FEED_SUMMARY_ONLY"})
    return {"source": FED_FEED, "fetched_at": known, "response_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_xml": raw.decode("utf-8"), "documents": documents, "status": "PUBLIC_FEED_CAPTURED"}


def run_worker(stream, db_path=DEFAULT_DB, refresh=False, *, source_root=None):
    """Bounded pass. Imports resume idempotently after interruption; refresh adds RSS.

    Returns counters and source status only. Does not expose source bodies or run
    a scheduler. Structure refresh requires new local browser/API exports.
    """
    HubStore._stream(stream)
    root = Path(source_root) if source_root else Path(config()["sources"]["kx"])
    with HubStore(db_path) as store:
        run_id = store.start_run(stream)
        result = {"stream": stream, "run_id": run_id, "status": "COMPLETE", "coverage_complete": False,
                  "counts": {"new_documents": 0, "new_revisions": 0, "unchanged": 0}, "sources": [], "errors": []}
        previous = store.checkpoint(stream) or {}
        try:
            documents, checkpoint = _edge_documents(root) if stream == "macro" else _structure_documents(root)
            checkpoint["input_fingerprint"] = digest(checkpoint)
            checkpoint["last_success_at"] = now_iso()
            if previous.get("input_fingerprint") == checkpoint["input_fingerprint"]:
                result["local_status"] = "UNCHANGED_RESUMED"
                result["counts"]["unchanged"] += len(documents)
            else:
                counts = store.ingest_documents(stream, documents, checkpoint=checkpoint, run_id=run_id)
                for key, value in counts.items():
                    result["counts"][key] += value
                result["local_status"] = "IMPORTED"
            result["sources"].append({"source": "legacy_corpus" if stream == "macro" else "kevinx_exports", "documents": len(documents), "coverage_complete": False})
        except Exception as error:
            result["status"] = "PARTIAL"
            result["errors"].append({"source": "local_import", "code": type(error).__name__})
        if refresh and stream == "macro":
            for source, capture_fn in (("edgerunner_rss", crawl_edgerunner), ("federal_reserve_monetary", _fed_capture)):
                try:
                    capture = capture_fn()
                    if capture.get("status") != "PUBLIC_FEED_CAPTURED":
                        raise RuntimeError("Feed capture incomplete")
                    path = Path(db_path).parent / "raw/research" / source / (digest(capture) + ".json")
                    if not path.exists():
                        save_json(path, capture)
                        path.chmod(0o600)
                    ref = _pin(path)
                    docs = []
                    for row in capture["documents"]:
                        doc = dict(row)
                        doc.update(source=source, source_id=doc["url"], artifact_refs=[ref])
                        doc["published_at"] = _iso(doc.get("published_at"))
                        docs.append(doc)
                    counts = store.ingest_documents(stream, docs)
                    for key, value in counts.items():
                        result["counts"][key] += value
                    result["sources"].append({"source": source, "documents": len(docs), "status": "CAPTURED_SUMMARIES"})
                except Exception as error:
                    result["status"] = "PARTIAL"
                    result["errors"].append({"source": source, "code": type(error).__name__})
        elif refresh and stream == "structure":
            result["refresh_status"] = "LOCAL_EXPORT_REFRESH_ONLY_BROWSER_BACKLOG_REMAINS"
        result["checkpoint"] = store.checkpoint(stream)
        result["database_counts"] = store.stats()
        store.finish_run(run_id, result)
        return result
