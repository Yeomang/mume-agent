"""
매수 유형 드롭다운 진단 스크립트 (읽기 전용 — 절대 실주문 안 함).

HTS가 이미 로그인되어 열려 있는 상태에서 실행한다. hts_order_buy.py와 동일하게
계좌 선택 + 비밀번호 안내창 처리까지 그대로 따라가서 실제 매수 흐름과 화면
상태를 최대한 똑같이 재현한 뒤, automation_id="3865"인 Pane을 전부 나열하고
이름이 있는(=현재 선택값이 표시된) 컨트롤을 골라 클릭해 드롭다운을 연다.
열린 뒤 나타나는 항목 목록(ListItem)을 순서대로 출력해 LOC가 몇 번째인지
직접 확인한다. 수량/가격 입력이나 매수 버튼 클릭은 하지 않고 주문창을 닫고
종료한다.

실행: (.venv 활성화 후) python diagnose_buy_type_dropdown.py
"""
from utils import setup_window, find_control_by_criteria, set_focus_and_type, _handle_password_dialog
from secrets_manager import get_account_password
from pywinauto import Application, Desktop
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


def _dump(ctrl, depth=0, max_depth=3):
    """컨트롤 서브트리를 재귀적으로 로그 출력 (print_control_identifiers 대체)."""
    if depth > max_depth:
        return
    try:
        info = ctrl.element_info
        rect = ctrl.rectangle()
        logging.info(f"{'  ' * depth}- type={info.control_type} name='{info.name}' automation_id='{info.automation_id}' rect={rect}")
    except Exception as e:
        logging.info(f"{'  ' * depth}- (읽기 실패: {e})")
        return
    try:
        for child in ctrl.children():
            _dump(child, depth + 1, max_depth)
    except Exception:
        pass


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

    logging.info(f"화면번호 [{SCREEN_NUM_ORDER}] 입력...")
    search_input = find_control_by_criteria(main_window, "Edit", automation_id=AUTO_ID_SCREEN_SEARCH_INPUT)
    set_focus_and_type(search_input, SCREEN_NUM_ORDER)

    order_window = find_control_by_criteria(main_window, "Window", title="[06100] 해외주식 주문", delay=2, retries=5)
    if not order_window:
        raise Exception("[06100] 해외주식 주문 창을 찾을 수 없습니다.")

    # 계좌번호 선택 (실제 매수 흐름과 동일하게 재현)
    logging.info(f"{selected_user}님의 {account_index}번째 계좌번호 선택 중...")
    dropdown_account = find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_DROPDOWN_ACCOUNT, index=CTRL_INDEX_DROPDOWN_ACCOUNT)
    if dropdown_account:
        dropdown_account.click_input()
    send_keys(f"{{PGUP}}{{DOWN {account_index}}}{{ENTER}}")
    logging.info("계좌번호 선택 완료")

    # 비밀번호 입력 안내창 처리 (있으면 처리, 없으면 그냥 통과)
    before = time.time()
    _handle_password_dialog(main_window, password)
    logging.info(f"비밀번호 안내창 처리 단계 통과 (소요 {time.time()-before:.1f}s — 3초 이상이면 실제로 창이 떴던 것)")

    tab_buy = find_control_by_criteria(main_window, "TabItem", title="매수")
    tab_buy.click_input()
    logging.info("매수 탭 클릭 완료")

    ticker_input = find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_TICKER_BUY_INPUT)
    set_focus_and_type(ticker_input, f"{TEST_TICKER}{{ENTER}}")
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

    # 이름이 채워진(=현재 선택값을 표시 중인) 컨트롤을 실제 드롭다운으로 판단
    named = [(i, c) for i, c in enumerate(candidates) if c.element_info.name.strip()]
    if not named:
        logging.warning("이름이 있는 컨트롤을 못 찾았습니다 — 수동으로 판단 필요")
        order_window.close()
        return

    target_idx, target = named[0]
    logging.info(f"실제 유형 드롭다운으로 판단되는 컨트롤: index={target_idx} name='{target.element_info.name}'")

    # 2) 클릭해서 드롭다운 열기
    target.click_input()
    time.sleep(1)

    # 3) 열린 드롭다운의 항목 목록 출력 — order_window 하위 재탐색
    logging.info("===== 드롭다운 오픈 후 order_window 하위 트리 (ListItem 위주) =====")
    _dump(order_window, max_depth=4)

    # 4) 팝업이 order_window 밖(Desktop 최상위)에 별도로 뜨는 경우 대비
    logging.info("===== Desktop 최상위에서 ListItem 직접 탐색 =====")
    try:
        desktop = Desktop(backend="uia")
        for w in desktop.windows():
            try:
                for item in w.descendants(control_type="ListItem"):
                    name = item.element_info.name
                    if name.strip():
                        logging.info(f"  ListItem: name='{name}' rect={item.rectangle()}")
            except Exception:
                continue
    except Exception as e:
        logging.warning(f"Desktop ListItem 탐색 실패: {e}")

    # ESC로 드롭다운 닫고 주문창 닫기 — 절대 주문 제출 안 함
    send_keys("{ESC}")
    time.sleep(0.5)
    try:
        order_window.close()
    except Exception:
        pass
    logging.info("진단 완료 — 실주문 없이 종료했습니다.")


if __name__ == "__main__":
    main()
