@echo off
cd /d "C:\mume-agent"
call "C:\mume-agent\.venv\Scripts\activate.bat"

set FAIL_COUNT=0

:loop
echo [%date% %time%] HTS Agent start...

REM 포트 9000을 점유 중인 기존 프로세스가 있으면 자동 종료
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :9000 ^| findstr LISTENING') do (
    echo [%date% %time%] 포트 9000 점유 프로세스 발견 (PID: %%a), 종료 시도...
    taskkill /F /PID %%a >nul 2>&1
    timeout /t 2 /nobreak >nul
)

python -m uvicorn hts_agent:app --host 0.0.0.0 --port 9000 --no-use-colors --no-access-log

REM 종료 코드 확인 — 정상 종료(배포 재시작 등)면 카운터 리셋
if %errorlevel% equ 0 (
    set FAIL_COUNT=0
) else (
    set /a FAIL_COUNT+=1
)

REM 연속 5회 실패 시 무한 루프 방지 — 사용자에게 알리고 대기
if %FAIL_COUNT% geq 5 (
    echo.
    echo [%date% %time%] 에이전트가 연속 5회 시작 실패했습니다.
    echo 포트 충돌, 설정 오류 등을 확인해주세요.
    echo 아무 키나 누르면 다시 시도합니다...
    set FAIL_COUNT=0
    pause >nul
)

echo [%date% %time%] HTS Agent stopped. Restarting in 3s...
timeout /t 3 /nobreak >nul
goto loop
