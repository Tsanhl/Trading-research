@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\enable_free_feeds.py
) else (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 scripts\enable_free_feeds.py
  ) else (
    python scripts\enable_free_feeds.py
  )
)
if errorlevel 1 (
  echo Python 3.11 or newer is required. Optional installation requires internet. See START-HERE.txt for help.
  pause
)
