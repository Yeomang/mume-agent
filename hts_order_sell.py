from utils import setup_window, find_control_by_criteria, set_focus_and_type, wait_for_window, block_input, _handle_password_dialog, send_telegram_message, with_desktop_retry
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


@with_desktop_retry
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
        order_window = find_control_by_criteria(main_window, "Window", title="[06100] 해외주식 주문", delay=2, retries=5)
        if not order_window:
            raise Exception("[06100] 해외주식 주문 창을 찾을 수 없습니다.")

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
        failed_orders = []
        for order in sell_orders:
            quantity = int(order["quantity"])  # float→int 방어 (1.0→1)
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

            # 매도 확인 버튼 클릭 후 안내 모달 체크
            alert_modal = wait_for_window("안내", main_window, "안내", "Window", timeout=1)
            if alert_modal:
                alert_text = ""
                try:
                    for ctrl in alert_modal.descendants():
                        if ctrl.element_info.control_type == "Text":
                            t = ctrl.element_info.name or ""
                            if t and t != "안내":
                                alert_text = t
                                break
                except Exception:
                    pass
                logging.warning(f"주문 실패 ({alert_text}): ${price} x {quantity}주 — 다음 주문으로 계속 진행")
                failed_orders.append({"quantity": quantity, "price": price, "reason": alert_text})
                ok_btn = find_control_by_criteria(alert_modal, "Button", automation_id="2", delay=0, silent=True)
                if not ok_btn:
                    ok_btn = find_control_by_criteria(alert_modal, "Button", title="확인", delay=0, silent=True)
                if ok_btn:
                    ok_btn.click_input()
                    logging.info("안내 모달의 확인 버튼을 클릭하였습니다.")
                continue

        # 실패한 주문이 있으면 텔레그램 알림
        if failed_orders:
            try:
                fail_lines = [f"  ${f['price']} x {f['quantity']}주 ({f['reason']})" for f in failed_orders]
                tg_msg = f"⚠️ [{selected_user} | {account_index}번 계좌] {ticker} 일부 매도 주문 실패\n" + "\n".join(fail_lines)
                send_telegram_message(Config.TELEGRAM_BOT_TOKEN_ORDER, Config.TELEGRAM_CHAT_ID, tg_msg)
            except Exception:
                pass

        order_window.close()
        logging.info("'해외주식 주문' 창을 닫았습니다.")
        logging.info(">>>>> 매도 주문 완료! <<<<<")

        if failed_orders:
            fail_detail = "; ".join(
                f"${f['price']} x {f['quantity']}주 ({f['reason']})"
                for f in failed_orders
            )
            if len(failed_orders) >= len(sell_orders):
                return False, fail_detail
            return True, fail_detail
        return True, ""
    
    except Exception as e:
        logging.error(f"매도 주문 실패: {e}")
        return False, e
    
    finally:
        # 마우스 및 키보드 잠금 해제 (예외 발생 여부와 관계없이 항상 실행)
        block_input(False)

if __name__ == "__main__":
    # ------------------------------------------------------------
    # 로컬 테스트용 실행 블록
    # - TEST_USER / TEST_ACCOUNT 를 직접 지정 가능. None 이면 Supabase 자동 로드.
    # - IS_TEST_MODE=True 면 주문 확인 팝업까지만 진행하고 실제 체결하지 않음.
    # - 주문 파라미터(ticker, sell_orders) 는 아래에서 직접 수정.
    #   order_type_index → 0: 보통(지정가), 3: LOC(장마감지정가)
    # ------------------------------------------------------------
    TEST_USER: str | None = None
    TEST_ACCOUNT: int | None = None
    IS_TEST_MODE = True

    TICKER = "TQQQ"
    SELL_ORDERS = [
        {"quantity": 1, "price": 65.49, "order_type_index": 0},
        {"quantity": 2, "price": 77.22, "order_type_index": 3},
    ]

    from automation_target_store import resolve_first_user_account

    selected_user, account_index = resolve_first_user_account(TEST_USER, TEST_ACCOUNT)
    hts_order_sell(selected_user, account_index, TICKER, SELL_ORDERS, IS_TEST_MODE)