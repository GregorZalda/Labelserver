@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo Virtuelle Umgebung nicht gefunden: %PY%
    pause
    exit /b 1
)
"%PY%" -X utf8 "%~dp0catalog_server.py" --open-browser
set "CODE=%ERRORLEVEL%"
echo.
echo Exit-Code: %CODE%
pause
exit /b %CODE%
