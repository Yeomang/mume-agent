@echo off
chcp 65001 >nul 2>&1
cd /d "C:\mume-agent"
call "C:\mume-agent\.venv\Scripts\activate.bat"

set FAIL_COUNT=0

:loop
echo [%date% %time%] HTS Agent start...

REM Kill existing process on port 9000 if any
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :9000 ^| findstr LISTENING') do (
    echo [%date% %time%] Port 9000 in use (PID: %%a), killing...
    taskkill /F /PID %%a >nul 2>&1
    timeout /t 2 /nobreak >nul
)

python -m uvicorn hts_agent:app --host 0.0.0.0 --port 9000 --no-use-colors --no-access-log

if %errorlevel% equ 0 (
    set FAIL_COUNT=0
) else (
    set /a FAIL_COUNT+=1
)

if %FAIL_COUNT% geq 5 (
    echo.
    echo [%date% %time%] Agent failed to start 5 times in a row.
    echo Check for port conflicts or config errors.
    echo Press any key to retry...
    set FAIL_COUNT=0
    pause >nul
)

echo [%date% %time%] HTS Agent stopped. Restarting in 3s...
timeout /t 3 /nobreak >nul
goto loop
