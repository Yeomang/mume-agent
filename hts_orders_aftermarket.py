"""
Aftermarket 추가주문 실행 모듈.
Supabase에서 사이클 정보를 읽어 시간외 추가매수 주문을 실행한다.
"""
from utils import (
    send_telegram_message,
    is_trading_day_yesterday,
    load_csv_if_exists,
)
from hts_order_buy import hts_order_buy
from hts_orders_from_supabase import _record_order_status
from config import Config
from supabase_client import get_supabase_client, supabase_fetch_all
import logging
import datetime as dt
import pandas as pd
import yfinance as yf
import traceback

TELEGRAM_BOT_TOKEN = Config.TELEGRAM_BOT_TOKEN_ORDER
TELEGRAM_CHAT_ID = Config.TELEGRAM_CHAT_ID


def _get_active_cycles(sb, selected_user, account_index, auth_user_ids=None, cycles=None):
    """cycle_master에서 활성 사이클 목록 조회"""
    from automation_target_store import get_auth_user_ids
    uids = auth_user_ids or get_auth_user_ids()
    res = supabase_fetch_all(
        lambda s, e: sb.table("cycle_master")
        .select("id, cycle_seq, status, method, stock_code, principal, split_count")
        .in_("status", ["진행중", "시작전"])
        .in_("auth_user_id", uids)
        .eq("user_name", selected_user)
        .eq("account_index", account_index)
        .eq("broker", "메리츠")
        .order("cycle_seq", desc=False)
        .range(s, e)
        .execute()
    )
    rows = res.data or []
    if cycles is not None:
        rows = [r for r in rows if r["cycle_seq"] in cycles]
    return rows


def _get_latest_computed(sb, cycle_id):
    """cycle_trades_latest에서 최신 computed JSON 조회"""
    res = sb.table("cycle_trades_latest").select("computed").eq("cycle_id", cycle_id).execute()
    if res.data:
        return res.data[0].get("computed") or {}
    return {}


def hts_orders_aftermarket(
    selected_user,
    account_index,
    is_test_mode,
    inquiry_start_date=None,
    inquiry_end_date=None,
    cycles=None,
):
    """
    Aftermarket 추가주문 실행
    - cycles: 실행할 사이클 번호 리스트 (None이면 해당 계좌의 모든 활성 사이클 실행)
    """
    logging.info(">>>>> Aftermarket 추가주문 데이터 리스트화 <<<<<")

    if not is_test_mode:
        if not is_trading_day_yesterday():
            return
    else:
        logging.info("테스트모드이므로 휴장일 여부와 관계없이 함수를 실행합니다.")

    if inquiry_start_date is None:
        inquiry_start_date = (dt.date.today() - dt.timedelta(days=1)).strftime('%Y%m%d')
    if inquiry_end_date is None:
        inquiry_end_date = (dt.date.today() - dt.timedelta(days=1)).strftime('%Y%m%d')

    file_path = f'./data/all_order_execution_processed/all_order_execution_processed_{selected_user}_{account_index}_{inquiry_start_date}-{inquiry_end_date}.csv'
    df = load_csv_if_exists(file_path)
    if df is None:
        logging.info("CSV 파일 불러오기 실패!")
        logging.info(f"> 파일 경로 : {file_path}")
        return

    logging.info(
        f"['{selected_user} {account_index}번째 계좌' 전체 주문 및 체결 내역 ({inquiry_start_date}~{inquiry_end_date})]\n"
        f"{df}"
    )

    sb = get_supabase_client()
    if sb is None:
        logging.error("Supabase 클라이언트를 초기화할 수 없습니다.")
        return

    active_cycles = _get_active_cycles(sb, selected_user, account_index, cycles=cycles)
    logging.info(f"| 사용자 '{selected_user}' | HTS계좌순번 '{account_index}' | 활성 사이클: {[c['cycle_seq'] for c in active_cycles]}")

    for iternum, cycle in enumerate(active_cycles, start=1):
        cycle_id = cycle["id"]
        cycle_seq = cycle["cycle_seq"]
        method_ver = cycle.get("method", "")
        ticker = cycle.get("stock_code", "")

        logging.info(f">>>>> 사이클 #{cycle_seq} Aftermarket 추가매수 주문 진행중... ({len(active_cycles)}개 사이클 중 {iternum}번째)")
        logging.info(f"주문 실행할 종목 : {ticker}")
        logging.info(f"방법론 : {method_ver}")

        # 1회 매수금액 계산
        computed = _get_latest_computed(sb, cycle_id)
        if method_ver == "V2.2":
            principal = cycle.get("principal", 0)
            split_count = cycle.get("split_count", 10)
            daily_buy_amount = float(principal / split_count) if split_count else 0
        elif method_ver in ("V3.0", "V4.0"):
            daily_buy_amount = float(computed.get("repeating_per_buy") or computed.get("dynamic_per_buy") or computed.get("per_buy") or 0)
            if daily_buy_amount == 0:
                principal = cycle.get("principal", 0)
                split_count = cycle.get("split_count", 10)
                daily_buy_amount = float(principal / split_count) if split_count else 0
        else:
            logging.info(f"'{method_ver}'은(는) 지원하지 않는 방법론 버전입니다.")
            continue

        logging.info(f"1회매수금액 : {daily_buy_amount}")

        filtered_df = df[df['종목코드'] == ticker]
        if filtered_df.empty:
            logging.info("체결내역 중 해당 사이클 종목에 대한 내역이 없습니다.")
            continue

        logging.info(f"종목코드 '{ticker}'에 해당하는 {len(filtered_df)}건의 데이터가 필터링되었습니다.")
        logging.info(f"[종목코드 '{ticker}' 내역 {len(filtered_df)}건]\n{filtered_df}")

        # 체결 건수 확인 (매도+매수 전체)
        total_executed_qty = filtered_df['체결수량'].abs().sum()

        if total_executed_qty > 0:
            logging.info(f"[aftermarket] 사이클 #{cycle_seq}: 체결 {int(total_executed_qty)}건 있음. 추가 주문 불필요. 스킵.")
            continue
        else:
            # 매도/매수 아무것도 체결 안 됨 → aftermarket 보충 주문 발동
            # (evening 주문 거부/에러로 누락됐거나, 가격 포지션 문제로 전량 미체결)
            logging.info(f"[aftermarket] 사이클 #{cycle_seq}: 체결 건수 0 → 추가 매수 주문 실행")
            buy_df = filtered_df[filtered_df['주문구분'] == '매수']
            executed_amount = round((buy_df['체결수량'] * buy_df['체결단가']).sum(), 2)
            logging.info(f"정규장 매수 체결금액 : {executed_amount}")

            remaining_daily_buy_amount = round(daily_buy_amount - executed_amount, 2)
            logging.info(f"잔여매수금액 (1회매수금액 - 정규장 매수 체결금액) : {remaining_daily_buy_amount}")

            yfticker = yf.Ticker(ticker)
            info = yfticker.info

            aftermarket_price = info.get("postMarketPrice") or info.get("regularMarketPrice")
            if aftermarket_price is None:
                logging.info(f"'{ticker}'의 Aftermarket 가격 정보를 가져올 수 없습니다. 추가 주문을 건너뜁니다.")
                continue

            aftermarket_price = round(aftermarket_price, 2)
            logging.info(f"'{ticker}' Aftermarket 현재가 : {aftermarket_price}")

            buy_price = round(aftermarket_price * 1.03, 2)
            logging.info(f"추가주문 매수가(현재가+3%) : {buy_price}")

            buy_quantity = int(remaining_daily_buy_amount / buy_price)
            logging.info(f"추가주문 매수개수 : {buy_quantity}")

            buy_orders = [
                {"quantity": buy_quantity, "price": buy_price}
            ]

            order_type_index = 0  # 보통(지정가)

            buy_orders = [
                order for order in buy_orders
                if str(order["quantity"]).strip() not in ["", "0", 0, "None"]
                and str(order["price"]).strip() not in ["", "0", 0, "None"]
            ]

            if buy_orders:
                order_buy_success, order_buy_error = hts_order_buy(
                    selected_user,
                    account_index,
                    ticker,
                    buy_orders,
                    order_type_index,
                    is_test_mode,
                )
                if order_buy_success and not is_test_mode:
                    _record_order_status(cycle_id, [
                        {"order_type": "aftermarket_buy", "side": "buy",
                         "qty": int(o["quantity"]), "price": float(o["price"])}
                        for o in buy_orders if o.get("quantity") and o.get("price")
                    ])
                if order_buy_success:
                    formatted_orders = "\n".join([
                        f"   •  ${float(order['price']):,.2f}  |  {order['quantity']}주  |  보통(지정가)"
                        for order in buy_orders
                    ])
                    fail_section = f"\n▶ ⚠️ 일부 실패: {order_buy_error}" if order_buy_error else ""
                    message = (
                        f"📈 *[무매사이클 #{cycle_seq}] Aftermarket 매수 주문 완료*\n\n"
                        f"▶ 계좌: {selected_user} | 메리츠 | {account_index}번째 계좌\n"
                        f"▶ 종목: {ticker} ({method_ver})\n"
                        f"▶ 1회매수금액 : ${daily_buy_amount}\n"
                        f"▶ 정규장 매수 체결금액 : ${executed_amount}\n"
                        f"▶ 잔여매수금액 : *${remaining_daily_buy_amount}*\n"
                        f"▶ 추가주문내역\n"
                        f"*{formatted_orders}*"
                        f"{fail_section}"
                    )
                    send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)
                else:
                    message = (
                        f"📉 *[무매사이클 #{cycle_seq}] Aftermarket 매수 주문 실패❌*\n\n"
                        f"▶ 계좌: {selected_user} | 메리츠 | {account_index}번째 계좌\n"
                        f"▶ 종목: {ticker} ({method_ver})\n"
                        f"▶ 1회매수금액 : ${daily_buy_amount}\n"
                        f"▶ 정규장 매수 체결금액 : ${executed_amount}\n"
                        f"▶ 잔여매수금액 : *${remaining_daily_buy_amount}*\n"
                        f"▶ Aftermarket 현재가 : *${aftermarket_price}*\n"
                        f"▶ 에러내역\n"
                        f"*{order_buy_error}*"
                    )
                    send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)
            else:
                logging.info(">>>>> Aftermarket에서 추가매수할 데이터가 없으므로 주문을 SKIP합니다. <<<<<")


if __name__ == "__main__":
    # ------------------------------------------------------------
    # 로컬 테스트용 실행 블록
    # - TEST_USER / TEST_ACCOUNT 를 직접 지정 가능. None 이면 Supabase 자동 로드.
    # - IS_TEST_MODE=True 면 추가매수 주문 확인 팝업까지만 진행 (실제 주문하지 않음).
    # ------------------------------------------------------------
    TEST_USER: str | None = None
    TEST_ACCOUNT: int | None = None
    IS_TEST_MODE = True

    try:
        from automation_target_store import resolve_first_user_account

        selected_user, account_index = resolve_first_user_account(TEST_USER, TEST_ACCOUNT)
        hts_orders_aftermarket(selected_user, account_index, is_test_mode=IS_TEST_MODE)

    except Exception as e:
        logging.info("에러가 발생했습니다:")
        logging.info(f"에러 메시지: {e}")
        logging.error(traceback.format_exc())
