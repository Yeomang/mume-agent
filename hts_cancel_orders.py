# C:\mume_meritz\hts_cancel_orders.py

from utils import setup_window, find_control_by_criteria, set_focus_and_type, wait_for_window, block_input
from secrets_manager import get_account_password
from config import Config
from pywinauto import Application
import win32gui
from pywinauto.keyboard import send_keys
import logging
import time

# 상수 정의
SCREEN_NUM_ORDER = "6100"  # 해외주식 주문 화면번호
AUTO_ID_SCREEN_SEARCH_INPUT = "1000"  # 화면검색 번호 입력 필드 automation_id
AUTO_ID_DROPDOWN_ACCOUNT = "3780"  # 계좌번호 드롭다운 필드 automation_id
CTRL_INDEX_DROPDOWN_ACCOUNT = 2  # order_window 하위 컨트롤 검색결과 계좌번호 드롭다운 필드 순번
AUTO_ID_PASSWORD_DIALOG_OK_BUTTON = "2"  # 비밀번호 입력 안내창 확인 버튼 automation_id

# 미체결 탭 및 일괄취소 관련 상수
TAB_TITLE_UNFILLED = "미체결"  # 미체결 탭 제목
AUTO_ID_SELECT_ALL_CHECKBOX = "5015"  # 전체 선택 체크박스 automation_id
AUTO_ID_BATCH_CANCEL_BUTTON = "5020"  # 일괄취소 버튼 automation_id
AUTO_ID_CANCEL_CONFIRM_BUTTON = "3885"  # 취소 확인 팝업에서 '취소' 버튼 automation_id
AUTO_ID_CLOSE_BUTTON = "3795"  # 팝업에서 '닫기' 버튼 automation_id


def hts_cancel_orders(selected_user, account_index, is_test_mode):
    """
    해외주식 미체결 주문 일괄 취소 실행
    
    절차:
    1. [6100] 해외주식 주문 창 열기
    2. 계좌 선택 및 비밀번호 입력
    3. 하단의 [미체결] 탭 클릭
    4. 전체 선택 체크박스 클릭
    5. [일괄취소] 버튼 클릭
    6. 팝업 창에서 [취소] 클릭
    """
    order_window = None
    try:
        logging.info(">>>>> 미체결 주문 일괄 취소 시작! <<<<<")
        logging.info(f">>>>> {selected_user} | {account_index}번 계좌 <<<<<")
        
        # 마우스 및 키보드 잠금 시작
        block_input(True)

        # 계좌 비밀번호 불러오기
        password = get_account_password(selected_user)
        
        # HTS 창 검색
        hwnd = win32gui.FindWindow(None, "iMeritz")
        if hwnd == 0:
            raise Exception("HTS 창을 찾을 수 없습니다.")    
        
        # HTS 창 메인모니터로 이동, 포커싱, 최대화, 항상위로
        setup_window(hwnd)

        # HTS 프로그램 연결
        app = Application(backend="uia").connect(handle=hwnd)
        main_window = app.window(handle=hwnd)

        # 화면 검색 입력
        logging.info(f"화면번호 [{SCREEN_NUM_ORDER}]를 입력하여 '해외주식 주문' 창을 띄우는 중...")
        search_input = find_control_by_criteria(main_window, "Edit", automation_id=AUTO_ID_SCREEN_SEARCH_INPUT)
        set_focus_and_type(search_input, SCREEN_NUM_ORDER)
        logging.info("'해외주식 주문' 창을 띄웠습니다.")

        # 해외주식 주문 창 접근
        order_window = find_control_by_criteria(main_window, "Window", title="[06100] 해외주식 주문") 
        
        # 계좌번호 드롭다운 클릭
        logging.info(f"{selected_user}님의 {account_index}번째 계좌번호 선택 중...")
        dropdown_account = find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_DROPDOWN_ACCOUNT, index=CTRL_INDEX_DROPDOWN_ACCOUNT)
        if dropdown_account:
            dropdown_account.click_input()
            
        # 계좌 선택
        send_keys(f"{{PGUP}}{{DOWN {account_index}}}{{ENTER}}")
        logging.info(f"{selected_user}님의 {account_index}번째 계좌번호를 선택하였습니다.")
        
        # 비밀번호 입력 안내창 처리
        dialog = wait_for_window("비밀번호 입력 안내창", main_window, "Meritz", "Window", timeout=3)
        if dialog:
            logging.info(f"확인 버튼 찾는 중...")
            ok_button = find_control_by_criteria(dialog, "Button", automation_id=AUTO_ID_PASSWORD_DIALOG_OK_BUTTON)
            if ok_button:
                ok_button.click_input()
                logging.info(f"확인 버튼을 클릭하였습니다.")
                set_focus_and_type(None, f"+{{TAB}}{password}{{ENTER}}")
                logging.info(f"비밀번호를 입력하였습니다.")

        # 미체결 탭 클릭
        logging.info(f"미체결 탭 버튼 찾는 중...")
        tab_unfilled = find_control_by_criteria(main_window, "TabItem", title=TAB_TITLE_UNFILLED)
        if tab_unfilled:
            tab_unfilled.click_input()
            logging.info(f"미체결 탭을 클릭하였습니다.")
        else:
            logging.warning("미체결 탭을 찾지 못했습니다.")
            raise Exception("미체결 탭을 찾을 수 없습니다.")

        # 잠시 대기 (미체결 데이터 로딩)
        time.sleep(2)

        # 전체 선택 체크박스 클릭
        logging.info("전체 선택 체크박스 찾는 중...")
        select_all_checkbox = find_control_by_criteria(order_window, "CheckBox", automation_id=AUTO_ID_SELECT_ALL_CHECKBOX)
        if select_all_checkbox:
            select_all_checkbox.click_input()
            logging.info("전체 선택 체크박스를 클릭하였습니다.")
        else:
            # automation_id로 못 찾으면 텍스트로 시도
            select_all_checkbox = find_control_by_criteria(order_window, "CheckBox", title="전체")
            if select_all_checkbox:
                select_all_checkbox.click_input()
                logging.info("전체 선택 체크박스를 클릭하였습니다. (title로 찾음)")
            else:
                logging.warning("전체 선택 체크박스를 찾지 못했습니다. 미체결 주문이 없을 수 있습니다.")

        # 일괄취소 버튼 클릭
        logging.info("일괄취소 버튼 찾는 중...")
        batch_cancel_button = find_control_by_criteria(order_window, "Button", automation_id=AUTO_ID_BATCH_CANCEL_BUTTON)
        if batch_cancel_button:
            batch_cancel_button.click_input()
            logging.info("일괄취소 버튼을 클릭하였습니다.")
        else:
            # automation_id로 못 찾으면 텍스트로 시도
            batch_cancel_button = find_control_by_criteria(order_window, "Button", title="일괄취소")
            if batch_cancel_button:
                batch_cancel_button.click_input()
                logging.info("일괄취소 버튼을 클릭하였습니다. (title로 찾음)")
            else:
                logging.warning("일괄취소 버튼을 찾지 못했습니다.")
                raise Exception("일괄취소 버튼을 찾을 수 없습니다.")

        # 취소 확인 팝업 처리
        time.sleep(1)
        
        if is_test_mode:
            # 테스트 모드: 닫기 버튼 클릭
            logging.info("테스트 모드이므로 취소 확인 팝업에서 '닫기' 버튼을 찾는 중...")
            close_button = find_control_by_criteria(main_window, "Button", automation_id=AUTO_ID_CLOSE_BUTTON)
            if close_button:
                close_button.click_input()
                logging.info("'테스트 모드'이므로 '닫기' 버튼을 클릭했습니다.")
            else:
                # 텍스트로 시도
                close_button = find_control_by_criteria(main_window, "Button", title="닫기")
                if close_button:
                    close_button.click_input()
                    logging.info("'테스트 모드'이므로 '닫기' 버튼을 클릭했습니다. (title로 찾음)")
        else:
            # 실제 모드: 취소 버튼 클릭
            logging.info("취소 확인 팝업에서 '취소' 버튼을 찾는 중...")
            cancel_confirm_button = find_control_by_criteria(main_window, "Button", automation_id=AUTO_ID_CANCEL_CONFIRM_BUTTON)
            if cancel_confirm_button:
                cancel_confirm_button.click_input()
                logging.info("'실제 모드'이므로 '취소' 버튼을 클릭했습니다.")
            else:
                # 텍스트로 시도
                cancel_confirm_button = find_control_by_criteria(main_window, "Button", title="취소")
                if cancel_confirm_button:
                    cancel_confirm_button.click_input()
                    logging.info("'실제 모드'이므로 '취소' 버튼을 클릭했습니다. (title로 찾음)")
                else:
                    logging.warning("취소 확인 버튼을 찾지 못했습니다.")
                    
        order_window.close()
        logging.info("'해외주식 주문' 창을 닫았습니다.")            
        logging.info(">>>>> 미체결 주문 일괄 취소 완료! <<<<<")
        
        return True, ""
    
    except Exception as e:
        logging.error(f"미체결 주문 일괄 취소 실패: {e}")
        return False, str(e)
    
    finally:
        # 마우스 및 키보드 잠금 해제 (예외 발생 여부와 관계없이 항상 실행)
        block_input(False)


if __name__ == "__main__":
    # 웹UI에서 저장한 자동 실행 대상에서 첫 번째 사용자/계좌 로드 (테스트용)
    from automation_target_store import load_automation_target_with_meta
    
    targets, _ = load_automation_target_with_meta(None, include_cycles=False)
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
    
    is_test_mode = True  # 테스트 모드 여부 설정
    hts_cancel_orders(selected_user, account_index, is_test_mode)

