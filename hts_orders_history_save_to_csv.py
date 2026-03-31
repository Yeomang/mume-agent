from utils import setup_window, get_window_handle, find_control_by_criteria, set_focus_and_type, wait_for_window, block_input, copy_to_clipboard
from secrets_manager import get_account_password
from config import Config
from pywinauto import Application
import time
from pywinauto.keyboard import send_keys
from pywinauto.mouse import click
import logging
from pathlib import Path
import os

# 상수 정의
SCREEN_NUM_ORDER = "6100"  # 해외주식 주문 화면번호
AUTO_ID_SCREEN_SEARCH_INPUT = "1000"  # 화면검색 번호 입력 필드 automation_id
AUTO_ID_DROPDOWN_ACCOUNT = "3780"  # 계좌번호 드롭다운 필드 automation_id
CTRL_INDEX_DROPDOWN_ACCOUNT = 2  # order_window 하위 컨트롤 검색결과 계좌번호 드롭다운 필드 순번
AUTO_ID_PASSWORD_DIALOG_OK_BUTTON = "2"  # 비밀번호 입력 안내창 확인 버튼 automation_id
AUTO_ID_TABLE_ORDER = "3795"  # 주문체결 탭 아래 표 영역 automation_id
CTRL_INDEX_TABLE_ORDER = 2  # order_window 하위 컨트롤 검색결과 주문체결 탭 아래 표 영역 순번


# main_window 하위의 모든 자식 및 자손 GUI 요소의 정보를 출력
# main_window.print_control_identifiers()


def save_orders_history(selected_user, account_index):
    logging.info(">>>>> HTS 해외주식 주문 내역 데이터 csv파일로 저장하기 시작! <<<<<")

    # 마우스 및 키보드 잠금 시작
    block_input(True)

    # 계좌 비밀번호 불러오기
    password = get_account_password(selected_user)

    # HTS 창 핸들 가져오기
    hwnd = get_window_handle("iMeritz")

    # HTS 창 메인모니터로 이동, 포커싱, 최대화, 항상위로
    setup_window(hwnd)

    # pywinauto 제어를 위해 핸들을 기반으로 실제 애플리케이션과 연결
    app = Application(backend="uia").connect(handle=hwnd)
    main_window = app.window(handle=hwnd)

    # 화면 검색 입력
    logging.info(f"화면번호 [{SCREEN_NUM_ORDER}]를 입력하여 '해외주식 주문' 창을 띄우는 중...")
    search_input = find_control_by_criteria(main_window, "Edit", automation_id=AUTO_ID_SCREEN_SEARCH_INPUT)
    set_focus_and_type(search_input, SCREEN_NUM_ORDER)
    logging.info("'해외주식 주문' 창을 띄웠습니다.")

    # 해외주식 주문 창 접근
    order_window = find_control_by_criteria(main_window, "Window", title="[06100] 해외주식 주문")    

    # 계좌 선택
    find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_DROPDOWN_ACCOUNT, index=CTRL_INDEX_DROPDOWN_ACCOUNT).click_input()
    send_keys(f"{{PGUP}}{{DOWN {account_index}}}{{ENTER}}")
    logging.info(f"{selected_user}님의 {account_index}번째 계좌번호를 선택하였습니다.")

    # 비밀번호 입력 안내창 처리 (parent의 자식 창에서 먼저 찾도록 title을 구체적으로 지정)
    dialog = wait_for_window("비밀번호 입력 안내창", main_window, "비밀번호", "Window", timeout=3)
    if dialog:
        logging.info(f"확인 버튼 찾는 중...")
        ok_button = find_control_by_criteria(dialog, "Button", automation_id=AUTO_ID_PASSWORD_DIALOG_OK_BUTTON)
        if ok_button:
            ok_button.click_input()
            logging.info(f"확인 버튼을 클릭하였습니다.")
            set_focus_and_type(None, f"+{{TAB}}{password}{{ENTER}}")
            logging.info(f"비밀번호를 입력하였습니다.")

    # 주문체결 탭 클릭
    logging.info(f"주문체결 탭 버튼 찾는 중...")
    tab_sell = find_control_by_criteria(main_window, "TabItem", title="주문체결")
    tab_sell.click_input()
    logging.info(f"주문체결 탭을 클릭하였습니다.")

    # 데이터 테이블 위치 찾기
    rect = find_control_by_criteria(main_window, "Pane", automation_id=AUTO_ID_TABLE_ORDER, index=CTRL_INDEX_TABLE_ORDER).rectangle()

    # 테이블의 첫 번째의 행 좌표 계산(대충) 후 우클릭
    table_width = rect.right - rect.left
    table_height = rect.bottom - rect.top
    x = int(rect.left + table_width/2)
    y = int(rect.top + 25)
    click(button="right", coords=(x, y))    
    logging.info(f"데이터 테이블의 첫 행 중앙 부분의 좌표를 찾아 우클릭하였습니다.")
    time.sleep(1)  # 우클릭 후 잠깐 대기 

    # "파일로 보내기" 항목 클릭 (우클릭 메뉴에서 6번째)
    send_keys(f"{{DOWN 6}}{{ENTER}}")
    logging.info(f"'파일로 보내기' 버튼을 클릭하였습니다.")
    time.sleep(0.5)

    # 'Csv로 저장' 항목 클릭
    send_keys("c")  # Csv로 저장 단축키 실행
    logging.info(f"'Csv로 저장' 버튼을 클릭하였습니다.")
    time.sleep(1)

    # "다른 이름으로 저장" 대화상자 뜰 때까지 기다리기
    wait_for_window("다른 이름으로 저장", main_window, "다른 이름으로 저장", "Window", timeout=10)

    # 현재 실행 중인 .py 파일의 디렉토리 경로 가져오기
    current_dir = os.path.dirname(os.path.abspath(__file__))

    # 현재 디렉토리 하위에 저장할 폴더 경로 지정
    save_dir = Path("./data/order_history_raw")
    save_dir.mkdir(parents=True, exist_ok=True)  # 폴더 없으면 자동 생성(부모 디렉토리까지)

    # 파일경로 Unicode 적용
    save_path = Path(current_dir) / "data" / "order_history_raw" / f"order_history_raw_{selected_user}_{account_index}.csv"

    # 클립보드에 UTF-16LE로 복사
    copy_to_clipboard(str(save_path))

    # "다른 이름으로 저장" 대화상자에서 'Alt + N'으로 파일 이름 입력 필드 선택
    send_keys("%n")

    # 클립보드에 UTF-16LE로 복사된 파일경로 붙여넣고 Enter 입력
    send_keys("^v{ENTER}")

    logging.info(f"csv 데이터를 아래 경로에 저장하였습니다.")
    logging.info(f"저장경로 : {str(save_path)}")

    # 마우스 및 키보드 잠금 해제
    block_input(False)

    order_window.close()
    logging.info("'해외주식 주문' 창을 닫았습니다.")

    logging.info(">>>>> HTS 해외주식 주문 내역 데이터 csv파일로 저장하기 완료! <<<<<")


if __name__ == "__main__":
    # 웹UI에서 저장한 자동 실행 대상에서 첫 번째 사용자/계좌 로드 (테스트용)
    from automation_target_store import load_automation_target_with_meta
    
    targets, _ = load_automation_target_with_meta(None, include_cycles=True)
    # 모든 job에서 첫 번째 사용자 찾기
    users = {}
    for job_targets in targets.values() if isinstance(targets, dict) else []:
        if isinstance(job_targets, dict):
            users.update(job_targets)
            break
    
    selected_user = list(users.keys())[0] if users else ""
    account_index = 1
    if selected_user and users.get(selected_user):
        # 첫 번째 계좌 사용
        first_account = users[selected_user][0] if isinstance(users[selected_user], list) else None
        if isinstance(first_account, dict):
            account_index = first_account.get("account", 1)
        elif isinstance(first_account, int):
            account_index = first_account
    
    if not selected_user:
        print("경고: 저장된 사용자/계좌 설정이 없습니다. 웹UI에서 먼저 설정해주세요.")
    else:
        save_orders_history(selected_user, account_index)

