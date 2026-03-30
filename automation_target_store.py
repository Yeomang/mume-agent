# C:\mume_meritz\automation_target_store.py

"""
웹 UI에서 지정한 자동 실행 대상을 파일로 저장/로드하는 유틸리티.
- 저장 위치: automation_targets.json
- 실제 저장 형식 예시:

    {
      "targets": {
        "최용준": [1, 2, 5],
        "홍길동": [3]
      },
      "updated_at": "2025-12-11T01:17:30"
    }

- 코드에서 사용할 때는 _normalize_user_accounts()를 통해
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

job 개념( morning / evening 등)은 완전히 제거했다.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parent
AUTOMATION_TARGET_FILE = BASE_DIR / "automation_targets.json"


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


def _read_target_file() -> Tuple[Dict[str, Any], str | None]:
    """
    저장 파일을 읽어 targets(dict)와 updated_at을 반환.
    targets 형식: {"이름": [계좌번호, ...]}
    """
    if not AUTOMATION_TARGET_FILE.exists():
        return {}, None

    try:
        text = AUTOMATION_TARGET_FILE.read_text(encoding="utf-8")
        data = json.loads(text) if text else {}
        if not isinstance(data, dict):
            return {}, None

        targets = data.get("targets", {})
        updated_at = data.get("updated_at")
        if not isinstance(targets, dict):
            targets = {}
        return targets, updated_at
    except Exception as e:
        logging.warning(f"[automation_target] 파일 읽기 실패: {e}")
        return {}, None


def load_automation_target(
    job: str | None = None,
    include_cycles: bool = True,  # 현재 로직에는 영향 없음, 시그니처 호환용
) -> Dict[str, List[Dict[str, Any]]]:
    """
    저장된 자동 실행 대상을 normalized 형태로 반환.

    - job 개념은 더 이상 사용하지 않지만,
      기존 코드와의 호환을 위해 인자만 유지한다.
    - 항상 전체 targets를 기준으로 반환한다.

    반환 예:
    {
      "최용준": [{"account": 1, "cycles": None}, ...],
      ...
    }
    """
    targets, _ = _read_target_file()
    if not isinstance(targets, dict):
        return {}
    return _normalize_user_accounts(targets)



def load_automation_target_with_meta(
    job: str | None = None,
    include_cycles: bool = True,  # 시그니처 호환용
) -> Tuple[Dict[str, Any], str | None]:
    """
    저장된 자동 실행 대상을 raw 형식으로 반환.

    - job 인자는 더 이상 사용하지 않고, 전체 targets를 그대로 반환한다.
    - raw 형식: {"이름": [1, 2, 5], ...}
    - updated_at: "YYYY-MM-DDTHH:MM:SS"
    """
    targets, updated_at = _read_target_file()
    return (targets if isinstance(targets, dict) else {}, updated_at)



def save_automation_target(user_accounts: Dict[str, List[Any]]) -> Dict[str, Any]:
    """
    자동 실행 대상을 파일에 저장하고, normalize된 결과를 반환.

    user_accounts 형식 예:
        {
          "최용준": [1, 2, 5]
        }
      또는
        {
          "최용준": [{"account": 1}, {"account": 2, "cycle": 3}]
        }

    파일에는 {"최용준": [1, 2, 5]} 형태로 저장한다.
    (기존 targets 전체를 이 cleaned 값으로 덮어쓴다.)
    """
    # 입력을 {user: [acc,...]} 형태로 강제 변환 (계좌번호만 저장)
    cleaned: Dict[str, List[int]] = {}
    for name, items in (user_accounts or {}).items():
        acc_set = set()
        for item in items or []:
            try:
                acc = int(item.get("account", item)) if isinstance(item, dict) else int(item)
            except Exception:
                continue
            if acc > 0:
                acc_set.add(acc)
        if acc_set:
            cleaned[str(name)] = sorted(acc_set)

    output = {
        "targets": cleaned,
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }

    try:
        AUTOMATION_TARGET_FILE.write_text(
            json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logging.warning(f"[automation_target] 파일 저장 실패: {e}")
        # 저장 실패 시라도 normalized 결과는 반환

    # 호출 측에서 바로 사용할 수 있도록 normalize해서 반환
    return _normalize_user_accounts(cleaned)
