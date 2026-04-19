@echo off
cd /d "C:\mume-agent"
call "C:\mume-agent\.venv\Scripts\activate.bat"

:loop
echo [%date% %time%] HTS Agent start...
python -m uvicorn hts_agent:app --host 0.0.0.0 --port 9000 --no-use-colors --no-access-log
echo [%date% %time%] HTS Agent stopped. Restarting in 3s...
timeout /t 3 /nobreak >nul
goto loop
