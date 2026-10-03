@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py -3 gui.py & goto :eof)
where python >nul 2>nul && (python gui.py & goto :eof)
echo Python wurde nicht gefunden. Bitte Python von python.org installieren.
pause
