from utils import setup_window, get_window_handle, find_control_by_criteria, set_focus_and_type, wait_for_window, block_input, copy_to_clipboard, with_desktop_retry
from config import Config
from pywinauto import Application
import time
from pywinauto.keyboard import send_keys
from pywinauto.mouse import click
import logging
from pathlib import Path
import os

# 상수 정의
AUTO_ID_SCREEN_SEARCH_INPUT = "1000"  # 화면검색 번호 입력 필드 automation_id
SCREEN_NUM_BALANCE = "6104"  # 해외주식 보유잔고 화면번호
AUTO_ID_DROPDOWN_ACCOUNT = "3785"  # 계좌번호 드롭다운 필드 automation_id
AUTO_ID_INQUIRY_BUTTON = "3815"  # 해외주식 보유잔고 조회 버튼 automation_id
AUTO_ID_TABLE_BALANCE = "3860"  # 해외주식 보유잔고 화면에서 표 영역 automation_id
AUTO_ID_TABLE_FOREIGN_DEPOSIT = "3880"  # 예수금 탭의 외화 예수금 표 영역 automation_id


def _export_table_to_csv(
    main_window, table_pane, save_path, label,
    first_row_offset=60, export_menu_index=9,
):
    """우클릭 메뉴 → '파일로 보내기' → 'Csv로 저장' 흐름으로 테이블을 CSV 파일로 저장합니다.

    first_row_offset: 테이블 상단(rect.top)으로부터 첫 데이터 행까지의 y 오프셋.
                      헤더만 있고 행이 적은 테이블(예: 외화 예수금)은 값을 더 작게 조정해야 한다.
    export_menu_index: 우클릭 메뉴에서 '파일로 보내기' 항목의 순번(1-based).
                      잔고 탭 테이블은 9, 예수금 탭 외화예수금 테이블은 6 등 테이블마다 다르다.
    """
    rect = table_pane.rectangle()
    table_width = rect.right - rect.left
    x = int(rect.left + table_width / 2)
    y = int(rect.top + first_row_offset)
    click(button="right", coords=(x, y))
    logging.info(f"{label} 테이블의 첫 행 중앙 부분의 좌표를 찾아 우클릭하였습니다.")
    time.sleep(1)

    send_keys(f"{{DOWN {export_menu_index}}}{{ENTER}}")
    logging.info(f"'파일로 보내기' 버튼을 클릭하였습니다. (메뉴 {export_menu_index}번째)")
    time.sleep(0.5)

    send_keys("c")
    logging.info("'Csv로 저장' 버튼을 클릭하였습니다.")
    time.sleep(1)

    dialog_found = wait_for_window("다른 이름으로 저장", main_window, "다른 이름으로 저장", "Window", timeout=10)
    if dialog_found:
        copy_to_clipboard(str(save_path))
        send_keys("%n")
        send_keys("^v{ENTER}")
        logging.info(f"[정상 저장] {save_path}")
        time.sleep(2)
    else:
        logging.warning(f"[저장 실패] '다른 이름으로 저장' 대화상자가 열리지 않음 ({label})")


# main_window 하위의 모든 자식 및 자손 GUI 요소의 정보를 출력
# main_window.print_control_identifiers()


@with_desktop_retry
def save_data_stock_balance(selected_user, account_index):
    logging.info(">>>>> HTS 해외주식 보유잔고 데이터 csv파일로 저장하기 시작! <<<<<")

    # 마우스 및 키보드 잠금 시작
    block_input(True)

    # 현재 실행 중인 .py 파일의 디렉토리 경로 가져오기
    current_dir = os.path.dirname(os.path.abspath(__file__))

    # 현재 디렉토리 하위에 저장할 폴더 경로 지정
    save_dir = Path("./data/stock_balance_raw")
    save_dir.mkdir(parents=True, exist_ok=True)  # 폴더 없으면 자동 생성(부모 디렉토리까지)

    # 파일경로 Unicode 적용
    save_path = Path(current_dir) / "data" / "stock_balance_raw" / f"stock_balance_raw_{selected_user}_{account_index}.csv"

    # 기존 파일 삭제 (덮어쓰기 방지)
    if save_path.exists():
        os.remove(save_path)

    # HTS 창 핸들 가져오기
    hwnd = get_window_handle("iMeritz")

    # HTS 창 메인모니터로 이동, 포커싱, 최대화, 항상위로
    setup_window(hwnd)

    # pywinauto 제어를 위해 핸들을 기반으로 실제 애플리케이션과 연결
    app = Application(backend="uia").connect(handle=hwnd)
    main_window = app.window(handle=hwnd)

    # 화면 검색 입력
    logging.info(f"화면번호 [{SCREEN_NUM_BALANCE}]를 입력하여 '해외주식 보유잔고' 창을 띄우는 중...")
    search_input = find_control_by_criteria(main_window, "Edit", automation_id=AUTO_ID_SCREEN_SEARCH_INPUT)
    set_focus_and_type(search_input, SCREEN_NUM_BALANCE)
    logging.info("'해외주식 보유잔고' 창을 띄웠습니다.")

    # 해외주식 보유잔고 창 접근 (delay 늘려서 창 로딩 대기)
    order_window = find_control_by_criteria(main_window, "Window", title="[06104] 해외주식 보유잔고", delay=2, retries=5)
    if not order_window:
        raise Exception("[06104] 해외주식 보유잔고 창을 찾을 수 없습니다.")

    # 계좌 선택
    dropdown = find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_DROPDOWN_ACCOUNT)
    if not dropdown:
        raise Exception("계좌 드롭다운을 찾을 수 없습니다.")
    dropdown.click_input()
    send_keys(f"{{PGUP}}{{DOWN {account_index}}}{{ENTER}}")
    logging.info(f"{selected_user}님의 {account_index}번째 계좌번호를 선택하였습니다.")
    time.sleep(3)

    # '조회' 버튼 클릭
    inquiry_btn = find_control_by_criteria(main_window, "Button", automation_id=AUTO_ID_INQUIRY_BUTTON)
    if not inquiry_btn:
        raise Exception("조회 버튼을 찾을 수 없습니다.")
    inquiry_btn.click_input()
    logging.info(f"'조회' 버튼을 클릭하였습니다.")
    time.sleep(4)  # 조회 결과 로딩 대기

    # '예수금' 탭이 열려있을 수 있으므로 '잔고' 탭을 명시적으로 클릭
    balance_tab = find_control_by_criteria(order_window, "TabItem", title="잔고", delay=0.5, retries=5)
    if not balance_tab:
        raise Exception("'잔고' 탭을 찾을 수 없습니다.")
    balance_tab.click_input()
    logging.info("'잔고' 탭을 클릭하였습니다.")
    time.sleep(1)  # 탭 전환 대기

    # 데이터 테이블 위치 찾기
    table_pane = find_control_by_criteria(main_window, "Pane", automation_id=AUTO_ID_TABLE_BALANCE, delay=2, retries=5)
    if not table_pane:
        raise Exception("보유잔고 데이터 테이블을 찾을 수 없습니다.")

    # 보유잔고 테이블 CSV 저장
    _export_table_to_csv(main_window, table_pane, save_path, label="보유잔고")

    logging.info(">>>>> HTS 해외주식 보유잔고 데이터 csv파일로 저장하기 완료! <<<<<")

    # ========== '예수금' 탭으로 전환 후 외화 예수금 CSV 저장 ==========
    logging.info(">>>>> HTS 외화 예수금 데이터 csv파일로 저장하기 시작! <<<<<")

    fx_save_dir = Path("./data/foreign_deposit_raw")
    fx_save_dir.mkdir(parents=True, exist_ok=True)
    fx_save_path = (
        Path(current_dir) / "data" / "foreign_deposit_raw"
        / f"foreign_deposit_raw_{selected_user}_{account_index}.csv"
    )
    if fx_save_path.exists():
        os.remove(fx_save_path)

    deposit_tab = find_control_by_criteria(order_window, "TabItem", title="예수금", delay=0.5, retries=5)
    if not deposit_tab:
        raise Exception("'예수금' 탭을 찾을 수 없습니다.")
    deposit_tab.click_input()
    logging.info("'예수금' 탭을 클릭하였습니다.")
    time.sleep(1.5)  # 탭 전환 및 데이터 로딩 대기

    fx_table = find_control_by_criteria(
        main_window, "Pane", automation_id=AUTO_ID_TABLE_FOREIGN_DEPOSIT, delay=2, retries=5
    )
    if not fx_table:
        raise Exception("외화 예수금 데이터 테이블을 찾을 수 없습니다.")

    # 외화 예수금 테이블은 헤더 + 1행 구조라 첫 행 y 오프셋을 더 작게 설정.
    # 또한 우클릭 메뉴 구성이 잔고 탭과 달라 '파일로 보내기' 위치가 6번째.
    _export_table_to_csv(
        main_window, fx_table, fx_save_path,
        label="외화 예수금",
        first_row_offset=35,
        export_menu_index=6,
    )

    logging.info(">>>>> HTS 외화 예수금 데이터 csv파일로 저장하기 완료! <<<<<")

    # 마우스 및 키보드 잠금 해제
    block_input(False)

    order_window.close()
    logging.info("'해외주식 보유잔고' 창을 닫았습니다.")


if __name__ == "__main__":
    # ------------------------------------------------------------
    # 로컬 테스트용 실행 블록
    # - 아래 TEST_USER / TEST_ACCOUNT 를 직접 지정하면 해당 값으로 실행됨.
    #   (예: TEST_USER = "최용준", TEST_ACCOUNT = 2)
    # - None 으로 두면 Supabase automation_target 에서 첫 번째 사용자/계좌를 자동 로드.
    # ------------------------------------------------------------
    TEST_USER: str | None = "홍승표"
    TEST_ACCOUNT: int | None = 3

    from automation_target_store import resolve_first_user_account

    selected_user, account_index = resolve_first_user_account(TEST_USER, TEST_ACCOUNT)
    save_data_stock_balance(selected_user, account_index)

