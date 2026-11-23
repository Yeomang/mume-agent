"""
절세 매매(같은 수량 팔고 다시 사기) — DB 조회·갱신과 체결내역 정리 헬퍼 (HTS 화면 조작 없음).

절세 매매는 사이클 거래가 아니다. 콘솔에서 예약(tax_trades, status='예약')하면 시간외 작업이
hts_tax_trades.run_tax_trades로 팔고 다시 산다. 에이전트는 주문을 넣기 **직전에** 그 주문(날짜·매수/매도·수량·가격)을
tax_trades.orders에 적어 두고(기록이 안 되면 주문하지 않음), 찾은 주문번호도 sell_order_nos/buy_order_nos에 남긴다.

아침 체결 반영(split_tax_rows)은
  - 이 주문들을 빼고 사이클에 반영하고,
  - 다시 못 산 수량(판 수량 − 다시 산 수량)은 그날 매도가로 '사이클 매도' 한 줄을 넣어
    사이클 보유수와 HTS 보유수를 바로 맞춘다 (다음 날로 넘기는 상태가 없다).
  - 에이전트가 실행 중 멈춘 건(실행중이 STALE_MINUTES 넘게 그대로)은 체결내역으로 결과를 정리해 '완료'로 바꾼다.
"""
import datetime as dt
import logging

import pandas as pd

STALE_MINUTES = 15
REMAINDER_LABEL = "절세 미매수분"   # 다시 못 산 수량을 사이클 매도로 넣은 줄의 주문조건 표시


def _is_missing_table(err: Exception) -> bool:
    """콘솔 047 마이그레이션 전이라 tax_trades 테이블이 없을 때의 오류인지."""
    msg = str(err)
    return "tax_trades" in msg and ("PGRST205" in msg or "42P01" in msg or "does not exist" in msg or "Could not find" in msg)


def normalize_order_no(value) -> str:
    """주문번호 표기 통일 — CSV를 읽을 때 숫자(123.0)로 들어와도 '123'."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip()
    try:
        return str(int(float(s)))
    except (TypeError, ValueError):
        return s


def normalize_date(value) -> str:
    """주문일자 → 'YYYYMMDD' (읽지 못하면 빈 문자열)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    try:
        return pd.to_datetime(str(value).strip()).strftime("%Y%m%d")
    except Exception:
        return ""


def order_key(order_date, order_no) -> str:
    return f"{normalize_date(order_date)}:{normalize_order_no(order_no)}"


def intent_key(order_date, side, qty, price) -> str:
    """넣은 주문의 '날짜|매수·매도|수량|가격' — 주문번호를 못 찾았어도 이것으로 절세 매매 주문을 알아본다."""
    try:
        return f"{normalize_date(order_date)}|{side}|{abs(int(float(qty)))}|{float(price):.2f}"
    except (TypeError, ValueError):
        return ""


def load_tax_trades(sb, auth_user_ids, user_name, account_index, ticker):
    """체결 정리에 필요한 절세 매매(실행중·완료)를 읽는다.

    Returns: (rows, error) — error가 있으면 절세 매매가 사이클에 섞일 수 있으니 호출부가 이번 반영을 건너뛴다
             (10시 2차 수집에서 다시 시도). 콘솔 마이그레이션 전이라 테이블이 없으면 ([], None).
    """
    try:
        q = (sb.table("tax_trades").select("*")
             .eq("user_name", user_name).eq("account_index", account_index).eq("stock_code", ticker)
             .in_("status", ["실행중", "완료"]))
        if auth_user_ids:
            q = q.in_("auth_user_id", list(auth_user_ids))
        return q.execute().data or [], None
    except Exception as e:
        if _is_missing_table(e):
            return [], None
        logging.error(f"[절세매매] 기록 조회 실패 ({user_name}/{account_index}/{ticker}): {e}")
        return [], f"절세 매매 기록 조회 실패({e}) — 절세 매매가 사이클에 섞일 수 있어 이번 반영을 건너뜁니다"


def _is_stale(row) -> bool:
    try:
        updated = pd.to_datetime(row.get("updated_at"), utc=True)
    except Exception:
        return True
    return (pd.Timestamp.now(tz="UTC") - updated) > pd.Timedelta(minutes=STALE_MINUTES)


def split_tax_rows(df: pd.DataFrame, tax_rows: list, date_from=None, date_to=None, with_remainder=True):
    """한 종목의 체결내역에서 절세 매매 주문을 빼고, 다시 못 산 수량을 사이클 매도 줄로 붙인다.

    Args:
        df: 한 종목의 체결내역 (전처리 CSV, 매도 수량이 아직 양수인 상태)
        tax_rows: load_tax_trades 결과
        date_from/date_to: 조회 기간 'YYYYMMDD' (이 기간의 절세 매매만 다시 못 산 수량을 붙임)
        with_remainder: False면 빼기만 한다 (시간외 보충 판단용)
    Returns:
        (정리된 df, 뺀 행 수, 실행 중 멈춘 건 정리 [(id, fields)])
    """
    if df is None or not tax_rows:
        return df, 0, []
    owner = {}   # 주문번호 키·주문 기록 키 → 절세 매매 id
    for r in tax_rows:
        for k in (r.get("sell_order_nos") or []) + (r.get("buy_order_nos") or []):
            owner[k] = r["id"]
        for o in r.get("orders") or []:
            k = intent_key(o.get("date"), o.get("side"), o.get("qty"), o.get("price"))
            if k:
                owner[k] = r["id"]

    matched_by = {}
    keep = []
    for idx, row in df.iterrows():
        d = row.get("주문일자")
        tid = (owner.get(order_key(d, row.get("주문No")))
               or owner.get(order_key(d, row.get("원No")))
               or owner.get(intent_key(d, row.get("주문구분"), row.get("주문수량") or 0, row.get("주문단가") or 0)))
        if tid is None:
            keep.append(idx)
        else:
            matched_by.setdefault(tid, []).append(row)
    out = df.loc[keep]
    excluded = len(df) - len(out)

    # 에이전트가 실행 중 멈춘 건 — 이 체결내역으로 판 수량·산 수량을 정리해 '완료'로
    finalized = []
    rows_by_id = {r["id"]: dict(r) for r in tax_rows}
    for r in rows_by_id.values():
        if r.get("status") != "실행중" or not _is_stale(r):
            continue
        sells = [x for x in matched_by.get(r["id"], []) if x.get("주문구분") == "매도"]
        buys = [x for x in matched_by.get(r["id"], []) if x.get("주문구분") == "매수"]
        sold = sum(abs(int(x.get("체결수량") or 0)) for x in sells)
        bought = sum(abs(int(x.get("체결수량") or 0)) for x in buys)
        fields = {
            "status": "완료" if sold else "취소",
            "sold_qty": sold,
            "bought_qty": min(bought, sold),
            "sell_amount": round(sum(abs(int(x.get("체결수량") or 0)) * float(x.get("체결단가") or 0) for x in sells), 2),
            "buy_amount": round(sum(abs(int(x.get("체결수량") or 0)) * float(x.get("체결단가") or 0) for x in buys), 2),
            "message": ((r.get("message") or "") + " / 에이전트가 실행 중 멈춰 체결내역으로 결과를 정리함").strip(" /"),
        }
        finalized.append((r["id"], fields))
        r.update(fields)

    # 다시 못 산 수량 → 그날 매도가로 사이클 매도 한 줄
    if with_remainder:
        d_from = (date_from or "").replace("-", "")
        d_to = (date_to or date_from or "").replace("-", "")
        extra = []
        for r in rows_by_id.values():
            if r.get("status") != "완료":
                continue
            td = str(r.get("trade_date") or "").replace("-", "")[:8]
            sold, bought = int(r.get("sold_qty") or 0), int(r.get("bought_qty") or 0)
            if not td or sold <= bought or (d_from and not (d_from <= td <= d_to)):
                continue
            left = sold - bought
            price = round(float(r.get("sell_amount") or 0) / sold, 4) if sold else 0.0
            line = {c: None for c in df.columns}
            line.update({"주문일자": f"{td[:4]}-{td[4:6]}-{td[6:]}", "주문No": f"TAX-{r['id']}", "주문구분": "매도",
                         "종목코드": r.get("stock_code"), "주문수량": left, "주문단가": price,
                         "체결수량": left, "체결단가": price, "주문조건": REMAINDER_LABEL, "주문상태": "체결"})
            extra.append(line)
        if extra:
            out = pd.concat([out, pd.DataFrame(extra, columns=df.columns)], ignore_index=True)
    return out, excluded, finalized


def has_tax_trade(sb, cycle_id) -> bool:
    """이 사이클에 실행된 절세 매매가 있는지 (있으면 증권사 평균단가가 사이클 평단가와 다르다)."""
    try:
        res = (sb.table("tax_trades").select("id").eq("cycle_id", cycle_id)
               .in_("status", ["완료", "실행중"]).gt("bought_qty", 0).limit(1).execute())
        return bool(res.data)
    except Exception:
        return False


def avg_and_pnl_text(broker_avg, broker_profit, broker_profit_rate, cycle_avg, current_price, holding_qty, tax_traded: bool):
    """텔레그램 '평단가'·'평가손익' 표시.

    절세 매매를 한 사이클은 증권사 평균단가가 새로 잡혀 사이클 평단가와 달라지므로
    사이클 평단가 기준으로 보여 주고 증권사 값은 괄호로 덧붙인다.
    """
    if not tax_traded:
        return f"${broker_avg}", f"${broker_profit} ({broker_profit_rate}%)"
    try:
        cavg = float(cycle_avg or 0)
        cur = float(str(current_price).replace(",", ""))
        qty = float(str(holding_qty).replace(",", ""))
    except (TypeError, ValueError):
        return f"${broker_avg}", f"${broker_profit} ({broker_profit_rate}%)"
    if cavg <= 0:
        return f"${broker_avg}", f"${broker_profit} ({broker_profit_rate}%)"
    pnl = (cur - cavg) * qty
    rate = (cur / cavg - 1) * 100
    return (f"${cavg:,.4f} (증권사 ${broker_avg})",
            f"${pnl:,.2f} ({rate:.2f}%, 사이클 평단 기준)")


def fetch_reserved(sb, auth_user_ids, user_name, account_index) -> list[dict]:
    """이 계좌의 예약된 절세 매매 (오래된 순)."""
    try:
        q = (sb.table("tax_trades").select("*")
             .eq("user_name", user_name).eq("account_index", account_index)
             .eq("status", "예약").order("id"))
        if auth_user_ids:
            q = q.in_("auth_user_id", list(auth_user_ids))
        return q.execute().data or []
    except Exception as e:
        if not _is_missing_table(e):
            logging.error(f"[절세매매] 예약 조회 실패 ({user_name}/{account_index}): {e}")
        return []


def update_tax_trade(sb, tax_trade_id, fields: dict, expect_status: str | None = None) -> bool:
    """tax_trades 한 행 갱신. expect_status를 주면 그 상태일 때만 바꾼다 (사용자 취소와 겹칠 때 대비).

    Returns: 실제로 바뀌었는지.
    """
    patch = dict(fields)
    patch["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if patch.get("status") == "완료":
        patch.setdefault("completed_at", patch["updated_at"])
    try:
        q = sb.table("tax_trades").update(patch).eq("id", tax_trade_id)
        if expect_status:
            q = q.eq("status", expect_status)
        res = q.execute()
        return bool(res.data)
    except Exception as e:
        logging.error(f"[절세매매] #{tax_trade_id} 갱신 실패 ({fields.get('status')}): {e}")
        return False
