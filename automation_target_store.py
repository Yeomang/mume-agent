# C:\mume_meritz\automation_target_store.py

"""
자동 실행 대상을 Supabase user_accounts 테이블에서 조회하는 유틸리티.

user_accounts.is_automation_target = true 인 계좌를 조회하여
아래처럼 변환된 형태로 반환한다.

    {
      "최용준": [
        {"account": 1, "cycles": None},
        {"account": 2, "cycles": None},
        {"account": 5, "cycles": None}
      ],
      "홍길동": [
        {"account": 3, "cycles": None}
      ]
    }
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple


def _load_from_supabase() -> Dict[str, List[int]] | None:
    """
    Supabase user_accounts 테이블에서 is_automation_target=true인 계좌를 조회.
    조회 실패 시 None을 반환.
    """
    try:
        from supabase_client import get_supabase_client

        sb = get_supabase_client()
        if sb is None:
            return None

        res = (
            sb.table("user_accounts")
            .select("user_name,account_index")
            .eq("is_automation_target", True)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None

        merged: Dict[str, List[int]] = {}
        for row in rows:
            name = (row.get("user_name") or "").strip()
            if not name:
                continue
            try:
                acc = int(row.get("account_index"))
            except (TypeError, ValueError):
                continue
            if name not in merged:
                merged[name] = []
            if acc not in merged[name]:
                merged[name].append(acc)
        return merged if merged else None
    except Exception as e:
        logging.warning(f"[automation_target] Supabase 조회 실패: {e}")
        return None


def load_automation_target(
    job: str | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Supabase user_accounts 테이블에서 자동 실행 대상을 normalized 형태로 반환.
    job 파라미터는 하위호환을 위해 유지하나 무시된다.

    반환 예:
    {
      "최용준": [{"account": 1, "cycles": None}, ...],
      ...
    }
    """
    targets = _load_from_supabase()
    if targets:
        logging.info("[automation_target] Supabase에서 대상 로드 완료")
        result: Dict[str, List[Dict[str, Any]]] = {}
        for name, accounts in targets.items():
            result[name] = [{"account": acc, "cycles": None} for acc in sorted(accounts)]
        return result

    logging.warning("[automation_target] Supabase에서 대상을 찾을 수 없음")
    return {}


def load_automation_target_with_meta(
    job: str | None = None,
    include_cycles: bool = True,
) -> Tuple[Dict[str, Any], str | None]:
    """
    자동 실행 대상을 raw 형식으로 반환. (각 모듈 __main__ 테스트용)
    Supabase에서 조회하며, updated_at은 None으로 반환.
    """
    targets = _load_from_supabase()
    if targets:
        return targets, None
    return {}, None
