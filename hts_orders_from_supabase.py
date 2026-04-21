"""
Supabase에서 주문 데이터를 읽어 HTS 매도/매수 주문을 실행하는 모듈.
기존 hts_orders_from_gspread.py를 Supabase 기반으로 대체.
"""
from utils import (
    send_telegram_message,
    is_trading_day_today,
    is_after_regular_session,
    is_aftermarket_open,
    load_csv_if_exists,
)
from hts_order_buy import hts_order_buy
from hts_order_sell import hts_order_sell
from config import Config
from supabase_client import get_supabase_client, supabase_fetch_all
import logging
import traceback
import httpx
import yfinance as yf
from hts_orders_history_save_to_csv import save_orders_history
from order_history_data_preprocessing import order_history_data_preprocessing

TELEGRAM_BOT_TOKEN = Config.TELEGRAM_BOT_TOKEN_ORDER
TELEGRAM_CHAT_ID = Config.TELEGRAM_CHAT_ID


def _get_aftermarket_price(ticker: str) -> float | None:
    """yfinance로 현재 애프터마켓/정규장 가격 조회."""
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info if hasattr(t, "fast_info") else t.info
        price = getattr(info, "last_price", None) or info.get("regularMarketPrice")
        return float(price) if price else None
    except Exception as e:
        logging.warning(f"[aftermarket] {ticker} 현재가 조회 실패: {e}")
        return None


def _convert_orders_for_aftermarket(sell_orders, buy_orders, ticker):
    """장 마감 후: LOC/MOC 주문을 지정가로 변환.

    - LOC매도(3) → 지정가매도(0), 같은 가격 유지
    - MOC매도(5) → 지정가매도(0), 현재가 × 0.97
    - LOC매수(3) → 지정가매수(0), 현재가 × 1.03
    - 지정가매도(0) → 변경 없음
    """
    current_price = _get_aftermarket_price(ticker)
    if current_price is None:
        logging.warning(f"[aftermarket] {ticker} 현재가를 조회할 수 없어 LOC/MOC 주문을 변환할 수 없습니다.")
        return sell_orders, buy_orders, 0  # 변환 실패 → 원본 그대로 (HTS에서 거부될 수 있음)

    logging.info(f"[aftermarket] {ticker} 현재가: ${current_price:.2f}")

    new_sell_orders = []
    for o in sell_orders:
        oti = o.get("order_type_index", 0)
        if oti == 5:  # MOC → 지정가, 현재가 × 0.97
            new_price = round(current_price * 0.97, 2)
            logging.info(f"[aftermarket] MOC매도 → 지정가매도 @ ${new_price:.2f} (현재가×0.97)")
            new_sell_orders.append({**o, "order_type_index": 0, "price": new_price})
        elif oti == 3:  # LOC → 지정가, 같은 가격
            logging.info(f"[aftermarket] LOC매도 → 지정가매도 @ ${o['price']}")
            new_sell_orders.append({**o, "order_type_index": 0})
        else:
            new_sell_orders.append(o)

    new_buy_orders = []
    buy_price_override = round(current_price * 1.03, 2)
    for o in buy_orders:
        new_buy_orders.append({**o, "price": buy_price_override})
    if new_buy_orders:
        logging.info(f"[aftermarket] 매수 가격 → 지정가 ${buy_price_override:.2f} (현재가×1.03)")

    return new_sell_orders, new_buy_orders, 0  # order_type_index for buy = 0 (지정가)


def _get_active_cycles(sb, selected_user, account_index, auth_user_ids=None, cycles=None):
    """cycle_master에서 활성 사이클 목록 조회"""
    from automation_target_store import get_auth_user_ids
    uids = auth_user_ids or get_auth_user_ids()
    res = supabase_fetch_all(
        lambda s, e: sb.table("cycle_master")
        .select("id, cycle_seq, status, method, stock_code, principal, split_count, target_rate, dip_buy_rate, max_drop_rate, start_date")
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


def _record_order_status(cycle_id: int, orders: list):
    """콘솔에 주문 상태를 기록한다."""
    console_url = Config.CONSOLE_URL.rstrip("/") if Config.CONSOLE_URL else ""
    agent_key = Config.HTS_AGENT_KEY
    if not console_url:
        return
    headers = {"X-Agent-Key": agent_key} if agent_key else {}
    try:
        httpx.post(
            f"{console_url}/api/order-status",
            json={"cycle_id": cycle_id, "orders": orders},
            headers=headers,
            timeout=10.0,
        )
    except Exception as e:
        logging.warning(f"[order-status] 기록 실패: {e}")


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


def _extract_order_list_v40(computed):
    """V4.0 주문 리스트 추출 (computed JSON 기반)

    V4.0 은 일반 모드와 리버스 모드 두 가지가 있다.
    리버스 모드 여부는 computed["v4_mode"] 필드로 판별.

    일반 모드: V3.0 과 동일한 필드 구조 (star_loc_sell, limit_sell, avg_loc_buy,
              star_loc_buy, dip_buy, extra_loc_buy 사용)
    리버스 모드: star_loc_sell (MOC 또는 LOC) + star_loc_buy 만 사용.
              avg_loc_buy / dip_buy / extra_loc_buy 는 비활성.
    """
    v4_mode = computed.get("v4_mode", "normal")
    star_loc_sell_price = computed.get("star_loc_sell_price")
    if star_loc_sell_price == "MOC매도":
        order_type_index_loc_sell = 5  # MOC
    else:
        order_type_index_loc_sell = 3  # LOC

    if v4_mode == "reverse":
        logging.info("[V4.0 리버스 모드]")
        sell_orders = [
            {"quantity": computed.get("star_loc_sell_qty"), "price": star_loc_sell_price, "order_type_index": order_type_index_loc_sell},
        ]
        buy_orders = [
            {"quantity": computed.get("star_loc_buy_qty"), "price": computed.get("star_loc_buy_price")},
        ]
    else:
        # 일반 모드 (V3.0 과 동일한 구조)
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

        from utils import set_log_context, _log_context
        set_log_context(job=_log_context.get("job", ""), user=selected_user, account=account_index, cycle=f"{cycle_seq} {ticker}")
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
                # CSV에 해당 종목이 없음: 시작전(보유수0)이면 정상, 진행중이면 경고
                if holding_qty_from_db > 0:
                    logging.warning(f"[경고] 종목코드 '{ticker}' 가 df_balance에 존재하지 않지만 DB 보유수는 {holding_qty_from_db}주. 스킵합니다.")
                    send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                        f"⚠️ *[무매사이클 #{cycle_seq}] 잔고 확인 불가*\n\n"
                        f"▶ 종목: {ticker} ({method_ver})\n"
                        f"▶ DB 보유수: {holding_qty_from_db}주\n"
                        f"▶ HTS 잔고 CSV에 해당 종목 없음\n"
                        f"▶ 잔고 확인 불가로 주문을 건너뜁니다."
                    )
                    continue
                else:
                    logging.info(f"종목코드 '{ticker}'가 df_balance에 없지만 DB 보유수도 0이므로 정상 (시작전 사이클)")
        else:
            # CSV 파일 자체가 없음
            if holding_qty_from_db > 0:
                logging.warning(f"해외주식 보유잔고 CSV 파일이 없고 DB 보유수는 {holding_qty_from_db}주. 진행중 사이클이므로 스킵합니다.")
                send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                    f"⚠️ *[무매사이클 #{cycle_seq}] 잔고 CSV 없음*\n\n"
                    f"▶ 종목: {ticker} ({method_ver})\n"
                    f"▶ DB 보유수: {holding_qty_from_db}주\n"
                    f"▶ 보유잔고 CSV 저장 실패로 잔고 확인 불가\n"
                    f"▶ 주문을 건너뜁니다."
                )
                continue
            else:
                logging.info("해외주식 보유잔고 CSV 파일이 없지만 DB 보유수가 0이므로 진행 (시작전 사이클)")

        # 주문 리스트 추출
        # 시작전 사이클이고 computed가 비어있으면 첫 매수 주문 직접 계산
        if cycle.get("status") == "시작전" and (not computed or not computed.get("avg_loc_buy_qty")):
            # 전일종가 실시간 조회
            prev_close = None
            try:
                t = yf.Ticker(ticker)
                info = t.fast_info if hasattr(t, 'fast_info') else {}
                prev_close = getattr(info, 'previous_close', None) or info.get('previousClose')
                if prev_close and float(prev_close) > 0:
                    logging.info(f"시작전 사이클: 전일종가 실시간 조회 ${prev_close}")
                else:
                    prev_close = None
            except Exception as e:
                logging.warning(f"전일종가 실시간 조회 실패: {e}")
            principal = float(cycle.get("principal") or 0)
            split_count = int(cycle.get("split_count") or 1)
            max_drop_rate = float(cycle.get("max_drop_rate") or -30)
            if prev_close and float(prev_close) > 0 and principal > 0 and split_count >= 1:
                pc = float(prev_close)
                per_buy = principal / split_count
                avg_loc_buy_price = round(pc * 1.1, 2)
                avg_loc_buy_qty = int(per_buy / avg_loc_buy_price) if avg_loc_buy_price > 0 else 0
                # 폭락대비 추가 LOC 매수
                max_drop_price = round(pc * (1 + max_drop_rate / 100), 2)
                extra_loc_buy_qty = max(0, int(per_buy // max_drop_price) - avg_loc_buy_qty) if max_drop_price > 0 else 0
                extra_loc_buy_prices = []
                for n in range(1, extra_loc_buy_qty + 1):
                    total = avg_loc_buy_qty + n
                    if total > 0 and per_buy > 0:
                        extra_loc_buy_prices.append(round(per_buy / total, 2))
                if avg_loc_buy_qty > 0:
                    computed["avg_loc_buy_qty"] = avg_loc_buy_qty
                    computed["avg_loc_buy_price"] = avg_loc_buy_price
                    computed["max_drop_price"] = max_drop_price
                    computed["extra_loc_buy_qty"] = extra_loc_buy_qty
                    computed["extra_loc_buy_prices"] = extra_loc_buy_prices
                    logging.info(f"시작전 사이클 첫 매수: LOC {avg_loc_buy_qty}주 @ ${avg_loc_buy_price}, 추가LOC {extra_loc_buy_qty}주")

        version_map = {
            "V2.2": _extract_order_list_v22,
            "V3.0": _extract_order_list_v30,
            "V4.0": _extract_order_list_v40,
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
        buy_orders = [
            order for order in buy_orders
            if str(order["quantity"]).strip() not in invalid_values
            and str(order["price"]).strip() not in invalid_values
        ]

        # 장 마감 후 실행 시: LOC/MOC → 지정가 전환 (애프터마켓 모드)
        aftermarket_mode = is_after_regular_session()
        order_type_index = 3  # LOC (기본)

        if aftermarket_mode:
            if not is_aftermarket_open():
                logging.warning("═══ 애프터마켓 시간 초과 (ET 19:50 이후): 주문 불가. 이 사이클을 건너뜁니다. ═══")
                send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                    f"⚠️ *[무매사이클 #{cycle_seq}] 주문 불가*\n\n"
                    f"▶ 종목: {ticker} ({method_ver})\n"
                    f"▶ 사유: 애프터마켓 시간(ET 16:00~19:50) 초과\n"
                    f"▶ 다음 거래일 evening job에서 자동 실행됩니다."
                )
                continue
            logging.info("═══ 애프터마켓 모드: 정규장 마감 후 실행. LOC/MOC → 지정가 전환 ═══")
            sell_orders, buy_orders, order_type_index = _convert_orders_for_aftermarket(
                sell_orders, buy_orders, ticker
            )

        # 중복 주문 방지: order_status에 당일 주문 기록이 있으면 스킵
        if not is_test_mode:
            try:
                from zoneinfo import ZoneInfo
                from datetime import datetime as _dt
                today_et = _dt.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
                console_url = Config.CONSOLE_URL.rstrip("/") if Config.CONSOLE_URL else ""
                agent_key = Config.HTS_AGENT_KEY
                if console_url:
                    headers = {"X-Agent-Key": agent_key} if agent_key else {}
                    check_res = httpx.get(
                        f"{console_url}/api/order-status/check",
                        params={"cycle_id": cycle_id, "order_date": today_et},
                        headers=headers,
                        timeout=5.0,
                    )
                    if check_res.status_code == 200:
                        check_data = check_res.json()
                        if check_data.get("has_orders"):
                            logging.info(f"[중복방지] 사이클 #{cycle_seq}의 당일({today_et}) 주문이 이미 존재합니다. 스킵합니다.")
                            continue
            except Exception as e:
                logging.warning(f"[중복방지] order_status 확인 실패 (무시하고 진행): {e}")

        if sell_orders:
            sell_success, sell_err = hts_order_sell(selected_user, account_index, ticker, sell_orders, is_test_mode)
            if sell_success and not is_test_mode:
                order_type_label = "limit_sell" if aftermarket_mode else None
                _record_order_status(cycle_id, [
                    {"order_type": order_type_label or (o.get("order_type_index", 0) == 0 and "limit_sell" or "loc_sell"),
                     "side": "sell", "qty": int(o["quantity"]), "price": float(o["price"])}
                    for o in sell_orders
                ])
        else:
            logging.info(">>>>> 매도할 데이터가 없으므로 주문을 SKIP합니다. <<<<<")

        if buy_orders:
            buy_success, buy_err = hts_order_buy(selected_user, account_index, ticker, buy_orders, order_type_index, is_test_mode)
            if buy_success and not is_test_mode:
                buy_type = "limit_buy" if aftermarket_mode else "loc_buy"
                _record_order_status(cycle_id, [
                    {"order_type": buy_type, "side": "buy",
                     "qty": int(o["quantity"]), "price": float(o["price"])}
                    for o in buy_orders if o.get("quantity") and o.get("price")
                ])
        else:
            logging.info(">>>>> 매수할 데이터가 없으므로 주문을 SKIP합니다. <<<<<")

        if not is_test_mode:
            save_orders_history(selected_user, account_index)
            order_history_data_preprocessing(selected_user, account_index)
            file_path = f'./data/order_history_processed/order_history_processed_{selected_user}_{account_index}.csv'
            df_order_history = load_csv_if_exists(file_path)
            if df_order_history is None or df_order_history.empty:
                order_lines = "(주문내역 CSV 없음)"
            else:
                df_order_history = df_order_history.sort_values(by='주문시간').reset_index(drop=True)
                df_order_history_filtered = df_order_history[df_order_history['종목코드'] == ticker]
                # 가격 내림차순 정렬
                df_order_history_filtered = df_order_history_filtered.copy()
                df_order_history_filtered['_price'] = df_order_history_filtered['주문가'].apply(lambda x: float(str(x).replace(',', '')) if x else 0)
                df_order_history_filtered = df_order_history_filtered.sort_values(by='_price', ascending=False)
                order_lines = "\n".join([
                    f"   •  ${float(str(row['주문가']).replace(',', '')):,.2f}  |  "
                    f"{'-' if '매도' in row['매매구분'] else ''}{int(float(str(row['주문량']).replace(',', '')))}주  |  "
                    f"{'지정가' if row['주문유형'] == '보통' else row['주문유형']}"
                    for _, row in df_order_history_filtered.iterrows()
                ])
        else:
            order_lines = "테스트모드"

        # 추가 정보 계산
        principal = float(cycle.get("principal") or 0)
        split_count = int(cycle.get("split_count") or 1)
        t_value = computed.get("t_value", 0) or 0
        per_buy_val = computed.get("dynamic_per_buy") or computed.get("repeating_per_buy") or computed.get("per_buy") or (principal / split_count if split_count else 0)
        cumulative_pnl = computed.get("cumulative_pnl", 0) or 0
        t_display = f"{float(t_value):.1f}T" if t_value else "0T"
        pnl_sign = "+" if cumulative_pnl >= 0 else ""

        start_date = cycle.get("start_date") or "-"

        message = (
            f"📝 *[무매사이클 #{cycle_seq}] 매매 주문 내역*\n\n"
            f"▶ 계좌: 메리츠 | {selected_user} | {account_index}번째 계좌\n"
            f"▶ 종목: *{ticker} ({method_ver})*\n"
            f"▶ 원금: ${principal:,.0f} | {split_count}분할 | 1회매수금: ${per_buy_val:,.0f}\n"
            f"▶ 시작일: {start_date}\n"
            f"▶ 보유수량: {balance_from_hts}주 | 진행률: {progress_rate} ({t_display}){quarter_progress}\n"
            f"▶ 현재가: ${current_price} | 평단가: ${average_price}\n"
            f"▶ 평가손익: ${profit} ({profit_rate_pct}%)\n"
            f"▶ 실현손익금: {pnl_sign}${abs(cumulative_pnl):,.2f}\n"
            f"▶ 실제 HTS 주문내역\n"
            f"{order_lines}"
        )
        send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)


if __name__ == "__main__":
    # ------------------------------------------------------------
    # 로컬 테스트용 실행 블록
    # - TEST_USER / TEST_ACCOUNT 를 직접 지정 가능. None 이면 Supabase 자동 로드.
    # - IS_TEST_MODE=True 면 주문 확인 팝업까지만 진행 (실제 주문하지 않음).
    # ------------------------------------------------------------
    TEST_USER: str | None = None
    TEST_ACCOUNT: int | None = None
    IS_TEST_MODE = True

    try:
        from automation_target_store import resolve_first_user_account

        selected_user, account_index = resolve_first_user_account(TEST_USER, TEST_ACCOUNT)
        hts_orders_from_supabase(selected_user, account_index, is_test_mode=IS_TEST_MODE)

    except Exception as e:
        logging.info("에러가 발생했습니다:")
        logging.info(f"에러 메시지: {e}")
        logging.error(traceback.format_exc())
