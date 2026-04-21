# C:\mume-agent\main_refresh_balance.py

"""
잔고·예수금 CSV를 HTS에서 최신화하는 전용 스크립트.
웹콘솔에서 수동 실행 요청으로 호출된다.
"""

import os
import json
import logging
import traceback
import sys
from pathlib import Path

from utils import set_log_context, install_log_context_filter, kill_window_by_title
from hts_login import hts_login
from hts_stock_balance_save_to_csv import save_data_stock_balance
from stock_balance_data_preprocessing import stock_balance_data_preprocessing
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


def run_refresh_balance():
    """HTS에서 보유잔고 + 외화예수금 CSV를 최신화한다."""
    os.chdir(BASE_DIR)

    env_user_accounts_json = os.getenv("JOB_USER_ACCOUNTS")
    saved_user_accounts = load_automation_target(job="refresh_balance")
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

    exe_path = Config.HTS_EXE_PATH
    hts_window_name = Config.HTS_WINDOW_NAME

    set_log_context(job="refresh_balance")
    logging.info(f"자동실행대상: {user_accounts}")

    for user, account_items in user_accounts.items():
        if not account_items:
            continue

        set_log_context(job="refresh_balance", user=user)
        login_ok = hts_login(exe_path, user)
        if not login_ok:
            logging.error(f"[{user}] HTS 로그인 실패 — 해당 사용자의 모든 작업을 건너뜁니다.")
            kill_window_by_title(hts_window_name)
            continue

        for item in account_items:
            account_index = item["account"]
            set_log_context(job="refresh_balance", user=user, account=account_index)

            # HTS에서 보유잔고 + 외화예수금 CSV 저장
            save_data_stock_balance(user, account_index)
            stock_balance_data_preprocessing(user, account_index)

            # 외화예수금 + 보유잔고를 Supabase에 동기화
            from main_evening import _sync_cash_balance_to_db, _sync_stock_balance_to_db
            _sync_cash_balance_to_db(user, account_index)
            _sync_stock_balance_to_db(user, account_index)

            logging.info(f"[{user}/{account_index}] 잔고·예수금 CSV 최신화 + DB 동기화 완료")

        kill_window_by_title(hts_window_name)

    set_log_context()
    logging.info("잔고·예수금 최신화 작업 완료")


def main():
    register_job_pid("refresh_balance")
    try:
        run_refresh_balance()
    except Exception:
        logging.error("=== Exception in main_refresh_balance.py::main() ===")
        logging.error(traceback.format_exc())
        raise
    finally:
        unregister_job_pid("refresh_balance")


if __name__ == "__main__":
    main()
