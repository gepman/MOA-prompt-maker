@echo off
rem MOA builder (7860) and editor (7863) with --data sample
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo .venv was not found. Run SETUP.bat first.
  pause
  exit /b 1
)
start "MOA editor" cmd /k ".venv\Scripts\python editor.py --data QuickStart/moa_public_samples --config QuickStart/config_quickstart.yaml"
start "MOA builder" cmd /k ".venv\Scripts\python app.py --data QuickStart/moa_public_samples --config QuickStart/config_quickstart.yaml"
rem open browsers after the servers start
timeout /t 8 /nobreak >nul
start "" http://127.0.0.1:7863
start "" http://127.0.0.1:7860

