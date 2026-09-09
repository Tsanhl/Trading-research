"""Sandbox-only metadata probe. No trading client, order routes or raw logs."""
from __future__ import annotations

import contextlib
import io
import logging
import os
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path
import sys

from .common import ROOT, config, finite, now_iso
from .paper import FUTURES

FIELDS = {"symbol","instrument_id","code","contract_month","contract_type","exchange_code","exchange","settlement_date","last_trading_date","first_notice_date","multiplier","size","min_tick","currency","list_status"}
ACCOUNT_FIELDS = {"account_type", "account_status", "currency", "base_currency", "status", "account_id"}
BALANCE_FIELDS = {"currency", "total_asset", "total_assets", "net_liquidation", "net_liquidation_value",
                  "buying_power", "available_funds", "cash_balance", "settled_cash", "cash",
                  "market_value", "maintenance_margin", "initial_margin", "margin_power"}
POSITION_FIELDS = {"symbol", "instrument_id", "asset_type", "currency", "quantity", "qty", "side",
                   "average_price", "avg_price", "market_price", "market_value",
                   "unrealized_profit_loss", "unrealized_pnl"}


def _private_setting(name):
    """Read a Webull value from process/local env without ever logging it."""
    value = os.environ.get(name, "").strip()
    path = ROOT / "local-data.env"
    if not value and path.is_file():
        if path.stat().st_size > 16_384:
            raise ValueError("local-data.env exceeds the 16 KB safety limit")
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, raw = line.split("=", 1)
            if key.strip() == name:
                value = raw.strip().strip('"').strip("'")
                break
    return value


def _keychain(service, account):
    if not service or not account or not Path("/usr/bin/security").is_file():
        return ""
    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-a", account,
             "-s", service, "-w"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _sandbox_credentials():
    cfg = config()["webull"]
    key = _private_setting(str(cfg.get("app_key_env") or "WEBULL_APP_KEY"))
    secret = _private_setting(str(cfg.get("app_secret_env") or "WEBULL_APP_SECRET"))
    account = str(cfg.get("keychain_account") or "market-sentinel")
    if not key:
        key = _keychain(str(cfg.get("app_key_keychain_service") or ""), account)
    if not secret:
        secret = _keychain(str(cfg.get("app_secret_keychain_service") or ""), account)
    if not key or not secret:
        raise ValueError("Webull sandbox credentials are not configured in the local environment or named Keychain entries")
    return key, secret


def _sandbox_api(log_sink):
    cfg = config()
    endpoint = "api.sandbox.webull.hk"
    if cfg["webull"]["environment"] != "sandbox" or cfg["webull"]["endpoint"] != endpoint:
        raise ValueError("Sandbox endpoint pinned")
    sys.dont_write_bytecode = True
    from webull.core.client import ApiClient
    key, secret = _sandbox_credentials()
    api = ApiClient(key, secret, "hk", connect_timeout=8, timeout=12, auto_retry=False,
                    token_check_duration_seconds=10, token_check_interval_seconds=5)
    api.add_endpoint("hk", endpoint)
    token_dir = ROOT / "state/private-webull-token"
    token_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_dir.chmod(0o700)
    api.set_token_dir(str(token_dir))
    api.set_stream_logger(log_level=logging.CRITICAL + 1, stream=log_sink)
    return api


def _records(value):
    if isinstance(value,list):
        return [r for child in value for r in _records(child)]
    if isinstance(value,dict):
        if "symbol" in value or "instrument_id" in value:
            return [{k:v for k,v in value.items() if k in FIELDS and isinstance(v,(str,int,float,bool,type(None)))}]
        return [r for key in ("data","result","instruments","list") if key in value for r in _records(value[key])]
    return []


def _market_records(value):
    allowed = {"symbol","instrument_id","price","bid","ask","quote_time","last_trade_time","time","timestamp","open","high","low","close","volume"}
    if isinstance(value,list):
        return [row for child in value for row in _market_records(child)]
    if isinstance(value,dict):
        if "price" in value or "open" in value or "bid" in value:
            return [{k:v for k,v in value.items() if k in allowed and isinstance(v,(str,int,float,bool,type(None)))}]
        return [row for key in ("data","result","snapshots","bars") if key in value for row in _market_records(value[key])]
    return []


def _valid_contract(row,root):
    try:
        return (row.get("code") == root and row.get("contract_type") == "MONTHLY" and
                bool(re.fullmatch(re.escape(root)+r"[FGHJKMNQUVXZ]\d{1,4}",row.get("symbol",""))) and
                row.get("currency") == "USD" and row.get("exchange_code") == "XCME" and
                finite(row["size"]) == FUTURES[root]["point_value_usd"] and finite(row["min_tick"]) == FUTURES[root]["tick"])
    except (KeyError,ValueError,TypeError):
        return False


def _json(response):
    status = int(response.status_code)
    try:
        payload = response.json() if status == 200 else {}
    except Exception:
        payload = {}
    return status, payload


def _dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _dicts(child)


def _scalars(row, fields):
    return {key: value for key, value in row.items()
            if str(key).lower() in fields and isinstance(value, (str, int, float, bool, type(None)))}


def _account_rows(value):
    rows = []
    for row in _dicts(value):
        lowered = {str(key).lower(): val for key, val in row.items()}
        if lowered.get("account_id"):
            safe = _scalars(lowered, ACCOUNT_FIELDS)
            account_id = str(lowered["account_id"])
            safe["account"] = "…" + account_id[-4:]
            safe.pop("account_id", None)
            rows.append((account_id, safe))
    unique = {}
    for account_id, safe in rows:
        unique[account_id] = safe
    return list(unique.items())


def _safe_rows(value, fields, identity_fields):
    output = []
    for row in _dicts(value):
        lowered = {str(key).lower(): val for key, val in row.items()}
        if any(lowered.get(key) not in (None, "") for key in identity_fields):
            safe = _scalars(lowered, fields)
            if safe and safe not in output:
                output.append(safe)
    return output[:200]


def _build_sandbox_client(log_sink):
    from webull.trade.trade_client import TradeClient
    previous_umask = os.umask(0o077)
    try:
        return TradeClient(_sandbox_api(log_sink))
    finally:
        os.umask(previous_umask)


def _build_sandbox_data_client(log_sink):
    """Return the official read-only data client in the isolated HK sandbox."""
    from webull.data.data_client import DataClient
    previous_umask = os.umask(0o077)
    try:
        return DataClient(_sandbox_api(log_sink))
    finally:
        os.umask(previous_umask)


def _option_contract_rows(value):
    rows = []
    for row in _dicts(value):
        lowered = {str(key).lower(): val for key, val in row.items()}
        symbol = str(lowered.get("symbol") or lowered.get("option_symbol") or "").upper()
        root = str(lowered.get("root_symbol") or "").upper()
        underlying = str(lowered.get("underlying_symbol") or lowered.get("underlying") or "").upper()
        if symbol.startswith(("SPX", "SPXW")) or root in {"SPX", "SPXW"} or underlying == "SPX":
            if lowered.get("instrument_id") or symbol:
                rows.append(lowered)
    unique = {}
    for row in rows:
        identity = str(row.get("instrument_id") or row.get("symbol") or row.get("option_symbol") or "")
        if identity:
            unique[identity] = row
    return list(unique.values())


def _option_quote_rows(value, requested):
    requested = {str(value).upper() for value in requested}
    rows = []
    for row in _dicts(value):
        lowered = {str(key).lower(): val for key, val in row.items()}
        symbol = str(lowered.get("symbol") or lowered.get("option_symbol") or "").upper()
        if symbol in requested and any(lowered.get(field) is not None for field in
                                       ("bid", "ask", "open_interest", "gamma", "imp_vol", "iv", "delta")):
            rows.append(lowered)
    return rows


def spx_option_probe(client_factory=None, *, today=None):
    """Check free sandbox SPX option coverage without returning market values.

    This deliberately does not create a GEX snapshot. Sandbox observations are
    not production market evidence, and date-only contracts lack the exact
    lifecycle metadata required by the qualified SPX model.
    """
    checked = today or date.today()
    result = {"checked_at": now_iso(), "environment": "sandbox",
              "endpoint": "api.sandbox.webull.hk", "read_only": True,
              "underlying": "SPX", "order_requests": 0,
              "contract_http_status": None, "contracts_observed": 0,
              "native_roots": [], "snapshot_http_status": None,
              "snapshots_observed": 0, "fields_observed": [],
              "current_market_qualified": False, "gex_import_ready": False,
              "status": "BLOCKED"}
    log_sink = io.StringIO()
    previous_logging = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(log_sink), contextlib.redirect_stderr(log_sink):
            client = client_factory() if client_factory else _build_sandbox_data_client(log_sink)
            response = client.instrument.get_option_contracts(
                category="US_OPTION", underlying_symbols="SPX", status="LISTING",
                start_date=checked.isoformat(), end_date=(checked + timedelta(days=35)).isoformat(),
                page_size=100, last_instrument_id=None)
            status, payload = _json(response)
            result["contract_http_status"] = status
            contracts = _option_contract_rows(payload) if status == 200 else []
            result["contracts_observed"] = len(contracts)
            roots = set()
            symbols = []
            for row in contracts:
                symbol = str(row.get("symbol") or row.get("option_symbol") or "").upper()
                root = str(row.get("root_symbol") or "").upper()
                if root in {"SPX", "SPXW"}:
                    roots.add(root)
                elif symbol.startswith("SPXW"):
                    roots.add("SPXW")
                elif symbol.startswith("SPX"):
                    roots.add("SPX")
                if symbol.startswith(("SPX", "SPXW")) and len(symbols) < 5:
                    symbols.append(symbol)
            result["native_roots"] = sorted(roots)
            if symbols:
                quote_response = client.option_market_data.get_option_snapshot(symbols, "US_OPTION")
                quote_status, quote_payload = _json(quote_response)
                result["snapshot_http_status"] = quote_status
                quotes = _option_quote_rows(quote_payload, symbols) if quote_status == 200 else []
                result["snapshots_observed"] = len(quotes)
                allowed = {"bid", "ask", "open_interest", "gamma", "imp_vol", "iv", "delta",
                           "quote_time", "timestamp"}
                result["fields_observed"] = sorted({key for row in quotes for key in row if key in allowed})
            if result["contracts_observed"] and result["snapshots_observed"]:
                result["status"] = "SANDBOX_SPX_OPTIONS_OBSERVED"
                result["note"] = ("Native SPX sandbox contracts and option fields were observed. "
                                  "They are not current production data and are not imported into qualified GEX.")
            else:
                result["status"] = "NO_NATIVE_SPX_OPTIONS_OBSERVED"
                result["note"] = "The bounded sandbox request did not establish a usable SPX option chain."
    except Exception as error:
        result["error_type"] = type(error).__name__
        result["note"] = ("Sandbox SPX option access is unavailable or provider-limited. "
                          "No response body, identifier, market value or credential was retained.")
    finally:
        logging.disable(previous_logging)
        log_sink.close()
    return result


def account_probe(client_factory=None):
    """Read sanitized sandbox account state using GET-only official SDK calls.

    The returned structure contains masked account identifiers and an allowlist
    of balance/position fields. No order, preview, cancel or replace method is
    called. Production remains separately unverified and disabled.
    """
    result = {"checked_at": now_iso(), "environment": "sandbox",
              "endpoint": "api.sandbox.webull.hk", "read_only": True,
              "account_read_requests": 0, "order_read_requests": 0,
              "order_write_requests": 0, "accounts": [], "status": "BLOCKED"}
    log_sink = io.StringIO()
    previous_logging = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(log_sink), contextlib.redirect_stderr(log_sink):
            client = client_factory() if client_factory else _build_sandbox_client(log_sink)
            status, payload = _json(client.account_v2.get_account_list())
            result["account_read_requests"] += 1
            result["account_list_http_status"] = status
            accounts = _account_rows(payload) if status == 200 else []
            for account_id, safe_account in accounts[:10]:
                account = dict(safe_account)
                b_status, balances = _json(client.account_v2.get_account_balance(account_id))
                result["account_read_requests"] += 1
                p_status, positions = _json(client.account_v2.get_account_position(account_id))
                result["account_read_requests"] += 1
                o_status, orders = _json(client.order_v2.get_order_open(account_id, page_size=100))
                result["order_read_requests"] += 1
                account.update({
                    "balanceHttpStatus": b_status,
                    "balances": _safe_rows(balances, BALANCE_FIELDS, {"currency", "total_asset", "buying_power", "cash"}),
                    "positionsHttpStatus": p_status,
                    "positions": _safe_rows(positions, POSITION_FIELDS, {"symbol", "instrument_id"}),
                    "openOrdersHttpStatus": o_status,
                    "openOrderCount": len(_safe_rows(orders, {"status", "symbol", "side", "quantity", "qty"}, {"status", "symbol"})),
                })
                result["accounts"].append(account)
        result["status"] = "READ_ONLY_ACCOUNT_OBSERVED" if result["accounts"] else "NO_ACCOUNT_VISIBLE"
        result["production_capability"] = "UNVERIFIED"
        result["execution_enabled"] = False
    except Exception as error:
        result["error_type"] = type(error).__name__
        result["note"] = "Sandbox account read is unavailable. Confirm the private HK OpenAPI app scope; no SDK diagnostics or credentials were retained."
    finally:
        logging.disable(previous_logging)
        log_sink.close()
    return result


def probe(client_factory=None):
    result = {"checked_at":now_iso(), "environment":"sandbox", "endpoint":"api.sandbox.webull.hk",
              "read_only":True, "order_requests":0, "products":{}, "paper_order_support":"UNVERIFIED",
              "live_data_entitlement":"UNVERIFIED"}
    log_sink = io.StringIO()
    previous_logging = logging.root.manager.disable
    try:
        # Capture possible SDK diagnostics. Never persist the capture, headers or response errors.
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(log_sink), contextlib.redirect_stderr(log_sink):
            client = client_factory() if client_factory else _build_sandbox_data_client(log_sink)
            for root in ("MES","ES","NQ"):
                try:
                    response = client.instrument.get_futures_instrument(category="US_FUTURES",code=root)
                    status = int(response.status_code)
                    rows = _records(response.json()) if status == 200 else []
                    matching = [row for row in rows if _valid_contract(row,root)]
                    result["products"][root] = {"http_status":status, "records":rows,
                                                "status":"MATCHING_CONTRACT_METADATA_OBSERVED" if matching else "UNPROVEN",
                                                "order_support":"UNVERIFIED"}
                    # Nearest future expiry is a probe target, not a liquidity-based trade selection.
                    eligible = sorted((r for r in matching if str(r.get("last_trading_date","")) > result["checked_at"][:10]),
                                      key=lambda r:r["last_trading_date"])
                    if eligible:
                        target = eligible[0]["symbol"]
                        checks = {"instrument":target,"selection":"NEAREST_EXPIRY_FOR_READ_ONLY_PROBE_NOT_TRADING",
                                  "session_semantics":"UNVERIFIED","live_entitlement":"UNVERIFIED"}
                        for operation in ("snapshot","M5_bars"):
                            try:
                                response = (client.futures_market_data.get_futures_snapshot(target,"US_FUTURES") if operation == "snapshot" else
                                            client.futures_market_data.get_futures_history_bars(target,"US_FUTURES","M5",count="60",real_time_required=False))
                                code = int(response.status_code)
                                market_rows = _market_records(response.json()) if code == 200 else []
                                checks[operation] = {"http_status":code,"row_count":len(market_rows),"rows":market_rows,
                                                     "status":"SANDBOX_DATA_OBSERVED" if market_rows else "UNPROVEN"}
                            except Exception as error:
                                checks[operation] = {"status":"UNPROVEN","error_type":type(error).__name__}
                        result["products"][root]["market_data_checks"] = checks
                except Exception as error:
                    result["products"][root] = {"status":"UNPROVEN", "error_type":type(error).__name__}
        result["status"] = "READ_ONLY_PROBE_COMPLETED"
    except Exception as error:
        result["status"] = "BLOCKED"
        result["error_type"] = type(error).__name__
        result["note"] = "Configure/confirm sandbox credentials privately in Keychain or use the existing provider's setup; no secret text is logged."
    finally:
        logging.disable(previous_logging)
        log_sink.close()
    return result
