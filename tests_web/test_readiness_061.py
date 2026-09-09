"""Sanitized readiness evidence tests. No providers, credentials or sibling writes."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from trading_hub.connection_health import _kx_readiness, _spx_readiness


class ReadinessEvidence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.kx = self.root / "KX"
        self.news = self.kx / "News+ macro"
        (self.kx / "audit").mkdir(parents=True)
        (self.news / "data").mkdir(parents=True)
        self.source = self.kx / "candidate.pine"
        self.source.write_text("//@version=6\nindicator('fixture')\n")
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def tearDown(self):
        self.temp.cleanup()

    def write_kx(self, *, released=False, source_path="candidate.pine"):
        contract = "a" * 64 if released else None
        candidate = {
            "status": "RELEASED" if released else "BLOCKED",
            "source_path": source_path,
            "source_sha256": self.sha,
            "build_contract_sha256": contract,
            "compiled": released,
            "compile_evidence_refs": ["compile-receipt.json"] if released else [],
            "frozen_at_ms": 1 if released else None,
            "blockers": [] if released else ["SPY_SPX_QQQ_NINE_CONFIGURATION_SMOKE_PENDING"],
        }
        manifest = {"release_state": "RELEASED" if released else "NOT_RELEASED",
                    "acceptance_state": "PASS" if released else "BLOCKED",
                    "artifacts": {"candidate": {"sha256": self.sha,
                                                  "build_contract_sha256": contract,
                                                  "compiled": released}}}
        (self.kx / "audit/release-candidate.json").write_text(json.dumps(candidate))
        (self.kx / "RELEASE_MANIFEST.json").write_text(json.dumps(manifest))

    def test_matching_source_does_not_clear_blocked_release(self):
        self.write_kx()
        result = _kx_readiness(self.kx)
        self.assertTrue(result["sourceHashMatch"])
        self.assertFalse(result["releaseAuthorized"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["blockerCount"], 1)

    def test_all_release_evidence_is_required(self):
        self.write_kx(released=True)
        result = _kx_readiness(self.kx)
        self.assertTrue(result["releaseAuthorized"])
        self.assertEqual(result["status"], "RELEASE_AUTHORIZED")

    def test_candidate_source_may_not_escape_kx_root(self):
        self.write_kx(source_path="../outside.pine")
        result = _kx_readiness(self.kx)
        self.assertEqual(result["status"], "EVIDENCE_INVALID")
        self.assertFalse(result["releaseAuthorized"])

    def test_spy_chain_is_visible_but_rejected_for_spx_gex(self):
        path = self.news / "data/options_chain.csv"
        path.write_text("symbol,option_symbol,expiry,strike,type,open_interest\nSPY,SPY261002C00720000,2026-10-02,720,call,31\n")
        result = _spx_readiness(self.root, self.news)
        self.assertEqual(result["status"], "IMPORT_REQUIRED")
        self.assertEqual(result["localCandidates"][0]["identities"], ["SPY"])
        self.assertEqual(result["localCandidates"][0]["status"], "PROXY_REJECTED_FOR_SPX_GEX")
        self.assertFalse(result["localCandidates"][0]["eligibleForImportReview"])

    def test_saved_synthetic_and_imported_chains_are_distinct(self):
        (self.root / "data").mkdir()
        database = sqlite3.connect(self.root / "data/hub.sqlite3")
        database.execute("CREATE TABLE web_snapshots(id TEXT PRIMARY KEY,symbol TEXT,kind TEXT,as_of TEXT,imported_at TEXT,payload_json TEXT)")
        payload = json.dumps({"options": [{"id": "SPXW-fixture"}]})
        database.execute("INSERT INTO web_snapshots VALUES(?,?,?,?,?,?)", ("one", "SPX", "synthetic", "x", "x", payload))
        database.commit()
        self.assertEqual(_spx_readiness(self.root, self.news)["status"], "SYNTHETIC_ONLY")
        database.execute("INSERT INTO web_snapshots VALUES(?,?,?,?,?,?)", ("two", "SPX", "imported", "x", "x", payload))
        database.commit()
        database.close()
        result = _spx_readiness(self.root, self.news)
        self.assertEqual(result["status"], "LOCAL_CHAIN_SAVED_REVIEW_REQUIRED")
        self.assertEqual((result["syntheticSnapshots"], result["importedSnapshots"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
