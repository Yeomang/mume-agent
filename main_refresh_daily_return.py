# C:\mume-agent\main_refresh_daily_return.py

"""
일별 계좌수익률 CSV를 HTS에서 최신화하는 전용 스크립트.
웹콘솔에서 수동 실행 요청으로 호출된다. 조회기간(JOB_DATE_FROM/JOB_DATE_TO)을
지정하면 해당 기간만 조회하고, 지정하지 않으면 화면 기본값(최근 1개월)을 사용한다.
"""

import os
import json
import logging
import traceback
import sys
from pathlib import Path

from utils import set_log_context, install_log_context_filter, kill_window_by_title, to_yyyymmdd
from hts_login import hts_login
from hts_daily_return_save_to_csv import save_data_daily_return
from daily_return_update_supabase import sync_daily_return_to_db
from job_control import register_job_pid, unregister_job_pid
from automation_target_store import load_automation_target
from config import Config

BASE_DIR = Path(__file__).resolve().parent

# ─────────────────────────────
# 로깅 설정 + 전역 예외 훅
# ─────────────────────────────
LOG_FILE = BASE_DIR / "log.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    encoding="utf-8",
)
install_log_context_filter()


def log_uncaught_exceptions(exctype, value, tb):
    logging.error("=== Uncaught Exception ===")
    logging.error("".join(traceback.format_exception(exctype, value, tb)))


sys.excepthook = log_uncaught_exceptions


def run_refresh_daily_return():
    """HTS에서 일별 계좌수익률 CSV를 최신화하고 Supabase에 동기화한다."""
    os.chdir(BASE_DIR)

    env_user_accounts_json = os.getenv("JOB_USER_ACCOUNTS")
    saved_user_accounts = load_automation_target(job="refresh_daily_return")
    user_accounts = saved_user_accounts

    if env_user_accounts_json:
        try:
            parsed = json.loads(env_user_accounts_json)
            user_accounts = {}
            for name, items in parsed.items():
                if not items:
                    continue
                acc_set = set()
                for item in items:
                    if isinstance(item, dict):
                        acc_set.add(int(item.get("account", 0)))
                    else:
                        acc_set.add(int(item))
                user_accounts[str(name)] = [
                    {"account": acc, "cycles": None} for acc in sorted(acc_set)
                ]
        except Exception as e:
            logging.warning(f"[WARN] JOB_USER_ACCOUNTS 파싱 실패, 기본 설정 사용: {e}")
            user_accounts = saved_user_accounts

    inquiry_start_date = to_yyyymmdd(os.getenv("JOB_DATE_FROM"))
    inquiry_end_date = to_yyyymmdd(os.getenv("JOB_DATE_TO"))

    exe_path = Config.HTS_EXE_PATH
    hts_window_name = Config.HTS_WINDOW_NAME

    set_log_context(job="refresh_daily_return")
    logging.info(f"자동실행대상: {user_accounts}")
    if inquiry_start_date and inquiry_end_date:
        logging.info(f"조회기간 지정: {inquiry_start_date}-{inquiry_end_date}")

    for user, account_items in user_accounts.items():
        if not account_items:
            continue

        set_log_context(job="refresh_daily_return", user=user)
        login_ok = hts_login(exe_path, user)
        if not login_ok:
            logging.error(f"[{user}] HTS 로그인 실패 — 해당 사용자의 모든 작업을 건너뜁니다.")
            kill_window_by_title(hts_window_name)
            continue

        for item in account_items:
            account_index = item["account"]
            set_log_context(job="refresh_daily_return", user=user, account=account_index)

            save_data_daily_return(user, account_index, inquiry_start_date, inquiry_end_date)
            sync_daily_return_to_db(user, account_index)

            logging.info(f"[{user}/{account_index}] 일별 계좌수익률 최신화 + DB 동기화 완료")

        kill_window_by_title(hts_window_name)

    set_log_context()
    logging.info("일별 계좌수익률 최신화 작업 완료")


def main():
    register_job_pid("refresh_daily_return")
    try:
        run_refresh_daily_return()
    except Exception:
        logging.error("=== Exception in main_refresh_daily_return.py::main() ===")
        logging.error(traceback.format_exc())
        raise
    finally:
        unregister_job_pid("refresh_daily_return")


if __name__ == "__main__":
    main()
