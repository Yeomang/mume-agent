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
set RELEASE_URL=https://github.com/Yeomang/mume-agent/releases/download/current/mume-agent.zip
set PYTHON_VERSION=3.10.7
set PYTHON_INSTALLER=python-%PYTHON_VERSION%-amd64.exe
set PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/%PYTHON_INSTALLER%
set PYTHON_DL_PATH=%TEMP%\%PYTHON_INSTALLER%

:: ─────────────────────────────────────
:: 1) Python 설치 확인
:: ─────────────────────────────────────
echo [1/7] Python 설치 확인 중...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo       Python이 설치되어 있지 않습니다. 다운로드 중...
    echo.

    :: --- 방법 1: curl ---
    curl.exe --connect-timeout 15 --max-time 300 -sL -o "%PYTHON_DL_PATH%" "%PYTHON_URL%" 2>nul
    if exist "%PYTHON_DL_PATH%" (
        for %%A in ("%PYTHON_DL_PATH%") do if %%~zA GTR 1000 goto :python_install
        del "%PYTHON_DL_PATH%" >nul 2>&1
    )
    echo       [1/3] curl 다운로드 실패. PowerShell 시도 중...

    :: --- 방법 2: PowerShell ---
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile('%PYTHON_URL%','%PYTHON_DL_PATH%')" 2>nul
    if exist "%PYTHON_DL_PATH%" (
        for %%A in ("%PYTHON_DL_PATH%") do if %%~zA GTR 1000 goto :python_install
        del "%PYTHON_DL_PATH%" >nul 2>&1
    )
    echo       [2/3] PowerShell 다운로드 실패. bitsadmin 시도 중...

    :: --- 방법 3: bitsadmin ---
    bitsadmin /transfer "PythonDownload" /download /priority foreground "%PYTHON_URL%" "%PYTHON_DL_PATH%" >nul 2>&1
    if exist "%PYTHON_DL_PATH%" (
        for %%A in ("%PYTHON_DL_PATH%") do if %%~zA GTR 1000 goto :python_install
        del "%PYTHON_DL_PATH%" >nul 2>&1
    )
    echo       [3/3] bitsadmin 다운로드 실패.

    :: --- 최후 수단: 브라우저 ---
    echo.
    echo       ============================================
    echo       자동 다운로드에 실패했습니다.
    echo       브라우저에서 Python 설치파일을 다운로드합니다...
    echo       ============================================
    echo.
    start "" "%PYTHON_URL%"
    echo       브라우저가 열렸습니다. 다운로드를 기다리는 중...
    echo       (파일명: %PYTHON_INSTALLER%)
    echo.

    :: Downloads 폴더 또는 TEMP에서 인스톨러 감지 대기
    set DOWNLOAD_DIRS="%USERPROFILE%\Downloads" "%TEMP%"
    :wait_python
    for %%D in (%DOWNLOAD_DIRS%) do (
        if exist "%%~D\%PYTHON_INSTALLER%" (
            copy /y "%%~D\%PYTHON_INSTALLER%" "%PYTHON_DL_PATH%" >nul
            echo       다운로드 감지!
            goto :python_install
        )
    )
    timeout /t 2 /nobreak >nul
    goto :wait_python
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo       %%v 감지됨
echo.
goto :after_python

:python_install
echo       Python %PYTHON_VERSION% 설치 중...
"%PYTHON_DL_PATH%" /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1
if %errorlevel% neq 0 (
    echo [오류] Python 설치 실패.
    pause
    exit /b 1
)
set "PATH=C:\Program Files\Python310;C:\Program Files\Python310\Scripts;%PATH%"
del "%PYTHON_DL_PATH%" >nul 2>&1
echo       Python %PYTHON_VERSION% 설치 완료!
echo.

:after_python

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

python -c "import urllib.request,sys; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" "%RELEASE_URL%" "%ZIP_PATH%"
if not exist "%ZIP_PATH%" (
    echo [오류] 코드 다운로드 실패. 인터넷 연결을 확인해주세요.
    pause
    exit /b 1
)

if exist "%EXTRACT_PATH%" rmdir /s /q "%EXTRACT_PATH%"
python -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "%ZIP_PATH%" "%EXTRACT_PATH%"

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

:: 아침 작업 (화수목금토 08:10)
schtasks /query /tn "MumeAgent_Morning" >nul 2>&1
if %errorlevel% neq 0 (
    schtasks /create /tn "MumeAgent_Morning" /tr "\"%INSTALL_DIR%\main_morning.bat\"" /sc weekly /d TUE,WED,THU,FRI,SAT /st 08:10 /rl highest /f >nul
    echo       [등록] MumeAgent_Morning (화수목금토 08:10)
) else (
    echo       [존재] MumeAgent_Morning
)

:: 저녁 작업 (월화수목금 18:10)
schtasks /query /tn "MumeAgent_Evening" >nul 2>&1
if %errorlevel% neq 0 (
    schtasks /create /tn "MumeAgent_Evening" /tr "\"%INSTALL_DIR%\main_evening.bat\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 18:10 /rl highest /f >nul
    echo       [등록] MumeAgent_Evening (월화수목금 18:10)
) else (
    echo       [존재] MumeAgent_Evening
)

:: 시간외 작업 (화수목금토 06:10)
schtasks /query /tn "MumeAgent_Aftermarket" >nul 2>&1
if %errorlevel% neq 0 (
    schtasks /create /tn "MumeAgent_Aftermarket" /tr "\"%INSTALL_DIR%\main_aftermarket.bat\"" /sc weekly /d TUE,WED,THU,FRI,SAT /st 06:10 /rl highest /f >nul
    echo       [등록] MumeAgent_Aftermarket (화수목금토 06:10)
) else (
    echo       [존재] MumeAgent_Aftermarket
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
echo     - 시간외 매수:   화수목금토 06:10
echo     - 아침 체결수집: 화수목금토 08:10
echo     - 저녁 자동주문: 월화수목금 18:10
echo     - 미체결 취소:   웹콘솔에서 수동 실행
echo.
echo   지금 에이전트를 시작하시겠습니까?
set /p START_NOW="  (Y/N): "
if /i "!START_NOW!"=="Y" (
    start "" "%INSTALL_DIR%\hts_agent.bat"
    echo   에이전트가 시작되었습니다!
)
echo.
pause
