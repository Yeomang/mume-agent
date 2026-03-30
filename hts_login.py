from utils import block_input, get_window_handle, setup_window
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

# 상수 정의
DEFAULT_TIMEOUT = 60
DEFAULT_SLEEP_INTERVAL = 1
AUTO_ID_CERT_LIST = "2026"
AUTO_ID_PASSWORD_FIELD = "2061"
DEFAULT_CERT_CATEGORY = "증권(개인)"

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
    """인증서 목록에서 조건에 맞는 항목 선택."""
    cert_list = window.child_window(auto_id=AUTO_ID_CERT_LIST, control_type="List")
    if not cert_list.exists():
        logging.error(f"인증서 리스트 컨트롤을 찾을 수 없습니다. auto_id={AUTO_ID_CERT_LIST}")
        return False

    log_cert_pw(cert_list)
    for item in cert_list.children():
        combined_text = f"{item.window_text()} {' '.join(sub_item.window_text() for sub_item in item.children())}"
        if cert_keyword_category in combined_text and selected_user in combined_text:
            logging.info(f"조건에 맞는 인증서 발견: {combined_text}")
            item.click_input()
            return True
    logging.warning(f"조건에 맞는 인증서를 찾지 못했습니다. category='{cert_keyword_category}', user='{selected_user}'")
    return False

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
    """
    start_ts = time.time()
    logging.info(
        f"HTS 로그인 시작: exe='{exe_path}', user='{selected_user}', "
        f"category='{cert_keyword_category}', window_keyword='{window_keyword}'"
    )

    # 마우스 및 키보드 잠금 시작
    block_input(True)
    logging.info("입력 잠금 활성화")

    try:
        # 공동인증서 비밀번호 가져오기 (keyring / 자격 증명 관리자)
        password = get_cert_password(selected_user)
        logging.info("공동인증서 비밀번호 로드 완료 (keyring).")

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
        if not click_confirm_button(window):
            logging.error("로그인 중단: 확인 버튼 클릭 실패.")
            return False

        # HTS 창 핸들 가져오기
        hwnd = get_window_handle("iMeritz")
        if not hwnd:
            logging.error("로그인 중단: 'iMeritz' 창 핸들을 찾지 못했습니다.")
            return False

        # HTS 창 메인모니터로 이동, 포커싱, 최대화, 항상위로
        setup_window(hwnd)
        time.sleep(3)

        # Esc 키를 10번 누르기
        send_keys('{ESC 10}')
        elapsed = time.time() - start_ts
        logging.info(f"HTS 로그인 완료 (총 소요: {elapsed:.2f}s)")
        return True

    except Exception as e:
        logging.exception(f"HTS 로그인 중 예외 발생: {e}")
        return False

    finally:
        block_input(False)
        logging.info("입력 잠금 해제")

if __name__ == "__main__":
    # 웹UI에서 저장한 자동 실행 대상에서 첫 번째 사용자 로드 (테스트용)
    from automation_target_store import load_automation_target_with_meta
    
    exe_path = Config.HTS_EXE_PATH
    targets, _ = load_automation_target_with_meta(None, include_cycles=True)
    # 모든 job에서 첫 번째 사용자 찾기
    users = {}
    for job_targets in targets.values() if isinstance(targets, dict) else []:
        if isinstance(job_targets, dict):
            users.update(job_targets)
            break
    
    selected_user = list(users.keys())[0] if users else ""
    if not selected_user:
        print("경고: 저장된 사용자/계좌 설정이 없습니다. 웹UI에서 먼저 설정해주세요.")
    else:
        hts_login(exe_path, selected_user)
