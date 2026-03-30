from utils import setup_window, get_window_handle, find_control_by_criteria, set_focus_and_type, wait_for_window, block_input, copy_to_clipboard
from config import Config
from pywinauto import Application
import time
from pywinauto.keyboard import send_keys
from pywinauto.mouse import click
import logging
import datetime as dt
from pathlib import Path
import os

# 상수 정의
AUTO_ID_SCREEN_SEARCH_INPUT = "1000"  # 화면검색 번호 입력 필드 automation_id
SCREEN_NUM_ORDER_EXECUTION_HISTORY = "6114"  # 해외주식 주문체결내역 화면번호
AUTO_ID_DROPDOWN_ACCOUNT = "3845"  # 계좌번호 드롭다운 필드 automation_id
AUTO_ID_INQUIRY_START_DATE = "3805"  # 해외주식 주문체결내역 조회기간 시작일 입력 필드 automation_id
AUTO_ID_INQUIRY_END_DATE = "3810"  # 해외주식 주문체결내역 조회기간 종료일 입력 필드 automation_id
AUTO_ID_EXECUTION_CLASS_BUTTON_AREA = "3870"  # 해외주식 주문체결내역 체결구분 라디오버튼 영역 automation_id
AUTO_ID_SORT_OPTION_BUTTON_AREA = "3880"  # 해외주식 주문체결내역 정렬구분 라디오버튼 영역 automation_id
AUTO_ID_CONTINUOUS_INQUIRY_CHECKBOX = "3935"  # 해외주식 주문체결내역 연속조회 체크박스 automation_id
AUTO_ID_INQUIRY_BUTTON = "3895"  # 해외주식 주문체결내역 조회 버튼 automation_id
AUTO_ID_TABLE_INQUIRY = "3910"  # 해외주식 주문체결내역 화면에서 아래 표 영역 automation_id


def save_data_order_execution(selected_user, account_index, inquiry_start_date=None, inquiry_end_date=None):
    logging.info(">>>>> HTS 주문체결내역 데이터 csv파일로 저장하기 시작! <<<<<")
    # 조회기간 매개변수를 별도로 지정하지 않은 경우, default로 어제 날짜로 지정
    if inquiry_start_date is None:
        inquiry_start_date = (dt.date.today()-dt.timedelta(days=1)).strftime('%Y%m%d')  # 조회기간 시작일을 어제로 지정 (yyyymmdd)
    if inquiry_end_date is None:
        inquiry_end_date = (dt.date.today()-dt.timedelta(days=1)).strftime('%Y%m%d')  # 조회기간 종료일로 어제로 지정 (yyyymmdd)
    
    # 마우스 및 키보드 잠금 시작
    block_input(True)
    
    # HTS 창 핸들 가져오기
    hwnd = get_window_handle("iMeritz")

    # HTS 창 메인모니터로 이동, 포커싱, 최대화, 항상위로
    setup_window(hwnd)
    
    # pywinauto 제어를 위해 핸들을 기반으로 실제 애플리케이션과 연결
    app = Application(backend="uia").connect(handle=hwnd)
    main_window = app.window(handle=hwnd)
    
    # 화면 검색 입력
    logging.info(f"화면번호 [{SCREEN_NUM_ORDER_EXECUTION_HISTORY}]를 입력하여 '해외주식 주문체결내역' 창을 띄우는 중...")
    search_input = find_control_by_criteria(main_window, "Edit", automation_id=AUTO_ID_SCREEN_SEARCH_INPUT)
    set_focus_and_type(search_input, SCREEN_NUM_ORDER_EXECUTION_HISTORY)
    logging.info("'해외주식 주문체결내역' 창을 띄웠습니다.")
    
    # 해외주식 주문체결내역 창 접근
    order_window = find_control_by_criteria(main_window, "Window", title="[06114] 해외주식 주문체결내역")

    # 계좌 선택
    find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_DROPDOWN_ACCOUNT).click_input()
    send_keys(f"{{PGUP}}{{DOWN {account_index}}}{{ENTER}}")
    logging.info(f"{selected_user}님의 {account_index}번째 계좌번호를 선택하였습니다.")

    # 조회기간 입력    
    inquiry_start_date_input = find_control_by_criteria(main_window, "Pane", automation_id=AUTO_ID_INQUIRY_START_DATE)
    set_focus_and_type(inquiry_start_date_input, inquiry_start_date)
    inquiry_end_date_input = find_control_by_criteria(main_window, "Pane", automation_id=AUTO_ID_INQUIRY_END_DATE)
    set_focus_and_type(inquiry_end_date_input, inquiry_end_date)
    logging.info(f"조회기간 입력 : {inquiry_start_date}-{inquiry_end_date}")

    # # 체결구분 선택
    # execution_class_button_area = find_control_by_criteria(main_window, "Button", automation_id=AUTO_ID_EXECUTION_CLASS_BUTTON_AREA)
    # execution_only_button = find_control_by_criteria(execution_class_button_area, "Button", automation_id="2")
    # execution_only_button.click_input()
    # logging.info(f"체결구분에서 '체결' 버튼을 클릭하였습니다.")
    
    # 정렬구분 선택
    sort_option_button_area = find_control_by_criteria(main_window, "Button", automation_id=AUTO_ID_SORT_OPTION_BUTTON_AREA)
    ascending_button = find_control_by_criteria(sort_option_button_area, "Button", automation_id="2")
    ascending_button.click_input()
    logging.info(f"정렬구분에서 '정순' 버튼을 클릭하였습니다.")

    # '연속조회' 체크박스 체크
    find_control_by_criteria(main_window, "Button", automation_id=AUTO_ID_CONTINUOUS_INQUIRY_CHECKBOX).click_input()
    logging.info(f"'연속조회' 체크박스 버튼을 클릭하였습니다.")

    # '조회' 버튼 클릭
    find_control_by_criteria(main_window, "Button", automation_id=AUTO_ID_INQUIRY_BUTTON).click_input()
    logging.info(f"'조회' 버튼을 클릭하였습니다.")

    # 데이터 테이블 위치 찾기
    rect = find_control_by_criteria(main_window, "Pane", automation_id=AUTO_ID_TABLE_INQUIRY).rectangle()

    # 테이블의 첫 번째의 행 좌표 계산(대충) 후 우클릭
    table_width = rect.right - rect.left
    table_height = rect.bottom - rect.top
    x = int(rect.left + table_width/2)
    y = int(rect.top + 60)
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
    save_dir = Path("./data/all_order_execution_raw")
    save_dir.mkdir(parents=True, exist_ok=True)  # 폴더 없으면 자동 생성(부모 디렉토리까지)

    # 파일경로 Unicode 적용
    save_path = Path(current_dir) / "data" / "all_order_execution_raw" / f"all_order_execution_raw_{selected_user}_{account_index}_{inquiry_start_date}-{inquiry_end_date}.csv"

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
    logging.info("'해외주식 주문체결내역' 창을 닫았습니다.")
    
    logging.info(">>>>> HTS 주문체결내역 데이터 csv파일로 저장하기 완료! <<<<<")
    

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
        save_data_order_execution(selected_user, account_index)

    # inquiry_start_date = "20250318"  # 조회기간 시작일 직접 설정 (yyyymmdd)
    # inquiry_end_date = "20250325"  # 조회기간 종료일 직접 설정 (yyyymmdd)    
    # save_data_order_execution(selected_user, account_index, inquiry_start_date, inquiry_end_date)