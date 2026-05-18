@echo off
cd /d "C:\mume-agent"
call "C:\mume-agent\.venv\Scripts\activate.bat"

:: 에이전트 자동시작 스케줄러를 onstart로 보정 (1회성 자동 수정)
schtasks /query /tn "MumeAgent_Startup" /fo csv /nh 2>nul | findstr /i "logon" >nul 2>&1
if %errorlevel% equ 0 (
    schtasks /delete /tn "MumeAgent_Startup" /f >nul 2>&1
    schtasks /create /tn "MumeAgent_Startup" /tr "C:\mume-agent\hts_agent.bat" /sc onstart /ru SYSTEM /rl highest /f >nul 2>&1
)

:: 에이전트가 안 떠있으면 백그라운드로 기동
netstat -ano 2>nul | findstr ":9000.*LISTENING" >nul 2>&1
if %errorlevel% neq 0 (
    start "" "C:\mume-agent\hts_agent.bat"
    timeout /t 5 /nobreak >nul
)

start "" /high "C:\mume-agent\.venv\Scripts\python.exe" "C:\mume-agent\main_evening.py"
exit