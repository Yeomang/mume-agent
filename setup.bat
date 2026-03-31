@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

echo ============================================
echo   무한매수법 HTS 자동매매 에이전트 설치
echo ============================================
echo.

:: ─────────────────────────────────────
:: 관리자 권한 확인
:: ─────────────────────────────────────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [오류] 관리자 권한으로 실행해주세요.
    echo        이 파일을 우클릭 → "관리자 권한으로 실행"
    pause
    exit /b 1
)

set INSTALL_DIR=C:\mume-agent
set RELEASE_URL=https://github.com/Yeomang/mume-agent/releases/download/latest/mume-agent.zip
set PYTHON_VERSION=3.12.8
set PYTHON_INSTALLER=python-%PYTHON_VERSION%-amd64.exe
set PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/%PYTHON_INSTALLER%

:: ─────────────────────────────────────
:: 1) Python 설치 확인
:: ─────────────────────────────────────
echo [1/7] Python 설치 확인 중...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo       Python이 설치되어 있지 않습니다. 자동 설치합니다...
    echo       다운로드 중: %PYTHON_URL%

    powershell -Command "Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%TEMP%\%PYTHON_INSTALLER%'" 2>nul
    if not exist "%TEMP%\%PYTHON_INSTALLER%" (
        echo [오류] Python 다운로드 실패. 인터넷 연결을 확인해주세요.
        pause
        exit /b 1
    )

    echo       Python 설치 중 (PATH 자동 등록)...
    "%TEMP%\%PYTHON_INSTALLER%" /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1
    if %errorlevel% neq 0 (
        echo [오류] Python 설치 실패.
        pause
        exit /b 1
    )

    :: PATH 갱신
    set "PATH=C:\Program Files\Python312;C:\Program Files\Python312\Scripts;%PATH%"
    del "%TEMP%\%PYTHON_INSTALLER%" >nul 2>&1
    echo       Python 설치 완료!
) else (
    for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo       %%v 감지됨
)
echo.

:: ─────────────────────────────────────
:: 2) 설치 디렉터리 생성
:: ─────────────────────────────────────
echo [2/7] 설치 디렉터리 준비 중...
if exist "%INSTALL_DIR%" (
    echo       기존 설치가 감지되었습니다. 코드만 업데이트합니다.
) else (
    mkdir "%INSTALL_DIR%"
    echo       %INSTALL_DIR% 생성 완료
)
echo.

:: ─────────────────────────────────────
:: 3) 최신 코드 다운로드 & 압축 해제
:: ─────────────────────────────────────
echo [3/7] 최신 코드 다운로드 중...
set ZIP_PATH=%TEMP%\mume-agent.zip
set EXTRACT_PATH=%TEMP%\mume-agent-extract

powershell -Command "Invoke-WebRequest -Uri '%RELEASE_URL%' -OutFile '%ZIP_PATH%'" 2>nul
if not exist "%ZIP_PATH%" (
    echo [오류] 코드 다운로드 실패. 인터넷 연결을 확인해주세요.
    pause
    exit /b 1
)

if exist "%EXTRACT_PATH%" rmdir /s /q "%EXTRACT_PATH%"
powershell -Command "Expand-Archive -Path '%ZIP_PATH%' -DestinationPath '%EXTRACT_PATH%' -Force"

:: .py, .bat, requirements.txt 파일만 복사
echo       코드 파일 복사 중...
for %%f in ("%EXTRACT_PATH%\*.py") do copy /y "%%f" "%INSTALL_DIR%\" >nul
for %%f in ("%EXTRACT_PATH%\*.bat") do (
    :: setup.bat 자신은 복사하지 않음
    if /i not "%%~nxf"=="setup.bat" copy /y "%%f" "%INSTALL_DIR%\" >nul
)
if exist "%EXTRACT_PATH%\requirements.txt" copy /y "%EXTRACT_PATH%\requirements.txt" "%INSTALL_DIR%\" >nul

:: 정리
del "%ZIP_PATH%" >nul 2>&1
rmdir /s /q "%EXTRACT_PATH%" >nul 2>&1
echo       코드 다운로드 완료!
echo.

:: ─────────────────────────────────────
:: 4) 가상환경 생성 & 의존성 설치
:: ─────────────────────────────────────
echo [4/7] Python 가상환경 및 의존성 설치 중...
if not exist "%INSTALL_DIR%\.venv" (
    python -m venv "%INSTALL_DIR%\.venv"
    echo       가상환경 생성 완료
)

call "%INSTALL_DIR%\.venv\Scripts\activate.bat"
pip install -r "%INSTALL_DIR%\requirements.txt" --quiet --disable-pip-version-check
echo       의존성 설치 완료!
echo.

:: ─────────────────────────────────────
:: 5) .env 파일 설정
:: ─────────────────────────────────────
echo [5/7] 환경 설정...
if exist "%INSTALL_DIR%\.env" (
    echo       기존 .env 파일이 있습니다. 건너뜁니다.
) else (
    echo       환경 변수를 설정합니다.
    echo.

    set /p SUPABASE_URL="  Supabase URL: "
    set /p SUPABASE_KEY="  Supabase Key: "
    set /p AGENT_KEY="  에이전트 인증 키 (X-Agent-Key): "
    set /p TELEGRAM_CHAT_ID="  텔레그램 Chat ID (없으면 Enter): "
    set /p TELEGRAM_BOT_TOKEN_ORDER="  텔레그램 봇 토큰 - 주문 (없으면 Enter): "
    set /p TELEGRAM_BOT_TOKEN_EXECUTION="  텔레그램 봇 토큰 - 체결 (없으면 Enter): "

    (
        echo # HTS 설정
        echo HTS_EXE_PATH=C:\iMeritz\imeritz.exe
        echo HTS_WINDOW_NAME=iMeritz
        echo.
        echo # Supabase
        echo SUPABASE_URL=!SUPABASE_URL!
        echo SUPABASE_KEY=!SUPABASE_KEY!
        echo.
        echo # 에이전트 인증
        echo HTS_AGENT_KEY=!AGENT_KEY!
        echo.
        echo # 텔레그램
        echo TELEGRAM_CHAT_ID=!TELEGRAM_CHAT_ID!
        echo TELEGRAM_BOT_TOKEN_ORDER=!TELEGRAM_BOT_TOKEN_ORDER!
        echo TELEGRAM_BOT_TOKEN_EXECUTION=!TELEGRAM_BOT_TOKEN_EXECUTION!
    ) > "%INSTALL_DIR%\.env"

    echo       .env 파일 생성 완료!
)
echo.

:: ─────────────────────────────────────
:: 6) 방화벽 포트 9000 허용
:: ─────────────────────────────────────
echo [6/7] 방화벽 설정 중...
netsh advfirewall firewall show rule name="HTS Agent (Port 9000)" >nul 2>&1
if %errorlevel% neq 0 (
    netsh advfirewall firewall add rule name="HTS Agent (Port 9000)" dir=in action=allow protocol=TCP localport=9000 >nul
    echo       포트 9000 방화벽 규칙 추가 완료
) else (
    echo       포트 9000 방화벽 규칙 이미 존재
)
echo.

:: ─────────────────────────────────────
:: 7) 윈도우 스케줄러 작업 등록
:: ─────────────────────────────────────
echo [7/7] 윈도우 스케줄러 작업 등록 중...

:: 에이전트 자동 시작 (로그온 시)
schtasks /query /tn "MumeAgent_Startup" >nul 2>&1
if %errorlevel% neq 0 (
    schtasks /create /tn "MumeAgent_Startup" /tr "\"%INSTALL_DIR%\hts_agent.bat\"" /sc onlogon /rl highest /f >nul
    echo       [등록] MumeAgent_Startup (로그온 시 에이전트 자동 시작)
) else (
    echo       [존재] MumeAgent_Startup
)

:: 아침 작업 (07:30)
schtasks /query /tn "MumeAgent_Morning" >nul 2>&1
if %errorlevel% neq 0 (
    schtasks /create /tn "MumeAgent_Morning" /tr "\"%INSTALL_DIR%\main_morning.bat\"" /sc daily /st 07:30 /rl highest /f >nul
    echo       [등록] MumeAgent_Morning (매일 07:30)
) else (
    echo       [존재] MumeAgent_Morning
)

:: 저녁 작업 (22:30)
schtasks /query /tn "MumeAgent_Evening" >nul 2>&1
if %errorlevel% neq 0 (
    schtasks /create /tn "MumeAgent_Evening" /tr "\"%INSTALL_DIR%\main_evening.bat\"" /sc daily /st 22:30 /rl highest /f >nul
    echo       [등록] MumeAgent_Evening (매일 22:30)
) else (
    echo       [존재] MumeAgent_Evening
)

:: 시간외 작업 (06:10)
schtasks /query /tn "MumeAgent_Aftermarket" >nul 2>&1
if %errorlevel% neq 0 (
    schtasks /create /tn "MumeAgent_Aftermarket" /tr "\"%INSTALL_DIR%\main_aftermarket.bat\"" /sc daily /st 06:10 /rl highest /f >nul
    echo       [등록] MumeAgent_Aftermarket (매일 06:10)
) else (
    echo       [존재] MumeAgent_Aftermarket
)

:: 미체결 취소 작업 (23:50)
schtasks /query /tn "MumeAgent_CancelOrders" >nul 2>&1
if %errorlevel% neq 0 (
    schtasks /create /tn "MumeAgent_CancelOrders" /tr "\"%INSTALL_DIR%\main_cancel_orders.bat\"" /sc daily /st 23:50 /rl highest /f >nul
    echo       [등록] MumeAgent_CancelOrders (매일 23:50)
) else (
    echo       [존재] MumeAgent_CancelOrders
)

echo.

:: ─────────────────────────────────────
:: 완료
:: ─────────────────────────────────────
echo ============================================
echo   설치 완료!
echo ============================================
echo.
echo   설치 경로: %INSTALL_DIR%
echo   에이전트 포트: 9000
echo.
echo   스케줄:
echo     - 에이전트 시작: 로그온 시 자동
echo     - 시간외 매수:   매일 06:10
echo     - 아침 체결수집: 매일 07:30
echo     - 저녁 자동주문: 매일 22:30
echo     - 미체결 취소:   매일 23:50
echo.
echo   지금 에이전트를 시작하시겠습니까?
set /p START_NOW="  (Y/N): "
if /i "!START_NOW!"=="Y" (
    start "" "%INSTALL_DIR%\hts_agent.bat"
    echo   에이전트가 시작되었습니다!
)
echo.
pause
