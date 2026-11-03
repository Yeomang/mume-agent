# C:\mume-agent\main_morning.py

import logging
import sys
import traceback
from pathlib import Path

# ─────────────────────────────
# 로깅 최우선 설정 — import 실패도 반드시 기록
# ─────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "log.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    encoding="utf-8",
)

def log_uncaught_exceptions(exctype, value, tb):
    logging.error("=== Uncaught Exception ===")
    logging.error("".join(traceback.format_exception(exctype, value, tb)))

sys.excepthook = log_uncaught_exceptions

import os
import json
import datetime as dt

from utils import set_log_context, install_log_context_filter, hide_job_console
install_log_context_filter()
hide_job_console()   # 스케줄러 실행 때 뜬 cmd 창 숨기기
from hts_login import hts_login
from hts_orders_execution_save_to_csv import save_data_order_execution
from order_execution_data_preprocessing import order_execution_data_preprocessing
from order_execution_update_supabase import orders_execution_update_supabase
from hts_stock_balance_save_to_csv import save_data_stock_balance
from stock_balance_data_preprocessing import stock_balance_data_preprocessing
from hts_daily_return_save_to_csv import save_data_daily_return
from daily_return_update_supabase import sync_daily_return_to_db
from utils import kill_window_by_title, to_yyyymmdd, block_input, send_telegram_message
from job_control import register_job_pid, unregister_job_pid
from automation_target_store import load_automation_target, get_auth_user_ids
from config import Config


def run_morning_job(is_test_mode: bool = False, manual: bool = False):
    """
    아침 체결내역/잔고 업데이트 전체 플로우 실행 함수.
    - is_test_mode: True면 테스트 모드(실제 시트 업데이트 로직에서 활용)
    - manual: 웹에서 수동 실행 여부(로그용)
    """
    os.chdir(BASE_DIR)

    # ─────────────────────────────────────
    # 1) 환경변수로부터 사용자/계좌/사이클/테스트모드 override
    #    (hts_agent.py 에서 JOB_* 로 세팅해줌)
    # ─────────────────────────────────────
    env_user_accounts_json = os.getenv("JOB_USER_ACCOUNTS")
    env_test_mode = os.getenv("JOB_TEST_MODE")
    env_date_from = os.getenv("JOB_DATE_FROM")
    env_date_to = os.getenv("JOB_DATE_TO")

    # 기본값: 웹UI에서 저장한 자동 실행 대상 사용 (없으면 빈 dict)
    saved_user_accounts = load_automation_target(job="morning")
    user_accounts = saved_user_accounts

    # 테스트 모드 환경변수 우선 적용
    if env_test_mode == "1":
        is_test_mode = True

    # JOB_USER_ACCOUNTS 가 있으면 그걸 그대로 사용 (웹UI에서 선택한 경우)
    if env_user_accounts_json:
        try:
            parsed = json.loads(env_user_accounts_json)
            user_accounts = {}
            for name, items in parsed.items():
                if not items:
                    continue
                acc_cycles = {}
                for item in items:
                    if isinstance(item, dict):
                        acc = int(item.get("account", 0))
                        cycle = item.get("cycle")
                        if acc not in acc_cycles:
                            acc_cycles[acc] = []
                        if cycle is not None:
                            acc_cycles[acc].append(int(cycle))
                    else:
                        acc = int(item)
                        acc_cycles[acc] = None

                user_accounts[str(name)] = [
                    {"account": acc, "cycles": cycles if cycles else None}
                    for acc, cycles in acc_cycles.items()
                ]
        except Exception as e:
            logging.warning(f"[WARN] JOB_USER_ACCOUNTS 파싱 실패, 기본 설정 사용: {e}")
            user_accounts = saved_user_accounts

    inquiry_start_date = to_yyyymmdd(env_date_from)
    inquiry_end_date = to_yyyymmdd(env_date_to)

    # ─────────────────────────────────────
    # 2) 고정 설정값 (.env 파일에서 로드)
    # ─────────────────────────────────────
    exe_path = Config.HTS_EXE_PATH
    hts_window_name = Config.HTS_WINDOW_NAME

    # ─────────────────────────────────────
    # 3) 메인 플로우
    # ─────────────────────────────────────
    set_log_context(job="morning")
    logging.info(f"자동실행대상: {user_accounts}")
    failures = []   # (사용자, 계좌, 오류) — 한 계좌가 실패해도 나머지 계좌는 계속 진행
    for user, account_items in user_accounts.items():
        if not account_items:
            continue

        set_log_context(job="morning", user=user)
        login_ok = hts_login(exe_path, user)
        if not login_ok:
            logging.error(f"[{user}] HTS 로그인 실패 — 해당 사용자의 모든 작업을 건너뜁니다.")
            kill_window_by_title(hts_window_name)
            continue

        for i, item in enumerate(account_items):
            account_index = item["account"]
            cycles = item.get("cycles")
            set_log_context(job="morning", user=user, account=account_index)
            if not login_ok:
                failures.append((user, account_index, "앞 계좌 실패 뒤 HTS 다시 로그인 실패 — 건너뜀"))
                continue
            try:
                _run_account(user, account_index, cycles, is_test_mode, inquiry_start_date, inquiry_end_date)
            except Exception as e:
                if "no active desktop" in str(e).lower():
                    raise   # 화면 세션이 끊겨 복구도 못 함 — 다음 계좌도 똑같이 실패하므로 작업 중단
                logging.error(f"[{user}/{account_index}번] 아침 작업 실패 — 다음 계좌로 넘어갑니다: {type(e).__name__}: {e}")
                logging.error(traceback.format_exc())
                failures.append((user, account_index, f"{type(e).__name__}: {e}"))
                block_input(False)
                if i < len(account_items) - 1:
                    # 실패한 화면·저장 창 등이 남아 있을 수 있어 HTS를 새로 켜고 로그인한 뒤 진행
                    logging.info(f"[{user}] HTS 상태를 알 수 없어 다시 로그인한 뒤 다음 계좌를 진행합니다.")
                    login_ok = hts_login(exe_path, user)

        kill_window_by_title(hts_window_name)
    set_log_context()

    # 2주 이상 된 order_status 정리 — 수동 실행 시 스킵 (재실행마다 추가 삭제 방지)
    if not manual:
        _cleanup_old_order_status()
    else:
        logging.info("[order-status] 수동 실행 — 오래된 레코드 정리 스킵")

    if failures:
        _notify_failures(failures)
        # 작업 상태를 '오류'로 남겨 콘솔에서 보이게 한다 (종료 코드 1)
        raise RuntimeError(f"아침 작업 {len(failures)}개 계좌 실패: {failures}")


def _run_account(user, account_index, cycles, is_test_mode, inquiry_start_date, inquiry_end_date):
    """계좌 하나의 아침 작업: 체결내역 → 보유잔고·예수금 → 일별 계좌수익률 → 체결내역 Supabase 동기화."""
    if inquiry_start_date and inquiry_end_date:
        save_data_order_execution(user, account_index, inquiry_start_date, inquiry_end_date)
        order_execution_data_preprocessing(user, account_index, inquiry_start_date, inquiry_end_date)
    else:
        save_data_order_execution(user, account_index)
        order_execution_data_preprocessing(user, account_index)

    # HTS에서 해외주식 보유잔고 데이터 csv로 저장 (계좌 레벨)
    save_data_stock_balance(user, account_index)
    stock_balance_data_preprocessing(user, account_index)

    # 외화예수금 + 보유잔고를 Supabase에 동기화
    from main_evening import _sync_cash_balance_to_db, _sync_stock_balance_to_db
    _sync_cash_balance_to_db(user, account_index)
    _sync_stock_balance_to_db(user, account_index)

    # HTS에서 일별 계좌수익률 데이터 csv로 저장 후 Supabase에 동기화 (계좌 레벨)
    save_data_daily_return(user, account_index)
    sync_daily_return_to_db(user, account_index)

    # Supabase에 체결내역 업데이트 (사이클 레벨)
    orders_execution_update_supabase(
        user,
        account_index,
        is_test_mode,
        inquiry_start_date if inquiry_start_date and inquiry_end_date else None,
        inquiry_end_date if inquiry_start_date and inquiry_end_date else None,
        cycles,
    )


def _notify_failures(failures):
    """실패한 계좌를 텔레그램(체결 알림 봇)으로 알린다."""
    lines = [f"· {user} {account}번 계좌 — {error[:150]}" for user, account, error in failures]
    text = ("⚠️ 아침 작업 일부 계좌 실패\n"
            "아래 계좌는 체결내역·잔고 반영이 안 됐을 수 있습니다. 나머지 계좌는 계속 진행했습니다.\n"
            "다음 아침 작업 때 다시 수집되며, 바로 반영하려면 콘솔에서 아침 작업을 수동 실행해 주세요.\n\n"
            + "\n".join(lines))
    try:
        if Config.TELEGRAM_BOT_TOKEN_EXECUTION and Config.TELEGRAM_CHAT_ID:
            send_telegram_message(Config.TELEGRAM_BOT_TOKEN_EXECUTION, Config.TELEGRAM_CHAT_ID, text)
    except Exception as e:
        logging.warning(f"실패 알림 전송 실패: {e}")


def _cleanup_old_order_status():
    """2주 이상 된 order_status 레코드를 삭제한다."""
    try:
        from supabase_client import get_supabase_client
        sb = get_supabase_client()
        if not sb:
            return
        cutoff = (dt.date.today() - dt.timedelta(days=14)).strftime("%Y-%m-%d")
        uids = get_auth_user_ids()
        if not uids:
            return
        for uid in uids:
            res = (
                sb.table("order_status")
                .delete()
                .eq("auth_user_id", uid)
                .lt("order_date", cutoff)
                .execute()
            )
            deleted = len(res.data) if res.data else 0
            if deleted:
                logging.info(f"[order-status] 오래된 레코드 {deleted}건 삭제 (uid={uid[:8]})")
    except Exception as e:
        logging.warning(f"[order-status] 정리 실패: {e}")


def main():
    """엔트리 포인트 래퍼: 여기서 예외 한 번 더 잡아서 traceback 남김"""
    register_job_pid("morning")

    try:
        run_morning_job(is_test_mode=False, manual=False)
    except Exception:
        logging.error("=== Exception in main_morning.py::main() ===")
        logging.error(traceback.format_exc())
        raise
    finally:
        unregister_job_pid("morning")


if __name__ == "__main__":
    main()
