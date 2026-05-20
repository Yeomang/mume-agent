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



def _get_active_cycles(sb, selected_user, account_index, auth_user_ids=None, cycles=None):
    """cycle_master에서 활성 사이클 목록 조회"""
    from automation_target_store import get_auth_user_ids
    uids = auth_user_ids or get_auth_user_ids()
    res = supabase_fetch_all(
        lambda s, e: sb.table("cycle_master")
        .select("id, cycle_seq, status, method, stock_code, auth_user_id, principal, split_count, start_date, parent_cycle_id")
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
    """콘솔 API를 호출하여 recompute를 트리거한다. 실패 시 1회 재시도.

    Returns:
        (성공여부, pending_notifications) 튜플.
        pending_notifications는 체결내역 알림 후 발송할 종료/재시작 알림 목록.
    """
    import time as _time
    console_url = Config.CONSOLE_URL.rstrip("/")
    agent_key = Config.HTS_AGENT_KEY
    if not console_url:
        logging.warning("[recompute] CONSOLE_URL이 설정되지 않아 recompute를 트리거할 수 없습니다.")
        return False, []
    headers = {"X-Agent-Key": agent_key} if agent_key else {}
    for attempt in range(2):
        try:
            resp = httpx.post(
                f"{console_url}/recompute/{cycle_id}",
                headers=headers,
                timeout=60.0,
            )
            resp.raise_for_status()
            logging.info(f"[recompute] 사이클 {cycle_id} recompute 완료")
            pending = []
            try:
                pending = resp.json().get("pending_notifications", [])
            except Exception:
                pass
            return True, pending
        except httpx.ConnectError:
            logging.warning(f"[recompute] 콘솔({console_url})에 연결할 수 없습니다.")
            if attempt == 0:
                _time.sleep(3)
                continue
            return False, []
        except httpx.HTTPStatusError as e:
            logging.warning(f"[recompute] 사이클 {cycle_id} recompute 실패 (시도 {attempt+1}/2): {e.response.status_code} {e.response.text}")
            if attempt == 0:
                _time.sleep(3)
                continue
            return False, []
        except Exception as e:
            logging.warning(f"[recompute] 사이클 {cycle_id} recompute 예외: {e}")
            return False, []
        return False, []


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

    # "시작전" 사이클 중복 INSERT 방지:
    # 1) 같은 종목에 "진행중" 사이클이 있으면 시작전 제외
    # 2) 자동재시작으로 생성된 시작전 사이클(parent_cycle_id 있음)이
    #    부모 사이클의 체결내역과 같은 조회기간이면 제외
    in_progress_tickers = {c.get("stock_code") for c in active_cycles if c.get("status") == "진행중"}
    # 부모가 당일 종료된 시작전 사이클 제외
    _skip_ids = set()
    for c in active_cycles:
        if c.get("status") != "시작전" or not c.get("parent_cycle_id"):
            continue
        try:
            parent_res = sb.table("cycle_master").select("end_date, stock_code").eq("id", c["parent_cycle_id"]).limit(1).execute()
            if parent_res.data:
                parent_end = parent_res.data[0].get("end_date") or ""
                # 부모 종료일이 조회 기간에 포함되면 스킵
                end_yyyymmdd = parent_end.replace("-", "")
                if end_yyyymmdd and inquiry_start_date <= end_yyyymmdd <= inquiry_end_date:
                    _skip_ids.add(c["id"])
                    logging.info(f"[중복방지] 시작전 사이클 #{c['cycle_seq']} 스킵 (부모 #{c['parent_cycle_id']} 종료일 {parent_end}이 조회기간 내)")
        except Exception:
            pass
    active_cycles = [
        c for c in active_cycles
        if c["id"] not in _skip_ids
        and (c.get("status") != "시작전" or c.get("stock_code") not in in_progress_tickers)
    ]
    if not active_cycles:
        logging.info("체결 수집 대상 사이클 없음")
        return

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

        _principal = float(cycle.get("principal") or 0)
        _split_count = int(cycle.get("split_count") or 1)
        _start_date = cycle.get("start_date") or "-"

        # 매도 손익 계산용 평균단가 사전 조회
        _pre_avg_price = 0.0
        try:
            _pre_comp_res = sb.table("cycle_trades_latest").select("computed").eq("cycle_id", cycle_id).execute()
            if _pre_comp_res.data:
                _pre_avg_price = float((_pre_comp_res.data[0].get("computed") or {}).get("avg_price") or 0)
        except Exception:
            pass

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
            if exec_qty < 0 and _pre_avg_price > 0:
                sell_pnl = (exec_price - _pre_avg_price) * abs(exec_qty)
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

        # computed 조회 헬퍼 (INSERT+recompute 후 다시 호출하여 체결 후 데이터 사용)
        def _load_computed_for_telegram():
            _computed = {}
            try:
                _comp_res = sb.table("cycle_trades_latest").select("computed").eq("cycle_id", cycle_id).execute()
                if _comp_res.data:
                    _computed = _comp_res.data[0].get("computed") or {}
            except Exception:
                pass
            _t_value = _computed.get("t_value", 0) or 0
            _progress_rate = _computed.get("progress_rate", 0) or 0
            _per_buy = _computed.get("dynamic_per_buy") or _computed.get("repeating_per_buy") or _computed.get("per_buy") or (_principal / _split_count if _split_count else 0)
            _cumulative_pnl = _computed.get("cumulative_pnl", 0) or 0
            _comp_avg = float(_computed.get("avg_price") or 0)
            _t_display = f"{float(_t_value):.1f}T" if _t_value else "0T"
            _progress_display = f"{_progress_rate * 100:.1f}%" if _progress_rate and abs(_progress_rate) <= 1 else f"{_progress_rate:.1f}%"
            _pnl_sign = "+" if _cumulative_pnl >= 0 else ""
            return _computed, _t_display, _progress_display, _per_buy, _cumulative_pnl, _comp_avg, _pnl_sign

        # 텔레그램 메시지는 INSERT+recompute 후에 전송 (체결 후 computed 사용)
        _telegram_context = {
            "is_rerun": is_rerun, "added_count": added_count,
            "formatted_orders": formatted_orders, "df_balance": df_balance,
        }

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

        # Supabase cycle_trades에 체결내역 동기화 + recompute
        # [안전장치] 트랜잭션 롤백 패턴:
        #   1. 기존 데이터와 동일하면 전체 스킵 (불필요한 위험 제거)
        #   2. 변경 시: 기존 데이터 스냅샷 → DELETE → INSERT → recompute
        #   3. recompute 또는 INSERT 실패 시: 스냅샷으로 롤백 (기존 상태 복원)
        _pending_notifs = []
        if not is_test_mode:
            # 기존 자동수집 거래 조회 (스냅샷 + 비교용)
            prev_trade_count = 0
            prev_trades = []  # 비교용 (id, price, qty)
            prev_snapshot = []  # 롤백용 (전체 데이터)
            if trade_date:
                try:
                    prev_res = sb.table("cycle_trades").select("id, cycle_id, trade_date, execution_price, execution_qty, event_type", count="exact").eq("cycle_id", cycle_id).eq("trade_date", trade_date).eq("event_type", "TRADE").execute()
                    prev_trade_count = prev_res.count if prev_res.count is not None else len(prev_res.data or [])
                    prev_trades = prev_res.data or []
                    # 롤백용 스냅샷 (id 제외, INSERT 가능한 형태)
                    prev_snapshot = [
                        {"cycle_id": t["cycle_id"], "trade_date": t["trade_date"],
                         "execution_price": float(t["execution_price"]), "execution_qty": int(t["execution_qty"]),
                         "event_type": t["event_type"]}
                        for t in prev_trades
                    ]
                except Exception:
                    pass

            # 새로 INSERT할 체결 건 준비
            rows_to_insert = []
            if not only_executed_df.empty:
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

            # 기존 데이터와 동일한지 비교 (건수 + 각 건의 가격/수량)
            data_changed = True
            if prev_trade_count == len(rows_to_insert) and prev_trade_count > 0:
                prev_set = {(round(float(t.get("execution_price", 0)), 2), int(t.get("execution_qty", 0))) for t in prev_trades}
                new_set = {(round(r["execution_price"], 2), r["execution_qty"]) for r in rows_to_insert}
                if prev_set == new_set:
                    data_changed = False
                    logging.info(f"[중복방지] 사이클 #{cycle_seq}: 기존 체결 데이터와 동일 ({prev_trade_count}건). 스킵.")

            new_trade_count = 0
            if data_changed and rows_to_insert:
                # DELETE → INSERT → recompute (실패 시 롤백)
                deleted_ok = False
                inserted_ok = False
                recompute_ok = False

                # Step 1: DELETE
                if trade_date and prev_trade_count > 0:
                    try:
                        sb.table("cycle_trades").delete().eq("cycle_id", cycle_id).eq("trade_date", trade_date).eq("event_type", "TRADE").execute()
                        deleted_ok = True
                        logging.info(f"기존 자동수집 거래 {prev_trade_count}건 삭제 (cycle_id={cycle_id}, date={trade_date})")
                    except Exception as e:
                        logging.error(f"기존 거래 삭제 실패: {e}")
                else:
                    deleted_ok = True  # 삭제할 것이 없는 경우

                # Step 2: INSERT
                if deleted_ok:
                    try:
                        sb.table("cycle_trades").insert(rows_to_insert).execute()
                        inserted_ok = True
                        new_trade_count = len(rows_to_insert)
                        logging.info(f"{new_trade_count}건의 체결내역을 cycle_trades에 INSERT 완료!")
                    except Exception as e:
                        logging.error(f"cycle_trades INSERT 실패: {e}")

                # Step 3: recompute
                if inserted_ok:
                    recompute_ok, _pending_notifs = _trigger_recompute(cycle_id)

                # 롤백: INSERT 또는 recompute 실패 시 기존 데이터 복원
                if deleted_ok and (not inserted_ok or not recompute_ok) and prev_snapshot:
                    logging.warning(f"[롤백] 사이클 #{cycle_seq}: {'INSERT' if not inserted_ok else 'recompute'} 실패. 기존 데이터 {len(prev_snapshot)}건 복원 시도.")
                    try:
                        # 새로 INSERT된 데이터 삭제
                        if inserted_ok:
                            sb.table("cycle_trades").delete().eq("cycle_id", cycle_id).eq("trade_date", trade_date).eq("event_type", "TRADE").execute()
                        # 스냅샷 복원
                        sb.table("cycle_trades").insert(prev_snapshot).execute()
                        logging.info(f"[롤백] 기존 데이터 {len(prev_snapshot)}건 복원 완료. recompute 재시도.")
                        _trigger_recompute(cycle_id)  # 롤백이므로 pending 무시
                    except Exception as re:
                        logging.error(f"[롤백] 복원 실패: {re}. 수동 확인 필요.")

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

        # ── 텔레그램 메시지 전송 (INSERT+recompute 후 — 체결 후 computed 사용) ──
        _, _t_display, _progress_display, _per_buy, _cumulative_pnl, _, _pnl_sign = _load_computed_for_telegram()
        _ctx = _telegram_context

        if _ctx["is_rerun"] and _ctx["added_count"] <= 0:
            send_telegram_message(Config.TELEGRAM_BOT_TOKEN_EXECUTION, Config.TELEGRAM_CHAT_ID,
                f"💵 *[무매사이클 #{cycle_seq}] 추가 체결 없음*\n\n"
                f"▶ 계좌: 메리츠 | {selected_user} | {account_index}번째 계좌\n"
                f"▶ 종목: *{ticker} ({method_ver})*")
        elif _ctx["is_rerun"] and _ctx["added_count"] > 0:
            send_telegram_message(Config.TELEGRAM_BOT_TOKEN_EXECUTION, Config.TELEGRAM_CHAT_ID,
                f"💵 *[무매사이클 #{cycle_seq}] 추가 체결 {_ctx['added_count']}건*\n\n"
                f"▶ 계좌: 메리츠 | {selected_user} | {account_index}번째 계좌\n"
                f"▶ 종목: *{ticker} ({method_ver})*\n"
                f"▶ 원금: ${_principal:,.0f} | {_split_count}분할 | 1회매수금: ${_per_buy:,.0f}\n"
                f"▶ 시작일: {_start_date}\n"
                f"▶ 진행률: {_progress_display} ({_t_display})\n"
                f"▶ 실현손익금: {_pnl_sign}${abs(_cumulative_pnl):,.2f}\n"
                f"▶ 실제 HTS 체결내역\n"
                f"{_ctx['formatted_orders']}")
        elif _ctx["df_balance"] is not None and not _ctx["df_balance"].empty:
            filtered = _ctx["df_balance"][_ctx["df_balance"]['종목코드'] == ticker]
            if not filtered.empty:
                balance_from_hts = str(filtered['보유수량'].iloc[0])
                current_price = filtered['현재가'].iloc[0]
                average_price = filtered['평균가'].iloc[0]
                profit = filtered['평가손익'].iloc[0]
                profit_rate = filtered['수익률(%)'].iloc[0]
                send_telegram_message(Config.TELEGRAM_BOT_TOKEN_EXECUTION, Config.TELEGRAM_CHAT_ID,
                    f"💵 *[무매사이클 #{cycle_seq}] 매매 체결 내역*\n\n"
                    f"▶ {inquiry_start_date}~{inquiry_end_date}\n"
                    f"▶ 계좌: 메리츠 | {selected_user} | {account_index}번째 계좌\n"
                    f"▶ 종목: *{ticker} ({method_ver})*\n"
                    f"▶ 원금: ${_principal:,.0f} | {_split_count}분할 | 1회매수금: ${_per_buy:,.0f}\n"
                    f"▶ 시작일: {_start_date}\n"
                    f"▶ 보유수량: {balance_from_hts}주 | 진행률: {_progress_display} ({_t_display})\n"
                    f"▶ 현재가: ${current_price} | 평단가: ${average_price}\n"
                    f"▶ 평가손익: ${profit} ({profit_rate}%)\n"
                    f"▶ 실현손익금: {_pnl_sign}${abs(_cumulative_pnl):,.2f}\n"
                    f"▶ 실제 HTS 체결내역\n"
                    f"{_ctx['formatted_orders']}")
            else:
                send_telegram_message(Config.TELEGRAM_BOT_TOKEN_EXECUTION, Config.TELEGRAM_CHAT_ID,
                    f"💵 *[무매사이클 #{cycle_seq}] 매매 체결 내역*\n\n"
                    f"▶ {inquiry_start_date}~{inquiry_end_date}\n"
                    f"▶ 계좌: 메리츠 | {selected_user} | {account_index}번째 계좌\n"
                    f"▶ 종목: *{ticker} ({method_ver})*\n"
                    f"▶ 원금: ${_principal:,.0f} | {_split_count}분할 | 1회매수금: ${_per_buy:,.0f}\n"
                    f"▶ 시작일: {_start_date}\n"
                    f"▶ 진행률: {_progress_display} ({_t_display})\n"
                    f"▶ 실현손익금: {_pnl_sign}${abs(_cumulative_pnl):,.2f}\n"
                    f"▶ 현재 해당종목의 잔고 없음\n"
                    f"▶ 실제 HTS 체결내역\n"
                    f"{_ctx['formatted_orders']}")
        else:
            # 보유잔고 CSV 로드 실패 시에도 체결 내역은 전송
            send_telegram_message(Config.TELEGRAM_BOT_TOKEN_EXECUTION, Config.TELEGRAM_CHAT_ID,
                f"💵 *[무매사이클 #{cycle_seq}] 매매 체결 내역*\n\n"
                f"▶ {inquiry_start_date}~{inquiry_end_date}\n"
                f"▶ 계좌: 메리츠 | {selected_user} | {account_index}번째 계좌\n"
                f"▶ 종목: *{ticker} ({method_ver})*\n"
                f"▶ 원금: ${_principal:,.0f} | {_split_count}분할 | 1회매수금: ${_per_buy:,.0f}\n"
                f"▶ 시작일: {_start_date}\n"
                f"▶ 진행률: {_progress_display} ({_t_display})\n"
                f"▶ 실현손익금: {_pnl_sign}${abs(_cumulative_pnl):,.2f}\n"
                f"▶ (보유잔고 조회 실패)\n"
                f"▶ 실제 HTS 체결내역\n"
                f"{_ctx['formatted_orders']}")

        # 체결내역 알림 후 종료/재시작 알림 발송 (recompute에서 지연된 알림)
        if not is_test_mode:
            for _notif in _pending_notifs:
                try:
                    send_telegram_message(Config.TELEGRAM_BOT_TOKEN_EXECUTION, _notif.get("chat_id", Config.TELEGRAM_CHAT_ID), _notif["message"])
                except Exception as _ne:
                    logging.warning(f"지연 알림 발송 실패: {_ne}")

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
