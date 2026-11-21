@echo off
rem Brings everything back after the computer was shut down or restarted: the app and the brain (start.cmd),
rem and, for Instagram, the gatekeeper and the ngrok tunnel. Safe to run twice: what is already running is left alone.
rem To run it by itself at sign-in, put a shortcut to this file in the Windows Startup folder (Win+R, shell:startup).
cd /d "%~dp0"
call "%~dp0start.cmd"

powershell -NoProfile -Command "if (-not (Get-NetTCPConnection -LocalPort 8790 -State Listen -ErrorAction SilentlyContinue)) { Start-Process -WindowStyle Hidden -FilePath '.venv\Scripts\python.exe' -ArgumentList 'scripts\public-gate.py' }"
powershell -NoProfile -Command "if (-not (Get-Process ngrok -ErrorAction SilentlyContinue)) { Start-Process -WindowStyle Minimized -FilePath 'ngrok' -ArgumentList 'http 8790' }"
echo Clipping Studio is back. Recordings of anyone live start again within two minutes.
