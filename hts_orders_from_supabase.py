"""
Supabase에서 주문 데이터를 읽어 HTS 매도/매수 주문을 실행하는 모듈.
기존 hts_orders_from_gspread.py를 Supabase 기반으로 대체.
"""
from utils import (
    send_telegram_message,
    is_trading_day_today,
    load_csv_if_exists,
)
from hts_order_buy import hts_order_buy
from hts_order_sell import hts_order_sell
from config import Config
from supabase_client import get_supabase_client, supabase_fetch_all
import logging
import traceback
from hts_orders_history_save_to_csv import save_orders_history
from order_history_data_preprocessing import order_history_data_preprocessing

TELEGRAM_BOT_TOKEN = Config.TELEGRAM_BOT_TOKEN_ORDER
TELEGRAM_CHAT_ID = Config.TELEGRAM_CHAT_ID


def _get_active_cycles(sb, selected_user, account_index, auth_user_ids=None, cycles=None):
    """cycle_master에서 활성 사이클 목록 조회"""
    from automation_target_store import get_auth_user_ids
    uids = auth_user_ids or get_auth_user_ids()
    res = supabase_fetch_all(
        lambda s, e: sb.table("cycle_master")
        .select("id, cycle_seq, status, method, stock_code, principal, split_count, target_rate, dip_buy_rate, max_drop_rate")
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


def _extract_order_list_v22(computed):
    """V2.2 주문 리스트 추출 (computed JSON 기반)"""
    quarter_mode = computed.get("quarter_mode", "")

    if quarter_mode == "쿼터손절모드":
        logging.info("[쿼터손절모드]")
        sell_orders = [
            {"quantity": computed.get("q10_limit_sell_qty"), "price": computed.get("q10_limit_sell_price"), "order_type_index": 0},
            {"quantity": computed.get("qn10_loc_sell_qty"), "price": computed.get("qn10_loc_sell_price"), "order_type_index": 3},
        ]
        buy_orders = [
            {"quantity": computed.get("qn10_loc_buy_qty"), "price": computed.get("qn10_loc_buy_price")},
            {"quantity": computed.get("q_dip_buy_qty"), "price": computed.get("q_dip_buy_price")},
        ]
    else:
        sell_orders = [
            {"quantity": computed.get("limit_sell_qty"), "price": computed.get("limit_sell_price"), "order_type_index": 0},
            {"quantity": computed.get("star_loc_sell_qty"), "price": computed.get("star_loc_sell_price"), "order_type_index": 3},
        ]
        buy_orders = [
            {"quantity": computed.get("avg_loc_buy_qty"), "price": computed.get("avg_loc_buy_price")},
            {"quantity": computed.get("star_loc_buy_qty"), "price": computed.get("star_loc_buy_price")},
            {"quantity": computed.get("dip_buy_qty"), "price": computed.get("dip_buy_price")},
        ]

    # 하락대비 추가 LOC 매수
    extra_qty = computed.get("extra_loc_buy_qty", 0) or 0
    extra_prices = computed.get("extra_loc_buy_prices", []) or []
    if extra_qty and extra_prices:
        for i in range(min(int(extra_qty), len(extra_prices))):
            if extra_prices[i]:
                buy_orders.append({"quantity": 1, "price": extra_prices[i]})

    logging.info(f"매도 주문 리스트 : {sell_orders}")
    logging.info(f"매수 주문 리스트 : {buy_orders}")
    return sell_orders, buy_orders


def _extract_order_list_v30(computed):
    """V3.0 주문 리스트 추출 (computed JSON 기반)"""
    star_loc_sell_price = computed.get("star_loc_sell_price")
    if star_loc_sell_price == "MOC매도":
        order_type_index_loc_sell = 5  # MOC
    else:
        order_type_index_loc_sell = 3  # LOC

    sell_orders = [
        {"quantity": computed.get("limit_sell_qty"), "price": computed.get("limit_sell_price"), "order_type_index": 0},
        {"quantity": computed.get("star_loc_sell_qty"), "price": star_loc_sell_price, "order_type_index": order_type_index_loc_sell},
    ]
    buy_orders = [
        {"quantity": computed.get("avg_loc_buy_qty"), "price": computed.get("avg_loc_buy_price")},
        {"quantity": computed.get("star_loc_buy_qty"), "price": computed.get("star_loc_buy_price")},
        {"quantity": computed.get("dip_buy_qty"), "price": computed.get("dip_buy_price")},
    ]

    # 하락대비 추가 LOC 매수
    extra_qty = computed.get("extra_loc_buy_qty", 0) or 0
    extra_prices = computed.get("extra_loc_buy_prices", []) or []
    if extra_qty and extra_prices:
        for i in range(min(int(extra_qty), len(extra_prices))):
            if extra_prices[i]:
                buy_orders.append({"quantity": 1, "price": extra_prices[i]})

    logging.info(f"매도 주문 리스트 : {sell_orders}")
    logging.info(f"매수 주문 리스트 : {buy_orders}")
    return sell_orders, buy_orders


def hts_orders_from_supabase(
    selected_user,
    account_index,
    is_test_mode,
    cycles=None,
):
    """
    Supabase에서 주문 데이터를 가져와 HTS 매도/매수 주문 실행.
    - cycles: 실행할 사이클 번호 리스트 (None이면 해당 계좌의 모든 활성 사이클 실행)
    """
    if not is_test_mode:
        if not is_trading_day_today():
            return
    else:
        logging.info("테스트모드이므로 휴장일 여부와 관계없이 함수를 실행합니다.")

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

        logging.info(f">>>>> 사이클 #{cycle_seq} 매도/매수 진행중... ({len(active_cycles)}개 사이클 중 {iternum}번째)")
        logging.info(f"주문 실행할 종목 : {ticker}")
        logging.info(f"적용 방법론 : {method_ver}")

        # 최신 computed 데이터 조회
        computed = _get_latest_computed(sb, cycle_id)
        if not computed:
            logging.info(f"사이클 #{cycle_seq}의 계산 데이터가 없습니다. 첫 주문(시작전 상태) 실행.")

        progress_rate_raw = computed.get("progress_rate", 0)
        progress_rate = f"{progress_rate_raw * 100:.1f}%"
        holding_qty_from_db = computed.get("holding_qty", 0)
        logging.info(f"현재 진행률 : {progress_rate}")

        quarter_progress = ""
        if method_ver == "V2.2":
            quarter_mode = computed.get("quarter_mode", "")
            if quarter_mode == "쿼터손절모드":
                quarter_rate = computed.get("quarter_progress", 0)
                quarter_progress = f" (쿼터손절모드 {quarter_rate}/10회)"

        # HTS 잔고 CSV와 DB 보유수 비교
        logging.info("HTS로부터 가져온 해외주식 보유잔고 데이터와 DB에 기록된 최신 보유수가 일치하는지 확인중...")
        file_path = f'./data/stock_balance_processed/stock_balance_processed_{selected_user}_{account_index}.csv'
        df_balance = load_csv_if_exists(file_path)

        balance_from_hts = 0
        current_price = 0
        average_price = 0
        profit = 0
        profit_rate_pct = 0
        eval_amount = 0
        purchase_amount = 0

        if df_balance is not None and not df_balance.empty:
            filtered = df_balance[df_balance['종목코드'] == ticker]
            if not filtered.empty:
                balance_from_hts = str(filtered['보유수량'].iloc[0])
                current_price = filtered['현재가'].iloc[0]
                average_price = filtered['평균가'].iloc[0]
                profit = filtered['평가손익'].iloc[0]
                profit_rate_pct = filtered['수익률(%)'].iloc[0]
                eval_amount = filtered['평가금액(외화)'].iloc[0]
                purchase_amount = filtered['매입금액(외화)'].iloc[0]

                logging.info(f"HTS 해외주식 보유잔고 : {balance_from_hts}")
                logging.info(f"DB에 기록된 최신 보유수 : {holding_qty_from_db}")

                if int(float(str(balance_from_hts).replace(',', ''))) == int(float(str(holding_qty_from_db))):
                    logging.info("데이터 일치하므로 매매 진행")
                else:
                    logging.info("데이터 불일치하므로 매매 진행하지 않고 다음 사이클로 넘어감")
                    message = (
                        f"📉 *[무매사이클 #{cycle_seq}] 주문 실패❌*\n\n"
                        f"▶ 계좌: {selected_user} | 메리츠 | {account_index}번째 계좌\n"
                        f"▶ 종목: {ticker} ({method_ver})\n"
                        f"▶ 보유수량: {balance_from_hts}주\n"
                        f"▶ 진행률: {progress_rate}{quarter_progress}\n"
                        f"▶ 평가금액: ${eval_amount} | 총매입금액: ${purchase_amount}\n"
                        f"▶ 현재가: ${current_price} | 평단가: ${average_price}\n"
                        f"▶ 평가손익 : ${profit} ({profit_rate_pct}%)\n"
                        f"▶ 에러내역\n"
                        f"(HTS로부터 가져온 해외주식 보유잔고 데이터와 DB에 기록된 최신 보유수가 일치하지 않음. DB 및 HTS 재확인 필요)"
                    )
                    send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)
                    continue
            else:
                logging.warning(f"[경고] 종목코드 '{ticker}' 가 df_balance에 존재하지 않습니다.")
        else:
            logging.info("해외주식 보유잔고 CSV 파일이 없으므로 DB에 기록된 최신 보유수와 일치여부 확인 불가")

        # 주문 리스트 추출
        version_map = {
            "V2.2": _extract_order_list_v22,
            "V3.0": _extract_order_list_v30,
        }

        if method_ver not in version_map:
            logging.info(f"'{method_ver}'은(는) 지원하지 않는 방법론 버전입니다. 빈 리스트를 반환합니다.")

        sell_orders, buy_orders = version_map.get(
            method_ver,
            lambda *_: ([], [])
        )(computed)

        # 유효하지 않은 주문 필터링
        invalid_values = {"", "0", 0, "쿼터손절모드", "None", "#N/A", "N/A", "#VALUE!", "#DIV/0!", None}
        sell_orders = [
            order for order in sell_orders
            if str(order["quantity"]).strip() not in invalid_values
            and str(order["price"]).strip() not in invalid_values
        ]
        if sell_orders:
            hts_order_sell(selected_user, account_index, ticker, sell_orders, is_test_mode)
        else:
            logging.info(">>>>> 매도할 데이터가 없으므로 주문을 SKIP합니다. <<<<<")

        order_type_index = 3  # LOC
        buy_orders = [
            order for order in buy_orders
            if str(order["quantity"]).strip() not in invalid_values
            and str(order["price"]).strip() not in invalid_values
        ]
        if buy_orders:
            hts_order_buy(selected_user, account_index, ticker, buy_orders, order_type_index, is_test_mode)
        else:
            logging.info(">>>>> 매수할 데이터가 없으므로 주문을 SKIP합니다. <<<<<")

        if not is_test_mode:
            save_orders_history(selected_user, account_index)
            order_history_data_preprocessing(selected_user, account_index)
            file_path = f'./data/order_history_processed/order_history_processed_{selected_user}_{account_index}.csv'
            df_order_history = load_csv_if_exists(file_path)
            df_order_history = df_order_history.sort_values(by='주문시간').reset_index(drop=True)
            df_order_history_filtered = df_order_history[df_order_history['종목코드'] == ticker]
            order_lines = "\n".join([
                f"   •  ${float(str(row['주문가']).replace(',', '')):,.2f}  |  "
                f"{'-' if '매도' in row['매매구분'] else ''}{int(float(str(row['주문량']).replace(',', '')))}주  |  "
                f"{'지정가' if row['주문유형'] == '보통' else row['주문유형']}"
                for _, row in df_order_history_filtered.iterrows()
            ])
        else:
            order_lines = "테스트모드"

        message = (
            f"📝 *[무매사이클 #{cycle_seq}] 매매 주문 내역*\n\n"
            f"▶ 계좌: 메리츠 | {selected_user} | {account_index}번째 계좌\n"
            f"▶ 종목: *{ticker} ({method_ver})*\n"
            f"▶ 보유수량: {balance_from_hts}주\n"
            f"▶ 진행률: {progress_rate}{quarter_progress}\n"
            f"▶ 평가금액: ${eval_amount} | 총매입금액: ${purchase_amount}\n"
            f"▶ 현재가: ${current_price} | 평단가: ${average_price}\n"
            f"▶ 평가손익 : ${profit} ({profit_rate_pct}%)\n"
            f"▶ 실제 HTS 주문내역\n"
            f"{order_lines}"
        )
        send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)


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

        hts_orders_from_supabase(selected_user, account_index, is_test_mode=True)

    except Exception as e:
        logging.info("에러가 발생했습니다:")
        logging.info(f"에러 메시지: {e}")
        logging.error(traceback.format_exc())
