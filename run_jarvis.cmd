@echo off
rem Starts Jarvis and brings it back if it crashes (a normal "goodbye Jarvis" exit stays closed).
rem Used by the login autostart (scripts\autostart.ps1); double-click it to start Jarvis by hand.
title Jarvis
cd /d "%~dp0"
:loop
".venv\Scripts\python.exe" main.py
if %errorlevel%==0 goto :eof
echo.
echo Jarvis stopped with an error (code %errorlevel%). Restarting in 10 seconds... close this window to stop.
timeout /t 10 /nobreak >nul
goto loop
