"""
매수 유형 드롭다운 진단 스크립트 (읽기 전용 — 절대 실주문 안 함).

HTS가 이미 로그인되어 열려 있는 상태에서 실행한다. 후보 인덱스 N마다
주문창을 완전히 새로 열고(계좌 재선택 + 비밀번호 안내창 처리 포함) 유형
드롭다운을 클릭해 {PGUP}{DOWN N}{ENTER}를 실행한 뒤 결과값을 읽고 창을
닫는다 — 같은 창에서 드롭다운을 반복 오픈하면 두 번째부터 재오픈이 안 되는
현상이 있어(1차 시도에서 확인), 매 N마다 창을 새로 열어 상태를 초기화한다.
수량/가격 입력이나 매수 버튼 클릭은 절대 하지 않는다.

실행: (.venv 활성화 후) python diagnose_buy_type_dropdown.py
"""
from utils import setup_window, find_control_by_criteria, set_focus_and_type, _handle_password_dialog
from secrets_manager import get_account_password
from pywinauto import Application
from pywinauto.keyboard import send_keys
import win32gui
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SCREEN_NUM_ORDER = "6100"
AUTO_ID_SCREEN_SEARCH_INPUT = "1000"
AUTO_ID_DROPDOWN_ACCOUNT = "3780"
CTRL_INDEX_DROPDOWN_ACCOUNT = 2
AUTO_ID_TICKER_BUY_INPUT = "3860"
AUTO_ID_DROPDOWN_TYPE_BUY = "3865"
TEST_TICKER = "TQQQ"

TEST_USER = None
TEST_ACCOUNT = None

CANDIDATES = [0, 1, 2, 3, 4, 5]


def probe(main_window, selected_user, account_index, password, n):
    """주문창을 새로 열고 유형 드롭다운에서 N을 선택한 결과를 반환."""
    search_input = find_control_by_criteria(main_window, "Edit", automation_id=AUTO_ID_SCREEN_SEARCH_INPUT)
    set_focus_and_type(search_input, SCREEN_NUM_ORDER)

    order_window = find_control_by_criteria(main_window, "Window", title="[06100] 해외주식 주문", delay=2, retries=5)
    if not order_window:
        return "(주문창 못찾음)"

    dropdown_account = find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_DROPDOWN_ACCOUNT, index=CTRL_INDEX_DROPDOWN_ACCOUNT)
    if dropdown_account:
        dropdown_account.click_input()
    send_keys(f"{{PGUP}}{{DOWN {account_index}}}{{ENTER}}")
    _handle_password_dialog(main_window, password)

    tab_buy = find_control_by_criteria(main_window, "TabItem", title="매수")
    tab_buy.click_input()

    ticker_input = find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_TICKER_BUY_INPUT)
    set_focus_and_type(ticker_input, f"{TEST_TICKER}{{ENTER}}")
    time.sleep(1)

    candidates = [
        ctrl for ctrl in order_window.descendants()
        if ctrl.element_info.control_type == "Pane"
        and ctrl.element_info.automation_id == AUTO_ID_DROPDOWN_TYPE_BUY
        and ctrl.element_info.name.strip()
    ]
    if not candidates:
        try:
            order_window.close()
        except Exception:
            pass
        return "(드롭다운 못찾음)"

    target = candidates[0]
    target.click_input()
    time.sleep(0.5)
    send_keys(f"{{PGUP}}{{DOWN {n}}}{{ENTER}}")
    time.sleep(0.6)

    result = "(읽기실패)"
    try:
        refreshed = [
            ctrl for ctrl in order_window.descendants()
            if ctrl.element_info.control_type == "Pane"
            and ctrl.element_info.automation_id == AUTO_ID_DROPDOWN_TYPE_BUY
            and ctrl.element_info.name.strip()
        ]
        if refreshed:
            result = refreshed[0].element_info.name
    except Exception as e:
        result = f"(에러: {e})"

    try:
        order_window.close()
    except Exception:
        pass
    time.sleep(0.5)
    return result


def main():
    from automation_target_store import resolve_first_user_account
    selected_user, account_index = resolve_first_user_account(TEST_USER, TEST_ACCOUNT)
    password = get_account_password(selected_user)
    logging.info(f"대상: {selected_user} / {account_index}번째 계좌")

    hwnd = win32gui.FindWindow(None, "iMeritz")
    if hwnd == 0:
        raise Exception("HTS 창을 찾을 수 없습니다. (로그인된 상태에서 실행하세요)")

    setup_window(hwnd)
    app = Application(backend="uia").connect(handle=hwnd)
    main_window = app.window(handle=hwnd)

    logging.info(f"===== 후보 N={CANDIDATES} 순서대로 주문창 새로 열어 확인 =====")
    for n in CANDIDATES:
        result = probe(main_window, selected_user, account_index, password, n)
        logging.info(f"  N={n} -> '{result}'")

    logging.info("진단 완료 — 실주문 없이 종료했습니다.")


if __name__ == "__main__":
    main()
