"""Supabase 클라이언트 및 페이지네이션"""
from supabase import create_client, Client as SupabaseClient
from config import Config

_client: SupabaseClient | None = None


def get_supabase_client() -> SupabaseClient | None:
    global _client
    if _client is not None:
        return _client
    if not Config.SUPABASE_URL or not Config.SUPABASE_KEY:
        return None
    _client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
    return _client


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
