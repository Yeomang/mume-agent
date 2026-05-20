# C:\mume-agent\config.py

"""
환경 변수 로드 및 설정 관리 모듈
부트스트랩 설정(.env): SUPABASE_URL, SUPABASE_KEY, HTS_AGENT_KEY, CONSOLE_URL
운영 설정(DB): 텔레그램 토큰, chat_id, HTS 경로 등
"""

import os
from pathlib import Path
from dotenv import load_dotenv
import logging

# .env 파일 로드 (부트스트랩 값만 사용)
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE, encoding="utf-8-sig")
else:
    logging.warning(f"[config] .env 파일을 찾을 수 없습니다: {ENV_FILE}")


class Config:
    """애플리케이션 설정 클래스"""

    # ── 부트스트랩 설정 (.env) ──
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
    HTS_AGENT_KEY = os.getenv("HTS_AGENT_KEY", "")
    CONSOLE_URL = os.getenv("CONSOLE_URL", "")

    # ── 운영 설정 (DB에서 로드, .env 폴백) ──
    TELEGRAM_BOT_TOKEN_ORDER = os.getenv("TELEGRAM_BOT_TOKEN_ORDER", "")
    TELEGRAM_BOT_TOKEN_EXECUTION = os.getenv("TELEGRAM_BOT_TOKEN_EXECUTION", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    _DEFAULT_HTS_EXE_PATH = r"C:\MeritzFire\iMeritz\imeritzmain.exe"
    HTS_EXE_PATH = os.getenv("HTS_EXE_PATH", "") or _DEFAULT_HTS_EXE_PATH
    HTS_WINDOW_NAME = os.getenv("HTS_WINDOW_NAME", "") or "iMeritz"

    AGENT_AUTH_USER_ID = os.getenv("AGENT_AUTH_USER_ID", "")

    _db_loaded = False

    @classmethod
    def load_from_console_db(cls):
        """콘솔 DB(agent_settings)에서 운영 설정을 로드한다."""
        if cls._db_loaded:
            return
        try:
            from supabase_client import get_supabase_client
            sb = get_supabase_client()
            if not sb:
                logging.warning("[config] Supabase 클라이언트 없음. .env 폴백 사용.")
                return

            agent_key = cls.HTS_AGENT_KEY
            if not agent_key:
                logging.warning("[config] HTS_AGENT_KEY 미설정. DB 로드 건너뜀.")
                return

            res = (
                sb.table("agent_settings")
                .select("telegram_bot_token_order, telegram_bot_token_execution, telegram_chat_id, hts_exe_path, hts_window_name, auth_user_id")
                .eq("agent_secret", agent_key)
                .limit(1)
                .execute()
            )
            if not res.data:
                logging.warning("[config] 콘솔 DB에서 에이전트 설정을 찾을 수 없음 (agent_secret 불일치).")
                return

            row = res.data[0]
            if row.get("telegram_bot_token_order"):
                cls.TELEGRAM_BOT_TOKEN_ORDER = row["telegram_bot_token_order"]
            if row.get("telegram_bot_token_execution"):
                cls.TELEGRAM_BOT_TOKEN_EXECUTION = row["telegram_bot_token_execution"]
            if row.get("telegram_chat_id"):
                cls.TELEGRAM_CHAT_ID = row["telegram_chat_id"]
            if row.get("hts_exe_path"):
                cls.HTS_EXE_PATH = row["hts_exe_path"]
            if row.get("hts_window_name"):
                cls.HTS_WINDOW_NAME = row["hts_window_name"]
            if row.get("auth_user_id") and not cls.AGENT_AUTH_USER_ID:
                cls.AGENT_AUTH_USER_ID = row["auth_user_id"]

            cls._db_loaded = True
            logging.info("[config] 콘솔 DB에서 설정 로드 완료")
        except Exception as e:
            logging.warning(f"[config] 콘솔 DB 설정 로드 실패 (.env 폴백): {e}")

    @classmethod
    def validate(cls) -> bool:
        """필수 설정값이 모두 있는지 검증"""
        required_fields = [
            ("SUPABASE_URL", cls.SUPABASE_URL),
            ("SUPABASE_KEY", cls.SUPABASE_KEY),
            ("TELEGRAM_BOT_TOKEN_ORDER", cls.TELEGRAM_BOT_TOKEN_ORDER),
            ("TELEGRAM_BOT_TOKEN_EXECUTION", cls.TELEGRAM_BOT_TOKEN_EXECUTION),
            ("TELEGRAM_CHAT_ID", cls.TELEGRAM_CHAT_ID),
            ("HTS_EXE_PATH", cls.HTS_EXE_PATH),
        ]

        missing = []
        for field_name, field_value in required_fields:
            if not field_value:
                missing.append(field_name)

        if missing:
            logging.error(f"[config] 필수 설정값 누락: {', '.join(missing)}")
            return False

        return True

    @classmethod
    def print_config(cls):
        """현재 설정 출력 (민감한 정보는 마스킹)"""
        def mask_token(token: str) -> str:
            if not token or len(token) < 8:
                return "****"
            return f"{token[:4]}...{token[-4:]}"

        logging.info("=" * 60)
        logging.info("[config] 현재 설정 정보:")
        logging.info(f"  - Supabase URL: {mask_token(cls.SUPABASE_URL)}")
        logging.info(f"  - 텔레그램 주문 봇 토큰: {mask_token(cls.TELEGRAM_BOT_TOKEN_ORDER)}")
        logging.info(f"  - 텔레그램 체결 봇 토큰: {mask_token(cls.TELEGRAM_BOT_TOKEN_EXECUTION)}")
        logging.info(f"  - 텔레그램 채팅 ID: {cls.TELEGRAM_CHAT_ID}")
        logging.info(f"  - HTS 실행 경로: {cls.HTS_EXE_PATH}")
        logging.info(f"  - HTS 창 이름: {cls.HTS_WINDOW_NAME}")
        logging.info(f"  - 콘솔 URL: {mask_token(cls.CONSOLE_URL)}")
        logging.info(f"  - DB 로드: {'완료' if cls._db_loaded else '미완료 (.env 폴백)'}")
        logging.info("=" * 60)


# 모듈 임포트 시 DB에서 설정 로드 → 검증
Config.load_from_console_db()
if not Config.validate():
    logging.warning("[config] 설정 검증 실패. 일부 기능이 제대로 작동하지 않을 수 있습니다.")
