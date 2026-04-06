from utils import setup_window, get_window_handle, find_control_by_criteria, set_focus_and_type, wait_for_window, block_input, copy_to_clipboard
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


# main_window 하위의 모든 자식 및 자손 GUI 요소의 정보를 출력
# main_window.print_control_identifiers()


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

    # '조회' 버튼 클릭
    inquiry_btn = find_control_by_criteria(main_window, "Button", automation_id=AUTO_ID_INQUIRY_BUTTON)
    if not inquiry_btn:
        raise Exception("조회 버튼을 찾을 수 없습니다.")
    inquiry_btn.click_input()
    logging.info(f"'조회' 버튼을 클릭하였습니다.")

    # 데이터 테이블 위치 찾기
    table_pane = find_control_by_criteria(main_window, "Pane", automation_id=AUTO_ID_TABLE_BALANCE, delay=2, retries=5)
    if not table_pane:
        raise Exception("보유잔고 데이터 테이블을 찾을 수 없습니다.")
    rect = table_pane.rectangle()

    # 테이블의 첫 번째의 행 좌표 계산(대충) 후 우클릭
    table_width = rect.right - rect.left
    table_height = rect.bottom - rect.top
    x = int(rect.left + table_width/2)
    y = int(rect.top + 60)
    click(button="right", coords=(x, y))    
    logging.info(f"데이터 테이블의 첫 행 중앙 부분의 좌표를 찾아 우클릭하였습니다.")
    time.sleep(1)  # 우클릭 후 잠깐 대기 

    # "파일로 보내기" 항목 클릭 (우클릭 메뉴에서 9번째)
    send_keys(f"{{DOWN 9}}{{ENTER}}")
    logging.info(f"'파일로 보내기' 버튼을 클릭하였습니다.")
    time.sleep(0.5)

    # 'Csv로 저장' 항목 클릭
    send_keys("c")  # Csv로 저장 단축키 실행
    logging.info(f"'Csv로 저장' 버튼을 클릭하였습니다.")
    time.sleep(1)

    # "다른 이름으로 저장" 대화상자 뜰 때까지 기다리기
    # 저장 대화상자 확인
    dialog_found = wait_for_window("다른 이름으로 저장", main_window, "다른 이름으로 저장", "Window", timeout=10)
    
    # try:
    #     wait_for_window("다른 이름으로 저장", main_window, "다른 이름으로 저장", "Window", timeout=10)
    #     dialog_found = True
    # except (TimeoutError, ElementNotFoundError):
    #     dialog_found = False

    
    if dialog_found:
        

        # 저장 진행
        copy_to_clipboard(str(save_path))
        send_keys("%n")
        send_keys("^v{ENTER}")
        logging.info(f"[정상 저장] {save_path}")

        # 저장 완료까지 대기
        time.sleep(2)

    else:
        logging.warning("[저장 실패] '다른 이름으로 저장' 대화상자가 열리지 않음")
    

    # 마우스 및 키보드 잠금 해제
    block_input(False)

    order_window.close()
    logging.info("'해외주식 보유잔고' 창을 닫았습니다.")

    logging.info(">>>>> HTS 해외주식 보유잔고 데이터 csv파일로 저장하기 완료! <<<<<")


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
        save_data_stock_balance(selected_user, account_index)

