@echo off
cd /d "C:\mume-agent"
call "C:\mume-agent\.venv\Scripts\activate.bat"

:: 에이전트 자동시작 스케줄러를 onstart로 보정 (1회성 자동 수정)
schtasks /query /tn "MumeAgent_Startup" /fo csv /nh 2>nul | findstr /i "logon" >nul 2>&1
if %errorlevel% equ 0 (
    schtasks /delete /tn "MumeAgent_Startup" /f >nul 2>&1
    schtasks /create /tn "MumeAgent_Startup" /tr "C:\mume-agent\hts_agent.bat" /sc onstart /ru SYSTEM /rl highest /f >nul 2>&1
)

:: 에이전트가 안 떠있으면 백그라운드로 기동 (시작 시 자동 업데이트 포함)
netstat -ano 2>nul | findstr ":9000.*LISTENING" >nul 2>&1
if %errorlevel% neq 0 start "" "C:\mume-agent\hts_agent.bat"

:: 에이전트 준비 대기 (최대 60초 폴링)
set /a _W=0
:_wait_agent_evening
netstat -ano 2>nul | findstr ":9000.*LISTENING" >nul 2>&1
if %errorlevel% equ 0 goto _agent_ready_evening
set /a _W+=1
if %_W% geq 12 goto _agent_ready_evening
timeout /t 5 /nobreak >nul
goto _wait_agent_evening
:_agent_ready_evening
set _W=

start "" /high "C:\mume-agent\.venv\Scripts\python.exe" "C:\mume-agent\main_evening.py"
exit
