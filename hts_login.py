from utils import block_input, setup_window, ensure_active_desktop, send_telegram_message
import subprocess
import time
import win32gui
import win32com.shell.shell as shell
import win32con
from pywinauto import Application
from pywinauto.keyboard import send_keys
import logging
import os

from secrets_manager import get_cert_password
from config import Config
import cert_guard

# 상수 정의
DEFAULT_TIMEOUT = 60
DEFAULT_SLEEP_INTERVAL = 1
AUTO_ID_CERT_LIST = "2026"
AUTO_ID_PASSWORD_FIELD = "2061"
DEFAULT_CERT_CATEGORY = "증권(개인)"
# 체결 봇으로 알림을 보내는 작업 (나머지는 주문 봇) — 인증서 알림도 그 작업 알림과 같은 곳으로
EXECUTION_BOT_JOBS = {"morning", "refresh_daily_return"}


def _job_notifier():
    """현재 작업(JOB_NAME)이 쓰는 텔레그램 봇으로 보내는 알림 함수. 설정이 없으면 None(로그만)."""
    job = os.environ.get("JOB_NAME", "")
    token = Config.TELEGRAM_BOT_TOKEN_EXECUTION if job in EXECUTION_BOT_JOBS else Config.TELEGRAM_BOT_TOKEN_ORDER
    chat_id = Config.TELEGRAM_CHAT_ID
    if not (token and chat_id):
        return None
    return lambda text: send_telegram_message(token, chat_id, text)


def _kill_hts():
    os.system("TASKKILL /F /IM imeritz.exe")
    os.system("TASKKILL /F /IM imeritzmain.exe")

def log_cert_pw(cert_list):
    """인증서 정보를 출력."""
    logging.info("사용 가능한 인증서:")
    for item in cert_list.children():
        item_text = item.window_text()
        sub_item_texts = [sub_item.window_text() for sub_item in item.children()]
        combined_text = f"{item_text} {' '.join(sub_item_texts)}"
        logging.info(f"- {combined_text}")

def find_windows_by_keyword(keyword, timeout=DEFAULT_TIMEOUT):
    """특정 단어를 포함하는 윈도우를 검색."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        def callback(hwnd, windows):
            title = win32gui.GetWindowText(hwnd)
            if keyword.lower() in title.lower():
                windows.append((hwnd, title))
        windows = []
        win32gui.EnumWindows(callback, windows)
        if windows:
            return windows[0]  # 첫 번째 윈도우 반환
        time.sleep(DEFAULT_SLEEP_INTERVAL)
    logging.warning(f"'{keyword}' 키워드를 가진 윈도우를 찾을 수 없습니다.")
    return None

def select_certificate(window, cert_keyword_category=DEFAULT_CERT_CATEGORY, selected_user=""):
    """인증서 목록에서 조건에 맞는 항목 선택 (여러 개면 만료일이 가장 늦은 것 — cert_guard.pick_certificate)."""
    cert_list = window.child_window(auto_id=AUTO_ID_CERT_LIST, control_type="List")
    if not cert_list.exists():
        logging.error(f"인증서 리스트 컨트롤을 찾을 수 없습니다. auto_id={AUTO_ID_CERT_LIST}")
        return False

    log_cert_pw(cert_list)
    item = cert_guard.pick_certificate(cert_list, cert_keyword_category, selected_user)
    if item is None:
        return False
    item.click_input()
    return True

def input_password(window, password):
    """비밀번호 입력."""
    password_field = window.child_window(auto_id=AUTO_ID_PASSWORD_FIELD, control_type="Edit")
    if not password_field.exists():
        logging.error(f"비밀번호 입력란을 찾을 수 없습니다. auto_id={AUTO_ID_PASSWORD_FIELD}")
        return False
    if not password_field.is_enabled():
        logging.error("비밀번호 입력란이 비활성화 상태입니다.")
        return False
    password_field.click_input()
    send_keys(password)
    logging.info("비밀번호가 입력되었습니다.")
    return True

def click_confirm_button(window):
    """확인 버튼 클릭."""
    confirm_button = window.child_window(title="인증서 선택(확인)", control_type="Button")
    if not confirm_button.exists() or not confirm_button.is_enabled():
        logging.error("확인 버튼을 찾을 수 없거나 비활성화되어 있습니다. title='인증서 선택(확인)'")
        return False
    confirm_button.click_input()
    logging.info("확인 버튼 클릭 완료.")
    return True

def launch_program(exe_path):
    """프로그램 실행 중이면 종료하고 재실행 (관리자 권한으로)."""
    logging.info("imeritz.exe 종료 시도 중...")
    os.system("TASKKILL /F /IM imeritz.exe")
    time.sleep(2)  # 종료 대기
    logging.info("imeritzmain.exe 종료 시도 중...")
    os.system("TASKKILL /F /IM imeritzmain.exe")
    time.sleep(2)  # 종료 대기
    logging.info(f"프로그램 실행 (관리자 권한): '{exe_path}'")
    
    # 관리자 권한으로 실행 (ShellExecuteEx 사용)
    shell.ShellExecuteEx(
        lpVerb="runas",  # 관리자 권한으로 실행
        lpFile=exe_path,
        nShow=win32con.SW_SHOWNORMAL,
    )
    time.sleep(2)

def find_window_and_connect(window_keyword, timeout=300, interval=0.5):
    """윈도우가 뜰 때까지 기다렸다가 검색하고 애플리케이션 연결."""
    logging.info(f"창 탐색 시작: keyword='{window_keyword}', timeout={timeout}s")
    start_time = time.time()
    while time.time() - start_time < timeout:
        found_window = find_windows_by_keyword(window_keyword)
        if found_window:
            hwnd, title = found_window
            logging.info(f"윈도우 발견: 핸들={hwnd}, 제목='{title}'")
            app = Application(backend="uia").connect(handle=hwnd)
            logging.info("애플리케이션 연결 성공.")
            return app.window(handle=hwnd)
        time.sleep(interval)  # 창이 나타날 때까지 대기
    logging.error(f"'{window_keyword}' 창을 {timeout}초 동안 찾지 못했습니다.")
    return None  # 시간 초과 시 None 반환

def hts_login(
    exe_path,
    selected_user,
    cert_keyword_category=DEFAULT_CERT_CATEGORY,
    window_keyword="인증서 선택"
):
    """HTS 로그인 함수.

    비밀번호는 cert_pw.json이 아니라 OS 보안 저장소(keyring)를 통해
    secrets_manager.get_cert_password()에서 가져온다.

    공동인증서 보호(cert_guard — vr-agent와 같은 파일):
    - 거부된 적 있는 비밀번호면 HTS를 켜지 않고 실패 (반복 시도로 인증서가 잠기지 않게)
    - 확인 버튼 뒤 비밀번호 거부 안내창이 뜨면 즉시 중단하고 그 비밀번호를 거부 기록에 남김
    - '인증서 만료공지'가 뜨면 '확인'으로 닫고 계속 진행 + 텔레그램(작업별 봇) 알림
    """
    start_ts = time.time()
    logging.info(
        f"HTS 로그인 시작: exe='{exe_path}', user='{selected_user}', "
        f"category='{cert_keyword_category}', window_keyword='{window_keyword}'"
    )

    cert_guard.set_notifier(_job_notifier())
    try:
        password = get_cert_password(selected_user)
    except Exception as e:
        logging.exception(f"로그인 중단: 공동인증서 비밀번호 조회 실패: {e}")
        return False
    if not password:
        logging.error(f"로그인 중단: 공동인증서 비밀번호가 설정되지 않았습니다. user='{selected_user}'")
        return False
    if cert_guard.is_rejected(selected_user, password):   # HTS를 켜지도 않음 (시도 횟수가 쌓이지 않게)
        logging.error(f"로그인 중단: 거부된 적 있는 공동인증서 비밀번호입니다. user='{selected_user}'")
        cert_guard.notify(cert_guard.rejected_message(selected_user, already=True))
        return False

    max_login_retries = 2
    for login_attempt in range(max_login_retries):
        # 마우스 및 키보드 잠금 시작
        block_input(True)
        logging.info("입력 잠금 활성화")

        try:
            # HTS 프로그램 실행
            launch_program(exe_path)

            # '인증서 선택' 창 찾기
            window = find_window_and_connect(window_keyword)
            if not window:
                logging.error(f"로그인 중단: 창 탐색 실패. keyword='{window_keyword}'")
                return False

            # 인증서 목록에서 조건에 맞는 항목 선택
            if not select_certificate(window, cert_keyword_category, selected_user):
                logging.error(f"로그인 중단: 인증서 선택 실패. category='{cert_keyword_category}', user='{selected_user}'")
                return False

            # 암호 입력
            if not input_password(window, password):
                logging.error("로그인 중단: 비밀번호 입력 실패.")
                return False

            # 확인 버튼 클릭
            before = cert_guard.visible_windows()
            if not click_confirm_button(window):
                logging.error("로그인 중단: 확인 버튼 클릭 실패.")
                return False

            # 로그인 결과 — 메인 창 / 비밀번호 거부 / 시간 초과 (그 사이 인증서 만료공지는 '확인'으로 닫음)
            status, detail = cert_guard.wait_login_result(before, selected_user)
            if status == "rejected":
                h, text = detail
                cert_guard.mark_rejected(selected_user, password)
                logging.error(f"로그인 중단: 공동인증서 비밀번호 거부 ({selected_user}): {text[:200]}")
                try:
                    win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
                except Exception:
                    pass
                _kill_hts()
                cert_guard.notify(cert_guard.rejected_message(selected_user, already=False))
                return False
            if status != "ok":
                logging.error(f"로그인 중단: 'iMeritz' 창을 찾지 못했습니다. 로그인 중 보인 창: {detail or '(없음)'}")
                return False
            hwnd = detail

            # HTS 창 메인모니터로 이동, 포커싱, 최대화, 항상위로
            setup_window(hwnd)
            time.sleep(3)

            # Esc 키를 10번 누르기
            send_keys('{ESC 10}')
            elapsed = time.time() - start_ts
            logging.info(f"HTS 로그인 완료 (총 소요: {elapsed:.2f}s)")
            return True

        except RuntimeError as e:
            err_msg = str(e).lower()
            if "no active desktop" in err_msg and login_attempt < max_login_retries - 1:
                logging.warning(f"데스크톱 세션 끊김 감지. 복원 후 재시도합니다. ({login_attempt+1}/{max_login_retries})")
                block_input(False)
                # 데스크톱 복원을 우선 시도 (창 정리는 다음 재시도의 launch_program()이 프로세스명 기준으로 처리)
                if not ensure_active_desktop():
                    logging.error("데스크톱 복원 실패. 로그인을 중단합니다.")
                    return False
                time.sleep(3)
                continue
            logging.exception(f"HTS 로그인 중 예외 발생: {e}")
            return False

        except Exception as e:
            logging.exception(f"HTS 로그인 중 예외 발생: {e}")
            return False

        finally:
            block_input(False)
            logging.info("입력 잠금 해제")

    logging.error("HTS 로그인 최대 재시도 횟수 초과")
    return False

if __name__ == "__main__":
    # ------------------------------------------------------------
    # 로컬 테스트용 실행 블록
    # - TEST_USER 를 직접 지정 가능. None 이면 Supabase 자동 로드한 첫 번째 사용자로 로그인.
    # ------------------------------------------------------------
    TEST_USER: str | None = None

    from automation_target_store import resolve_first_user_account

    selected_user, _ = resolve_first_user_account(TEST_USER, override_account=1)
    hts_login(Config.HTS_EXE_PATH, selected_user)
