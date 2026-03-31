@echo off
cd /d "C:\mume-agent"
call "C:\mume-agent\.venv\Scripts\activate.bat"

:loop
echo [%date% %time%] HTS Agent 시작...
python -m uvicorn hts_agent:app --host 0.0.0.0 --port 9000 --no-use-colors --no-access-log
echo [%date% %time%] HTS Agent 종료됨. 3초 후 재시작...
timeout /t 3 /nobreak >nul
goto loop
