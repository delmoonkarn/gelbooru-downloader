@echo off
setlocal

cd /d "%~dp0"

set "VENV_DIR=venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "PIP_EXE=%VENV_DIR%\Scripts\pip.exe"

where py >nul 2>&1
if %ERRORLEVEL%==0 (
    set "PY_LAUNCHER=py -3"
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Python is not installed or not on PATH.
        echo Install Python 3.10+ from https://www.python.org/downloads/
        pause
        exit /b 1
    )
    set "PY_LAUNCHER=python"
)

if not exist "%PYTHON_EXE%" (
    echo [*] Creating virtual environment...
    %PY_LAUNCHER% -m venv "%VENV_DIR%"
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

"%PYTHON_EXE%" -c "import customtkinter, requests, pyexiv2, PIL" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [*] Installing/updating dependencies...
    "%PYTHON_EXE%" -m pip install --upgrade pip >nul
    "%PIP_EXE%" install -r requirements.txt
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo [*] Launching Gelbooru Downloader...
start "" "%VENV_DIR%\Scripts\pythonw.exe" main.py

endlocal
