# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 응답 지침

모든 결과값과 설명은 반드시 한글로 작성한다.

## Overview

무한매수법 HTS 자동매매 에이전트. Windows 환경에서 메리츠증권 HTS(iMeritz)를 GUI 자동화하여 해외주식 주문을 실행하고, 체결 데이터를 수집하여 Supabase와 동기화하는 시스템.

웹 콘솔(mume-console)에서 HTTP API로 제어하며, 4가지 작업(morning/evening/aftermarket/cancel_orders)을 스케줄 또는 수동으로 실행.

## Commands

```bash
# 에이전트 HTTP 서버 실행 (포트 9000)
uvicorn hts_agent:app --host 0.0.0.0 --port 9000

# 개별 작업 직접 실행 (테스트)
python main_evening.py
python main_morning.py
python main_aftermarket.py
python main_cancel_orders.py

# 의존성 설치
pip install -r requirements.txt
```

## Architecture

### 진입점
- `hts_agent.py` — FastAPI HTTP 서버 (웹콘솔 → 에이전트 통신)
- `main_evening.py` — 저녁 작업: 잔고 조회 → Supabase 주문 실행
- `main_morning.py` — 아침 작업: 체결내역 수집 → 전처리 → Supabase 동기화
- `main_aftermarket.py` — 시간외 작업: 추가 매수 실행
- `main_cancel_orders.py` — 미체결 주문 일괄 취소

### API 엔드포인트 (hts_agent.py)
| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/health` | 헬스체크 |
| POST | `/run?job=<type>` | 작업 실행 (morning/evening/aftermarket/cancel_orders) |
| POST | `/stop?job=<type>` | 작업 중지 + HTS 프로세스 종료 + 입력 잠금 해제 |
| GET | `/status?job=<type>` | 작업 상태 조회 (never_run/running/success/error/stopped) |
| GET | `/logs` | 로그 tail 조회 |
| GET | `/processes` | HTS/Python 프로세스 상태 조회 |
| GET | `/password-status?users=<csv>` | 비밀번호 설정 여부 확인 |
| POST | `/update-passwords` | 비밀번호 저장 (Windows Credential Manager) |
| POST | `/delete-passwords` | 비밀번호 삭제 |
| POST | `/deploy` | 원격 배포 (GitHub Release zip 다운로드 & 적용) |
| GET | `/deploy-status` | 현재 배포 버전 정보 조회 |

모든 엔드포인트는 `X-Agent-Key` 헤더로 인증. `/run`은 subprocess를 생성하며, `JOB_NAME`, `JOB_USER_ACCOUNTS`, `JOB_TEST_MODE`, `JOB_DATE_FROM`, `JOB_DATE_TO` 환경변수를 전달.

### 작업 실행 흐름
```
웹 콘솔 → POST /run?job=evening → hts_agent.py
  → subprocess 생성 (main_evening.py)
    → load_automation_target() (사용자/계좌 목록)
    → 사용자별 루프:
      → hts_login() (HTS 로그인)
      → 계좌별 루프:
        → HTS GUI 자동화 (주문/데이터수집)
      → kill_window_by_title() (HTS 종료)
```

### HTS GUI 자동화 패턴
모든 HTS 모듈은 동일한 패턴을 따름:
1. `block_input(True)` — 마우스/키보드 잠금
2. `win32gui.FindWindow()` → `setup_window()` — HTS 창 찾기 및 포커싱
3. `Application(backend="uia").connect()` — pywinauto 연결
4. `find_control_by_criteria()` — UI 컨트롤 탐색 (automation_id 우선, title 폴백)
5. `set_focus_and_type()` / `click_input()` — 입력 및 클릭
6. `block_input(False)` — 잠금 해제 (finally 블록에서 항상 실행)

### 데이터 파이프라인
```
HTS 화면 → CSV 내보내기 → pandas 전처리 → Supabase 업데이트
  [6114] 체결내역    → all_order_execution_raw/    → all_order_execution_processed/
  [6100] 주문체결탭  → order_history_raw/           → order_history_processed/
  [6104] 보유잔고    → stock_balance_raw/            → stock_balance_processed/
```

### 주요 모듈
| 파일 | 역할 |
|------|------|
| `hts_login.py` | HTS 실행 + 공동인증서 로그인 |
| `hts_order_buy.py` | 해외주식 매수 주문 ([6100] 화면) |
| `hts_order_sell.py` | 해외주식 매도 주문 ([6100] 화면) |
| `hts_cancel_orders.py` | 미체결 주문 일괄 취소 ([6100] 미체결 탭) |
| `hts_orders_from_supabase.py` | Supabase에서 주문 데이터 읽어 HTS 실행 |
| `hts_orders_aftermarket.py` | 시간외 추가 매수 로직 |
| `hts_orders_execution_save_to_csv.py` | 체결내역 CSV 저장 ([6114] 화면) |
| `hts_orders_history_save_to_csv.py` | 주문내역 CSV 저장 ([6100] 주문체결 탭) |
| `hts_stock_balance_save_to_csv.py` | 보유잔고 CSV 저장 ([6104] 화면) |
| `order_execution_data_preprocessing.py` | 체결내역 CSV 전처리 (이중 헤더 처리) |
| `order_history_data_preprocessing.py` | 주문내역 CSV 전처리 |
| `stock_balance_data_preprocessing.py` | 잔고 CSV 전처리 |
| `order_execution_update_supabase.py` | 전처리된 체결 데이터 → Supabase 동기화 |

### 설정 및 인프라
| 파일 | 역할 |
|------|------|
| `config.py` | .env 환경변수 로드 (HTS 경로, Supabase, Telegram 등) |
| `secrets_manager.py` | Windows Credential Manager로 비밀번호 안전 저장 |
| `supabase_client.py` | Supabase 클라이언트 싱글톤 + 페이지네이션 |
| `utils.py` | 윈도우 관리, UI 컨트롤, 프로세스 관리, 텔레그램, 거래일 판별 |
| `job_control.py` | 작업 PID 추적 (pids/ 디렉터리) |
| `automation_target_store.py` | 자동화 대상 사용자/계좌 로컬 저장 (automation_targets.json) |

### HTS 화면번호
- `6100` — 해외주식 주문 (매수/매도/미체결/주문체결)
- `6104` — 해외주식 보유잔고
- `6114` — 해외주식 주문체결내역

### Environment
`.env`에서 로드:
- `HTS_EXE_PATH` — iMeritz 실행 파일 경로
- `HTS_WINDOW_NAME` — HTS 창 제목 (기본: "iMeritz")
- `SUPABASE_URL`, `SUPABASE_KEY` — 데이터베이스
- `TELEGRAM_BOT_TOKEN_ORDER`, `TELEGRAM_BOT_TOKEN_EXECUTION` — 텔레그램 알림
- `TELEGRAM_CHAT_ID` — 텔레그램 채팅 ID
- `HTS_AGENT_KEY` — API 인증 키 (X-Agent-Key 헤더)
- `CONSOLE_URL` — 웹 콘솔 URL

### Data Directory
```
data/
├── all_order_execution_raw/        # HTS 체결내역 원본 CSV
├── all_order_execution_processed/  # 전처리된 체결내역
├── order_history_raw/              # HTS 주문내역 원본 CSV
├── order_history_processed/        # 전처리된 주문내역
├── stock_balance_raw/              # HTS 잔고 원본 CSV
└── stock_balance_processed/        # 전처리된 잔고
pids/                               # 작업 PID 파일
automation_targets.json             # 자동화 대상 설정
log.log                             # 애플리케이션 로그
```

### Key Patterns

**자격 증명**: Windows Credential Manager(keyring)에 저장. 코드/파일에 비밀번호 절대 저장하지 않음.

**프로세스 격리**: 각 작업은 별도 subprocess로 실행. 독립적 실행/중단 가능.

**테스트 모드**: `is_test_mode=True` 시 실제 주문 대신 "닫기" 버튼 클릭. 주문 확인 팝업까지만 진행.

**에러 복구**: finally 블록에서 반드시 `block_input(False)` 호출. 예외 발생 시에도 마우스/키보드 잠금 해제.

**텔레그램 알림**: 주문 실행 결과를 실시간으로 텔레그램에 전송.

### 배포 파이프라인
```
git push main → GitHub Actions → Release zip 생성 → webhook → mume-console → 각 서버 POST /deploy → zip 다운로드 & 적용
```
- `setup.bat` — 윈도우 서버 원클릭 설치 스크립트 (Python, 가상환경, 의존성, .env, 방화벽, 스케줄러 모두 자동 설정)
- `.github/workflows/release-deploy.yml` — push 시 자동 Release 생성 + 콘솔 webhook 트리거
- `POST /deploy` — 에이전트가 Release zip을 다운로드하여 .py/.bat 파일만 업데이트
- `hts_agent.bat` — 무한루프 래퍼. 에이전트 프로세스 종료 시 자동 재시작

### 크로스 플랫폼 참고사항
코드는 macOS에서 import/편집 가능하도록 `platform.system() == "Windows"` 분기 처리가 되어 있으나, **실제 GUI 자동화 실행은 Windows 전용**. `pywinauto`, `win32gui`, `block_input(ctypes.windll)` 등은 Windows에서만 동작.

### 관련 프로젝트
- `mume-console` (`/Users/pio/Documents/mume-console`) — 웹 콘솔 (FastAPI + Supabase + 프론트엔드)
