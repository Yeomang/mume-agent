cd /d "C:\mume-agent"
call "C:\mume-agent\.venv\Scripts\activate.bat"
python -m uvicorn hts_agent:app --host 0.0.0.0 --port 9000 --no-use-colors --no-access-log
exit
