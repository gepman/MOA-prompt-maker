@echo off
rem MOA setup: create .venv and install requirements. Safe to run again.
cd /d "%~dp0"

rem --- find Python 3.11 or newer ---
set "PY="
py -3.11 --version >nul 2>&1 && set "PY=py -3.11"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python 3.11 or newer was not found.
  echo         Install Python 3.11 from https://www.python.org/downloads/
  echo         and check "Add python.exe to PATH" in the installer.
  echo         Then run SETUP.bat again.
  pause
  exit /b 1
)
echo Using Python: %PY%

rem --- create .venv (skipped when it already exists) ---
if exist ".venv\Scripts\python.exe" (
  echo .venv already exists. Skipping creation.
) else (
  echo Creating .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create .venv.
    pause
    exit /b 1
  )
)

rem --- install requirements ---
echo Installing requirements ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip install failed. Check the network connection and run SETUP.bat again.
  pause
  exit /b 1
)

echo.
echo Setup finished. Next: start ComfyUI, then double-click QUICKSTART.bat
pause
