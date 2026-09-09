"""Canonical instrument identity and explicit provider mappings.

The canonical identity stays independent of any vendor ticker. Unknown or
ambiguous instruments are returned with unavailable provider mappings instead of
being substituted with a convenient U.S. security.
"""
from __future__ import annotations

import re

PATTERN = re.compile(r"[A-Z0-9^][A-Z0-9.^_=!:\-]{0,39}\Z")
ALIASES = {
    "APPLE": "AAPL", "TESLA": "TSLA", "NVIDIA": "NVDA", "GOOGLE": "GOOGL",
    "ALPHABET": "GOOGL", "MICROSOFT": "MSFT", "AMAZON": "AMZN",
    "MICRON": "MU", "PALANTIR": "PLTR", "VISA": "V", "MCDONALDS": "MCD",
    "^GSPC": "SPX", "^SPX": "SPX", "ES=F": "ES", "NQ=F": "NQ",
    "MES=F": "MES", "MNQ=F": "MNQ", "ES1!": "ES", "NQ1!": "NQ",
    "MES1!": "MES", "MNQ1!": "MNQ",
}
FUTURES = {"ES", "MES", "NQ", "MNQ"}
US_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "NYSEARCA", "ARCA", "BATS", "CBOE"}
INDEX_ALIASES = {"SP:SPX": "SPX", "TVC:SPX": "SPX", "CBOE:SPX": "SPX"}
KNOWN_ETFS = {"SPY", "QQQ", "DIA", "IWM"}
KNOWN_NASDAQ = {"AAPL", "MSFT", "AMD", "NVDA", "TSLA", "GOOGL", "GOOG", "AMZN", "MU", "PLTR", "META"}
KNOWN_NYSE = {"MCD", "V", "KO", "EOG", "KMI"}

TV = {
    "SPX": "SP:SPX", "SPY": "AMEX:SPY", "QQQ": "NASDAQ:QQQ",
    "DIA": "AMEX:DIA", "IWM": "AMEX:IWM",
    "ES": "CME_MINI:ES1!", "NQ": "CME_MINI:NQ1!",
    "MES": "CME_MINI:MES1!", "MNQ": "CME_MINI:MNQ1!",
    **{x: "NASDAQ:" + x for x in KNOWN_NASDAQ},
    **{x: "NYSE:" + x for x in KNOWN_NYSE},
    "BRK.B": "NYSE:BRK.B", "BRK-B": "NYSE:BRK.B",
    "BTC-USD": "COINBASE:BTCUSD", "ETH-USD": "COINBASE:ETHUSD",
}

VENUES = {
    "NASDAQ": ("USD", "America/New_York", "us-rth"),
    "NYSE": ("USD", "America/New_York", "us-rth"),
    "AMEX": ("USD", "America/New_York", "us-rth"),
    "NYSEARCA": ("USD", "America/New_York", "us-rth"),
    "ARCA": ("USD", "America/New_York", "us-rth"),
    "BATS": ("USD", "America/New_York", "us-rth"),
    "CBOE": ("USD", "America/New_York", "us-rth"),
    "LSE": ("GBP", "Europe/London", "unknown"),
    "HKEX": ("HKD", "Asia/Hong_Kong", "unknown"),
}


def normalize(raw):
    if not isinstance(raw, str):
        raise ValueError("Enter one ticker symbol")
    value = raw.strip().upper()
    if not PATTERN.fullmatch(value) or ".." in value or value.count(":") > 1 or value.endswith(":"):
        raise ValueError("Use a ticker such as AAPL, NYSE:BRK.B, LSE:VOD, HKEX:700, SPX or ES; URLs and expressions are not accepted")
    value = INDEX_ALIASES.get(value, value)
    if value.startswith("CME_MINI:") and value.split(":", 1)[1] in ALIASES:
        value = ALIASES[value.split(":", 1)[1]]
    return ALIASES.get(value, value)


def _known_exchange(value):
    if value in KNOWN_NASDAQ or value == "QQQ":
        return "NASDAQ"
    if value in KNOWN_NYSE or value in {"BRK.B", "BRK-B"}:
        return "NYSE"
    if value in {"SPY", "DIA", "IWM"}:
        return "AMEX"
    return "US_UNQUALIFIED"


def resolve(raw):
    value = normalize(raw)
    original = value
    explicit_exchange = None
    ticker = value
    if ":" in value:
        explicit_exchange, ticker = value.split(":", 1)

    asset_class = "equity"
    exchange = explicit_exchange or _known_exchange(value)
    currency, zone, session = VENUES.get(exchange, ("UNKNOWN", "UNKNOWN", "unknown"))
    continuous = False
    reference_only = False
    actual_contract = False
    yahoo = None

    dated_future = re.fullmatch(r"(MES|MNQ|ES|NQ)[FGHJKMNQUVXZ]\d{1,4}", value)
    if dated_future:
        asset_class, exchange, currency, zone, session = "future", "CME", "USD", "America/Chicago", "futures-reference"
        yahoo, actual_contract = None, True
    elif value in FUTURES:
        asset_class, exchange, currency, zone, session = "future", "CME", "USD", "America/Chicago", "futures-reference"
        yahoo, continuous, reference_only = value + "=F", True, True
    elif value == "SPX":
        asset_class, exchange, currency, zone, session = "index", "CBOE", "USD", "America/New_York", "us-rth"
        yahoo, reference_only = "^GSPC", True
    elif value.endswith(("-USD", "-USDT")):
        asset_class, exchange, currency, zone, session = "crypto", "COMPOSITE", value.rsplit("-", 1)[1], "Etc/UTC", "continuous"
        yahoo = value
    elif explicit_exchange:
        if explicit_exchange in US_EXCHANGES:
            yahoo = ticker.replace(".", "-")
        elif explicit_exchange == "LSE" and re.fullmatch(r"[A-Z0-9.\-]{1,18}", ticker):
            yahoo = ticker + ".L"
        elif explicit_exchange == "HKEX" and ticker.isdigit():
            yahoo = ticker.zfill(4) + ".HK"
        elif explicit_exchange in {"COINBASE", "BITSTAMP"} and ticker in {"BTCUSD", "ETHUSD"}:
            asset_class, exchange, currency, zone, session = "crypto", explicit_exchange, "USD", "Etc/UTC", "continuous"
            yahoo = ticker[:-3] + "-USD"
        else:
            yahoo = None
    else:
        yahoo = {"BRK.B": "BRK-B", "BF.B": "BF-B"}.get(value, value)
        if value.endswith("=X"):
            asset_class, exchange, currency, zone, session = "fx-reference", "COMPOSITE", "UNKNOWN", "Etc/UTC", "continuous"
            reference_only = True
        elif value.endswith("=F"):
            asset_class, exchange, currency, zone, session = "future", "UNKNOWN", "UNKNOWN", "UNKNOWN", "futures-reference"
            continuous = reference_only = True
        elif re.search(r"\.(L|HK|TO|V|AX|T|DE|PA|MI|SW|F|NS|BO)$", value):
            exchange, currency, zone, session = "PROVIDER_SUFFIX", "UNKNOWN", "UNKNOWN", "unknown"
        elif value in KNOWN_ETFS:
            asset_class = "etf"

    if explicit_exchange and yahoo is not None:
        canonical = next((key for key, mapped in TV.items() if mapped == original and key != "BRK-B"), None)
        if canonical:
            value = canonical
            exchange = _known_exchange(value) if value != "SPX" else "CBOE"

    us_stock = bool(session == "us-rth" and asset_class in {"equity", "etf"} and yahoo and
                    re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", yahoo))
    tv = TV.get(value, "CME_MINI:" + value if actual_contract else original)
    return {
        "symbol": value,
        "instrumentId": f"{asset_class}:{exchange}:{ticker}",
        "assetClass": asset_class,
        "exchange": exchange,
        "currency": currency,
        "exchangeTimezone": zone,
        "tradingview": tv,
        "yahoo": yahoo,
        "alpaca": yahoo.replace("-", ".") if us_stock else None,
        "providerSymbols": {"tradingview": tv, "yahoo": yahoo,
                            "alpacaIex": yahoo.replace("-", ".") if us_stock else None},
        "sessionModel": session,
        "continuous": continuous,
        "referenceOnly": reference_only,
        "actualDatedContract": actual_contract,
        "chartCaveat": "Provider availability, session coverage and delay depend on the mapped venue. An unqualified ticker is treated as a U.S. research lookup; verify exchange identity before using a plan.",
    }
