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
from config import Config
from supabase_client import get_supabase_client, supabase_fetch_all
import logging
import datetime as dt
import pandas as pd
import yfinance as yf
import traceback

TELEGRAM_BOT_TOKEN = Config.TELEGRAM_BOT_TOKEN_ORDER
TELEGRAM_CHAT_ID = Config.TELEGRAM_CHAT_ID


def _get_active_cycles(sb, selected_user, account_index, cycles=None):
    """cycle_master에서 활성 사이클 목록 조회"""
    res = supabase_fetch_all(
        lambda s, e: sb.table("cycle_master")
        .select("id, cycle_seq, status, method, stock_code, principal, split_count")
        .in_("status", ["진행중", "시작전"])
        .eq("user_name", selected_user)
        .eq("account_index", account_index)
        .eq("broker", "메리츠")
        .not_.is_("account_id", "null")
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

    active_cycles = _get_active_cycles(sb, selected_user, account_index, cycles)
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
        elif method_ver == "V3.0":
            # V3.0: computed에서 repeating_per_buy 또는 per_buy 사용
            daily_buy_amount = float(computed.get("repeating_per_buy") or computed.get("per_buy") or 0)
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

        loc_sell_df = filtered_df[(filtered_df['주문구분'] == '매도') & (filtered_df['주문조건'] == 'LOC')]
        total_loc_sell_qty = loc_sell_df['체결수량'].sum()

        buy_df = filtered_df[(filtered_df['주문구분'] == '매수')]
        total_buy_qty = buy_df['체결수량'].sum()

        if total_loc_sell_qty == 0:
            has_rejection = filtered_df['주문상태'].str.contains("거부", na=False).any()
            if (total_buy_qty == 0) or (has_rejection):
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
                    if order_buy_success:
                        formatted_orders = "\n".join([
                            f"   •  ${float(order['price']):,.2f}  |  {order['quantity']}주  |  보통(지정가)"
                            for order in buy_orders
                        ])
                        message = (
                            f"📈 *[무매사이클 #{cycle_seq}] Aftermarket 매수 주문 완료*\n\n"
                            f"▶ 계좌: {selected_user} | 메리츠 | {account_index}번째 계좌\n"
                            f"▶ 종목: {ticker} ({method_ver})\n"
                            f"▶ 1회매수금액 : ${daily_buy_amount}\n"
                            f"▶ 정규장 매수 체결금액 : ${executed_amount}\n"
                            f"▶ 잔여매수금액 : *${remaining_daily_buy_amount}*\n"
                            f"▶ 추가주문내역\n"
                            f"*{formatted_orders}*"
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
            else:
                logging.info(">>>>> 정규장에서 주문거부되거나 매수주문 전량 미체결되지 않았으므로 Aftermarket 추가매수 주문을 SKIP합니다. <<<<<")
                continue
        else:
            logging.info(">>>>> 정규장에서 LOC 매도가 이루어졌으므로 Aftermarket 추가매수 주문을 SKIP합니다. <<<<<")
            continue


if __name__ == "__main__":
    try:
        from automation_target_store import load_automation_target_with_meta

        targets, _ = load_automation_target_with_meta(None, include_cycles=True)
        users = {}
        for job_targets in targets.values() if isinstance(targets, dict) else []:
            if isinstance(job_targets, dict):
                users.update(job_targets)
                break

        selected_user = list(users.keys())[0] if users else ""
        account_index = 1
        if selected_user and users.get(selected_user):
            first_account = users[selected_user][0] if isinstance(users[selected_user], list) else None
            if isinstance(first_account, dict):
                account_index = first_account.get("account", 1)
            elif isinstance(first_account, int):
                account_index = first_account

        if not selected_user:
            print("경고: 저장된 사용자/계좌 설정이 없습니다. 웹UI에서 먼저 설정해주세요.")

        hts_orders_aftermarket(selected_user, account_index, is_test_mode=True)

    except Exception as e:
        logging.info("에러가 발생했습니다:")
        logging.info(f"에러 메시지: {e}")
        logging.error(traceback.format_exc())
