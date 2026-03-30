# C:\mume-agent\main_evening.py

import os
import json
from pathlib import Path
import logging
import traceback
import sys

from hts_login import hts_login
from hts_stock_balance_save_to_csv import save_data_stock_balance
from stock_balance_data_preprocessing import stock_balance_data_preprocessing
from hts_orders_from_supabase import hts_orders_from_supabase
from utils import kill_window_by_title
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
    encoding="utf-8",
)

def log_uncaught_exceptions(exctype, value, tb):
    """전역(마지막까지 처리 안 된) 예외를 모두 log.log에 traceback 포함해서 기록"""
    logging.error("=== Uncaught Exception ===")
    logging.error("".join(traceback.format_exception(exctype, value, tb)))

sys.excepthook = log_uncaught_exceptions


def run_evening_job(is_test_mode: bool = False, manual: bool = False):
    """
    저녁 자동주문 전체 플로우 실행 함수.
    - is_test_mode: True면 테스트 모드(실주문 X)
    - manual: 웹에서 사람이 버튼 눌러 실행했는지 여부(로그용으로 필요시)
    """
    os.chdir(BASE_DIR)

    # ─────────────────────────────────────
    # 1) 환경변수로부터 사용자/계좌/사이클/테스트모드 override
    # ─────────────────────────────────────
    env_user_accounts_json = os.getenv("JOB_USER_ACCOUNTS")
    env_test_mode = os.getenv("JOB_TEST_MODE")

    saved_user_accounts = load_automation_target(job="evening")
    user_accounts = saved_user_accounts

    if env_test_mode == "1":
        is_test_mode = True

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

    # ─────────────────────────────────────
    # 2) 고정 설정값 (.env 파일에서 로드)
    # ─────────────────────────────────────
    exe_path = Config.HTS_EXE_PATH
    hts_window_name = Config.HTS_WINDOW_NAME

    # ─────────────────────────────────────
    # 3) 메인 플로우
    # ─────────────────────────────────────
    logging.info(f"자동실행대상: {user_accounts}")
    for user, account_items in user_accounts.items():
        if not account_items:
            continue

        hts_login(exe_path, user)

        for item in account_items:
            account_index = item["account"]
            cycles = item.get("cycles")

            # HTS에서 해외주식 보유잔고 데이터 csv로 저장 (계좌 레벨)
            save_data_stock_balance(user, account_index)
            stock_balance_data_preprocessing(user, account_index)

            # Supabase에서 주문 데이터 읽어 매도/매수 실행 (사이클 레벨)
            hts_orders_from_supabase(
                user,
                account_index,
                is_test_mode,
                cycles,
            )

        kill_window_by_title(hts_window_name)


def main():
    """엔트리 포인트 래퍼: 여기서 예외 한 번 더 잡아서 traceback 남김"""
    register_job_pid("evening")

    try:
        run_evening_job(is_test_mode=False, manual=False)
    except Exception:
        logging.error("=== Exception in main_evening.py::main() ===")
        logging.error(traceback.format_exc())
        raise
    finally:
        unregister_job_pid("evening")


if __name__ == "__main__":
    main()
