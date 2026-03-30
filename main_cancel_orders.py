# C:\mume_meritz\main_cancel_orders.py

import os
from pathlib import Path
import json
import logging
import traceback
import sys

from hts_login import hts_login
from hts_cancel_orders import hts_cancel_orders
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

# 모든 처리 안 된 예외는 여기로 들어옴
sys.excepthook = log_uncaught_exceptions


def run_cancel_orders_job(is_test_mode: bool = False, manual: bool = False):
    """
    미체결 주문 일괄 취소 전체 플로우.
    - is_test_mode: True면 테스트 모드 (실제 취소 X)
    - manual: 웹에서 수동 실행 여부
    """
    os.chdir(BASE_DIR)

    # ─────────────────────────────────────
    # 1) 환경변수로 사용자/계좌/테스트모드 override
    # ─────────────────────────────────────
    env_user_accounts_json = os.getenv("JOB_USER_ACCOUNTS")
    env_test_mode = os.getenv("JOB_TEST_MODE")
    
    # 기본값: 웹UI에서 저장한 자동 실행 대상 사용 (없으면 빈 dict)
    saved_user_accounts = load_automation_target()
    user_accounts = saved_user_accounts

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
                # 계좌별로 그룹화 (사이클은 취소 작업에서는 사용하지 않음)
                acc_set = set()
                for item in items:
                    if isinstance(item, dict):
                        acc = int(item.get("account", 0))
                        acc_set.add(acc)
                    else:
                        # 하위 호환: 기존 형식 [1, 2, 3]
                        acc = int(item)
                        acc_set.add(acc)
                
                user_accounts[str(name)] = [
                    {"account": acc, "cycles": None}
                    for acc in sorted(acc_set)
                ]
        except Exception as e:
            logging.warning(f"[WARN] JOB_USER_ACCOUNTS 파싱 실패, 기본 설정 사용: {e}")
            # 파싱 실패 시 저장된 자동 실행 대상으로 복원
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

        # 공동인증서 로그인
        hts_login(exe_path, user)

        for item in account_items:
            account_index = item["account"]
            
            logging.info(f">>>>> {user}님의 {account_index}번 계좌 미체결 주문 일괄 취소 시작 <<<<<")
            
            # 미체결 주문 일괄 취소 실행
            success, error = hts_cancel_orders(
                user,
                account_index,
                is_test_mode,
            )
            
            if success:
                logging.info(f"[{user} | {account_index}번 계좌] 미체결 주문 일괄 취소 성공")
            else:
                logging.error(f"[{user} | {account_index}번 계좌] 미체결 주문 일괄 취소 실패: {error}")

        # HTS 프로그램 종료
        kill_window_by_title(hts_window_name)


def main():
    """엔트리 포인트 래퍼: 여기서 예외 한 번 더 잡아서 traceback 남김"""
    # ★ 여기서 cancel_orders job PID 등록
    register_job_pid("cancel_orders")

    try:
        run_cancel_orders_job(is_test_mode=False, manual=False)
    except Exception:
        # 여기서 한 번 로그로 남기고
        logging.error("=== Exception in main_cancel_orders.py::main() ===")
        logging.error(traceback.format_exc())
        # 다시 던지면 sys.excepthook이 또 전체 traceback을 log로 남김
        raise
    finally:
    # ★ 정상/비정상 종료와 관계없이 PID 등록 해제
        unregister_job_pid("cancel_orders")


if __name__ == "__main__":
    main()

