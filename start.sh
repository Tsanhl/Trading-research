#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "Python 3.11+ is required. Install it from https://www.python.org/downloads/ and reopen this file."
  read -r -p "Press Enter to close. "
  exit 1
fi
"$PY" launch.py "$@"
result=$?
if [ "$result" -ne 0 ]; then read -r -p "App did not start. Read the message above; press Enter to close. "; fi
exit "$result"
