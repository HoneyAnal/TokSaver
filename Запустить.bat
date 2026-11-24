@echo off
rem TokSaver launcher: opens the app window and closes this console at once.
rem File must stay UTF-8 (no BOM) with CRLF line endings.
chcp 65001 >nul
cd /d "%~dp0"

rem 1) windowed python - the app opens, no black window remains
where pythonw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pythonw.exe main.py
    exit /b 0
)

rem 2) py-launcher windowed variant
where pyw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pyw.exe main.py
    exit /b 0
)

rem 3) only console python exists (rare): run it here, window stays while
rem the app works, but closes without any prompts when all goes well
where python.exe >nul 2>nul
if not errorlevel 1 (
    python.exe main.py
    if errorlevel 1 pause
    exit /b 0
)
where py.exe >nul 2>nul
if not errorlevel 1 (
    py.exe main.py
    if errorlevel 1 pause
    exit /b 0
)

rem 4) python missing: keep the window and explain how to fix
echo.
echo Python not found. Install Python 3.9+ from https://python.org
echo Python не найден. Установите Python 3.9+ с сайта https://python.org
echo (при установке включите галочку "Add Python to PATH") и запустите снова.
echo.
pause
exit /b 1
