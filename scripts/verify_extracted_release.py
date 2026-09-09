#!/usr/bin/env python3
"""Exercise a release after extraction. The supplied copy is intentionally mutated."""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if not (root / "launch.py").is_file():
        raise SystemExit("Extracted release root does not contain launch.py")
    sys.path.insert(0, str(root))
    from trading_hub.private_backup import restore_backup
    from trading_hub.web_server import LocalServer

    results = []

    def check(name, value, detail=None):
        results.append({"check": name, "passed": bool(value), "detail": detail})
        if not value:
            raise AssertionError(name)

    def start():
        server = LocalServer(("127.0.0.1", 0), root=root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, f"http://127.0.0.1:{server.server_address[1]}"

    def request(base, server, path, value=None, headers=None):
        data = json.dumps(value).encode() if value is not None else None
        sent = {"Content-Type": "application/json", **(headers or {})}
        if value is not None and "X-Hub-CSRF" not in sent:
            sent["X-Hub-CSRF"] = server.csrf
        try:
            response = urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=sent), timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        return response.status, response.read(), dict(response.headers)

    server, thread, base = start()
    marker = "EXTRACTED RELEASE PERSISTENCE CHECK"
    try:
        code, body, _ = request(base, server, "/api/status")
        status = json.loads(body)
        check("loopback server starts", code == 200 and status["version"] == "0.7.0")
        check("KX readiness is fail-closed", status["health"]["kx"]["releaseAuthorized"] is False)
        check("SPX readiness is explicit", status["health"]["spx"]["status"] in {"IMPORT_REQUIRED", "SYNTHETIC_ONLY", "LOCAL_CHAIN_SAVED_REVIEW_REQUIRED"})
        check("sandbox SPX coverage cannot qualify GEX", status["health"]["webull"]["spxOptionSandbox"]["currentMarketQualified"] is False and status["health"]["webull"]["spxOptionSandbox"]["gexImportReady"] is False)
        check("research corpus preserved", status["hub"]["counts"]["documents"] >= 826, status["hub"]["counts"]["documents"])
        check("records preserved", status["hub"]["counts"]["records"] >= 42, status["hub"]["counts"]["records"])
        check("schema migration present", status["migration"]["userVersion"] >= 4)
        check("broker execution disabled", status["brokerExecution"] is False)
        check("sandbox paper execution declared", status["health"]["broker"]["paperExecutionEnabled"] is True
              and status["health"]["broker"]["perOrderConfirmation"] is True)
        check("KX and news references are self-contained", all(Path(row["path"]).is_relative_to(root)
              for row in status["health"]["sources"]))
        check("starts without bundled credentials", status["alpacaConfigured"] is False)
        check("SPX GEX independent", status["spxGexOnly"] is True)
        code, body, headers = request(base, server, "/api/spx/sandbox-export")
        sandbox_chain = json.loads(body)
        check("included sandbox SPX file downloads", code == 200 and sandbox_chain["dataKind"] == "sandbox"
              and len(sandbox_chain["options"]) == 368 and sandbox_chain["chainComplete"] is False)
        check("sandbox SPX download is an attachment", "Webull-HK-SANDBOX-SPX" in headers.get("Content-Disposition", ""))

        for symbol, asset, exchange in (("HKEX:700", "equity", "HKEX"), ("LSE:VOD", "equity", "LSE"), ("BTC-USD", "crypto", "COMPOSITE"), ("MESZ26", "future", "CME")):
            code, body, _ = request(base, server, "/api/chart/resolve?symbol=" + urllib.parse.quote(symbol))
            info = json.loads(body)
            check("identity " + symbol, code == 200 and info["assetClass"] == asset and info["exchange"] == exchange)

        code, _, _ = request(base, server, "/api/status", headers={"Origin": "https://evil.example"})
        check("cross-origin request rejected", code == 403)
        code, _, _ = request(base, server, "/api/settings", {"equity": 1000}, {"X-Hub-CSRF": ""})
        check("missing CSRF rejected", code == 403)

        settings = status["settings"]
        settings["favorites"] = list(dict.fromkeys([*settings.get("favorites", []), "HKEX:700"]))
        code, _, _ = request(base, server, "/api/settings", settings)
        check("favorite saved", code == 200)
        code, _, _ = request(base, server, "/api/notes", {"text": marker, "symbol": "HKEX:700"})
        check("journal note saved", code == 200)

        template = json.loads((root / "examples/web/spx-gex-demo.json").read_text())
        code, body, _ = request(base, server, "/api/gex/preview", {"snapshot": template})
        preview = json.loads(body)
        check("SPX preview accepts native synthetic rows", code == 200 and preview["accepted"] > 0 and preview["rejected"] == 0)
        code, body, _ = request(base, server, "/api/broker/ticket", {"symbol": "AAPL", "side": "BUY", "quantity": 1, "limitPrice": 1, "orderType": "LIMIT", "timeInForce": "DAY"})
        ticket = json.loads(body)
        check("broker ticket remains blocked", code == 200 and ticket["state"] == "BLOCKED_REVIEW_ONLY" and ticket["executionEnabled"] is False)
        code, _, _ = request(base, server, "/api/broker/order", ticket)
        check("no broker order endpoint", code == 404)

        code, body, _ = request(base, server, "/api/research?q=inflation")
        research = json.loads(body)
        check("full-text research search", code == 200 and len(research) > 0)
        code, body, _ = request(base, server, "/api/backtests")
        before = json.loads(body)
        check("legacy backtests present", code == 200 and len(before) > 0)
        code, body, _ = request(base, server, "/api/backtests/run", {})
        check("legacy backtest reruns", code == 200 and json.loads(body).get("ok") is True)

        code, backup, _ = request(base, server, "/api/backup")
        check("private backup downloads", code == 200 and zipfile.is_zipfile(io.BytesIO(backup)))
        with tempfile.TemporaryDirectory(prefix="Hub extracted restore ") as folder:
            archive = Path(folder) / "backup.zip"
            archive.write_bytes(backup)
            receipt = restore_backup(archive, Path(folder) / "Restore With Spaces")
            check("backup restores both databases", len(receipt["databases"]) == 2)
    finally:
        server.shutdown(); server.server_close(); thread.join()

    server, thread, base = start()
    try:
        status = json.loads(request(base, server, "/api/status")[1])
        notes = json.loads(request(base, server, "/api/notes")[1])
        check("favorite persists after restart", "HKEX:700" in status["settings"]["favorites"])
        check("journal persists after restart", any(item.get("text") == marker for item in notes))
    finally:
        server.shutdown(); server.server_close(); thread.join()

    report = {"root": str(root), "checks": len(results), "passed": sum(item["passed"] for item in results), "failed": sum(not item["passed"] for item in results), "results": results}
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
