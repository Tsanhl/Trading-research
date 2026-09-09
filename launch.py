#!/usr/bin/env python3
"""No third-party packages are needed for the core local app."""
import sys
from pathlib import Path
if sys.version_info < (3, 11):
    print("Python 3.11 or newer is required. Install it from python.org, then run this launcher again.")
    raise SystemExit(1)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trading_hub.web_server import main
raise SystemExit(main())
