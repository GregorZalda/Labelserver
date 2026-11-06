@echo off
rem Startet den Ein-PDF-Prototypen mit der vorhandenen virtuellen Umgebung.
rem Kein Netzwerkzugriff, kein pip, keine Installation beim Start.
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo Virtuelle Umgebung nicht gefunden: %PY%
    pause
    exit /b 1
)
"%PY%" "%~dp0analyze_label.py" %*
set "CODE=%ERRORLEVEL%"
echo.
echo Exit-Code: %CODE%
pause
exit /b %CODE%
