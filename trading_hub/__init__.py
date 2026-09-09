"""Private market research, simulation and user-controlled data connections."""

__version__ = "0.8.1"

from pathlib import Path as _Path
from zoneinfo import TZPATH as _TZPATH, reset_tzpath as _reset_tzpath

_zone_dir = _Path(__file__).resolve().parents[1] / "vendor/zoneinfo"
if _zone_dir.is_dir():
    _reset_tzpath([*_TZPATH, str(_zone_dir)])
