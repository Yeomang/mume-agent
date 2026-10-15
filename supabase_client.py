"""Supabase 클라이언트 및 페이지네이션

[보안] DB 접근 권한 — 콘솔에서 받은 '에이전트 DB 토큰'으로 접속한다.
  콘솔 POST /api/agent/db-token (X-Agent-Key) → 6시간짜리 JWT (이 서버를 쓰는 auth_user_id 목록 포함).
  무매 테이블에 RLS가 켜지면 DB가 그 사용자들의 행만 허용한다 (.env의 공개 키만으로는 아무 행도 못 읽음).
  토큰은 만료 30분 전에 새로 받는다 (hts_agent처럼 오래 떠 있는 프로세스 대비).
  토큰을 못 받으면(콘솔 장애·구버전 콘솔) 공개 키로 폴백 — RLS를 켜기 전까지는 동작이 같다.
  지금 어느 방식인지는 db_mode()로 확인 (/deploy-status 응답에 포함).
"""
import logging
import time

import requests
from supabase import create_client, Client as SupabaseClient

from config import Config

_client: SupabaseClient | None = None
_mode = "none"            # "token" | "anon" | "none"
_token_exp = 0.0          # 토큰 만료 시각 (epoch)
_anon_retry_at = 0.0      # 공개 키 폴백 중이면 이 시각 이후 토큰 재시도
REFRESH_BEFORE_SEC = 1800
ANON_RETRY_SEC = 600


def _fetch_db_token() -> tuple[str, float] | None:
    if not Config.CONSOLE_URL or not Config.HTS_AGENT_KEY:
        return None
    try:
        r = requests.post(f"{Config.CONSOLE_URL.rstrip('/')}/api/agent/db-token",
                          headers={"X-Agent-Key": Config.HTS_AGENT_KEY}, timeout=15)
        if r.status_code != 200:
            logging.warning(f"[supabase] 에이전트 DB 토큰 발급 실패 (HTTP {r.status_code}) — 공개 키로 계속")
            return None
        d = r.json()
        return d["token"], float(d["expires_at"])
    except Exception as e:
        logging.warning(f"[supabase] 에이전트 DB 토큰 요청 실패 ({e}) — 공개 키로 계속")
        return None


def _token_client(token: str) -> SupabaseClient | None:
    """토큰으로 접속하는 클라이언트를 만들고, 토큰이 실제로 받아지는지 한 번 조회해 확인."""
    try:
        client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        client.options.headers["Authorization"] = f"Bearer {token}"
        client.postgrest.auth(token)
        pg = client.postgrest
        auth_headers = [str(getattr(pg, "headers", {}).get("Authorization", "")),
                        str(getattr(getattr(pg, "session", None), "headers", {}).get("Authorization", ""))]
        if f"Bearer {token}" not in auth_headers:
            logging.warning("[supabase] 토큰 헤더 적용 실패 — 공개 키로 계속")
            return None
        client.table("agent_settings").select("auth_user_id").limit(1).execute()   # 서명이 틀리면 여기서 401
        return client
    except Exception as e:
        logging.warning(f"[supabase] 에이전트 DB 토큰 확인 실패 ({e}) — 공개 키로 계속")
        return None


def get_supabase_client() -> SupabaseClient | None:
    global _client, _mode, _token_exp, _anon_retry_at
    if not Config.SUPABASE_URL or not Config.SUPABASE_KEY:
        return None
    now = time.time()
    if _client is not None:
        if _mode == "token" and now < _token_exp - REFRESH_BEFORE_SEC:
            return _client
        if _mode == "anon" and now < _anon_retry_at:
            return _client
    got = _fetch_db_token()
    client = _token_client(got[0]) if got else None
    if client is not None:
        if _mode != "token":
            logging.info("[supabase] 에이전트 DB 토큰으로 접속 (RLS 대상 사용자 행만)")
        _client, _mode, _token_exp = client, "token", got[1]
        return _client
    if _mode == "token" and _client is not None and now < _token_exp:
        return _client   # 갱신만 실패 — 아직 유효한 토큰으로 계속
    if _mode != "anon":
        logging.info("[supabase] 공개 키로 접속")
    _client, _mode, _anon_retry_at = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY), "anon", now + ANON_RETRY_SEC
    return _client


def db_mode() -> str:
    return _mode


def supabase_fetch_all(build_query, page_size: int = 1000):
    all_data = []
    start = 0
    while True:
        res = build_query(start, start + page_size - 1)
        rows = res.data or []
        all_data.extend(rows)
        if len(rows) < page_size:
            break
        start += page_size
    return type("PaginatedResult", (), {"data": all_data})()
