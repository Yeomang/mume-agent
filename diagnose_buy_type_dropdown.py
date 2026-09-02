"""
매수 유형 드롭다운 진단 스크립트 (읽기 전용 — 절대 실주문 안 함).

HTS가 이미 로그인되어 열려 있는 상태에서 실행한다. hts_order_buy.py와 동일하게
계좌 선택 + 비밀번호 안내창 처리까지 그대로 따라가서 실제 매수 흐름과 화면
상태를 최대한 똑같이 재현한 뒤, automation_id="3865"인 Pane을 전부 나열하고
이름이 있는(=현재 선택값이 표시된) 컨트롤을 실제 유형 드롭다운으로 판단한다.

이후 실제 코드와 동일한 {PGUP}{DOWN N}{ENTER} 시퀀스를 N=0..9까지 하나씩
실행하면서, 그 결과 드롭다운 컨트롤에 표시되는 값(name)을 그대로 읽어
"N번 -> 실제 선택된 값"을 로그로 남긴다. LOC가 정확히 몇 번인지 이 로그로
바로 확인 가능하다. 수량/가격 입력이나 매수 버튼 클릭은 절대 하지 않고
주문창을 닫고 종료한다.

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

    # 2) 실제 코드와 동일한 {PGUP}{DOWN N}{ENTER}를 N=0..9까지 하나씩 실행하며
    #    결과로 표시되는 값을 그대로 읽는다 — 이게 실제 order_type_index별 정답.
    logging.info("===== N별 {PGUP}{DOWN N}{ENTER} 결과 확인 (N=0~9) =====")
    for n in range(10):
        target.click_input()
        time.sleep(0.4)
        send_keys(f"{{PGUP}}{{DOWN {n}}}{{ENTER}}")
        time.sleep(0.5)
        refreshed = []
        try:
            # 컨트롤 참조가 오래돼 무효화될 수 있어 매번 재탐색
            refreshed = [
                ctrl for ctrl in order_window.descendants()
                if ctrl.element_info.control_type == "Pane"
                and ctrl.element_info.automation_id == AUTO_ID_DROPDOWN_TYPE_BUY
                and ctrl.element_info.name.strip()
            ]
            result_name = refreshed[0].element_info.name if refreshed else "(못찾음)"
        except Exception as e:
            result_name = f"(에러: {e})"
        logging.info(f"  N={n} -> '{result_name}'")
        # 다음 시도를 위해 target 참조 갱신
        if refreshed:
            target = refreshed[0]

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
