"""
HTS 일별 계좌수익률 CSV를 Supabase account_daily_return에 upsert하고,
콘솔에 asset_daily_snapshots(broker_reported) 재계산을 트리거하는 모듈.

CSV 파싱 로직은 콘솔의 routes/csv_import.py::_parse_daily_return_csv와
동일하게 맞춰야 한다 (헤더: 일자/계좌평가액/매매금액/입출금액).
"""
import csv
import io
import logging
import datetime as dt
from pathlib import Path

import httpx

from config import Config
from supabase_client import get_supabase_client
from automation_target_store import get_auth_user_id_for, get_account_id_for
from utils import send_telegram_message

BASE_DIR = Path(__file__).resolve().parent


def _decode(content: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "euc-kr", "cp949"):
        try:
            return content.decode(enc)
        except Exception:
            continue
    return content.decode("utf-8", errors="replace")


def _amount(row: list, i: int) -> float:
    if i < 0 or i >= len(row):
        return 0.0
    s = (row[i] or "").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_daily_return_csv(content: bytes) -> list[dict]:
    """콘솔의 routes/csv_import.py::_parse_daily_return_csv와 동일 로직."""
    text = _decode(content)
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []

    headers = [h.strip() for h in rows[0]]

    def col(name: str) -> int:
        try:
            return headers.index(name)
        except ValueError:
            return -1

    idx = {
        "date": col("일자"),
        "value": col("계좌평가액"),
        "trade": col("매매금액"),
        "cashflow": col("입출금액"),
    }
    if idx["date"] < 0 or idx["value"] < 0:
        raise ValueError("헤더를 인식할 수 없습니다. 일별 계좌수익률 CSV인지 확인해주세요.")

    result = []
    for row in rows[1:]:
        if not row or idx["date"] >= len(row):
            continue
        d = (row[idx["date"]] or "").strip()
        if not d:
            continue
        result.append({
            "snapshot_date": d,
            "account_value_krw": _amount(row, idx["value"]),
            "trade_amount_krw": _amount(row, idx["trade"]),
            "cashflow_krw": _amount(row, idx["cashflow"]),
        })
    return result


def _trigger_daily_return_rebuild(auth_user_id: str) -> bool:
    """콘솔 API를 호출해 asset_daily_snapshots(broker_reported) 재계산을 트리거한다.

    order_execution_update_supabase.py::_trigger_recompute와 동일한 패턴
    (raw 데이터는 agent가 직접 upsert, 파생 집계는 콘솔의 기존 로직을 재사용).
    """
    console_url = Config.CONSOLE_URL.rstrip("/") if Config.CONSOLE_URL else ""
    agent_key = Config.HTS_AGENT_KEY
    if not console_url:
        logging.warning("[일별계좌수익률] CONSOLE_URL이 설정되지 않아 재계산을 트리거할 수 없습니다.")
        return False
    headers = {"X-Agent-Key": agent_key} if agent_key else {}
    try:
        resp = httpx.post(
            f"{console_url}/api/csv-import/daily-return/agent-rebuild",
            headers=headers,
            json={"auth_user_id": auth_user_id},
            timeout=60.0,
        )
        resp.raise_for_status()
        logging.info("[일별계좌수익률] 콘솔 재계산 트리거 완료")
        return True
    except Exception as e:
        logging.warning(f"[일별계좌수익률] 콘솔 재계산 트리거 실패: {e}")
        return False


def sync_daily_return_to_db(selected_user: str, account_index: int):
    """CSV를 파싱해 account_daily_return에 upsert하고, 사람 판단이 필요한
    입출금(미분류)이 있으면 텔레그램으로 알린 뒤, 콘솔에 재계산을 트리거한다.

    입출금 분류(classifications)는 보내지 않는다 — cashflow_status는 DB 기본값인
    'pending'으로 남고, 사람이 콘솔에서 나중에 분류한다.
    """
    sb = get_supabase_client()
    if sb is None:
        logging.warning("[일별계좌수익률] Supabase 미설정, 동기화 스킵")
        return

    auth_user_id = get_auth_user_id_for(selected_user)
    if not auth_user_id:
        logging.warning(f"[일별계좌수익률] {selected_user}의 auth_user_id를 찾을 수 없습니다.")
        return

    account_id = get_account_id_for(selected_user, account_index)
    if not account_id:
        logging.warning(f"[일별계좌수익률] {selected_user}/{account_index}의 account_id를 찾을 수 없습니다.")
        return

    csv_path = BASE_DIR / "data" / "daily_return_raw" / f"daily_return_raw_{selected_user}_{account_index}.csv"
    if not csv_path.exists():
        logging.warning(f"[일별계좌수익률] CSV 파일이 없습니다: {csv_path}")
        return

    try:
        rows = _parse_daily_return_csv(csv_path.read_bytes())
    except Exception as e:
        logging.error(f"[일별계좌수익률] CSV 파싱 실패: {e}")
        return

    if not rows:
        logging.info(f"[일별계좌수익률] {selected_user}/{account_index} 가져올 데이터 없음")
        return

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    upsert_rows = [{
        "auth_user_id": auth_user_id,
        "account_id": account_id,
        "snapshot_date": r["snapshot_date"],
        "account_value_krw": r["account_value_krw"],
        "trade_amount_krw": r["trade_amount_krw"],
        "cashflow_krw": r["cashflow_krw"],
        "updated_at": now,
    } for r in rows]

    try:
        for i in range(0, len(upsert_rows), 500):
            batch = upsert_rows[i:i + 500]
            sb.table("account_daily_return").upsert(
                batch, on_conflict="auth_user_id,account_id,snapshot_date"
            ).execute()
        logging.info(f"[일별계좌수익률] {selected_user}/{account_index} {len(upsert_rows)}건 upsert 완료")
    except Exception as e:
        logging.error(f"[일별계좌수익률] {selected_user}/{account_index} upsert 실패: {e}")
        return

    # 입출금이 있는데 아직 사람이 분류 안 한 날짜 확인 → 텔레그램 알림
    try:
        pending_res = (
            sb.table("account_daily_return")
            .select("snapshot_date, cashflow_krw")
            .eq("auth_user_id", auth_user_id)
            .eq("account_id", account_id)
            .eq("cashflow_status", "pending")
            .neq("cashflow_krw", 0)
            .order("snapshot_date", desc=False)
            .execute()
        )
        pending = pending_res.data or []
        if pending:
            lines = "\n".join(
                f"  · {p['snapshot_date']}: {p['cashflow_krw']:,.0f}원" for p in pending
            )
            message = (
                f"📋 *[입출금 분류 필요]*\n\n"
                f"▶ 계좌: {selected_user} | {account_index}번째 계좌\n"
                f"▶ 미분류 입출금 {len(pending)}건:\n{lines}\n\n"
                f"콘솔 → 자산 대시보드 → 일별 계좌잔고에서 분류해주세요."
            )
            send_telegram_message(Config.TELEGRAM_BOT_TOKEN_EXECUTION, Config.TELEGRAM_CHAT_ID, message)
            logging.info(f"[일별계좌수익률] 입출금 분류 필요 알림 전송 ({len(pending)}건)")
    except Exception as e:
        logging.warning(f"[일별계좌수익률] pending 입출금 조회/알림 실패: {e}")

    _trigger_daily_return_rebuild(auth_user_id)


if __name__ == "__main__":
    # ------------------------------------------------------------
    # 로컬 테스트용 실행 블록
    # ------------------------------------------------------------
    TEST_USER: str | None = "홍승표"
    TEST_ACCOUNT: int | None = 3

    from automation_target_store import resolve_first_user_account

    selected_user, account_index = resolve_first_user_account(TEST_USER, TEST_ACCOUNT)
    sync_daily_return_to_db(selected_user, account_index)
