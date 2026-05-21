@echo off
cd /d "C:\mume-agent"
echo [%date% %time%] [evening] bat started >> "C:\mume-agent\log.log"
call "C:\mume-agent\.venv\Scripts\activate.bat"

:: Fix MumeAgent_Startup: onlogon -> onstart/SYSTEM (one-time)
schtasks /query /tn "MumeAgent_Startup" /fo csv /nh 2>nul | findstr /i "logon" >nul 2>&1
if %errorlevel% equ 0 (
    schtasks /delete /tn "MumeAgent_Startup" /f >nul 2>&1
    schtasks /create /tn "MumeAgent_Startup" /tr "C:\mume-agent\hts_agent.bat" /sc onstart /ru SYSTEM /rl highest /f >nul 2>&1
)

:: Start agent in background if not running
netstat -ano 2>nul | findstr ":9000.*LISTENING" >nul 2>&1
if %errorlevel% neq 0 (
    start "" "C:\mume-agent\hts_agent.bat"
    timeout /t 30 /nobreak >nul
)

start "" /high "C:\mume-agent\.venv\Scripts\python.exe" "C:\mume-agent\main_evening.py"
exit
