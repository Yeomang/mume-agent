# C:\mume_meritz\secrets_manager.py

"""
비밀번호/토큰 등 민감정보를 안전하게 저장/조회하는 헬퍼 모듈.

현재 역할:
- 사용자별 공동인증서 비밀번호
- 사용자별 계좌 비밀번호 (같은 사용자의 모든 계좌에 공통 적용)

저장 위치:
- Windows Credential Manager (keyring 사용)
"""

from __future__ import annotations
import keyring
import logging

# 서비스 이름 상수 (Windows Credential Manager에서 구분용)
SERVICE_CERT = "mume_meritz_cert"      # 공동인증서 비밀번호
SERVICE_ACCOUNT = "mume_meritz_account"  # 계좌 비밀번호


def set_cert_password(user: str, password: str) -> None:
    """
    사용자별 공동인증서 비밀번호 저장.
    - user: 웹UI에서 사용하는 사용자 이름 ("홍승표", "김경민" 등)
    """
    if not user:
        raise ValueError("user는 빈 문자열일 수 없습니다.")
    if password is None:
        raise ValueError("password는 None일 수 없습니다.")

    keyring.set_password(SERVICE_CERT, user, password)
    logging.info(f"[secrets_manager] 공동인증서 비밀번호 저장 완료: user={user}")


def get_cert_password(user: str) -> str | None:
    """
    사용자별 공동인증서 비밀번호 조회.
    - 설정되어 있지 않으면 None 반환.
    """
    if not user:
        return None
    pw = keyring.get_password(SERVICE_CERT, user)
    if pw is None:
        logging.warning(f"[secrets_manager] 공동인증서 비밀번호가 설정되지 않음: user={user}")
    return pw


def set_account_password(user: str, password: str) -> None:
    """
    사용자별 계좌 비밀번호 저장.
    - 같은 사용자의 모든 계좌에 동일 비밀번호 사용.
    """
    if not user:
        raise ValueError("user는 빈 문자열일 수 없습니다.")
    if password is None:
        raise ValueError("password는 None일 수 없습니다.")

    keyring.set_password(SERVICE_ACCOUNT, user, password)
    logging.info(f"[secrets_manager] 계좌 비밀번호 저장 완료: user={user}")


def get_account_password(user: str) -> str | None:
    """
    사용자별 계좌 비밀번호 조회.
    - 설정되어 있지 않으면 None 반환.
    """
    if not user:
        return None
    pw = keyring.get_password(SERVICE_ACCOUNT, user)
    if pw is None:
        logging.warning(f"[secrets_manager] 계좌 비밀번호가 설정되지 않음: user={user}")
    return pw


def delete_cert_password(user: str) -> bool:
    """
    사용자별 공동인증서 비밀번호 삭제.
    - 성공 시 True, 실패 시 False 반환.
    """
    if not user:
        return False
    try:
        keyring.delete_password(SERVICE_CERT, user)
        logging.info(f"[secrets_manager] 공동인증서 비밀번호 삭제 완료: user={user}")
        return True
    except keyring.errors.PasswordDeleteError:
        logging.warning(f"[secrets_manager] 공동인증서 비밀번호 삭제 실패 (존재하지 않음): user={user}")
        return False


def delete_account_password(user: str) -> bool:
    """
    사용자별 계좌 비밀번호 삭제.
    - 성공 시 True, 실패 시 False 반환.
    """
    if not user:
        return False
    try:
        keyring.delete_password(SERVICE_ACCOUNT, user)
        logging.info(f"[secrets_manager] 계좌 비밀번호 삭제 완료: user={user}")
        return True
    except keyring.errors.PasswordDeleteError:
        logging.warning(f"[secrets_manager] 계좌 비밀번호 삭제 실패 (존재하지 않음): user={user}")
        return False