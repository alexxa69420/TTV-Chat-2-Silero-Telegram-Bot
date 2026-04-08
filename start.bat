@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Virtual environment not found: .venv
    echo Run setup.bat first.
    pause
    exit /b 1
)

echo [INFO] Running tests...
"%PYTHON_EXE%" -m pytest
if errorlevel 1 (
    echo [ERROR] Tests failed. Bot start aborted.
    pause
    exit /b 1
)

echo [INFO] Tests passed. Starting bot...
"%PYTHON_EXE%" "TTV-Chat-2-Silero-Telegram-Bot.py"
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%