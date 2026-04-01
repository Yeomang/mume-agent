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
        .select("id, cycle_seq, status, method, stock_code")
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

        # 텔레그램 메시지용 주문 내역 포맷
        formatted_orders = "\n".join([
            f"   •  ${float(order['주문단가']):,.2f}  |  {int(order['주문수량'])}주  |  {order['주문조건']}  |  "
            f"{'미체결' if int(order['체결수량']) == 0 else '*${:,.2f}  |  {}주*'.format(float(order['체결단가']), int(order['체결수량']))}"
            for _, order in filtered_df.iterrows()
        ])

        # 해외주식 보유잔고 CSV 로 텔레그램 메시지 구성
        file_path = f'./data/stock_balance_processed/stock_balance_processed_{selected_user}_{account_index}.csv'
        df_balance = load_csv_if_exists(file_path)
        if df_balance is not None and not df_balance.empty:
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
                    f"▶ 보유수량: {balance_from_hts}주\n"
                    f"▶ 평가금액: ${eval_amount} | 총매입금액: ${purchase_amount}\n"
                    f"▶ 현재가: ${current_price} | 평단가: ${average_price}\n"
                    f"▶ 평가손익 : ${profit} ({profit_rate}%)\n"
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
                    f"▶ 현재 해당종목의 잔고 없음\n"
                    f"▶ 실제 HTS 체결내역\n"
                    f"{formatted_orders}"
                )
                send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)

        # 체결된 것만 추출 (체결수량 != 0)
        only_executed_df = filtered_df[filtered_df['체결수량'] != 0]
        if only_executed_df.empty:
            logging.info("주문내역 중 해당 사이클 종목의 체결내역이 없습니다.")
            continue
        logging.info(f"체결내역 {len(only_executed_df)}건의 데이터가 필터링되었습니다.")
        logging.info(f"[체결내역 {len(only_executed_df)}건]\n{only_executed_df}")

        # Supabase cycle_trades에 INSERT
        if not is_test_mode:
            rows_to_insert = []
            for _, row in only_executed_df.iterrows():
                trade_date = None
                if not pd.isnull(row['주문일자']):
                    dt_obj = pd.to_datetime(row['주문일자'])
                    trade_date = dt_obj.strftime('%Y-%m-%d')

                rows_to_insert.append({
                    "cycle_id": cycle_id,
                    "trade_date": trade_date,
                    "execution_price": float(row['체결단가']),
                    "execution_qty": int(row['체결수량']),
                    "event_type": "TRADE",
                })

            try:
                sb.table("cycle_trades").insert(rows_to_insert).execute()
                logging.info(f"{len(rows_to_insert)}건의 체결내역을 cycle_trades에 INSERT 완료!")
            except Exception as e:
                logging.error(f"cycle_trades INSERT 실패: {e}")
                continue

            # recompute 트리거
            _trigger_recompute(cycle_id)
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

    is_test_mode = True
    inquiry_start_date = "20250617"
    inquiry_end_date = "20250617"
    orders_execution_update_supabase(selected_user, account_index, is_test_mode, inquiry_start_date, inquiry_end_date)
