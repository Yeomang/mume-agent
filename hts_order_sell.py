from utils import setup_window, find_control_by_criteria, set_focus_and_type, wait_for_window, block_input, _handle_password_dialog
from secrets_manager import get_account_password
from config import Config
from pywinauto import Application
import win32gui
from pywinauto.keyboard import send_keys
import logging

# 상수 정의
SCREEN_NUM_ORDER = "6100"  # 해외주식 주문 화면번호
AUTO_ID_SCREEN_SEARCH_INPUT = "1000"  # 화면검색 번호 입력 필드 automation_id
AUTO_ID_DROPDOWN_ACCOUNT = "3780"  # 계좌번호 드롭다운 필드 automation_id
CTRL_INDEX_DROPDOWN_ACCOUNT = 2  # order_window 하위 컨트롤 검색결과 계좌번호 드롭다운 필드 순번
AUTO_ID_PASSWORD_DIALOG_OK_BUTTON = "2"  # 비밀번호 입력 안내창 확인 버튼 automation_id
AUTO_ID_TICKER_SELL_INPUT = "3860"  # 매도 종목 입력 필드 automation_id
AUTO_ID_DROPDOWN_TYPE_SELL = "4030"  # 매도 유형 드롭다운 필드 automation_id
CTRL_INDEX_DROPDOWN_TYPE_SELL = 0  # order_window 하위 컨트롤 검색결과 매도 유형 드롭다운 필드 순번
AUTO_ID_QUANTITY_SELL_INPUT = "4035"  # 매도 수량 입력 필드 automation_id
AUTO_ID_PRICE_SELL_INPUT = "4390"  # 매도 가격 입력 필드 automation_id
HOTKEY_SELL = "{F2}"  # 매도 실행 버튼
AUTO_ID_CLOSE_BUTTON = "3795"  # 매도주문확인팝업에서 '닫기' 버튼 automation_id
AUTO_ID_SELL_BUTTON = "3880"  # 매도주문확인팝업에서 '매도' 버튼 automation_id

# main_window 하위의 모든 자식 및 자손 GUI 요소의 정보를 출력
# main_window.print_control_identifiers()


def hts_order_sell(selected_user, account_index, ticker, sell_orders, is_test_mode):
    order_window = None
    try:
        logging.info(">>>>> 매도 주문 시작! <<<<<")
        logging.info(f">>>>> {selected_user} | {account_index} | {ticker} <<<<<")
        logging.info(f">>>>> {sell_orders} <<<<<")
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

        # 계좌번호 드롭다운 클릭 (index=2 설명 : Pane이면서 id 3780인 item 중에 2번째가 계좌번호 드롭다운 컨트롤)
        logging.info(f"{selected_user}님의 {account_index}번째 계좌번호 선택 중...")
        dropdown_account = find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_DROPDOWN_ACCOUNT, index=CTRL_INDEX_DROPDOWN_ACCOUNT)
        if dropdown_account:
            dropdown_account.click_input()
            
        # 계좌 선택
        send_keys(f"{{PGUP}}{{DOWN {account_index}}}{{ENTER}}")
        logging.info(f"{selected_user}님의 {account_index}번째 계좌번호를 선택하였습니다.")
        
        # 비밀번호 입력 안내창 처리
        _handle_password_dialog(main_window, password)    

        # 매도 탭 클릭
        logging.info(f"매도 탭 버튼 찾는 중...")
        tab_sell = find_control_by_criteria(main_window, "TabItem", title="매도")
        tab_sell.click_input()
        logging.info(f"매도 탭을 클릭하였습니다.")

        # 매도 종목 입력
        logging.info(f"종목 입력 필드 찾는중...")
        ticker_sell_input = find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_TICKER_SELL_INPUT)
        set_focus_and_type(ticker_sell_input, f"{ticker}{{ENTER}}")
        logging.info(f"종목 입력 필드에 '{ticker}'를 입력하였습니다.")

        # 반복문을 사용해 `sell_orders` 리스트 내의 모든 주문을 실행
        for order in sell_orders:
            quantity = order["quantity"]
            price = order["price"]
            order_type_index = order["order_type_index"]
            
            if not quantity or not price:
                logging.warning(f"유효하지 않은 주문 데이터: {order}")
                continue  # 값이 없으면 건너뜀
            
            logging.info(f"매도 주문 실행: ${price} | {quantity}주 | 유형: {order_type_index}")

            # 매도 유형 선택
            dropdown_type_sell = find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_DROPDOWN_TYPE_SELL, index=CTRL_INDEX_DROPDOWN_TYPE_SELL)
            dropdown_type_sell.click_input()
            send_keys(f"{{PGUP}}{{DOWN {order_type_index}}}{{ENTER}}")
            logging.info(f"유형 중 {order_type_index}번째 항목을 선택했습니다.")

            # 매도 수량 입력
            quantity_sell_input = find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_QUANTITY_SELL_INPUT)
            set_focus_and_type(quantity_sell_input, f"{quantity}{{ENTER}}")

            # 매도 가격 입력
            price_sell_input = find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_PRICE_SELL_INPUT)
            set_focus_and_type(price_sell_input, f"{price}{{ENTER}}")

            # 매도 실행
            send_keys(HOTKEY_SELL)
            logging.info(f"매도 실행 버튼을 클릭하였습니다.")

            # 테스트 모드 및 실제 모드에 따른 버튼 클릭
            if is_test_mode:
                close_button = find_control_by_criteria(main_window, "Button", automation_id=AUTO_ID_CLOSE_BUTTON)
                if close_button:
                    close_button.click_input()
                    logging.info("'테스트 모드'이므로 '닫기' 버튼을 클릭했습니다.")
            else:
                sell_button = find_control_by_criteria(main_window, "Button", automation_id=AUTO_ID_SELL_BUTTON)
                if sell_button:
                    sell_button.click_input()
                    logging.info("'실제 모드'이므로 '매도' 버튼을 클릭했습니다.")
                    
        order_window.close()
        logging.info("'해외주식 주문' 창을 닫았습니다.")
        logging.info(">>>>> 매도 주문 완료! <<<<<")
        # 주문 성공 시 True 반환
        return True, ""
    
    except Exception as e:
        logging.error(f"매도 주문 실패: {e}")
        return False, e
    
    finally:
        # 마우스 및 키보드 잠금 해제 (예외 발생 여부와 관계없이 항상 실행)
        block_input(False)

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
    
    ticker = "TQQQ"
    sell_orders = [
        {"quantity": 1, "price": 65.49, "order_type_index": 0},  # 0: 보통(지정가)
        {"quantity": 2, "price": 77.22, "order_type_index": 3}   # 3: LOC(장마감지정가)
    ]
    is_test_mode = True  # 테스트 모드 여부 설정
    hts_order_sell(selected_user, account_index, ticker, sell_orders, is_test_mode)