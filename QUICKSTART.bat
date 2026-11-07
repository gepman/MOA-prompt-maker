@echo off
rem MOA builder (7860) and editor (7863) with --data Main
cd /d "%~dp0"
start "MOA editor" cmd /k ".venv\Scripts\python editor.py --data QuickStart/moa_public_samples"
start "MOA builder" cmd /k ".venv\Scripts\python app.py --data QuickStart/moa_public_samples"
rem open browsers after the servers start
timeout /t 8 /nobreak >nul
start "" http://127.0.0.1:7860
start "" http://127.0.0.1:7863
