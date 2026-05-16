@echo off
cd /d "C:\mume-agent"
call "C:\mume-agent\.venv\Scripts\activate.bat"

:: 에이전트 자동시작 스케줄러를 onstart로 보정 (1회성 자동 수정)
schtasks /query /tn "MumeAgent_Startup" /fo csv /nh 2>nul | findstr /i "logon" >nul 2>&1
if %errorlevel% equ 0 (
    schtasks /delete /tn "MumeAgent_Startup" /f >nul 2>&1
    schtasks /create /tn "MumeAgent_Startup" /tr "C:\mume-agent\hts_agent.bat" /sc onstart /ru SYSTEM /rl highest /f >nul 2>&1
)

start "" /high "C:\mume-agent\.venv\Scripts\python.exe" "C:\mume-agent\main_morning.py"
exit