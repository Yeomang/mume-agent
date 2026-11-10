# C:\mume-agent\main_evening.py

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
import csv
import io

from utils import set_log_context, install_log_context_filter, send_telegram_message, hide_job_console
install_log_context_filter()
hide_job_console()   # 스케줄러 실행 때 뜬 cmd 창 숨기기
from hts_login import hts_login
from hts_stock_balance_save_to_csv import save_data_stock_balance
from stock_balance_data_preprocessing import stock_balance_data_preprocessing
from hts_orders_from_supabase import hts_orders_from_supabase
from utils import kill_window_by_title
from job_control import register_job_pid, unregister_job_pid
from automation_target_store import load_automation_target
from config import Config


def _read_usd_deposit(user: str, account_index: int) -> float:
    """외화예수금 CSV에서 USD 추정예수금을 읽어 반환한다. 실패 시 0."""
    raw_dir = BASE_DIR / "data" / "foreign_deposit_raw"
    csv_path = raw_dir / f"foreign_deposit_raw_{user}_{account_index}.csv"
    if not csv_path.exists():
        return 0.0
    try:
        raw_text = None
        for enc in ("utf-8-sig", "cp949"):
            try:
                raw_text = csv_path.read_text(encoding=enc, errors="strict")
                break
            except (UnicodeDecodeError, ValueError):
                continue
        if raw_text is None:
            raw_text = csv_path.read_text(encoding="cp949", errors="replace")

        first_line = raw_text.split("\n", 1)[0]
        delimiter = "\t" if "\t" in first_line else ","
        reader = csv.DictReader(io.StringIO(raw_text), delimiter=delimiter)
        for row in reader:
            currency = (row.get("통화") or "").strip()
            if currency == "USD":
                val = str(row.get("외화추정예수금") or "0").replace(",", "").strip()
                return float(val)
    except Exception as e:
        logging.warning(f"[예수금체크] USD 예수금 읽기 실패 ({csv_path.name}): {e}")
    return 0.0


def _estimate_total_buy_amount(user: str, account_index: int) -> tuple[float, float]:
    """Supabase에서 해당 계좌 활성 사이클들의 예상 매수 금액 합계 (본 주문, 하락대비추가매수)를 계산한다."""
    try:
        from supabase_client import get_supabase_client, supabase_fetch_all
        from automation_target_store import get_auth_user_ids
        from hts_orders_from_supabase import buy_amounts
        sb = get_supabase_client()
        if sb is None:
            return 0.0, 0.0

        uids = get_auth_user_ids()
        cycles_res = supabase_fetch_all(
            lambda s, e: sb.table("cycle_master")
            .select("id, status, method")
            .in_("status", ["진행중", "시작전"])
            .in_("auth_user_id", uids)
            .eq("user_name", user)
            .eq("account_index", account_index)
            .eq("broker", "메리츠")
            .range(s, e)
            .execute()
        )
        method_by_id = {int(r["id"]): r.get("method", "") for r in (cycles_res.data or []) if r.get("id")}
        if not method_by_id:
            return 0.0, 0.0

        latest_res = supabase_fetch_all(
            lambda s, e: sb.table("cycle_trades_latest")
            .select("cycle_id, computed")
            .in_("cycle_id", list(method_by_id))
            .range(s, e)
            .execute()
        )
        core = extra = 0.0
        for r in (latest_res.data or []):
            c, x = buy_amounts(method_by_id.get(int(r["cycle_id"]), ""), r.get("computed") or {})
            core += c
            extra += x
        return core, extra
    except Exception as e:
        logging.warning(f"[예수금체크] 매수 금액 추정 실패: {e}")
        return 0.0, 0.0


def _sync_cash_balance_to_db(user: str, account_index: int):
    """외화예수금 CSV를 읽어 Supabase에 upsert한다."""
    try:
        from supabase_client import get_supabase_client
        from automation_target_store import get_auth_user_id_for
        sb = get_supabase_client()
        if sb is None:
            return

        auth_user_id = get_auth_user_id_for(user)
        if not auth_user_id:
            return

        usd_deposit = _read_usd_deposit(user, account_index)
        if usd_deposit <= 0:
            return

        # CSV에서 상세 데이터 읽기
        raw_dir = BASE_DIR / "data" / "foreign_deposit_raw"
        csv_path = raw_dir / f"foreign_deposit_raw_{user}_{account_index}.csv"
        if not csv_path.exists():
            return

        raw_text = None
        for enc in ("utf-8-sig", "cp949"):
            try:
                raw_text = csv_path.read_text(encoding=enc, errors="strict")
                break
            except (UnicodeDecodeError, ValueError):
                continue
        if raw_text is None:
            raw_text = csv_path.read_text(encoding="cp949", errors="replace")

        first_line = raw_text.split("\n", 1)[0]
        delimiter = "\t" if "\t" in first_line else ","
        reader = csv.DictReader(io.StringIO(raw_text), delimiter=delimiter)

        def parse_num(v):
            try:
                return float(str(v).replace(",", "").strip())
            except Exception:
                return 0.0

        for row in reader:
            currency = (row.get("통화") or "").strip()
            if not currency:
                continue
            record = {
                "auth_user_id": auth_user_id,
                "user_name": user,
                "account_index": account_index,
                "currency": currency,
                "estimated_deposit": parse_num(row.get("외화추정예수금")),
                "exchange_rate": parse_num(row.get("기준환율")),
                "updated_at": "now()",
            }
            sb.table("account_cash_balance").upsert(
                record,
                on_conflict="auth_user_id,user_name,account_index,currency",
            ).execute()

        logging.info(f"[예수금DB] {user}/{account_index} Supabase 동기화 완료")
    except Exception as e:
        logging.warning(f"[예수금DB] Supabase 동기화 실패: {e}")


def _sync_stock_balance_to_db(user: str, account_index: int):
    """보유잔고 CSV를 읽어 Supabase에 upsert한다."""
    try:
        from supabase_client import get_supabase_client
        from automation_target_store import get_auth_user_id_for
        from utils import load_csv_if_exists
        sb = get_supabase_client()
        if sb is None:
            return

        auth_user_id = get_auth_user_id_for(user)
        if not auth_user_id:
            return

        file_path = f'./data/stock_balance_processed/stock_balance_processed_{user}_{account_index}.csv'
        df = load_csv_if_exists(file_path)

        if df is None:
            return

        # CSV가 비어있으면 보유종목 없음 → 기존 잔고 레코드 삭제
        if df.empty:
            sb.table("account_stock_balance").delete().eq(
                "auth_user_id", auth_user_id
            ).eq("user_name", user).eq("account_index", account_index).execute()
            logging.info(f"[잔고DB] {user}/{account_index} 보유종목 없음 → 기존 잔고 삭제")
            return

        def parse_num(v):
            try:
                return float(str(v).replace(",", "").strip())
            except Exception:
                return 0.0

        csv_stocks = set()
        for _, row in df.iterrows():
            stock_code = str(row.get("종목코드", "")).strip()
            if not stock_code:
                continue
            csv_stocks.add(stock_code)
            record = {
                "auth_user_id": auth_user_id,
                "user_name": user,
                "account_index": account_index,
                "stock_code": stock_code,
                "holding_qty": int(parse_num(row.get("보유수량", 0))),
                "current_price": parse_num(row.get("현재가", 0)),
                "avg_price": parse_num(row.get("평균가", 0)),
                "eval_amount": parse_num(row.get("평가금액(외화)", 0)),
                "purchase_amount": parse_num(row.get("매입금액(외화)", 0)),
                "profit": parse_num(row.get("평가손익", 0)),
                "profit_rate": parse_num(row.get("수익률(%)", 0)),
                "updated_at": "now()",
            }
            sb.table("account_stock_balance").upsert(
                record,
                on_conflict="auth_user_id,user_name,account_index,stock_code",
            ).execute()

        # CSV에 없는 종목(전량 매도)은 DB에서 삭제
        existing = sb.table("account_stock_balance").select("stock_code").eq(
            "auth_user_id", auth_user_id
        ).eq("user_name", user).eq("account_index", account_index).execute()
        for r in (existing.data or []):
            if r["stock_code"] not in csv_stocks:
                sb.table("account_stock_balance").delete().eq(
                    "auth_user_id", auth_user_id
                ).eq("user_name", user).eq("account_index", account_index).eq(
                    "stock_code", r["stock_code"]
                ).execute()
                logging.info(f"[잔고DB] {user}/{account_index} 매도 완료 종목 {r['stock_code']} 삭제")

        logging.info(f"[잔고DB] {user}/{account_index} Supabase 동기화 완료")
    except Exception as e:
        logging.warning(f"[잔고DB] Supabase 동기화 실패: {e}")


def _check_cash_sufficiency(user: str, account_index: int) -> float:
    """예수금이 예상 매수 금액 대비 부족하면 텔레그램 경고를 보낸다. 읽은 USD 예수금을 돌려준다 (없으면 0)."""
    usd_deposit = _read_usd_deposit(user, account_index)
    core_buy, extra_buy = _estimate_total_buy_amount(user, account_index)
    estimated_buy = core_buy + extra_buy

    logging.info(f"[예수금체크] {user}/{account_index} 예수금: ${usd_deposit:,.2f}, "
                 f"예상 매수 합계: ${estimated_buy:,.2f} (본 주문 ${core_buy:,.2f} + 하락대비추가매수 ${extra_buy:,.2f})")

    if usd_deposit <= 0:
        logging.info(f"[예수금체크] {user}/{account_index} USD 예수금 데이터 없음")
        return 0.0

    if estimated_buy <= 0:
        logging.info(f"[예수금체크] {user}/{account_index} 매수 예정 금액 없음 — 예수금만 확인 완료")
        return usd_deposit

    if usd_deposit < estimated_buy:
        shortage = estimated_buy - usd_deposit
        if usd_deposit >= core_buy:
            outlook = "본 주문은 모두 들어가고, 하락대비추가매수은 낮은 가격부터 일부를 빼고 주문합니다."
        else:
            outlook = "본 주문도 일부 실패할 수 있습니다. 하락대비추가매수은 넣지 않습니다."
        message = (
            f"⚠️ *[예수금 부족 경고]*\n\n"
            f"▶ 계좌: {user} | 메리츠 | {account_index}번째 계좌\n"
            f"▶ USD 예수금: ${usd_deposit:,.2f}\n"
            f"▶ 예상 매수 합계: ${estimated_buy:,.2f}\n"
            f"   (본 주문 ${core_buy:,.2f} + 하락대비추가매수 ${extra_buy:,.2f})\n"
            f"▶ 부족액: ${shortage:,.2f}\n\n"
            f"{outlook}"
        )
        send_telegram_message(
            Config.TELEGRAM_BOT_TOKEN_ORDER,
            Config.TELEGRAM_CHAT_ID,
            message,
        )
        logging.warning(f"[예수금체크] 예수금 부족! 부족액: ${shortage:,.2f}")
    else:
        logging.info(f"[예수금체크] 예수금 충분 (여유: ${usd_deposit - estimated_buy:,.2f})")
    return usd_deposit


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
    set_log_context(job="evening")
    logging.info(f"자동실행대상: {user_accounts}")
    for user, account_items in user_accounts.items():
        if not account_items:
            continue

        set_log_context(job="evening", user=user)
        login_ok = hts_login(exe_path, user)
        if not login_ok:
            logging.error(f"[{user}] HTS 로그인 실패 — 해당 사용자의 모든 작업을 건너뜁니다.")
            kill_window_by_title(hts_window_name)
            continue

        for item in account_items:
            account_index = item["account"]
            cycles = item.get("cycles")

            set_log_context(job="evening", user=user, account=account_index)

            # HTS에서 해외주식 보유잔고 데이터 csv로 저장 (계좌 레벨)
            save_data_stock_balance(user, account_index)
            stock_balance_data_preprocessing(user, account_index)

            # 외화예수금 + 보유잔고를 Supabase에 동기화
            _sync_cash_balance_to_db(user, account_index)
            _sync_stock_balance_to_db(user, account_index)

            # 예수금 부족 여부 사전 체크 (부족 시 텔레그램 경고)
            usd_deposit = _check_cash_sufficiency(user, account_index)

            # Supabase에서 주문 데이터 읽어 매도/매수 실행 (사이클 레벨)
            # 예수금을 넘겨 사이클 간 배분 — 본 주문은 예수금 안으로, 하락대비추가매수는 뒤 사이클 본 주문 몫을 남긴 돈 안에서만
            hts_orders_from_supabase(
                user,
                account_index,
                is_test_mode,
                cycles,
                usd_deposit=usd_deposit,
            )

        kill_window_by_title(hts_window_name)
    set_log_context()


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
