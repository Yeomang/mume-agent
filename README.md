# mume-agent

무한매수법 자동매매 에이전트. Windows 환경에서 메리츠증권 HTS(iMeritz)를 GUI 자동화하여 해외주식 주문을 자동 실행하고, 체결 데이터를 수집해 Supabase와 동기화한다.

웹 콘솔([mume-console](../mume-console))에서 HTTP API로 제어하며, 스케줄 또는 수동으로 작업을 실행한다.

> **최종 업데이트:** 2026-08-03

---

## 변경 히스토리

| 날짜 | 내용 |
|------|------|
| 2026-08-03 | main_evening.py: `account_cash_balance` upsert에서 `deposit`/`krw_value`/`withdrawable` 필드 제거 — mume-console 쪽 Supabase 테이블 필드 전수 분석 중, 이 값들은 쓰기만 되고 콘솔·에이전트 어디서도 저장 이후 다시 읽지 않는 죽은 컬럼임을 확인(실제 화면·계산엔 `estimated_deposit`/`exchange_rate`만 사용됨). mume-console의 `042_drop_batch_dead_columns.sql`로 해당 컬럼 자체도 제거 |
| 2026-08-01 | V4.0 리버스모드 매도/매수 주문가(직전 5거래일 종가평균 기반)가 실시간가 대비 사이클 설정값(`dip_buy_rate`, 기본 15%)을 넘게 벌어지면 브로커 거부(NBBO 이탈)를 막기 위해 실시간가 기준으로 자동 보정하는 `_apply_reverse_price_guard()` 추가(`hts_orders_from_supabase.py`) — 2026-07-31 사이클 #294에서 매도 주문이 "96.54>24.88% Limit 20.00% thru nbbo" 사유로 브로커 거부됐는데 아무도 알지 못했던 사례가 계기. 저녁/애프터마켓 양쪽에서 브로커 거부("주문상태"="거부") 주문을 즉시 텔레그램으로 알림하도록 추가 — 기존엔 이 상태를 아예 안 봐서 거부돼도 조용히 넘어갔음. 짝을 이루는 mume-console 수정: V3.0 쿼터매도 진입 첫날 매도(MOC)+매수(LOC)가 동시에 나가던 버그(`calc_engine.py`) |
| 2026-07-29 | config.py: HTS_EXE_PATH 하드코딩 기본값을 실제 근거 없이 지어낸 값(C:\MeritzFire\iMeritz\imeritzmain.exe)에서 실제 확인된 설치 경로(C:\메리츠증권\iMERITZ XII\Main\imeritz.exe)로 수정. 새 서버(Lightsail)에 처음 배포할 때 imeritzmain.exe를 직접 실행하면 "실제 서버에서는 [imeritzmain] 로그인을 통하여 실행해주십시오" HTS 자체 팝업이 뜨며 멈추는 문제를 발견 — imeritz.exe(정식 런처)를 거친 적 없는 새 기기에서만 발생. 기존에 이미 DB(agent_settings.hts_exe_path)나 .env에 경로를 저장해둔 계정은 영향 없음, 신규 온보딩 시의 폴백 기본값만 변경됨 |
| 2026-07-28 | get_unfilled_tickers_dict: 그리드가 비어 팝업이 안 떴을 때 보내던 send_keys("{ESC}")가, 열려있는 팝업이 없는 상태라 [06100] 주문 창 자체를 닫아버려 그 직후 order_window.close()가 ElementNotVisible로 실패하던 버그 수정. ESC 전송 제거 + 모든 order_window.close() 호출을 실패해도 무시하도록 방어 |
| 2026-07-28 | get_unfilled_tickers_dict/hts_orders_from_supabase.py: 미체결 조회 실패 로그가 str(e)만 남겨 빈 문자열 예외(일부 COM 에러 등)의 원인을 알 수 없던 문제 수정. 예외 타입 + traceback을 함께 남기도록 로깅 보강 |
| 2026-07-28 | get_unfilled_tickers_dict: send_keys("{ESCAPE}") 오타 수정 → "{ESC}" (pywinauto에서 정의되지 않은 코드라 "Unknown code: ESCAPE" 예외 발생). 이전엔 wait_for_window 버그로 이 분기가 실행된 적이 없어 드러나지 않다가, 그 버그를 고치자마자 처음 실행되며 발견됨 |
| 2026-07-28 | hts_agent.py: /deploy가 실행 중인 작업 때문에 거부되면(409) 요청을 PENDING_DEPLOY로 기억해두고, 백그라운드 스레드(30초 주기)가 작업 종료를 감지해 자동으로 재적용하도록 개선. 이전엔 콘솔에서 수동으로 /deploy를 다시 눌러야 했음 |
| 2026-07-28 | get_unfilled_tickers_dict: wait_for_window가 못 찾을 때 예외를 던진다고 잘못 가정해 try/except로 감쌌던 버그 수정 (실제로는 None 반환 — utils.py:449). 이 때문에 미체결 그리드가 비어있어 저장창이 안 떠도 "정상 없음" 분기가 전혀 실행되지 않고 항상 "CSV 생성 안 됨" 실패로 빠지고 있었음. 리턴값을 직접 확인하는 방식으로 교체 |
| 2026-07-28 | get_unfilled_tickers_dict: 화면 진입/탭 클릭/그리드 우클릭/메뉴 선택 등 단계별 로그를 다른 CSV 저장 함수들과 동일한 수준으로 추가 (실패 시 정확히 어느 단계에서 멈췄는지 로그만으로 진단 가능하도록) |
| 2026-07-28 | get_unfilled_tickers_dict(미체결 조회): CSV 저장이 조용히 실패하며 며칠 전 파일을 계속 재사용해 미체결 오판(evening job 전체 스킵)이 8일간 지속되던 근본 버그 수정. 저장 전 기존 파일 삭제 + 실패 지점을 예외로 승격해 호출부(hts_orders_from_supabase.py)가 "확인된 없음"과 "확인 실패"를 구분해 안전 스킵하도록 변경. save_orders_history(주문내역 저장)에도 동일한 파일 삭제 적용 |
| 2026-07-27 | main_cancel_orders.py: logging.basicConfig() 호출 순서를 다른 import보다 앞으로 이동 (다른 모듈이 root logger에 먼저 핸들러를 붙여 log.log에 아무 로그도 안 남던 버그 수정) |
| 2026-06-27 | README 최초 작성 — 전체 아키텍처, 파일 구조, 작업 흐름, 데이터 파이프라인 문서화 |

---

## 목차

- [시스템 개요](#시스템-개요)
- [파일 구조](#파일-구조)
- [주요 컴포넌트](#주요-컴포넌트)
  - [HTTP API 서버 (hts_agent.py)](#http-api-서버-hts_agentpy)
  - [진입점 스크립트 (main_*.py)](#진입점-스크립트-main_py)
  - [HTS 자동화 모듈](#hts-자동화-모듈)
  - [데이터 파이프라인 모듈](#데이터-파이프라인-모듈)
  - [인프라 모듈](#인프라-모듈)
- [일별 작업 흐름](#일별-작업-흐름)
  - [저녁 작업 (Evening Job)](#저녁-작업-evening-job)
  - [아침 작업 (Morning Job)](#아침-작업-morning-job)
  - [시간외 작업 (Aftermarket Job)](#시간외-작업-aftermarket-job)
  - [미체결 취소 작업 (Cancel Orders Job)](#미체결-취소-작업-cancel-orders-job)
- [HTS GUI 자동화 패턴](#hts-gui-자동화-패턴)
- [데이터 파이프라인](#데이터-파이프라인)
- [설정 및 환경 변수](#설정-및-환경-변수)
- [보안](#보안)
- [배포 파이프라인](#배포-파이프라인)
- [실행 방법](#실행-방법)
- [크로스 플랫폼 참고사항](#크로스-플랫폼-참고사항)

---

## 시스템 개요

```
┌─────────────────────────────────────────────────────────────────┐
│                         mume-console (웹)                        │
│              FastAPI + Supabase + 프론트엔드 대시보드             │
└────────────────────────┬────────────────────────────────────────┘
                         │  HTTP API (X-Agent-Key 인증)
                         ▼  POST /run?job=evening 등
┌─────────────────────────────────────────────────────────────────┐
│                   hts_agent.py (포트 9000)                       │
│          FastAPI HTTP 서버 — 웹콘솔 ↔ 에이전트 브릿지            │
│  /run  /stop  /status  /logs  /processes  /deploy  /passwords   │
└──────────┬───────────────┬──────────────────┬───────────────────┘
           │ subprocess    │ subprocess        │ subprocess
           ▼               ▼                  ▼
    main_evening.py  main_morning.py   main_aftermarket.py
    main_cancel_orders.py
           │
           ▼
    ┌──────────────────────────────┐
    │  HTS (iMeritz) GUI 자동화    │
    │  pywinauto + win32gui        │
    │  [6100] 주문  [6104] 잔고    │
    │  [6114] 체결내역             │
    └──────────┬───────────────────┘
               │  CSV 내보내기
               ▼
    ┌──────────────────────────────┐
    │  전처리 (pandas)             │
    │  order_execution / balance   │
    └──────────┬───────────────────┘
               │  upsert/insert
               ▼
    ┌──────────────────────────────┐
    │       Supabase (DB)          │
    │  cycle_master / cycle_trades │
    │  account_stock_balance       │
    │  account_cash_balance        │
    └──────────────────────────────┘
```

---

## 파일 구조

```
mume-agent/
│
├── hts_agent.py                  # FastAPI HTTP 서버 (웹콘솔 ↔ 에이전트 통신)
│
├── main_evening.py               # 저녁 작업 진입점 (잔고조회 + 매도/매수 주문)
├── main_morning.py               # 아침 작업 진입점 (체결내역 수집 + Supabase 동기화)
├── main_aftermarket.py           # 시간외 작업 진입점 (추가 매수)
├── main_cancel_orders.py         # 미체결 주문 일괄 취소 진입점
├── main_refresh_balance.py       # 잔고 수동 갱신
│
├── hts_login.py                  # HTS 실행 + 공동인증서 로그인
├── hts_order_buy.py              # 해외주식 매수 주문 ([6100] 화면)
├── hts_order_sell.py             # 해외주식 매도 주문 ([6100] 화면)
├── hts_cancel_orders.py          # 미체결 주문 일괄 취소 ([6100] 미체결 탭)
├── hts_orders_from_supabase.py   # Supabase 주문 데이터 → HTS 주문 실행 (핵심 비즈니스 로직)
├── hts_orders_aftermarket.py     # 시간외 추가 매수 로직
├── hts_orders_execution_save_to_csv.py  # 체결내역 CSV 저장 ([6114] 화면)
├── hts_orders_history_save_to_csv.py    # 주문내역 CSV 저장 ([6100] 주문체결 탭)
├── hts_stock_balance_save_to_csv.py     # 보유잔고 CSV 저장 ([6104] 화면)
│
├── order_execution_data_preprocessing.py # 체결내역 CSV 전처리 (이중 헤더 처리)
├── order_history_data_preprocessing.py   # 주문내역 CSV 전처리
├── stock_balance_data_preprocessing.py   # 잔고 CSV 전처리
├── order_execution_update_supabase.py    # 전처리된 체결 데이터 → Supabase 동기화
│
├── config.py                     # 환경 변수 로드 + DB 설정 병합
├── secrets_manager.py            # Windows Credential Manager 래퍼 (비밀번호 관리)
├── supabase_client.py            # Supabase 클라이언트 싱글톤 + 페이지네이션
├── automation_target_store.py    # 자동화 대상 사용자/계좌 조회 (Supabase)
├── job_control.py                # 작업 PID 추적 (pids/ 디렉터리)
├── utils.py                      # 윈도우 관리, UI 컨트롤, 프로세스, 텔레그램, 거래일 판별
│
├── hts_agent.bat                 # 무한루프 래퍼 — 에이전트 종료 시 자동 재시작
├── main_morning.bat              # 아침 작업 직접 실행 bat
├── main_evening.bat              # 저녁 작업 직접 실행 bat
├── main_aftermarket.bat          # 시간외 작업 직접 실행 bat
├── main_cancel_orders.bat        # 미체결 취소 직접 실행 bat
├── setup.bat                     # 원클릭 Windows 설치 스크립트
│
├── requirements.txt              # Python 패키지 의존성
├── .env.example                  # 환경 변수 예시
├── .gitignore
│
├── data/                         # 런타임 데이터 (gitignore 대상)
│   ├── all_order_execution_raw/      # HTS 체결내역 원본 CSV
│   ├── all_order_execution_processed/ # 전처리된 체결내역
│   ├── order_history_raw/            # HTS 주문내역 원본 CSV
│   ├── order_history_processed/      # 전처리된 주문내역
│   ├── stock_balance_raw/            # HTS 잔고 원본 CSV
│   ├── stock_balance_processed/      # 전처리된 잔고
│   └── foreign_deposit_raw/          # 외화예수금 원본 CSV
│
└── pids/                         # 작업별 PID 파일 (running job 추적)
```

---

## 주요 컴포넌트

### HTTP API 서버 (hts_agent.py)

웹 콘솔과 에이전트 사이의 브릿지. FastAPI로 구현되며 포트 9000에서 실행된다.

모든 엔드포인트는 `X-Agent-Key` 헤더로 인증한다 (`/health` 제외). 키가 설정되지 않으면 `/health` 외 모든 요청을 차단한다 (타이밍 공격 방지를 위해 `hmac.compare_digest` 사용).

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/health` | 헬스체크 (인증 불필요) |
| POST | `/run?job=<type>` | 작업 실행 subprocess 생성 |
| POST | `/stop?job=<type>` | 작업 중지 + HTS 프로세스 종료 + 입력 잠금 해제 |
| GET | `/status?job=<type>` | 작업 상태 조회 (`never_run` / `running` / `success` / `error` / `stopped`) |
| GET | `/logs` | log.log tail 조회 |
| GET | `/processes` | HTS/Python 프로세스 상태 조회 |
| GET | `/password-status?users=<csv>` | 비밀번호 설정 여부 확인 |
| POST | `/update-passwords` | 비밀번호 저장 (Windows Credential Manager) |
| POST | `/delete-passwords` | 비밀번호 삭제 |
| POST | `/deploy` | 원격 배포 (GitHub Release zip 다운로드 & 적용) |
| GET | `/deploy-status` | 현재 배포 버전 정보 조회 |

**작업 실행 흐름 (`/run`):**

`/run` 호출 시 `subprocess.Popen`으로 별도 프로세스를 생성한다. 웹콘솔이 선택한 사용자/계좌/사이클 정보는 환경 변수로 전달된다.

```
환경 변수 전달 목록:
  JOB_NAME          — 작업 타입 (morning/evening/aftermarket/cancel_orders)
  JOB_USER_ACCOUNTS — 사용자별 계좌/사이클 JSON
  JOB_TEST_MODE     — "1" 이면 테스트 모드 (실주문 없음)
  JOB_DATE_FROM     — 조회 시작일 (morning/aftermarket)
  JOB_DATE_TO       — 조회 종료일
```

---

### 진입점 스크립트 (main_*.py)

각 작업 유형마다 독립적인 Python 스크립트가 존재한다. `/run` API 또는 `.bat` 파일로 직접 실행 가능하다.

모든 스크립트는 동일한 구조를 따른다:

```python
# 1. 로깅 설정 (가장 먼저 — import 실패도 기록)
logging.basicConfig(filename="log.log", ...)
sys.excepthook = log_uncaught_exceptions  # 미처리 예외도 로그에 기록

# 2. 모듈 import

# 3. run_*_job() 함수 — 실제 비즈니스 로직

# 4. main() — PID 등록 + run_*_job() 호출 + PID 해제
def main():
    register_job_pid("evening")
    try:
        run_evening_job(...)
    finally:
        unregister_job_pid("evening")
```

| 파일 | 작업 시점 | 주요 역할 |
|------|-----------|-----------|
| `main_evening.py` | 장 마감 후 저녁 | 잔고 조회 → 예수금 체크 → 매도/매수 주문 실행 |
| `main_morning.py` | 장 개시 전 아침 | 체결내역 수집 → 전처리 → Supabase 동기화 |
| `main_aftermarket.py` | 시간외 거래 시간 | 추가 매수 주문 실행 |
| `main_cancel_orders.py` | 수동 또는 긴급 | 미체결 주문 일괄 취소 |
| `main_refresh_balance.py` | 수동 | 잔고만 수동 갱신 |

---

### HTS 자동화 모듈

#### hts_login.py — HTS 실행 및 공동인증서 로그인

```
실행 흐름:
  1. imeritz.exe 기존 프로세스 강제 종료 (TASKKILL)
  2. 관리자 권한으로 HTS 재실행 (ShellExecuteEx "runas")
  3. "인증서 선택" 창이 뜰 때까지 대기 (최대 300초)
  4. 인증서 목록에서 "증권(개인)" + 사용자명 조건으로 항목 선택
  5. Windows Credential Manager에서 비밀번호 조회 후 입력
  6. "인증서 선택(확인)" 버튼 클릭
  7. "iMeritz" 메인 창 확인 + 창 포커싱/최대화
  8. ESC 10회 → 불필요한 팝업 닫기
  9. 데스크톱 세션 끊김 감지 시 2회까지 자동 재시도
```

#### hts_order_buy.py / hts_order_sell.py — 매수/매도 주문

HTS의 [6100] 해외주식 주문 화면을 GUI로 제어한다.

```
실행 흐름:
  1. 입력 잠금 (block_input(True))
  2. HTS 창 핸들 찾기 + 포커싱
  3. 화면번호 "6100" 입력 → 해외주식 주문 창 열기
  4. 계좌번호 드롭다운에서 account_index에 해당하는 계좌 선택
  5. 계좌 비밀번호 입력 (팝업 처리)
  6. 종목코드 입력
  7. 주문 유형 선택 (지정가/LOC/MOC 등)
  8. 수량 + 가격 입력
  9. F1(매수) / F2(매도) 단축키로 주문 실행
  10. 주문 확인 팝업에서 "매수/매도" 클릭 (테스트 모드에서는 "닫기" 클릭)
  finally: block_input(False) — 예외 시에도 반드시 잠금 해제
```

#### hts_cancel_orders.py — 미체결 주문 일괄 취소

```
실행 흐름:
  1. [6100] 해외주식 주문 창 열기
  2. 계좌 선택 + 비밀번호 입력
  3. 하단 [미체결] 탭 클릭
  4. 그리드 헤더의 전체 선택 체크박스 클릭
  5. [일괄취소] 버튼 클릭
  6. "해외주식 일괄 취소주문 확인창" 팝업에서 [취소주문] 클릭
```

#### hts_orders_execution_save_to_csv.py — 체결내역 CSV 저장

[6114] 해외주식 주문체결내역 화면에서 날짜 범위 조회 후 CSV로 내보내기

#### hts_stock_balance_save_to_csv.py — 잔고 CSV 저장

[6104] 해외주식 보유잔고 화면에서 계좌별 잔고를 CSV로 내보내기

---

### 데이터 파이프라인 모듈

#### hts_orders_from_supabase.py — 핵심 비즈니스 로직

Supabase에서 무한매수법 계산 결과(`cycle_trades_latest.computed`)를 읽어 실제 HTS 주문을 생성하는 가장 중요한 모듈.

```
처리 흐름:
  1. cycle_master에서 "진행중"/"시작전" 사이클 목록 조회
  2. 사이클별 computed JSON 조회 (calc_engine이 계산해둔 값)
     - 데이터 신선도 검사: 1시간 이내 갱신된 computed만 사용
  3. computed에서 주문 대상 추출:
     - avg_loc_sell_price/qty → LOC 매도 주문
     - avg_loc_buy_price/qty → LOC 매수 주문
     - 기타 star/dip/quarter 매수 등
  4. 장 마감 후이면 LOC/MOC → 지정가 변환 (현재가 ×0.97/×1.03)
  5. hts_order_sell() / hts_order_buy() 호출
  6. 콘솔 API에 주문 상태 기록 (POST /api/order-status)
  7. 텔레그램으로 주문 결과 알림 전송
```

#### hts_orders_aftermarket.py — 시간외 추가 매수

전일 체결내역을 기반으로 시간외 거래 시간에 추가 매수가 필요한지 판단 후 실행한다.

#### order_execution_data_preprocessing.py — 체결내역 전처리

HTS가 내보내는 CSV는 이중 헤더 구조를 가진다. 이를 pandas로 정규화하고, 종목코드 포맷 통일, 수량/가격 타입 변환, 중복 제거 등을 처리한다.

#### order_execution_update_supabase.py — Supabase 동기화

전처리된 체결 데이터를 `cycle_trades` 테이블에 INSERT하고, 콘솔 API의 `/recompute/{cycle_id}`를 호출하여 계산 결과를 갱신한다. 실패 시 1회 재시도한다.

---

### 인프라 모듈

#### config.py — 설정 관리

2계층 설정 구조:
- **부트스트랩 설정 (.env 파일)**: `SUPABASE_URL`, `SUPABASE_KEY`, `HTS_AGENT_KEY`, `CONSOLE_URL`
- **운영 설정 (Supabase DB)**: 텔레그램 토큰, HTS 경로 등

모듈 import 시 자동으로 `Config.load_from_console_db()`를 호출하여 DB에서 설정을 로드한다. `HTS_AGENT_KEY`로 `agent_settings` 테이블을 조회한다.

```python
# 설정 우선순위: DB > .env > 기본값
Config.TELEGRAM_BOT_TOKEN_ORDER  # 텔레그램 주문 봇 토큰
Config.HTS_EXE_PATH              # HTS 실행 파일 경로
Config.HTS_WINDOW_NAME           # HTS 창 제목
```

#### secrets_manager.py — 비밀번호 안전 저장

Windows Credential Manager(keyring)를 사용하여 민감 정보를 OS 보안 저장소에 보관한다. 파일에 비밀번호를 저장하지 않는다.

```python
# 공동인증서 비밀번호
set_cert_password(user="홍길동", password="****")
get_cert_password(user="홍길동")  # → "****"

# 계좌 비밀번호 (같은 사용자의 모든 계좌에 공통 적용)
set_account_password(user="홍길동", password="****")
get_account_password(user="홍길동")
```

#### automation_target_store.py — 자동화 대상 관리

Supabase `user_accounts` 테이블에서 `is_automation_target=true`인 계좌를 조회하여 작업 대상 목록을 반환한다.

```python
# 반환 형태
{
  "홍길동": [{"account": 1, "cycles": None}, {"account": 2, "cycles": None}],
  "김영희": [{"account": 3, "cycles": None}]
}
```

`HTS_AGENT_KEY`로 `agent_settings` 역조회하여 어느 콘솔 계정의 에이전트인지 자동 판별한다 (멀티 사용자 지원).

#### job_control.py — PID 추적

각 작업 실행 시 PID를 `pids/{job}.pid` 파일에 기록하고, 종료 시 제거한다. `/stop` API 호출 시 이 파일을 읽어 해당 프로세스를 강제 종료한다.

#### utils.py — 공통 유틸리티

| 기능 | 함수 |
|------|------|
| GUI 입력 잠금/해제 | `block_input(True/False)` |
| HTS 창 핸들 찾기 | `get_window_handle(title)` |
| HTS 창 포커싱/최대화 | `setup_window(hwnd)` |
| UI 컨트롤 탐색 | `find_control_by_criteria(window, type, automation_id, title)` |
| 텍스트 입력 | `set_focus_and_type(control, text)` |
| HTS 프로세스 종료 | `kill_window_by_title(title)` |
| 텔레그램 전송 | `send_telegram_message(token, chat_id, text)` |
| 거래일 판별 | `is_trading_day_today()` |
| 시간외 거래 여부 | `is_aftermarket_open()` |
| RDP 세션 복원 | `ensure_active_desktop()` |
| CSV 로드 | `load_csv_if_exists(path)` |
| 데스크톱 재시도 데코레이터 | `@with_desktop_retry` |
| 로그 컨텍스트 설정 | `set_log_context(job, user, account, cycle)` |

---

## 일별 작업 흐름

### 저녁 작업 (Evening Job)

미국 장 개시 전, 주문을 준비하고 실행하는 메인 작업.

```
1. automation_target_store → 자동화 대상 사용자/계좌 목록 조회 (Supabase)
2. 사용자별 루프:
   a. hts_login() — HTS 실행 + 공동인증서 로그인
   b. 계좌별 루프:
      i.  [6104] 보유잔고 CSV 저장 → 전처리
      ii. 외화예수금 CSV → Supabase account_cash_balance 동기화
      iii. 보유잔고 CSV → Supabase account_stock_balance 동기화
      iv. 예수금 부족 체크 → 부족 시 텔레그램 경고
      v.  hts_orders_from_supabase() — Supabase computed 값으로 실제 주문 실행
   c. kill_window_by_title() — HTS 종료
```

### 아침 작업 (Morning Job)

미국 장 마감 후, 전날 체결내역을 수집하여 Supabase를 갱신하는 작업.

```
1. automation_target_store → 자동화 대상 조회
2. 사용자별 루프:
   a. hts_login() — HTS 로그인
   b. 계좌별 루프:
      i.  [6114] 체결내역 CSV 저장 (지정 기간 또는 기본 기간)
      ii. 체결내역 CSV 전처리 (이중 헤더 정규화, 중복 제거)
      iii. [6104] 보유잔고 CSV 저장 → 전처리
      iv. 외화예수금 + 보유잔고 → Supabase 동기화
      v.  order_execution_update_supabase() — 체결 데이터 cycle_trades INSERT
                                           → 콘솔 /recompute 트리거
   c. kill_window_by_title() — HTS 종료
3. 2주 이상 된 order_status 레코드 자동 정리 (수동 실행 시 스킵)
```

### 시간외 작업 (Aftermarket Job)

미국 시간외 거래 시간에 추가 매수 주문을 실행하는 작업.

```
1. automation_target_store → 자동화 대상 조회
2. 사용자별 루프:
   a. hts_login() — HTS 로그인
   b. 계좌별 루프:
      i.  체결내역 CSV 저장 + 전처리
      ii. hts_orders_aftermarket() — 시간외 추가매수 주문
   c. HTS 종료
```

시간외 주문 로직:
- 전일 미체결/체결 데이터를 분석
- 추가 매수가 필요한 사이클 판별
- LOC 매수 → 지정가 변환 (yfinance 현재가 × 1.03)

### 미체결 취소 작업 (Cancel Orders Job)

```
1. automation_target_store → 자동화 대상 조회
2. 사용자별 루프:
   a. hts_login() — HTS 로그인
   b. 계좌별 루프:
      i.  hts_cancel_orders() — [6100] 미체결 탭 전체 일괄 취소
      ii. 성공 시 콘솔 API POST /api/order-status/clear 호출
   c. HTS 종료
3. 취소 결과 텔레그램 전송
```

---

## HTS GUI 자동화 패턴

모든 HTS 자동화 모듈이 공유하는 패턴:

```python
@with_desktop_retry  # RDP 세션 끊김 시 자동 재시도
def hts_order_buy(...):
    order_window = None
    try:
        block_input(True)  # 1. 마우스/키보드 잠금

        hwnd = win32gui.FindWindow(None, "iMeritz")  # 2. HTS 창 찾기
        setup_window(hwnd)                            #    포커싱 + 최대화

        app = Application(backend="uia").connect(handle=hwnd)  # 3. pywinauto 연결
        main_window = app.window(handle=hwnd)

        # 4. UI 컨트롤 탐색 (automation_id 우선, title 폴백)
        control = find_control_by_criteria(
            main_window, "Edit", automation_id="3860"
        )

        set_focus_and_type(control, value)  # 5. 입력
        control.click_input()               #    또는 클릭
        send_keys("{F1}")                   #    또는 단축키

    except Exception as e:
        logging.exception(...)
        raise
    finally:
        block_input(False)  # 6. 반드시 잠금 해제 (예외 시에도)
```

**HTS 화면 번호:**

| 화면번호 | 설명 |
|----------|------|
| `6100` | 해외주식 주문 (매수/매도/미체결/주문체결 탭) |
| `6104` | 해외주식 보유잔고 |
| `6114` | 해외주식 주문체결내역 (날짜 범위 조회) |

---

## 데이터 파이프라인

```
HTS 화면 → CSV 내보내기 → pandas 전처리 → Supabase 업데이트

[6114] 체결내역
  → data/all_order_execution_raw/all_order_execution_raw_{user}_{account}.csv
  → data/all_order_execution_processed/...csv
  → cycle_trades INSERT + /recompute 트리거

[6100] 주문체결탭
  → data/order_history_raw/...csv
  → data/order_history_processed/...csv

[6104] 보유잔고
  → data/stock_balance_raw/...csv
  → data/stock_balance_processed/...csv
  → account_stock_balance upsert

외화예수금 화면
  → data/foreign_deposit_raw/foreign_deposit_raw_{user}_{account}.csv
  → account_cash_balance upsert
```

**Supabase 주요 테이블:**

| 테이블 | 설명 |
|--------|------|
| `cycle_master` | 무한매수법 사이클 파라미터 (원금, 분할수, 종목 등) |
| `cycle_trades` | 개별 거래 내역 + 계산 결과(computed) |
| `cycle_trades_latest` | 최신 상태 VIEW (사이클당 최신 1건) |
| `user_accounts` | 사용자 증권 계좌 + 자동화 대상 여부 |
| `account_stock_balance` | 계좌별 보유잔고 |
| `account_cash_balance` | 계좌별 외화예수금 |
| `agent_settings` | 에이전트 설정 (텔레그램 토큰, HTS 경로 등) |
| `order_status` | 당일 주문 상태 (취소 시 삭제됨) |

---

## 설정 및 환경 변수

`.env` 파일에서 로드 (`.env.example` 참조):

```ini
# Supabase (필수)
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=eyJ...

# HTS 에이전트 인증 키 (콘솔의 HTS_AGENT_SECRET과 동일)
HTS_AGENT_KEY=your-secret-key

# 콘솔 API URL (recompute 트리거 + order-status 정리용)
CONSOLE_URL=https://your-app.vercel.app

# HTS 실행 파일 경로 (기본값: C:\메리츠증권\iMERITZ XII\Main\imeritz.exe — 실제 설치 경로는 다를 수 있으니 탐색기에서 확인 후 설정)
HTS_EXE_PATH=C:\메리츠증권\iMERITZ XII\Main\imeritz.exe
HTS_WINDOW_NAME=iMeritz

# 텔레그램 알림 (DB에서 자동 로드되므로 선택)
TELEGRAM_BOT_TOKEN_ORDER=
TELEGRAM_BOT_TOKEN_EXECUTION=
TELEGRAM_CHAT_ID=
```

텔레그램 토큰, HTS 경로 등 운영 설정은 `.env`가 없어도 Supabase `agent_settings` 테이블에서 자동 로드된다.

---

## 보안

- **비밀번호 저장**: Windows Credential Manager(keyring). 파일/코드에 비밀번호 절대 저장 안 함.
- **API 인증**: 모든 엔드포인트에 `X-Agent-Key` 헤더 필수. `hmac.compare_digest`로 타이밍 공격 방지.
- **키 미설정 보호**: `HTS_AGENT_KEY`가 비어있으면 `/health` 제외 모든 요청 차단.
- **테스트 모드**: `JOB_TEST_MODE=1` 시 실제 주문 대신 "닫기" 버튼 클릭. 주문 확인 팝업까지만 진행.
- **에러 복구**: `finally` 블록에서 반드시 `block_input(False)` 호출. 예외 발생 시에도 마우스/키보드 잠금 해제.

---

## 배포 파이프라인

```
git push main
  → .github/workflows/release-deploy.yml
    → GitHub Release zip 생성 (소스 코드 패키징)
    → 콘솔 webhook 트리거
      → mume-console → 각 Windows 서버에 POST /deploy
        → 에이전트가 GitHub Release zip 다운로드
          → .py / .bat 파일만 덮어쓰기 (데이터/설정 보존)
```

- 배포 시점에 해당 서버에서 morning/evening/aftermarket/cancel_orders 중 하나라도 실행 중이면 `/deploy`는 409로 거부되지만, 요청 내용을 에이전트가 기억해뒀다가 작업이 끝나는 대로(최대 30초 이내) 자동으로 재적용한다 — 콘솔에서 수동으로 다시 배포를 트리거할 필요 없음.
- **`setup.bat`**: 신규 서버 원클릭 설치 스크립트. Python, 가상환경, pip 패키지, `.env`, 방화벽 규칙, Windows 스케줄러 등록 자동화.
- **`hts_agent.bat`**: 에이전트를 무한 루프로 실행. 프로세스 종료 시 자동 재시작.

---

## 실행 방법

```bash
# 에이전트 HTTP 서버 실행 (포트 9000)
uvicorn hts_agent:app --host 0.0.0.0 --port 9000

# 개별 작업 직접 실행 (테스트용)
python main_evening.py
python main_morning.py
python main_aftermarket.py
python main_cancel_orders.py

# 의존성 설치
pip install -r requirements.txt
```

---

## 크로스 플랫폼 참고사항

코드는 macOS/Linux에서도 import/편집 가능하도록 `platform.system() == "Windows"` 분기 처리가 되어 있다.

**실제 HTS GUI 자동화 실행은 Windows 전용**이다. 아래 라이브러리는 Windows에서만 동작한다:
- `pywinauto` — Windows UI 자동화
- `win32gui`, `win32com` — Win32 API 바인딩
- `ctypes.windll` — `block_input()` 구현 (마우스/키보드 잠금)
- `keyring` — Windows Credential Manager 연동

macOS에서는 `win32gui = None` 분기로 처리되어 import는 되지만 GUI 자동화 함수 호출 시 예외가 발생한다.

---

## 관련 프로젝트

- [mume-console](../mume-console) — 웹 콘솔 (FastAPI + Supabase + 프론트엔드 대시보드)
