import json
import time
import logging
import ctypes
import os
import subprocess
import requests
import pandas_market_calendars as mcal
from datetime import datetime, timedelta
import platform
from pathlib import Path
import pandas as pd

_IS_WINDOWS = platform.system() == "Windows"

if _IS_WINDOWS:
    from pywinauto.findwindows import ElementNotFoundError
    from pywinauto.keyboard import send_keys
    import win32gui, win32con, win32api, win32clipboard, win32process


# ─────────────────────────────────────
# 로깅 설정 (프로젝트 전역에서 공통 사용)
# ─────────────────────────────────────
_logging_initialized = False


def setup_logging(log_file: str = "log.log", level: int = logging.INFO) -> None:
    """
    프로젝트 전역에서 사용할 로깅 설정.
    여러 번 호출되어도 한 번만 설정됨.
    """
    global _logging_initialized
    if _logging_initialized:
        return
    
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8")
        ]
    )
    _logging_initialized = True


# 기본 로깅 설정 (utils.py import 시 자동 적용)
setup_logging()


def get_monitor_info(hwnd, timeout=120):
    """해당 창이 어느 모니터에 있는지 확인합니다."""
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            window_rect = win32gui.GetWindowRect(hwnd)  # (left, top, right, bottom)
            if window_rect:
                monitor_info = win32api.MonitorFromPoint((window_rect[0], window_rect[1]))
                return win32api.GetMonitorInfo(monitor_info)
        except Exception as e:
            logging.warning(f"윈도우 정보 가져오기 실패, 다시 시도 중... {e}")
        
        time.sleep(1)  # 1초마다 재시도

    raise Exception(f"[ERROR] 창의 모니터 정보를 가져오는 데 {timeout}초 초과함.")


def move_window_to_main_monitor(hwnd):
    """창이 보조 모니터에 있을 경우 메인 모니터로 이동시킵니다."""
    monitor_info = get_monitor_info(hwnd)
    primary_monitor_rect = win32api.GetMonitorInfo(win32api.MonitorFromPoint((0, 0)))["Monitor"]
    if monitor_info["Monitor"] != primary_monitor_rect:
        logging.info("창이 보조 모니터에 있습니다. 메인 모니터로 이동 중...")
        win32gui.MoveWindow(hwnd, primary_monitor_rect[0] + 100, primary_monitor_rect[1] + 100, 800, 600, True)
        time.sleep(0.5)  # 창 이동 후 안정적인 동작을 위해 대기
    else:
        logging.info("창이 이미 메인 모니터에 있습니다.")
        
        
def get_window_handle(window_title, timeout=300):
    """창 핸들을 찾을 때까지 기다립니다."""
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        hwnd = win32gui.FindWindow(None, window_title)
        if hwnd:
            if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
                logging.info(f"'{window_title}' 창 핸들 발견: {hwnd}")
                return hwnd
            else:
                logging.info(f"'{window_title}' 창이 존재하지만 보이지 않습니다. 다시 시도 중...")
        
        logging.info(f"'{window_title}' 창을 찾을 수 없습니다. 다시 시도 중...")
        time.sleep(1)  # 1초마다 재시도
    
    raise Exception(f"[ERROR] '{window_title}' 창을 {timeout}초 동안 찾지 못했습니다.")


def focus_window(hwnd):
    """창을 활성화하고 포커스를 맞춥니다."""
    if hwnd == win32gui.GetForegroundWindow():
        logging.info("창이 이미 활성화 상태입니다.")
        return  # 창이 이미 포커스를 가지고 있으면 무시    
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)  # 최소화 되어 있으면 복원
    time.sleep(0.5)    
    try:
        win32gui.SetForegroundWindow(hwnd)
        logging.info("창에 포커스를 맞췄습니다.")
    except Exception as e:
        logging.info(f"SetForegroundWindow 호출 실패: {e}")
        
        
def maximize_window(hwnd):
    """창이 최대화되지 않은 경우 최대화합니다."""
    placement = win32gui.GetWindowPlacement(hwnd)
    if placement[1] != win32con.SW_MAXIMIZE:  # 창이 최대화 상태가 아닌 경우
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        logging.info("창을 최대화하였습니다.")


def always_on_top(hwnd):
    """창을 항상 위에 유지"""
    # 강제로 포커스를 맞춤
    win32gui.BringWindowToTop(hwnd)
    time.sleep(0.2)
    # 비최상위 설정 후 최상위 설정 (시스템 적용 문제 해결)
    win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                          win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
    time.sleep(0.2)  # 대기 후 다시 적용
    # 최상위 설정
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                          win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
    logging.info("창을 항상 위로 설정 완료.")


def setup_window(hwnd):
    """윈도우 제어를 위한 셋업 함수들 통합 실행"""
    # move_window_to_main_monitor(hwnd)
    # focus_window(hwnd)
    time.sleep(1)
    maximize_window(hwnd)
    # time.sleep(1)
    # always_on_top(hwnd)
    
    
def block_input(state=True):
    """마우스 및 키보드 잠금을 실행합니다."""
    if not _IS_WINDOWS:
        logging.warning("block_input은 Windows에서만 지원됩니다.")
        return
    ctypes.windll.user32.BlockInput(state)
    if state:
        logging.info("(입력 잠금 실행) 사용자 임의의 마우스 및 키보드 입력이 제한됩니다.")
    else:
        logging.info("(입력 잠금 해제)마우스 및 키보드 잠금이 해제되었습니다.")


def find_control_by_criteria(parent, control_type, automation_id=None, title=None, index=0, retries=3, delay=1, silent=False):
    """특정 기준에 따라 하위 컨트롤을 찾습니다. silent=True이면 못 찾아도 WARNING 생략."""
    time.sleep(delay)
    for attempt in range(retries):
        try:
            controls = [
                ctrl for ctrl in parent.descendants()
                if ctrl.element_info.control_type == control_type
                and (automation_id is None or ctrl.element_info.automation_id == automation_id)
                and (title is None or ctrl.element_info.name == title)
            ]
            if len(controls) > index:
                logging.info(f"하위 컨트롤 중 control_type='{control_type}', automation_id='{automation_id}', title='{title}' 인 것 중 '{index+1}'번째 컨트롤을 찾았습니다.")
                return controls[index]
            else:
                if not silent:
                    logging.warning(f"컨트롤을 찾지 못했습니다: control_type='{control_type}', automation_id='{automation_id}', title='{title}', index={index}")
                return None
        except Exception as e:
            if not silent:
                logging.warning(f"컨트롤 탐색 중 오류 발생 (시도 {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(1)
    if not silent:
        logging.error(f"컨트롤 탐색 실패 (최대 재시도 초과): control_type='{control_type}', automation_id='{automation_id}', title='{title}'")
    return None


def set_focus_and_type(control, text):
    """컨트롤에 포커스를 설정하고 텍스트를 입력합니다."""
    if control:
        control.set_focus()
        logging.info(f"컨트롤[{control}]에 포커스를 설정하였습니다.")
    send_keys(text)
    logging.info(f"텍스트를 입력하였습니다.")


def wait_for_window(message, parent, title, control_type, timeout=120):
    """주어진 시간 동안 창이 뜰 때까지 기다립니다.
    
    여러 방법으로 창을 찾습니다:
    1. win32gui로 창 제목에 특정 문자열이 포함된 창 찾기 (부분 매칭)
    2. Desktop에서 정확한 제목으로 찾기
    3. Desktop에서 Dialog 타입으로도 찾기
    """
    from pywinauto import Desktop, Application
    
    logging.info(f"'{message}' 창이 나타날 때까지 최대 {timeout}초 동안 대기 중...")
    
    def find_window_by_partial_title(partial_title):
        """창 제목에 특정 문자열이 포함된 창의 핸들을 찾습니다."""
        result = []
        def enum_handler(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                window_title = win32gui.GetWindowText(hwnd)
                if partial_title in window_title:
                    result.append(hwnd)
            return True
        win32gui.EnumWindows(enum_handler, None)
        return result[0] if result else None
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        # 1) win32gui로 창 제목에 포함된 문자열로 찾기 (부분 매칭)
        try:
            hwnd = find_window_by_partial_title(title)
            if hwnd:
                logging.info(f"'{message}' 창이 나타났습니다! (win32gui로 발견, hwnd={hwnd})")
                # pywinauto로 연결하여 반환
                app = Application(backend="uia").connect(handle=hwnd)
                return app.window(handle=hwnd)
        except Exception as e:
            logging.debug(f"win32gui 검색 실패: {e}")
        
        # 2) Desktop에서 정확한 제목 + Window 타입으로 찾기
        try:
            desktop = Desktop(backend="uia")
            control = desktop.window(title=title, control_type="Window")
            if control.exists():
                logging.info(f"'{message}' 창이 나타났습니다! (Desktop Window에서 발견)")
                return control
        except Exception:
            pass
        
        # 3) Desktop에서 Dialog 타입으로도 찾기 (시스템 대화상자)
        try:
            desktop = Desktop(backend="uia")
            control = desktop.window(title=title, control_type="Dialog")
            if control.exists():
                logging.info(f"'{message}' 창이 나타났습니다! (Desktop Dialog에서 발견)")
                return control
        except Exception:
            pass
        
        # 4) parent의 자식 창에서도 찾기
        try:
            control = parent.child_window(title=title, control_type=control_type)
            if control.exists():
                logging.info(f"'{message}' 창이 나타났습니다! (자식 창에서 발견)")
                return control
        except Exception:
            pass

        time.sleep(0.5)  # 0.5초마다 다시 체크
    
    logging.warning(f"'{message}' 창이 {timeout}초 내에 나타나지 않았습니다.")
    return None  # 창을 찾지 못한 경우
        
        

def _handle_password_dialog(main_window, password):
    """
    계좌 선택 후 나타나는 비밀번호 관련 모달을 처리합니다.

    1) '비밀번호 입력 안내창' → 확인 클릭 → 비밀번호 입력
    2) '비밀번호를 확인후 다시 입력하십시오' 모달 → 확인 클릭
    3) 모달 없음 → 이미 인증된 상태
    """
    dialog = wait_for_window("비밀번호 입력 안내창", main_window, "Meritz", "Window", timeout=3)
    if dialog:
        ok_button = find_control_by_criteria(dialog, "Button", automation_id="2", silent=True)
        if ok_button:
            ok_button.click_input()
            logging.info("비밀번호 입력 안내창의 확인 버튼을 클릭하였습니다.")
            set_focus_and_type(None, f"+{{TAB}}{password}{{ENTER}}")
            logging.info("비밀번호를 입력하였습니다.")

            # 비밀번호 오류 모달 체크 ("비밀번호를 확인후 다시 입력하십시오")
            # 메인 "iMeritz" 창이 아닌 정확히 "Meritz" 제목의 별도 모달만 찾음
            time.sleep(1)
            from pywinauto import Desktop
            try:
                desktop = Desktop(backend="uia")
                pw_error = desktop.window(title="Meritz", control_type="Window")
                if pw_error.exists(timeout=1):
                    err_ok = find_control_by_criteria(pw_error, "Button", title="확인", silent=True, delay=0)
                    if err_ok:
                        err_ok.click_input()
                        logging.info("비밀번호 오류 안내 모달의 확인 버튼을 클릭하였습니다.")
            except Exception:
                pass
        else:
            logging.info("비밀번호 입력 안내창 없음 (이미 인증된 상태)")
    else:
        logging.info("비밀번호 입력 안내창 없음 (이미 인증된 상태)")


def copy_to_clipboard(text):
    """클립보드에 텍스트 복사합니다."""
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)  # UTF-16LE 사용
    win32clipboard.CloseClipboard()


def kill_task(task):
    logging.info(f"{task} 프로그램을 종료합니다.")
    os.system(f"TASKKILL /F /IM {task}")
    
def kill_window_by_title(window_title):
    # 윈도우 핸들 찾기
    hwnd = get_window_handle(window_title)
    if hwnd:
        logging.info(f'"{window_title}" 창을 종료합니다.')

        # 프로세스 ID 가져오기
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        
        try:
            logging.info(f'프로세스 {pid} 강제 종료 시도 (WMIC 사용)')
            os.system(f'wmic process where ProcessId={pid} delete')
            logging.info(f'프로세스 {pid} 강제 종료 완료')
        except Exception as e:
            logging.error(f'프로세스 {pid} 강제 종료 실패: {e}')
    else:
        logging.warning(f'"{window_title}" 창을 찾을 수 없습니다.')
    

def shutdown():
    logging.info("10초 후 컴퓨터를 종료합니다.")
    time.sleep(10)

    # Windows에서 shutdown 명령어 실행
    subprocess.call(["shutdown", "/s", "/t", "5"])


def send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message):
    """텔레그램 봇을 통해 메시지를 전송하는 함수"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logging.info(f"텔레그램 알림 전송 성공:\n{message}")
    except requests.exceptions.RequestException as e:
        logging.error(f"텔레그램 알림 전송 실패: {e}")
        
        
def _now_et():
    """현재 미국 동부시간(ET) datetime 반환."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


def is_trading_day_today():
    """미국 동부시간(ET) 기준 오늘이 NYSE 거래일인지 확인."""
    nyse = mcal.get_calendar("NYSE")
    et_now = _now_et()
    today_et = et_now.strftime("%Y-%m-%d")
    schedule = nyse.schedule(start_date=today_et, end_date=today_et)

    if schedule.empty:
        logging.info(f"오늘({today_et}, ET {et_now.strftime('%H:%M')})은 미국 주식시장 휴장일입니다. 주문을 실행하지 않습니다.")
        return False
    else:
        logging.info(f"오늘({today_et}, ET {et_now.strftime('%H:%M')})은 미국 주식시장 거래 가능한 날입니다. 주문을 실행합니다.")
        return True


def is_trading_day_yesterday():
    """미국 동부시간(ET) 기준 어제가 NYSE 거래일인지 확인."""
    nyse = mcal.get_calendar("NYSE")
    et_now = _now_et()
    yesterday_et = (et_now - timedelta(days=1)).strftime("%Y-%m-%d")
    schedule = nyse.schedule(start_date=yesterday_et, end_date=yesterday_et)

    if schedule.empty:
        logging.info(f"어제({yesterday_et}, ET {et_now.strftime('%H:%M')})는 미국 주식시장 휴장일입니다. 주문을 실행하지 않습니다.")
        return False
    else:
        logging.info(f"어제({yesterday_et}, ET {et_now.strftime('%H:%M')})는 미국 주식시장 거래 가능한 날입니다. 주문을 실행합니다.")
        return True
    

# ─────────────────────────────────────
# CSV 관련 공통 함수
# ─────────────────────────────────────
def load_csv_if_exists(file_path: str) -> pd.DataFrame | None:
    """
    CSV 파일이 존재하면 읽어서 DataFrame 반환, 없으면 None 반환.
    """
    if not os.path.exists(file_path):
        logging.info(f"csv 파일이 존재하지 않습니다. 불러오기 시도한 csv 파일 경로 : {file_path}")
        return None
    df = pd.read_csv(file_path, encoding='utf-8-sig')
    logging.info("파일을 성공적으로 불러왔습니다.")
    return df


def save_csv(df: pd.DataFrame, path: str, file_name: str) -> None:
    """
    DataFrame을 CSV 파일로 저장.
    path 디렉토리가 없으면 자동 생성.
    """
    output_dir = Path(path)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / file_name
    df.to_csv(output_file, index=False, encoding="utf-8-sig")
    logging.info(f"{output_file} 파일 저장 완료!")



# ─────────────────────────────────────
# 날짜 변환 공통 함수
# ─────────────────────────────────────
def to_yyyymmdd(date_str: str | None) -> str | None:
    """
    'YYYY-MM-DD' 형식 -> 'YYYYMMDD' 형식으로 변환.
    입력이 없으면 None 반환.
    """
    if not date_str:
        return None
    return date_str.replace("-", "")