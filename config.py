# C:\mume-agent\config.py

"""
환경 변수 로드 및 설정 관리 모듈
.env 파일에서 민감한 정보를 불러옵니다.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
import logging

# .env 파일 로드
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)
else:
    logging.warning(f"[config] .env 파일을 찾을 수 없습니다: {ENV_FILE}")


class Config:
    """애플리케이션 설정 클래스"""

    # 텔레그램 설정
    TELEGRAM_BOT_TOKEN_ORDER = os.getenv("TELEGRAM_BOT_TOKEN_ORDER", "")
    TELEGRAM_BOT_TOKEN_EXECUTION = os.getenv("TELEGRAM_BOT_TOKEN_EXECUTION", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # HTS 설정
    HTS_EXE_PATH = os.getenv("HTS_EXE_PATH", "")
    HTS_WINDOW_NAME = os.getenv("HTS_WINDOW_NAME", "iMeritz")

    # Supabase 설정
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

    # HTS Agent 인증 키 (콘솔의 HTS_AGENT_SECRET과 동일한 값)
    HTS_AGENT_KEY = os.getenv("HTS_AGENT_KEY", "")

    # 콘솔 API URL (recompute 트리거용)
    CONSOLE_URL = os.getenv("CONSOLE_URL", "")

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
        logging.info("=" * 60)


# 모듈 임포트 시 자동으로 설정 검증
if __name__ != "__main__":
    if not Config.validate():
        logging.warning("[config] 설정 검증 실패. 일부 기능이 제대로 작동하지 않을 수 있습니다.")
