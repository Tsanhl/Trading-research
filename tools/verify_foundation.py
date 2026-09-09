"""Run local tests and write a hash-pinned implementation checkpoint, not a trade seal."""
from pathlib import Path
import ast
import io
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from trading_hub.common import file_hash, now_iso, save_json
from trading_hub.sources import snapshot


def main():
    suite = unittest.defaultTestLoader.discover(str(ROOT/"tests"))
    buffer = io.StringIO()
    tests = unittest.TextTestRunner(stream=buffer,verbosity=1).run(suite)
    print(buffer.getvalue())
    files = sorted(p for folder in ("trading_hub","tests","tools") for p in (ROOT/folder).glob("*.py"))
    forbidden_imports = []
    for path in files:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules = ([node.module or ""] if isinstance(node,ast.ImportFrom) else [n.name for n in node.names] if isinstance(node,ast.Import) else [])
            if any(name.startswith("webull.trade") for name in modules):
                forbidden_imports.append(str(path))
    sources = snapshot()
    manifest = {"schema":"trading-hub.foundation-check.v1", "checked_at":now_iso(),
                "status":"PASS_FOUNDATION_ONLY" if tests.wasSuccessful() and not forbidden_imports else "FAIL",
                "tests_run":tests.testsRun,"failures":len(tests.failures),"errors":len(tests.errors),
                "source_hashes":{str(p.relative_to(ROOT)):file_hash(p) for p in files+[ROOT/"config.toml",ROOT/"pyproject.toml"]},
                "forbidden_broker_imports":forbidden_imports,
                "current_kx_gate":sources["kx_gate"],"pine_release_sealed":False,
                "live_market_data_verified":False,"broker_paper_verified":False,
                "holdouts_frozen":False,"strategy_profitability_established":False}
    save_json(ROOT/"state/foundation-verification.json",manifest)
    print(manifest["status"])
    return 0 if manifest["status"] == "PASS_FOUNDATION_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
