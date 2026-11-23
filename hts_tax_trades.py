"""
절세 매매 실행 모듈 — 시간외 작업(main_aftermarket)에서 계좌별로 호출한다.

절세 매매: 콘솔에서 예약한 N주를 시간외에 지정가로 팔고 곧바로 N주를 다시 산다.
증권사 평균단가만 새로 잡히고 사이클(평단가·T값)은 그대로다.

한 번 실행에서 끝낸다 (다음 날로 넘기는 상태 없음):
  1. 팔기 전에 예수금을 보고 다시 살 수 있는 만큼만 판다.
  2. 판 뒤 같은 세션 안에서 지정가를 +1% → +3% → +5%로 올려 가며 다시 산다
     (매번 그때 예수금 안에서, 덜 사지면 미체결을 취소하고 다음 단계로).
  3. 그래도 못 산 수량은 아침 체결 반영이 '사이클 매도'로 넣는다 (tax_trades_store.split_tax_rows)
     → 사이클 보유수와 HTS 보유수가 바로 같아진다.

주문을 넣기 직전에 그 주문(날짜·매수/매도·수량·가격)을 tax_trades.orders에 적는다 — 기록이 안 되면 주문하지 않는다.
아침 체결 반영은 이 기록(과 찾은 주문번호)으로 절세 매매 주문을 알아보고 뺀다.
수량은 HTS 보유잔고([6104])의 전후 차이로, 체결가·주문번호는 주문체결내역([6114])에서 새로 생긴 주문으로 정한다.
"""
import datetime as dt
import logging
import os
import time
import traceback

import yfinance as yf

from config import Config
from utils import (
    send_telegram_message, is_aftermarket_open, is_trading_day_yesterday, load_csv_if_exists, read_usd_deposit,
)
from hts_order_sell import hts_order_sell
from hts_order_buy import hts_order_buy
from hts_cancel_orders import hts_cancel_orders
from hts_orders_execution_save_to_csv import save_data_order_execution
from order_execution_data_preprocessing import order_execution_data_preprocessing
from hts_stock_balance_save_to_csv import save_data_stock_balance
from stock_balance_data_preprocessing import stock_balance_data_preprocessing
from supabase_client import get_supabase_client
from automation_target_store import get_auth_user_ids
from tax_trades_store import (
    fetch_reserved,
    load_tax_trades,
    split_tax_rows,
    order_key,
    update_tax_trade,
)

SELL_PRICE_RATE = 0.97                    # 매도 지정가 = 시간외 현재가 × 0.97 (바로 체결되게)
BUY_PRICE_RATES = (1.01, 1.03, 1.05)      # 다시 살 때 지정가 단계 (첫 단계는 max(매도 체결가, 현재가) 기준)
CASH_MARGIN_RATE = 0.01                   # 팔기 전 예수금 확인: 수량 × 현재가 × 1% 를 예수금으로 감당할 수 있는 만큼만
POLL_INTERVAL_SEC = 10
POLL_TIMES = 4


def _tg(message: str):
    send_telegram_message(Config.TELEGRAM_BOT_TOKEN_ORDER, Config.TELEGRAM_CHAT_ID, message)


def _usd(v) -> str:
    return "-" if v is None else f"${v:,.2f}"


# ─────────────────────────────────────
# HTS 조회 (주문체결내역 / 보유잔고·예수금)
# ─────────────────────────────────────

def _load_executions(user, account_index, ymd):
    """[6114] 주문체결내역을 새로 저장해 그날 전체 주문 DataFrame을 돌려준다. 실패하면 None."""
    try:
        save_data_order_execution(user, account_index, ymd, ymd)
        order_execution_data_preprocessing(user, account_index, ymd, ymd)
    except Exception as e:
        logging.warning(f"[절세매매] 주문체결내역 저장 실패: {e}")
        return None
    path = f"./data/all_order_execution_processed/all_order_execution_processed_{user}_{account_index}_{ymd}-{ymd}.csv"
    return load_csv_if_exists(path)


def _orders_of(df, ticker, side):
    """{주문키: (주문수량, 체결수량, 체결단가)} — side는 '매도'/'매수'."""
    out = {}
    if df is None or df.empty:
        return out
    rows = df[(df["종목코드"] == ticker) & (df["주문구분"] == side)]
    for _, r in rows.iterrows():
        try:
            filled = abs(int(r.get("체결수량") or 0))
            price = float(r.get("체결단가") or 0) if filled else 0.0
            out[order_key(r.get("주문일자"), r.get("주문No"))] = (abs(int(r.get("주문수량") or 0)), filled, price)
        except (TypeError, ValueError):
            continue
    return out


def _load_balance(user, account_index):
    """[6104] 보유잔고·예수금을 새로 저장해 ({종목: {"qty", "avg", "price"}}, USD 추정예수금)을 돌려준다.

    저장에 실패하면(원본 CSV가 안 생김) (None, None) — 빈 잔고(0주)와 구분해야 수량 차이를 잘못 계산하지 않는다.
    예수금을 못 읽으면 예수금만 None.
    """
    raw = f"./data/stock_balance_raw/stock_balance_raw_{user}_{account_index}.csv"
    try:
        if os.path.exists(raw):
            os.remove(raw)
        save_data_stock_balance(user, account_index)   # 외화예수금 CSV도 함께 저장됨
        if not os.path.exists(raw):
            logging.warning("[절세매매] 보유잔고 CSV가 저장되지 않았습니다.")
            return None, None
        stock_balance_data_preprocessing(user, account_index)
    except Exception as e:
        logging.warning(f"[절세매매] 보유잔고 저장 실패: {e}")
        return None, None
    df = load_csv_if_exists(f"./data/stock_balance_processed/stock_balance_processed_{user}_{account_index}.csv")
    if df is None:
        return None, None
    out = {}
    for _, r in df.iterrows():
        try:
            out[str(r["종목코드"]).strip()] = {
                "qty": int(float(str(r.get("보유수량") or 0).replace(",", ""))),
                "avg": float(str(r.get("평균가") or 0).replace(",", "")),
                "price": float(str(r.get("현재가") or 0).replace(",", "")),
            }
        except (TypeError, ValueError):
            continue
    deposit = read_usd_deposit(user, account_index)
    return out, (deposit if deposit > 0 else None)


def _held(balance, ticker) -> int:
    return int((balance or {}).get(ticker, {}).get("qty", 0))


def _live_price(ticker, hts_price=None):
    """시간외 현재가 (yfinance postMarketPrice → 정규장 종가 → HTS 잔고 현재가)."""
    try:
        info = yf.Ticker(ticker).info
        p = info.get("postMarketPrice") or info.get("regularMarketPrice")
        if p:
            return float(p)
    except Exception as e:
        logging.warning(f"[절세매매] {ticker} 시세 조회 실패: {e}")
    return float(hts_price) if hts_price else None


# ─────────────────────────────────────
# 주문 → 체결 확인
# ─────────────────────────────────────

def _wait_fills(user, account_index, ymd, ticker, side, before_keys, target_qty, seen):
    """새로 생긴 우리 주문(before_keys에 없던 키)의 체결을 기다린다.

    seen: {주문키: (주문수량, 체결수량, 체결단가)} — 확인할 때마다 갱신 (취소되면 CSV에서 빠지므로 마지막 값을 보관).
    Returns: 체결 합계
    """
    filled = sum(v[1] for v in seen.values())
    for i in range(POLL_TIMES):
        time.sleep(POLL_INTERVAL_SEC)
        df = _load_executions(user, account_index, ymd)
        if df is None:
            continue
        for k, v in _orders_of(df, ticker, side).items():
            if k not in before_keys:
                seen[k] = v
        filled = sum(v[1] for v in seen.values())
        logging.info(f"[절세매매] {side} 체결 확인 {i + 1}/{POLL_TIMES}: {filled}/{target_qty}주 (주문 {list(seen)})")
        if filled >= target_qty:
            break
    return filled


def _avg_price(seen) -> float | None:
    qty = sum(v[1] for v in seen.values())
    if not qty:
        return None
    return sum(v[1] * v[2] for v in seen.values()) / qty


def _cancel_open_orders(user, account_index, notes):
    ok, err = hts_cancel_orders(user, account_index, False)
    notes.append("덜 체결된 주문을 일괄 취소함" + ("" if ok else f" (취소 실패: {err})"))
    time.sleep(3)


class _Recorder:
    """주문 직전 기록 — tax_trades.orders에 먼저 적고 성공해야 주문한다."""

    def __init__(self, sb, tax_trade_id, ymd):
        self.sb, self.id, self.ymd = sb, tax_trade_id, ymd
        self.orders = []

    def note(self, side, qty, price) -> bool:
        entry = {"date": self.ymd, "side": side, "qty": int(qty), "price": round(float(price), 2),
                 "at": dt.datetime.now(dt.timezone.utc).isoformat()}
        if update_tax_trade(self.sb, self.id, {"orders": self.orders + [entry]}):
            self.orders.append(entry)
            return True
        logging.error(f"[절세매매] #{self.id} 주문 기록 실패 → 이 주문은 넣지 않음 ({side} {qty}주 ${price})")
        return False


# ─────────────────────────────────────
# 메인
# ─────────────────────────────────────

def run_tax_trades(selected_user, account_index, is_test_mode=False, trade_ymd=None, busy_tickers=None):
    """이 계좌의 예약된 절세 매매를 실행한다.

    - trade_ymd: 미국 거래일 'YYYYMMDD' (시간외 작업의 조회일, 기본 = 한국 어제)
    - busy_tickers: 이번 시간외 작업이 보충매수·재주문을 낸 종목 — 수량 계산이 섞이므로 그 종목은 다음 거래일로 미룬다
    """
    sb = get_supabase_client()
    if sb is None:
        return
    uids = get_auth_user_ids()
    rows = fetch_reserved(sb, uids, selected_user, account_index)
    if not rows:
        return
    trade_ymd = trade_ymd or (dt.date.today() - dt.timedelta(days=1)).strftime("%Y%m%d")
    label = f"{selected_user} | 메리츠 | {account_index}번째 계좌"
    logging.info(f">>>>> 절세 매매 {len(rows)}건 실행 ({label}, 거래일 {trade_ymd}) <<<<<")

    if not is_test_mode:
        if not is_trading_day_yesterday():
            return
        if not is_aftermarket_open():
            logging.info("[절세매매] 시간외 거래 시간이 아니라 다음 시간외 작업으로 미룹니다.")
            return

    for row in rows:
        try:
            if row["stock_code"] in (busy_tickers or set()):
                _tg(f"⏸ *[절세 매매] {row['stock_code']} 다음 거래일로 미룸*\n\n▶ 계좌: {label}\n"
                    f"▶ 이번 시간외 작업에서 이 종목에 보충 주문이 나가 수량 확인이 섞일 수 있어 오늘은 쉽니다. 예약은 그대로입니다.")
                continue
            _execute(sb, row, uids, selected_user, account_index, trade_ymd, label, is_test_mode)
        except Exception as e:
            # 실행중으로 남은 건은 아침 체결 반영이 체결내역으로 결과를 정리한다 (tax_trades_store.split_tax_rows)
            logging.error(f"[절세매매] #{row.get('id')} 실행 중 예외: {e}\n{traceback.format_exc()}")
            _tg(f"🚨 *[절세 매매] {row.get('stock_code')} 실행 중 오류*\n\n▶ 계좌: {label}\n▶ {e}\n"
                f"▶ 넣은 주문은 기록되어 있어 아침 체결 반영 때 정리됩니다 (다시 못 산 수량은 사이클 매도로 반영).")


def _cycle_info(sb, cycle_id):
    res = sb.table("cycle_master").select("id, cycle_seq, status, early_end, method").eq("id", cycle_id).limit(1).execute()
    cycle = res.data[0] if res.data else None
    comp = {}
    if cycle:
        lat = sb.table("cycle_trades_latest").select("computed").eq("cycle_id", cycle_id).execute()
        comp = (lat.data[0].get("computed") or {}) if lat.data else {}
    return cycle, comp


def _expected_cycle_qty(sb, uids, user, account_index, ticker, comp, df, ymd):
    """사이클이 지금 들고 있어야 할 수량 = 최신 계산 보유수 + 오늘(아직 반영 전) 체결 (절세 매매 제외)."""
    base = int(comp.get("holding_qty") or 0)
    if df is None or df.empty:
        return base
    tax_rows, _ = load_tax_trades(sb, uids, user, account_index, ticker)
    today, _, _ = split_tax_rows(df[df["종목코드"] == ticker], tax_rows, ymd, ymd)
    net = 0
    for _, r in today.iterrows():
        q = abs(int(r.get("체결수량") or 0))
        net += q if r.get("주문구분") == "매수" else -q
    return base + net


def _execute(sb, row, uids, user, account_index, ymd, label, is_test_mode):
    tid, ticker, qty = row["id"], row["stock_code"], int(row["qty"])
    cycle, comp = _cycle_info(sb, row["cycle_id"])
    seq = cycle.get("cycle_seq") if cycle else row["cycle_id"]
    head = f"[무매사이클 #{seq}] 절세 매매"

    def skip(reason):
        logging.info(f"[절세매매] #{tid} 실행 안 함: {reason}")
        if not is_test_mode:
            update_tax_trade(sb, tid, {"status": "취소", "message": reason}, expect_status="예약")
        _tg(f"⏹ *{head} 실행 안 함*\n\n▶ 계좌: {label}\n▶ 종목: {ticker} {qty}주\n▶ {reason}\n▶ 필요하면 콘솔에서 다시 예약해 주세요.")

    def postpone(reason):
        _tg(f"⏸ *{head} 다음 거래일로 미룸*\n\n▶ 계좌: {label}\n▶ {reason}\n▶ 예약은 그대로이고 다음 시간외 작업이 다시 시도합니다.")

    if not cycle or cycle.get("status") != "진행중" or cycle.get("early_end"):
        return skip("사이클이 진행중이 아닙니다.")

    # 지금 상태 (주문체결내역은 다시 저장 — 시간외 작업이 앞에서 낸 주문까지 포함되게)
    snap = _load_executions(user, account_index, ymd)
    if snap is None:
        return postpone("주문체결내역을 읽지 못했습니다.")
    expected = _expected_cycle_qty(sb, uids, user, account_index, ticker, comp, snap, ymd)
    bal0, deposit0 = _load_balance(user, account_index)
    if bal0 is None:
        return postpone("보유잔고를 읽지 못했습니다.")
    held0 = _held(bal0, ticker)
    if qty > expected:
        return skip(f"사이클 보유수({expected}주)가 예약 수량보다 적습니다 (오늘 매도 체결 등).")
    if held0 < expected:
        return skip(f"HTS 보유수량({held0}주)이 사이클 보유수({expected}주)보다 적습니다 — 보유수가 맞지 않아 실행하지 않습니다.")

    price_now = _live_price(ticker, bal0.get(ticker, {}).get("price"))
    if not price_now:
        return postpone(f"{ticker} 시세를 조회하지 못했습니다.")

    # 다시 살 수 있는 만큼만 판다 — 매도 대금 + 예수금으로 (현재가보다 약간 높게 다시 사는) 차액을 감당할 수 있게
    sell_qty, notes = qty, []
    if deposit0 is not None:
        affordable = int(deposit0 / (price_now * CASH_MARGIN_RATE))
        if affordable < qty:
            if affordable < 1:
                return skip(f"예수금(${deposit0:,.2f})이 너무 적어 판 뒤 다시 살 여유가 없습니다.")
            sell_qty = affordable
            notes.append(f"예수금(${deposit0:,.2f})으로 다시 살 수 있는 {sell_qty}주만 팖 (예약 {qty}주)")
    sell_price = round(price_now * SELL_PRICE_RATE, 2)

    if is_test_mode:
        logging.info(f"[절세매매][테스트] #{tid} {ticker} {sell_qty}주 매도 ${sell_price} → 같은 수량 매수 (주문 확인 창까지만)")
        hts_order_sell(user, account_index, ticker, [{"quantity": sell_qty, "price": sell_price, "order_type_index": 0}], True)
        hts_order_buy(user, account_index, ticker, [{"quantity": sell_qty, "price": round(price_now * BUY_PRICE_RATES[0], 2)}], 0, True)
        _tg(f"🧪 *{head} 테스트*\n\n▶ 계좌: {label}\n▶ {ticker} {sell_qty}주 매도 {_usd(sell_price)} → 다시 매수 (실제 주문 없음)")
        return

    # 실행 시작 — 사용자가 그사이 취소했으면 여기서 멈춘다
    if not update_tax_trade(sb, tid, {"status": "실행중", "trade_date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}",
                                      "broker_avg_before": bal0.get(ticker, {}).get("avg"), "orders": []},
                            expect_status="예약"):
        logging.info(f"[절세매매] #{tid} 예약 상태가 아니라 건너뜀 (사용자 취소 등)")
        return
    rec = _Recorder(sb, tid, ymd)

    # ── 1) 매도 ──
    sell_before = set(_orders_of(snap, ticker, "매도"))
    sell_seen = {}
    for attempt in (1, 2):   # 주문창 오류 등으로 안 들어갔으면 한 번 더
        if not rec.note("매도", sell_qty, sell_price):
            break
        ok, err = hts_order_sell(user, account_index, ticker, [{"quantity": sell_qty, "price": sell_price, "order_type_index": 0}], False)
        filled = _wait_fills(user, account_index, ymd, ticker, "매도", sell_before, sell_qty, sell_seen)
        if ok or sell_seen:
            if filled < sell_qty:
                _cancel_open_orders(user, account_index, notes)
            break
        notes.append(f"매도 주문 실패({attempt}회): {err}")
    bal1, deposit1 = _load_balance(user, account_index)
    sold = (held0 - _held(bal1, ticker)) if bal1 is not None else sum(v[1] for v in sell_seen.values())
    sold = max(0, min(sold, sell_qty))
    if sold <= 0:
        update_tax_trade(sb, tid, {"status": "취소", "sell_order_nos": sorted(sell_seen), "orders": rec.orders,
                                   "message": "매도가 체결되지 않았습니다. " + " / ".join(notes)})
        _tg(f"⏹ *{head} 매도 안 됨*\n\n▶ 계좌: {label}\n▶ {ticker} {sell_qty}주 매도 {_usd(sell_price)}가 체결되지 않았습니다.\n"
            + "".join(f"▶ {n}\n" for n in notes) + "▶ 필요하면 콘솔에서 다시 예약해 주세요.")
        return
    sell_avg = _avg_price(sell_seen) or sell_price

    # ── 2) 같은 세션에서 다시 사기 (+1% → +3% → +5%) ──
    after_sell = _load_executions(user, account_index, ymd)
    buy_before = set(_orders_of(after_sell if after_sell is not None else snap, ticker, "매수"))
    buy_seen = {}
    bought, bal_now, deposit_now = 0, bal1, deposit1
    for step, rate in enumerate(BUY_PRICE_RATES):
        remaining = sold - bought
        if remaining <= 0:
            break
        ref = max(sell_avg, price_now) if step == 0 else (_live_price(ticker) or price_now)
        limit = round(ref * rate, 2)
        order_qty = remaining if deposit_now is None else min(remaining, int(deposit_now / limit))
        if order_qty < 1:
            notes.append(f"예수금(${deposit_now:,.2f})으로 더 살 수 없음")
            break
        placed = False
        for attempt in (1, 2):
            if not rec.note("매수", order_qty, limit):
                break
            ok, err = hts_order_buy(user, account_index, ticker, [{"quantity": order_qty, "price": limit}], 0, False)
            if ok:
                placed = True
                break
            notes.append(f"매수 주문 실패({_usd(limit)} × {order_qty}주): {err}")
            if "부족" in str(err):
                order_qty = int(order_qty * 0.95)   # 주문가능금액이 예수금 표시보다 적은 경우
                if order_qty < 1:
                    break
        if placed or buy_seen:
            target = sum(v[1] for v in buy_seen.values()) + (order_qty if placed else 0)
            got = _wait_fills(user, account_index, ymd, ticker, "매수", buy_before, target, buy_seen)
            if got < target:
                _cancel_open_orders(user, account_index, notes)
        bal_now, deposit_now = _load_balance(user, account_index)
        bought = (_held(bal_now, ticker) - _held(bal1, ticker)) if (bal_now is not None and bal1 is not None) \
            else sum(v[1] for v in buy_seen.values())
        bought = max(0, min(bought, sold))
        if not placed and not buy_seen:
            break   # 주문 자체가 안 들어가는 상황 — 단계를 올려도 같음

    buy_avg = _avg_price(buy_seen) or (bought and round(price_now * BUY_PRICE_RATES[0], 2)) or 0
    left = sold - bought
    if left:
        notes.append(f"다시 못 산 {left}주는 아침 체결 반영 때 사이클 매도({_usd(sell_avg)})로 넣습니다")
    fields = {
        "status": "완료",
        "orders": rec.orders,
        "sell_order_nos": sorted(sell_seen),
        "buy_order_nos": sorted(buy_seen),
        "sold_qty": sold,
        "sell_amount": round(sold * sell_avg, 2),
        "bought_qty": bought,
        "buy_amount": round(bought * buy_avg, 2),
        "broker_avg_after": (bal_now or {}).get(ticker, {}).get("avg"),
        "message": " / ".join(notes),
    }
    update_tax_trade(sb, tid, fields)
    _report(head, label, ticker, fields, sell_price, bal0.get(ticker, {}).get("avg"), notes)
    _trigger_recompute_quietly(row["cycle_id"])


def _report(head, label, ticker, f, sell_price, avg_before, notes):
    sold, bought = int(f.get("sold_qty") or 0), int(f.get("bought_qty") or 0)
    sell_avg = (float(f.get("sell_amount") or 0) / sold) if sold else None
    buy_avg = (float(f.get("buy_amount") or 0) / bought) if bought else None
    icon = "🔄" if bought == sold else "⚠️"
    lines = [f"{icon} *{head} 완료*" + ("" if bought == sold else " (일부만 다시 삼)"), "",
             f"▶ 계좌: {label}", f"▶ 종목: {ticker}",
             f"▶ 매도: {sold}주 @ {_usd(sell_avg)} (지정가 {_usd(sell_price)})",
             f"▶ 매수: {bought}주 @ {_usd(buy_avg)}"]
    if avg_before or f.get("broker_avg_after"):
        lines.append(f"▶ 증권사 평균단가: {_usd(avg_before)} → {_usd(f.get('broker_avg_after'))}")
    if bought:
        diff = bought * ((sell_avg or 0) - (buy_avg or 0))
        lines.append(f"▶ 현금 차이(수수료 전): {diff:+,.2f}달러")
    lines.append("▶ 사이클 평단가·T값은 그대로입니다 (절세 매매는 사이클에 넣지 않음)")
    for n in notes:
        lines.append(f"▶ {n}")
    _tg("\n".join(lines))


def _trigger_recompute_quietly(cycle_id):
    """절세 매매 현금 차이를 잔금에 바로 반영 (실패해도 저녁 주문 전 재계산에서 반영됨)."""
    try:
        from order_execution_update_supabase import _trigger_recompute
        _trigger_recompute(cycle_id)
    except Exception as e:
        logging.warning(f"[절세매매] 재계산 요청 실패 (저녁 주문 전에 다시 계산됨): {e}")
