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

# 모듈 레벨에서 auth_user_id 목록 캐시
_cached_auth_user_ids: List[str] = []
# user_name → auth_user_id 매핑 캐시
_cached_user_auth_map: Dict[str, str] = {}


def _load_from_supabase() -> Dict[str, List[int]] | None:
    """
    Supabase user_accounts 테이블에서 is_automation_target=true인 계좌를 조회.
    조회 실패 시 None을 반환.
    """
    global _cached_auth_user_ids, _cached_user_auth_map
    try:
        from supabase_client import get_supabase_client
        from config import Config

        sb = get_supabase_client()
        if sb is None:
            return None

        # 이 에이전트가 담당하는 계정 자동 판별
        owner_uid = Config.AGENT_AUTH_USER_ID  # .env 직접 설정 (우선)
        if not owner_uid and Config.HTS_AGENT_KEY:
            # agent_secret으로 agent_settings 역조회하여 auth_user_id 자동 감지
            try:
                agent_res = (
                    sb.table("agent_settings")
                    .select("auth_user_id")
                    .eq("agent_secret", Config.HTS_AGENT_KEY)
                    .limit(1)
                    .execute()
                )
                if agent_res.data:
                    owner_uid = agent_res.data[0].get("auth_user_id", "")
                    logging.info(f"[automation_target] agent_secret으로 계정 자동 감지: {owner_uid[:8]}...")
            except Exception as e:
                logging.warning(f"[automation_target] agent_settings 역조회 실패: {e}")

        query = (
            sb.table("user_accounts")
            .select("auth_user_id,user_name,account_index")
            .eq("is_automation_target", True)
        )
        if owner_uid:
            query = query.eq("auth_user_id", owner_uid)
            logging.info(f"[automation_target] auth_user_id 필터 적용: {owner_uid[:8]}...")

        res = query.execute()
        rows = res.data or []
        if not rows:
            return None

        merged: Dict[str, List[int]] = {}
        auth_user_ids: set = set()
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
            uid = row.get("auth_user_id")
            if uid:
                auth_user_ids.add(str(uid))
                _cached_user_auth_map[name] = str(uid)

        _cached_auth_user_ids = list(auth_user_ids)
        logging.info(f"[automation_target] auth_user_ids: {_cached_auth_user_ids}")
        return merged if merged else None
    except Exception as e:
        logging.warning(f"[automation_target] Supabase 조회 실패: {e}")
        return None


def get_auth_user_ids() -> List[str]:
    """자동 실행 대상에서 조회된 auth_user_id 목록을 반환."""
    if not _cached_auth_user_ids:
        _load_from_supabase()
    return _cached_auth_user_ids


def get_auth_user_id_for(user_name: str) -> str | None:
    """user_name에 대응하는 auth_user_id를 반환."""
    if not _cached_user_auth_map:
        _load_from_supabase()
    return _cached_user_auth_map.get(user_name)


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


def resolve_first_user_account(
    override_user: str | None = None,
    override_account: int | None = None,
    default_account: int = 1,
) -> Tuple[str, int]:
    """
    각 모듈의 `__main__` 테스트 블록에서 사용자/계좌를 결정하기 위한 헬퍼.

    동작:
    - `override_user` / `override_account` 가 주어지면 해당 값을 우선 사용.
    - 지정되지 않은 값은 Supabase `automation_target` 에서 첫 번째 사용자와
      해당 사용자의 첫 번째 계좌(account_index)로 자동 보정.
    - 저장된 대상이 없고 override도 없으면 `RuntimeError` 를 발생시켜
      웹UI 설정이 누락됐음을 즉시 알림.

    Returns:
        (selected_user, account_index) 튜플.
    """
    user = override_user
    account = override_account

    if user is None or account is None:
        targets = _load_from_supabase() or {}

        if user is None:
            if not targets:
                raise RuntimeError(
                    "저장된 사용자/계좌 설정이 없습니다. "
                    "웹UI에서 먼저 자동 실행 대상을 설정하거나, "
                    "override_user/override_account 인자를 직접 지정해주세요."
                )
            user = next(iter(targets.keys()))

        if account is None:
            accounts = targets.get(user, []) or []
            first = accounts[0] if accounts else None
            if isinstance(first, dict):
                account = int(first.get("account", default_account))
            elif isinstance(first, int):
                account = first
            else:
                account = default_account

    return user, int(account)
