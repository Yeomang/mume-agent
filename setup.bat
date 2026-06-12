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
echo [1/8] Python 설치 확인 중...
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

    :: Downloads 폴더, OneDrive Downloads, 또는 TEMP에서 인스톨러 감지 대기
    :wait_python
    for %%D in ("%USERPROFILE%\Downloads" "%USERPROFILE%\OneDrive\Downloads" "%TEMP%") do (
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
echo       Python %PYTHON_VERSION% 설치 중 (1~2분 소요)...
"%PYTHON_DL_PATH%" /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1
if %errorlevel% neq 0 (
    echo [오류] Python 설치 실패.
    pause
    exit /b 1
)
set "PATH=C:\Program Files\Python310;C:\Program Files\Python310\Scripts;%PATH%"
set "PATH=C:\Python310;C:\Python310\Scripts;%PATH%"
set "PATH=%LOCALAPPDATA%\Programs\Python\Python310;%LOCALAPPDATA%\Programs\Python\Python310\Scripts;%PATH%"
del "%PYTHON_DL_PATH%" >nul 2>&1

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [오류] Python 설치 완료되었으나 PATH에서 찾을 수 없습니다.
    echo       이 창을 닫고 새 cmd 창에서 setup.bat을 다시 실행해주세요.
    pause
    exit /b 1
)
echo       Python %PYTHON_VERSION% 설치 완료!
echo.

:after_python

:: ─────────────────────────────────────
:: 2) 설치 디렉터리 생성
:: ─────────────────────────────────────
echo [2/8] 설치 디렉터리 준비 중...
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
echo [3/8] 최신 코드 다운로드 중...
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
echo [4/8] Python 가상환경 및 의존성 설치 중...
if not exist "%INSTALL_DIR%\.venv" (
    python -m venv "%INSTALL_DIR%\.venv"
    echo       가상환경 생성 완료
)

call "%INSTALL_DIR%\.venv\Scripts\activate.bat"
python -m pip install --upgrade pip --quiet --disable-pip-version-check 2>nul
pip install -r "%INSTALL_DIR%\requirements.txt" --quiet --disable-pip-version-check
if %errorlevel% neq 0 (
    echo [오류] 의존성 설치 실패. 인터넷 연결을 확인해주세요.
    pause
    exit /b 1
)
echo       의존성 설치 완료!
echo.

:: ─────────────────────────────────────
:: 5) .env 파일 설정
:: ─────────────────────────────────────
echo [5/8] 환경 설정...
if exist "%INSTALL_DIR%\.env" (
    echo       기존 .env 파일이 있습니다. 건너뜁니다.
) else (
    :: HTS 실행파일 자동 탐색
    set HTS_EXE=
    for %%P in (
        "C:\iMeritz\imeritz.exe"
        "C:\iMeritz XII\Main\imeritz.exe"
        "C:\Program Files\iMeritz\imeritz.exe"
        "C:\Program Files (x86)\iMeritz\imeritz.exe"
    ) do (
        if exist %%P (
            set "HTS_EXE=%%~P"
        )
    )
    if "!HTS_EXE!"=="" (
        for /f "tokens=*" %%F in ('dir /s /b C:\imeritz.exe 2^>nul ^| findstr /i imeritz.exe') do (
            set "HTS_EXE=%%F"
        )
    )
    if "!HTS_EXE!"=="" (
        set "HTS_EXE=C:\iMeritz\imeritz.exe"
        echo       [경고] HTS가 설치되어 있지 않습니다.
        echo       HTS 설치 후 %INSTALL_DIR%\.env 파일의 HTS_EXE_PATH를 수정해주세요.
    ) else (
        echo       HTS 감지: !HTS_EXE!
    )

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
        echo HTS_EXE_PATH=!HTS_EXE!
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
echo [6/8] 방화벽 설정 중...
netsh advfirewall firewall show rule name="HTS Agent (Port 9000)" >nul 2>&1
if %errorlevel% neq 0 (
    netsh advfirewall firewall add rule name="HTS Agent (Port 9000)" dir=in action=allow protocol=TCP localport=9000 >nul
    echo       포트 9000 방화벽 규칙 추가 완료
) else (
    echo       포트 9000 방화벽 규칙 이미 존재
)
echo.

:: ─────────────────────────────────────
:: 7) 자동 로그인 설정
:: ─────────────────────────────────────
echo [7/8] 자동 로그인 설정...
echo.
echo   ┌──────────────────────────────────────────────────────────────┐
echo   │                                                              │
echo   │   윈도우 업데이트 등으로 서버가 재시작될 때,                      │
echo   │   자동으로 로그인되도록 설정합니다.                               │
echo   │                                                              │
echo   │   이 설정이 있어야 사용자가 직접 접속하지 않아도                    │
echo   │   에이전트가 자동으로 다시 켜집니다.                              │
echo   │                                                              │
echo   ├──────────────────────────────────────────────────────────────┤
echo   │                                                              │
echo   │   ▶ 어떤 비밀번호를 입력해야 하나요?                             │
echo   │                                                              │
echo   │     원격 데스크톱(RDP)으로 이 서버에 접속할 때                    │
echo   │     입력하는 Administrator 계정의 비밀번호입니다.                 │
echo   │                                                              │
echo   │     확인 방법:                                                │
echo   │       내 PC에서 "원격 데스크톱 연결" 실행                        │
echo   │       → 서버 IP 주소 입력 후 연결                               │
echo   │       → 사용자 이름: Administrator                             │
echo   │       → 비밀번호: [바로 그 비밀번호]                             │
echo   │                                                              │
echo   │   ※ 비밀번호는 이 서버 안에만 저장되며 외부로 전송되지 않습니다.     │
echo   │   ※ 그냥 Enter를 누르면 이 단계를 건너뜁니다.                    │
echo   │     (나중에 setup.bat을 다시 실행하여 설정할 수 있습니다.)         │
echo   │                                                              │
echo   └──────────────────────────────────────────────────────────────┘
echo.
set /p AUTO_LOGON_PWD="  Administrator 비밀번호: "
echo.

if "!AUTO_LOGON_PWD!"=="" (
    echo       [건너뜀] 자동 로그인 설정을 건너뜁니다.
    echo       서버 재시작 시 에이전트가 자동으로 켜지려면
    echo       나중에 setup.bat을 다시 실행해 이 단계를 완료해주세요.
    set AUTO_LOGON_CONFIGURED=0
) else (
    reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v AutoAdminLogon /t REG_SZ /d "1" /f >nul
    reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultUserName /t REG_SZ /d "Administrator" /f >nul
    reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultPassword /t REG_SZ /d "!AUTO_LOGON_PWD!" /f >nul
    reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultDomainName /t REG_SZ /d "." /f >nul
    echo       자동 로그인 설정 완료!
    echo       서버 재시작 시 Administrator로 자동 로그인됩니다.
    set AUTO_LOGON_CONFIGURED=1
)
echo.

:: ─────────────────────────────────────
:: 8) 윈도우 스케줄러 작업 등록
:: ─────────────────────────────────────
echo [8/8] 윈도우 스케줄러 작업 등록 중...

:: 에이전트 자동 시작 (로그인 시)
schtasks /delete /tn "MumeAgent_Startup" /f >nul 2>&1
if "!AUTO_LOGON_CONFIGURED!"=="1" (
    :: 자동 로그인 설정 완료: Administrator 계정으로 로그인 시 실행 (HTS GUI 접근 가능)
    schtasks /create /tn "MumeAgent_Startup" /tr "\"%INSTALL_DIR%\hts_agent.bat\"" /sc onlogon /ru Administrator /rp "!AUTO_LOGON_PWD!" /rl highest /f >nul
    echo       [등록] MumeAgent_Startup (로그인 시 자동 실행 / Administrator 계정)
) else (
    :: 자동 로그인 미설정: 시스템 시작 시 SYSTEM 계정으로 실행 (임시)
    schtasks /create /tn "MumeAgent_Startup" /tr "\"%INSTALL_DIR%\hts_agent.bat\"" /sc onstart /ru SYSTEM /rl highest /f >nul
    echo       [등록] MumeAgent_Startup (시스템 시작 시 / SYSTEM 계정 - 임시)
    echo       [주의] 자동 로그인 미설정으로 HTS GUI 자동화가 제한될 수 있습니다.
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
if "!AUTO_LOGON_CONFIGURED!"=="1" (
    echo     - 에이전트 시작: 서버 재시작 후 자동 로그인 시 자동 실행
) else (
    echo     - 에이전트 시작: 시스템 부팅 시 자동 [주의: 자동 로그인 미설정]
)
echo     - 시간외 매수:   화수목금토 06:10
echo     - 아침 체결수집: 화수목금토 08:10
echo     - 저녁 자동주문: 월화수목금 18:10
echo     - 미체결 취소:   웹콘솔에서 수동 실행
echo.
if "!AUTO_LOGON_CONFIGURED!"=="0" (
    echo   ╔══════════════════════════════════════════════════╗
    echo   ║  [중요] 자동 로그인이 설정되지 않았습니다.           ║
    echo   ║  서버 재시작 후 에이전트가 자동으로 켜지려면           ║
    echo   ║  setup.bat을 다시 실행하여 7단계를 완료해주세요.      ║
    echo   ╚══════════════════════════════════════════════════╝
    echo.
)
echo   지금 에이전트를 시작하시겠습니까?
set /p START_NOW="  (Y/N): "
if /i "!START_NOW!"=="Y" (
    start "" "%INSTALL_DIR%\hts_agent.bat"
    echo   에이전트가 시작되었습니다!
)
echo.
pause
