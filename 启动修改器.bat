@echo off
setlocal
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0AoE4_RogueTalentTool.py"
    exit /b 0
)

where python >nul 2>nul
if %errorlevel%==0 (
    python "%~dp0AoE4_RogueTalentTool.py"
    exit /b %errorlevel%
)

echo Python not found. Please install Python 3.10 or newer from python.org.
pause
exit /b 1
