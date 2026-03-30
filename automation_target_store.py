# C:\mume_meritz\automation_target_store.py

"""
자동 실행 대상을 Supabase automation_job_targets 테이블에서 조회하는 유틸리티.

코드에서 사용할 때는 _normalize_user_accounts()를 통해
아래처럼 변환된 형태로 쓰게 된다.

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


def _normalize_user_accounts(
    raw: Dict[str, List[Any]] | None
) -> Dict[str, List[Dict[str, Any]]]:
    """
    {"이름": [1, 3]} 또는 {"이름": [{"account":1}, {"account":3, "cycle":2}]} 형태를
    {"이름": [{"account": 1, "cycles": None}, {"account": 3, "cycles": None}]} 로 정리.
    사이클은 모두 무시하고 계좌 레벨만 사용.
    """
    if not raw:
        return {}

    result: Dict[str, List[Dict[str, Any]]] = {}
    for name, items in raw.items():
        if not items:
            continue
        acc_set = set()
        for item in items:
            try:
                if isinstance(item, dict):
                    acc = int(item.get("account", 0))
                else:
                    acc = int(item)
            except Exception:
                continue
            if acc > 0:
                acc_set.add(acc)

        if acc_set:
            result[str(name)] = [{"account": acc, "cycles": None} for acc in sorted(acc_set)]

    return result


def _load_from_supabase(job: str) -> Dict[str, List[Any]] | None:
    """
    Supabase automation_job_targets 테이블에서 해당 job의 자동 실행 대상을 조회.
    여러 auth_user가 같은 job에 대해 설정했을 수 있으므로 모든 행을 병합한다.
    조회 실패 시 None을 반환.
    """
    try:
        from supabase_client import get_supabase_client

        sb = get_supabase_client()
        if sb is None:
            return None

        res = (
            sb.table("automation_job_targets")
            .select("user_accounts")
            .eq("job", job)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None

        merged: Dict[str, List[Any]] = {}
        for row in rows:
            ua = row.get("user_accounts")
            if not isinstance(ua, dict):
                continue
            for name, accounts in ua.items():
                if name not in merged:
                    merged[name] = accounts
                else:
                    existing = set(merged[name])
                    for acc in accounts:
                        if acc not in existing:
                            merged[name].append(acc)
        return merged if merged else None
    except Exception as e:
        logging.warning(f"[automation_target] Supabase 조회 실패: {e}")
        return None


def load_automation_target(
    job: str | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Supabase automation_job_targets 테이블에서 자동 실행 대상을 normalized 형태로 반환.

    반환 예:
    {
      "최용준": [{"account": 1, "cycles": None}, ...],
      ...
    }
    """
    if job:
        supabase_targets = _load_from_supabase(job)
        if supabase_targets:
            logging.info(f"[automation_target] Supabase에서 '{job}' 대상 로드 완료")
            return _normalize_user_accounts(supabase_targets)

    logging.warning(f"[automation_target] Supabase에서 '{job}' 대상을 찾을 수 없음")
    return {}


def load_automation_target_with_meta(
    job: str | None = None,
    include_cycles: bool = True,
) -> Tuple[Dict[str, Any], str | None]:
    """
    자동 실행 대상을 raw 형식으로 반환. (각 모듈 __main__ 테스트용)
    Supabase에서 조회하며, updated_at은 None으로 반환.
    """
    if job:
        targets = _load_from_supabase(job)
        if targets:
            return targets, None
    return {}, None
