@echo off
cd /d "C:\mume-agent"
call "C:\mume-agent\.venv\Scripts\activate.bat"

:: 에이전트가 안 떠있으면 백그라운드로 기동 (시작 시 자동 업데이트 포함)
netstat -ano 2>nul | findstr ":9000.*LISTENING" >nul 2>&1
if %errorlevel% neq 0 start "" "C:\mume-agent\hts_agent.bat"

:: 에이전트 준비 대기 (최대 60초 폴링)
set /a _W=0
:_wait_agent_cancel
netstat -ano 2>nul | findstr ":9000.*LISTENING" >nul 2>&1
if %errorlevel% equ 0 goto _agent_ready_cancel
set /a _W+=1
if %_W% geq 12 goto _agent_ready_cancel
timeout /t 5 /nobreak >nul
goto _wait_agent_cancel
:_agent_ready_cancel
set _W=

python main_cancel_orders.py
pause
