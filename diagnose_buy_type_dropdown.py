"""
매수 유형 드롭다운 진단 스크립트 (읽기 전용 — 절대 실주문 안 함).

HTS가 이미 로그인되어 열려 있는 상태에서 실행한다.
[06100] 해외주식 주문 → 매수 탭까지 이동한 뒤, automation_id="3865"인
Pane을 전부 나열하고, 코드가 지금 잡고 있는 index=1 컨트롤을 클릭해
열리는 드롭다운의 전체 컨트롤 트리를 출력한다. 수량/가격 입력이나
매수 버튼 클릭은 하지 않고 주문창을 닫고 종료한다.

실행: (.venv 활성화 후) python diagnose_buy_type_dropdown.py
"""
from utils import setup_window, find_control_by_criteria
from pywinauto import Application
from pywinauto.keyboard import send_keys
import win32gui
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SCREEN_NUM_ORDER = "6100"
AUTO_ID_SCREEN_SEARCH_INPUT = "1000"
AUTO_ID_TICKER_BUY_INPUT = "3860"
AUTO_ID_DROPDOWN_TYPE_BUY = "3865"
TEST_TICKER = "TQQQ"


def main():
    hwnd = win32gui.FindWindow(None, "iMeritz")
    if hwnd == 0:
        raise Exception("HTS 창을 찾을 수 없습니다. (로그인된 상태에서 실행하세요)")

    setup_window(hwnd)
    app = Application(backend="uia").connect(handle=hwnd)
    main_window = app.window(handle=hwnd)

    logging.info(f"화면번호 [{SCREEN_NUM_ORDER}] 입력...")
    search_input = find_control_by_criteria(main_window, "Edit", automation_id=AUTO_ID_SCREEN_SEARCH_INPUT)
    search_input.set_focus()
    send_keys(SCREEN_NUM_ORDER)
    send_keys("{ENTER}")

    order_window = find_control_by_criteria(main_window, "Window", title="[06100] 해외주식 주문", delay=2, retries=5)
    if not order_window:
        raise Exception("[06100] 해외주식 주문 창을 찾을 수 없습니다.")

    tab_buy = find_control_by_criteria(main_window, "TabItem", title="매수")
    tab_buy.click_input()
    logging.info("매수 탭 클릭 완료")

    ticker_input = find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_TICKER_BUY_INPUT)
    ticker_input.set_focus()
    send_keys(f"{TEST_TICKER}{{ENTER}}")
    logging.info(f"종목 '{TEST_TICKER}' 입력 완료")
    time.sleep(1)

    # 1) automation_id=3865 매칭되는 Pane 전부 나열
    candidates = [
        ctrl for ctrl in order_window.descendants()
        if ctrl.element_info.control_type == "Pane"
        and ctrl.element_info.automation_id == AUTO_ID_DROPDOWN_TYPE_BUY
    ]
    logging.info(f"===== automation_id='{AUTO_ID_DROPDOWN_TYPE_BUY}' Pane 매칭 개수: {len(candidates)} =====")
    for i, ctrl in enumerate(candidates):
        try:
            rect = ctrl.rectangle()
            name = ctrl.element_info.name
        except Exception as e:
            rect, name = f"(에러: {e})", "(에러)"
        logging.info(f"  [index={i}] name='{name}' rect={rect}")

    if len(candidates) <= 1:
        logging.warning("매칭이 1개뿐입니다 — 기존 코드의 index=1 가정이 이미 범위를 벗어납니다!")
        order_window.close()
        return

    target = candidates[1]
    logging.info(f"기존 코드가 클릭하는 index=1 컨트롤: name='{target.element_info.name}'")

    # 2) 클릭해서 드롭다운 열기
    target.click_input()
    time.sleep(1)

    # 3) 열린 드롭다운의 전체 컨트롤 트리 출력 (항목 텍스트/순서 확인용)
    logging.info("===== 드롭다운 오픈 후 order_window 전체 컨트롤 트리 =====")
    try:
        order_window.print_control_identifiers()
    except Exception as e:
        logging.warning(f"print_control_identifiers 실패: {e}")

    # 4) 혹시 별도 팝업(List)으로 뜨는 경우 대비 — 최상위 데스크톱에서도 한번 더 탐색
    try:
        from pywinauto import Desktop
        logging.info("===== Desktop 최상위 List/Pane 탐색 (팝업 드롭다운 대비) =====")
        for w in Desktop(backend="uia").windows():
            try:
                if w.element_info.control_type in ("List", "Pane") and w.is_visible():
                    logging.info(f"  top-level: type={w.element_info.control_type} name='{w.element_info.name}' rect={w.rectangle()}")
            except Exception:
                continue
    except Exception as e:
        logging.warning(f"Desktop 탐색 실패: {e}")

    # ESC로 드롭다운 닫고 (혹시 열려있으면) 주문창 닫기 — 절대 주문 제출 안 함
    send_keys("{ESC}")
    time.sleep(0.5)
    try:
        order_window.close()
    except Exception:
        pass
    logging.info("진단 완료 — 실주문 없이 종료했습니다.")


if __name__ == "__main__":
    main()
