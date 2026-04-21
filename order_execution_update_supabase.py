"""
HTS 체결내역을 Supabase cycle_trades에 INSERT하고 콘솔에 recompute를 트리거하는 모듈.
기존 order_execution_update_gspread.py를 Supabase 기반으로 대체.
"""
from utils import (
    send_telegram_message,
    load_csv_if_exists,
)
from config import Config
from supabase_client import get_supabase_client, supabase_fetch_all
import logging
import datetime as dt
import pandas as pd
import httpx

TELEGRAM_BOT_TOKEN = Config.TELEGRAM_BOT_TOKEN_EXECUTION
TELEGRAM_CHAT_ID = Config.TELEGRAM_CHAT_ID


def _get_active_cycles(sb, selected_user, account_index, auth_user_ids=None, cycles=None):
    """cycle_master에서 활성 사이클 목록 조회"""
    from automation_target_store import get_auth_user_ids
    uids = auth_user_ids or get_auth_user_ids()
    res = supabase_fetch_all(
        lambda s, e: sb.table("cycle_master")
        .select("id, cycle_seq, status, method, stock_code, auth_user_id, principal, split_count")
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


def _trigger_recompute(cycle_id: int):
    """콘솔 API를 호출하여 recompute를 트리거한다."""
    console_url = Config.CONSOLE_URL.rstrip("/")
    agent_key = Config.HTS_AGENT_KEY
    if not console_url:
        logging.warning("[recompute] CONSOLE_URL이 설정되지 않아 recompute를 트리거할 수 없습니다.")
        return False
    headers = {"X-Agent-Key": agent_key} if agent_key else {}
    try:
        resp = httpx.post(
            f"{console_url}/recompute/{cycle_id}",
            headers=headers,
            timeout=60.0,
        )
        resp.raise_for_status()
        logging.info(f"[recompute] 사이클 {cycle_id} recompute 완료")
        return True
    except httpx.ConnectError:
        logging.warning(f"[recompute] 콘솔({console_url})에 연결할 수 없습니다.")
        return False
    except httpx.HTTPStatusError as e:
        logging.warning(f"[recompute] 사이클 {cycle_id} recompute 실패: {e.response.status_code} {e.response.text}")
        return False
    except Exception as e:
        logging.warning(f"[recompute] 사이클 {cycle_id} recompute 예외: {e}")
        return False


def _sync_order_status(sb, cycle_id: int, auth_user_id: str, all_orders_df, trade_date: str):
    """
    CSV의 전체 주문내역(체결+미체결)을 기준으로 order_status를 동기화.
    - 기존 ordered 중 체결된 건 → filled로 업데이트
    - order_status에 없는 주문 → 신규 생성 (filled 또는 ordered)
    """
    if all_orders_df is None or all_orders_df.empty:
        return
    try:
        # 해당 사이클의 기존 order_status 조회 (ordered + filled 모두)
        os_res = (
            sb.table("order_status")
            .select("id, side, price, status")
            .eq("cycle_id", cycle_id)
            .gte("order_date", trade_date)
            .execute()
        )
        os_rows = os_res.data or []

        update_filled = []  # (id, execution_price) — ordered → filled
        new_rows = []  # 신규 생성

        for _, row in all_orders_df.iterrows():
            raw_side = row.get("주문구분", "")
            side = "buy" if raw_side == "매수" else "sell"
            order_price = float(row.get("주문단가", 0))
            exec_qty = int(row.get("체결수량", 0))
            exec_price = float(row.get("체결단가", 0)) if exec_qty != 0 else None
            is_filled = exec_qty != 0
            target_status = "filled" if is_filled else "ordered"

            # 기존 order_status에서 매칭 찾기 (주문가 기준)
            matched_os = None
            used_ids = [u[0] for u in update_filled]
            for os_row in os_rows:
                if os_row["id"] in used_ids:
                    continue
                if os_row["side"] == side and abs(float(os_row["price"]) - order_price) < 0.5:
                    matched_os = os_row
                    break

            if matched_os:
                # 기존 레코드 있음 — 상태/체결가 업데이트
                if matched_os["status"] == "ordered" and is_filled:
                    update_filled.append((matched_os["id"], exec_price))
            else:
                # 기존 레코드 없음 — 신규 생성
                order_cond = row.get("주문조건", "")
                if order_cond == "LOC":
                    ot = "loc_buy" if side == "buy" else "loc_sell"
                else:
                    ot = "limit_buy" if side == "buy" else "limit_sell"
                new_row = {
                    "auth_user_id": auth_user_id,
                    "cycle_id": cycle_id,
                    "order_type": ot,
                    "side": side,
                    "qty": abs(int(row.get("주문수량", 0))),
                    "price": order_price,
                    "status": target_status,
                    "order_date": trade_date,
                }
                if exec_price is not None:
                    new_row["execution_price"] = exec_price
                new_rows.append(new_row)

        # 기존 레코드 업데이트 (개별 — 각각 다른 execution_price)
        if update_filled:
            for os_id, ep in update_filled:
                patch = {"status": "filled"}
                if ep is not None:
                    patch["execution_price"] = ep
                sb.table("order_status").update(patch).eq("id", os_id).execute()
            logging.info(f"[order-status] 사이클 {cycle_id}: {len(update_filled)}건 filled 업데이트")

        if new_rows:
            sb.table("order_status").insert(new_rows).execute()
            filled_cnt = sum(1 for r in new_rows if r["status"] == "filled")
            ordered_cnt = len(new_rows) - filled_cnt
            logging.info(f"[order-status] 사이클 {cycle_id}: 신규 {len(new_rows)}건 (체결 {filled_cnt}, 미체결 {ordered_cnt})")
    except Exception as e:
        logging.warning(f"[order-status] 동기화 실패: {e}")


def orders_execution_update_supabase(
    selected_user,
    account_index,
    is_test_mode,
    inquiry_start_date=None,
    inquiry_end_date=None,
    cycles=None,
):
    """
    체결내역 CSV를 읽어 Supabase cycle_trades에 INSERT하고 recompute 트리거.
    - cycles: 업데이트할 사이클 번호 리스트 (None이면 해당 계좌의 모든 활성 사이클 업데이트)
    """
    logging.info(">>>>> Supabase에 체결내역 데이터 업데이트 시작! <<<<<")

    if inquiry_start_date is None:
        inquiry_start_date = (dt.date.today() - dt.timedelta(days=1)).strftime('%Y%m%d')
    if inquiry_end_date is None:
        inquiry_end_date = (dt.date.today() - dt.timedelta(days=1)).strftime('%Y%m%d')

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

        logging.info(f">>>>> {cycle_seq}번 사이클 업데이트 진행중... ({len(active_cycles)}개 사이클 중 {iternum}번째)")
        logging.info(f"해당 사이클의 종목코드 : {ticker}")

        # 전체 주문 및 체결 내역 CSV 파일 불러오기
        file_path = f'./data/all_order_execution_processed/all_order_execution_processed_{selected_user}_{account_index}_{inquiry_start_date}-{inquiry_end_date}.csv'
        df = load_csv_if_exists(file_path)
        if df is None:
            continue

        logging.info(
            f"['{selected_user} {account_index}번째 계좌' 전체 주문 및 체결 내역 ({inquiry_start_date}~{inquiry_end_date})]\n"
            f"{df}"
        )

        # ticker 필터링
        filtered_df = df[df['종목코드'] == ticker]
        if filtered_df.empty:
            logging.info("체결내역 중 해당 사이클 종목에 대한 내역이 없습니다.")
            continue
        logging.info(f"종목코드 '{ticker}'에 해당하는 {len(filtered_df)}건의 데이터가 필터링되었습니다.")

        # 매도 시 수량 음수 변환
        filtered_df = filtered_df.copy()
        filtered_df.loc[filtered_df['주문구분'] == '매도', '체결수량'] *= -1
        filtered_df.loc[filtered_df['주문구분'] == '매도', '주문수량'] *= -1

        logging.info(f"[종목코드 '{ticker}' 내역 {len(filtered_df)}건]\n{filtered_df}")

        # computed에서 추가 정보 가져오기
        _computed = {}
        try:
            _comp_res = sb.table("cycle_trades_latest").select("computed").eq("cycle_id", cycle_id).execute()
            if _comp_res.data:
                _computed = _comp_res.data[0].get("computed") or {}
        except Exception:
            pass
        _principal = float(cycle.get("principal") or 0)
        _split_count = int(cycle.get("split_count") or 1)
        _t_value = _computed.get("t_value", 0) or 0
        _progress_rate = _computed.get("progress_rate", 0) or 0
        _per_buy = _computed.get("dynamic_per_buy") or _computed.get("repeating_per_buy") or _computed.get("per_buy") or (_principal / _split_count if _split_count else 0)
        _cumulative_pnl = _computed.get("cumulative_pnl", 0) or 0
        _avg_price = float(_computed.get("avg_price") or 0)
        _t_display = f"{float(_t_value):.1f}T" if _t_value else "0T"
        _progress_display = f"{_progress_rate * 100:.1f}%" if _progress_rate and abs(_progress_rate) <= 1 else f"{_progress_rate:.1f}%"
        _pnl_sign = "+" if _cumulative_pnl >= 0 else ""

        # 텔레그램 메시지용 주문 내역 포맷 (가격 내림차순)
        _sorted_df = filtered_df.copy()
        _sorted_df['_price'] = _sorted_df['주문단가'].apply(lambda x: float(x) if x else 0)
        _sorted_df = _sorted_df.sort_values(by='_price', ascending=False)

        def _fmt_exec_line(order):
            price_str = f"${float(order['주문단가']):,.2f}"
            qty_str = f"{int(order['주문수량'])}주"
            cond_str = order['주문조건']
            exec_qty = int(order['체결수량'])
            if exec_qty == 0:
                return f"   •  {price_str}  |  {qty_str}  |  {cond_str}  |  미체결"
            exec_price = float(order['체결단가'])
            exec_part = f"*${exec_price:,.2f}  |  {exec_qty}주*"
            # 매도 체결 시 실현손익 표시
            pnl_part = ""
            if exec_qty < 0 and _avg_price > 0:
                sell_pnl = (exec_price - _avg_price) * abs(exec_qty)
                pnl_emoji = "💰" if sell_pnl >= 0 else "📉"
                pnl_part = f" {pnl_emoji}{'+' if sell_pnl >= 0 else ''}${sell_pnl:,.0f}"
            return f"   •  {price_str}  |  {qty_str}  |  {cond_str}  |  {exec_part}{pnl_part}"

        formatted_orders = "\n".join([_fmt_exec_line(order) for _, order in _sorted_df.iterrows()])

        # 기존 자동수집 거래 건수 (텔레그램 2차 알림 분기용 — INSERT 전에 미리 조회)
        _prev_trade_count_for_telegram = 0
        _trade_date_for_check = None
        if not filtered_df.empty and not pd.isnull(filtered_df.iloc[0]['주문일자']):
            _trade_date_for_check = pd.to_datetime(filtered_df.iloc[0]['주문일자']).strftime('%Y-%m-%d')
        if _trade_date_for_check and not is_test_mode:
            try:
                _sb = get_supabase_client()
                if _sb:
                    _prev_res = _sb.table("cycle_trades").select("id", count="exact").eq("cycle_id", cycle_id).eq("trade_date", _trade_date_for_check).eq("event_type", "TRADE").execute()
                    _prev_trade_count_for_telegram = _prev_res.count if _prev_res.count is not None else len(_prev_res.data or [])
            except Exception:
                pass

        # 해외주식 보유잔고 CSV 로 텔레그램 메시지 구성
        file_path = f'./data/stock_balance_processed/stock_balance_processed_{selected_user}_{account_index}.csv'
        df_balance = load_csv_if_exists(file_path)

        # 1차 실행 (기존 거래 없음): 전체 내역 전송 / 2차 실행 (기존 거래 있음): 추가분만 전송
        is_rerun = _prev_trade_count_for_telegram > 0
        executed_count = len(filtered_df[filtered_df['체결수량'] != 0])
        added_count = executed_count - _prev_trade_count_for_telegram

        if is_rerun and added_count <= 0:
            # 2차 실행인데 추가 체결 없음
            send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                f"💵 *[무매사이클 #{cycle_seq}]* 추가 체결 없음\n▶ 계좌: {selected_user} | {account_index}번째 | {ticker}")
        elif is_rerun and added_count > 0:
            # 2차 실행, 추가 체결 있음
            message = (
                f"💵 *[무매사이클 #{cycle_seq}] 추가 체결 {added_count}건*\n\n"
                f"▶ 계좌: 메리츠 | {selected_user} | {account_index}번째 계좌\n"
                f"▶ 종목: *{ticker} ({method_ver})*\n"
                f"▶ 원금: ${_principal:,.0f} | {_split_count}분할 | 1회매수금: ${_per_buy:,.0f}\n"
                f"▶ 진행률: {_progress_display} ({_t_display})\n"
                f"▶ 실현손익금: {_pnl_sign}${abs(_cumulative_pnl):,.2f}\n"
                f"▶ 실제 HTS 체결내역\n"
                f"{formatted_orders}"
            )
            send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)
        elif df_balance is not None and not df_balance.empty:
            filtered = df_balance[df_balance['종목코드'] == ticker]
            if not filtered.empty:
                balance_from_hts = str(filtered['보유수량'].iloc[0])
                current_price = filtered['현재가'].iloc[0]
                average_price = filtered['평균가'].iloc[0]
                profit = filtered['평가손익'].iloc[0]
                profit_rate = filtered['수익률(%)'].iloc[0]
                eval_amount = filtered['평가금액(외화)'].iloc[0]
                purchase_amount = filtered['매입금액(외화)'].iloc[0]

                message = (
                    f"💵 *[무매사이클 #{cycle_seq}] 매매 체결 내역*\n\n"
                    f"▶ {inquiry_start_date}~{inquiry_end_date}\n"
                    f"▶ 계좌: 메리츠 | {selected_user} | {account_index}번째 계좌\n"
                    f"▶ 종목: *{ticker} ({method_ver})*\n"
                    f"▶ 원금: ${_principal:,.0f} | {_split_count}분할 | 1회매수금: ${_per_buy:,.0f}\n"
                    f"▶ 보유수량: {balance_from_hts}주 | 진행률: {_progress_display} ({_t_display})\n"
                    f"▶ 현재가: ${current_price} | 평단가: ${average_price}\n"
                    f"▶ 평가손익: ${profit} ({profit_rate}%)\n"
                    f"▶ 실현손익금: {_pnl_sign}${abs(_cumulative_pnl):,.2f}\n"
                    f"▶ 실제 HTS 체결내역\n"
                    f"{formatted_orders}"
                )
                send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)
            else:
                logging.warning(f"[경고] 종목코드 '{ticker}' 가 df_balance에 존재하지 않습니다.")
                message = (
                    f"💵 *[무매사이클 #{cycle_seq}] 매매 체결 내역*\n\n"
                    f"▶ {inquiry_start_date}~{inquiry_end_date}\n"
                    f"▶ 계좌: 메리츠 | {selected_user} | {account_index}번째 계좌\n"
                    f"▶ 종목: *{ticker} ({method_ver})*\n"
                    f"▶ 원금: ${_principal:,.0f} | {_split_count}분할 | 1회매수금: ${_per_buy:,.0f}\n"
                    f"▶ 진행률: {_progress_display} ({_t_display})\n"
                    f"▶ 실현손익금: {_pnl_sign}${abs(_cumulative_pnl):,.2f}\n"
                    f"▶ 현재 해당종목의 잔고 없음\n"
                    f"▶ 실제 HTS 체결내역\n"
                    f"{formatted_orders}"
                )
                send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)

        # 주문일자 → trade_date 변환
        trade_date = None
        if not filtered_df.empty and not pd.isnull(filtered_df.iloc[0]['주문일자']):
            trade_date = pd.to_datetime(filtered_df.iloc[0]['주문일자']).strftime('%Y-%m-%d')

        # 체결된 것만 추출 (체결수량 != 0)
        only_executed_df = filtered_df[filtered_df['체결수량'] != 0]
        if not only_executed_df.empty:
            logging.info(f"체결내역 {len(only_executed_df)}건의 데이터가 필터링되었습니다.")
            logging.info(f"[체결내역 {len(only_executed_df)}건]\n{only_executed_df}")
        else:
            logging.info("주문내역 중 해당 사이클 종목의 체결내역이 없습니다.")

        # Supabase cycle_trades에 삭제 후 재입력 + order_status 동기화
        if not is_test_mode:
            # 기존 자동수집 거래(TRADE) 건수 조회 (텔레그램 알림 분기용)
            prev_trade_count = 0
            if trade_date:
                try:
                    prev_res = sb.table("cycle_trades").select("id", count="exact").eq("cycle_id", cycle_id).eq("trade_date", trade_date).eq("event_type", "TRADE").execute()
                    prev_trade_count = prev_res.count if prev_res.count is not None else len(prev_res.data or [])
                except Exception:
                    pass

            # 기존 자동수집 거래 삭제 (MANUAL은 보존)
            if trade_date and prev_trade_count > 0:
                try:
                    sb.table("cycle_trades").delete().eq("cycle_id", cycle_id).eq("trade_date", trade_date).eq("event_type", "TRADE").execute()
                    logging.info(f"기존 자동수집 거래 {prev_trade_count}건 삭제 (cycle_id={cycle_id}, date={trade_date})")
                except Exception as e:
                    logging.error(f"기존 거래 삭제 실패: {e}")

            # 체결 건 INSERT
            new_trade_count = 0
            if not only_executed_df.empty:
                rows_to_insert = []
                for _, row in only_executed_df.iterrows():
                    td = None
                    if not pd.isnull(row['주문일자']):
                        td = pd.to_datetime(row['주문일자']).strftime('%Y-%m-%d')
                    rows_to_insert.append({
                        "cycle_id": cycle_id,
                        "trade_date": td,
                        "execution_price": float(row['체결단가']),
                        "execution_qty": int(row['체결수량']),
                        "event_type": "TRADE",
                    })

                try:
                    sb.table("cycle_trades").insert(rows_to_insert).execute()
                    new_trade_count = len(rows_to_insert)
                    logging.info(f"{new_trade_count}건의 체결내역을 cycle_trades에 INSERT 완료!")
                except Exception as e:
                    logging.error(f"cycle_trades INSERT 실패: {e}")

                # recompute 트리거
                _trigger_recompute(cycle_id)

            # order_status 동기화 (전체 주문내역 기준: 체결 + 미체결)
            _sync_order_status(sb, cycle_id, cycle.get("auth_user_id", ""), filtered_df, trade_date)
        else:
            for _, row in only_executed_df.iterrows():
                order_date = pd.to_datetime(row['주문일자'], format='%Y-%m-%d', errors='coerce')
                order_date = order_date.strftime('%Y-%m-%d') if not pd.isnull(order_date) else row['주문일자']
                logging.info(
                    f"(테스트모드) INSERT 예정 데이터: "
                    f"{order_date}, {row['체결단가']}, {row['체결수량']}"
                )

        logging.info(f"{cycle_seq}번 사이클 체결내역 업데이트 완료!")

    logging.info(">>>>> Supabase 체결내역 데이터 업데이트 완료! <<<<<")


if __name__ == "__main__":
    # ------------------------------------------------------------
    # 로컬 테스트용 실행 블록
    # - TEST_USER / TEST_ACCOUNT 를 직접 지정 가능. None 이면 Supabase 자동 로드.
    # - IS_TEST_MODE=True 면 실제 Supabase 기록 대신 dry-run 로그만 출력.
    # - INQUIRY_START_DATE / INQUIRY_END_DATE: 조회 기간 yyyymmdd.
    # ------------------------------------------------------------
    TEST_USER: str | None = None
    TEST_ACCOUNT: int | None = None
    IS_TEST_MODE = True
    INQUIRY_START_DATE = "20250617"
    INQUIRY_END_DATE = "20250617"

    from automation_target_store import resolve_first_user_account

    selected_user, account_index = resolve_first_user_account(TEST_USER, TEST_ACCOUNT)
    orders_execution_update_supabase(
        selected_user, account_index, IS_TEST_MODE, INQUIRY_START_DATE, INQUIRY_END_DATE
    )
