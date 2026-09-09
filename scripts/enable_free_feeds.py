"""Explicit optional public-research adapter install in a local .venv."""
import subprocess
import sys
import venv
from pathlib import Path
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required.")
root = Path(__file__).resolve().parents[1]
env = root / ".venv"
print("Installing optional yfinance and Alpaca IEX WebSocket support inside this folder only. No API key or payment required.")
print("Internet is required to download packages. Data access is not guaranteed.")
try:
    if not env.exists():
        venv.EnvBuilder(with_pip=True).create(env)
    py = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(root / "requirements-free.txt")], check=True)
except (OSError, subprocess.CalledProcessError) as exc:
    print("Optional installation failed. The dependency-free app still works. Details:", exc)
    raise SystemExit(1)
print("Done. Close any running Hub terminal and use the normal START launcher again.")
