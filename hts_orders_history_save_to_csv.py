from utils import setup_window, get_window_handle, find_control_by_criteria, set_focus_and_type, wait_for_window, block_input, copy_to_clipboard, _handle_password_dialog
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
AUTO_ID_BOTTOM_TAB = "3785"  # 하단 탭 컨트롤 (미체결/주문체결/주문가능)
AUTO_ID_UNFILLED_GRID = "3780"  # 미체결 그리드 Pane


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
    order_window = find_control_by_criteria(main_window, "Window", title="[06100] 해외주식 주문", delay=2, retries=5)
    if not order_window:
        raise Exception("[06100] 해외주식 주문 창을 찾을 수 없습니다.")

    # 계좌 선택
    dropdown = find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_DROPDOWN_ACCOUNT, index=CTRL_INDEX_DROPDOWN_ACCOUNT)
    if not dropdown:
        raise Exception("계좌 드롭다운을 찾을 수 없습니다.")
    dropdown.click_input()
    send_keys(f"{{PGUP}}{{DOWN {account_index}}}{{ENTER}}")
    logging.info(f"{selected_user}님의 {account_index}번째 계좌번호를 선택하였습니다.")

    # 비밀번호 입력 안내창 처리
    _handle_password_dialog(main_window, password)

    # 주문체결 탭 클릭
    logging.info(f"주문체결 탭 버튼 찾는 중...")
    tab_sell = find_control_by_criteria(main_window, "TabItem", title="주문체결")
    if not tab_sell:
        raise Exception("주문체결 탭을 찾을 수 없습니다.")
    tab_sell.click_input()
    logging.info(f"주문체결 탭을 클릭하였습니다.")
    time.sleep(2)  # 탭 전환 후 데이터 로딩 대기

    # 데이터 테이블 위치 찾기
    table_pane = find_control_by_criteria(main_window, "Pane", automation_id=AUTO_ID_TABLE_ORDER, index=CTRL_INDEX_TABLE_ORDER, delay=2, retries=5)
    if not table_pane:
        raise Exception("주문체결 데이터 테이블을 찾을 수 없습니다.")
    rect = table_pane.rectangle()

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


def get_unfilled_tickers_dict(selected_user, account_index) -> dict:
    """
    미체결 탭에서 현재 미체결 주문을 조회하여 종목별 매도/매수 여부를 반환.
    반환: {"TQQQ": {"sell": True, "buy": True}, ...}
    빈 dict({})는 "이번에 새로 확인했더니 미체결이 없었다"만 의미한다.
    조회 자체가 실패하면(창/컨트롤을 못 찾음, CSV가 새로 안 써짐 등) 예외를 던진다 —
    호출부가 "확인된 없음"과 "확인 자체를 못 함"을 구분해서 후자는 안전하게 스킵하도록 하기 위함.
    (과거엔 실패도 빈 dict로 뭉뚱그려 반환했는데, CSV 저장이 매번 조용히 실패하면서
    수일 전 파일을 계속 재사용해 "미체결 있음"을 오래도록 잘못 보고한 사고가 있었음.)
    """
    logging.info(">>>>> 미체결 주문 조회 시작 <<<<<")
    order_window = None
    block_input(True)
    try:
        password = get_account_password(selected_user)
        hwnd = get_window_handle("iMeritz")
        setup_window(hwnd)
        app = Application(backend="uia").connect(handle=hwnd)
        main_window = app.window(handle=hwnd)

        search_input = find_control_by_criteria(main_window, "Edit", automation_id=AUTO_ID_SCREEN_SEARCH_INPUT)
        set_focus_and_type(search_input, SCREEN_NUM_ORDER)

        order_window = find_control_by_criteria(main_window, "Window", title="[06100] 해외주식 주문", delay=2, retries=5)
        if not order_window:
            raise Exception("[06100] 해외주식 주문 창을 찾을 수 없습니다.")

        dropdown = find_control_by_criteria(order_window, "Pane", automation_id=AUTO_ID_DROPDOWN_ACCOUNT, index=CTRL_INDEX_DROPDOWN_ACCOUNT)
        if not dropdown:
            raise Exception("계좌 드롭다운을 찾을 수 없습니다.")
        dropdown.click_input()
        send_keys(f"{{PGUP}}{{DOWN {account_index}}}{{ENTER}}")

        _handle_password_dialog(main_window, password)

        tab_unfilled = find_control_by_criteria(main_window, "TabItem", title="미체결")
        if not tab_unfilled:
            raise Exception("미체결 탭을 찾을 수 없습니다.")
        tab_unfilled.click_input()
        time.sleep(2)

        bottom_tab = find_control_by_criteria(order_window, "Tab", automation_id=AUTO_ID_BOTTOM_TAB)
        if not bottom_tab:
            raise Exception("하단 탭 컨트롤을 찾을 수 없습니다.")

        # 저장 경로를 그리드 존재 확인보다 먼저 계산해, 그리드가 없는 케이스도
        # 아래에서 "이전 파일 삭제"와 동일한 방식으로 안전하게 처리한다.
        save_dir = Path("./data/unfilled_orders_raw")
        save_dir.mkdir(parents=True, exist_ok=True)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        save_path = Path(current_dir) / "data" / "unfilled_orders_raw" / f"unfilled_orders_{selected_user}_{account_index}.csv"

        # 이전 실행의 CSV를 먼저 지운다 — 이번 저장이 조용히 실패해도
        # "며칠 전 파일을 새 조회 결과인 것처럼" 재사용하는 사고를 원천 차단.
        save_path.unlink(missing_ok=True)

        grid = find_control_by_criteria(bottom_tab, "Pane", automation_id=AUTO_ID_UNFILLED_GRID, silent=True)
        if not grid:
            # 그리드 "틀"조차 못 찾은 건 화면이 예상과 다르다는 뜻 — 정말 미체결이
            # 없는 것과는 다른 상황이라 실패로 처리한다 (호출부가 스킵하도록).
            order_window.close()
            raise Exception("미체결 그리드 컨트롤을 찾을 수 없습니다 (화면 구조 이상 의심).")

        grid_rect = grid.rectangle()
        x = int(grid_rect.left + (grid_rect.right - grid_rect.left) / 2)
        y = int(grid_rect.top + 25)
        click(button="right", coords=(x, y))
        time.sleep(1)
        send_keys("{DOWN 6}{ENTER}")
        time.sleep(0.5)
        send_keys("c")
        time.sleep(1)

        try:
            wait_for_window("다른 이름으로 저장", main_window, "다른 이름으로 저장", "Window", timeout=5)
        except Exception:
            # 방금 파일을 지웠으므로, 저장창이 안 뜬 게 "미체결 없음"인지
            # "그리드가 비어 우클릭 메뉴 구성이 달라져 저장 자체가 안 열린 것"인지
            # 구분할 수 없다 — 안전하게 실패로 처리한다.
            send_keys("{ESCAPE}")
            order_window.close()
            raise Exception("'다른 이름으로 저장' 창이 뜨지 않음 — CSV 갱신 여부 확인 불가.")

        copy_to_clipboard(str(save_path))
        send_keys("%n")
        send_keys("^v{ENTER}")
        time.sleep(1)

        order_window.close()

        if not save_path.exists():
            # 저장창은 떴지만 실제로 파일이 새로 생기지 않음 (예: 덮어쓰기 확인
            # 등 추가 팝업을 처리 못해 저장이 완결되지 않음) — 실패로 처리.
            raise Exception("미체결 CSV가 새로 생성되지 않음 — 저장이 실제로 완료되지 않은 것으로 추정.")

        logging.info(f"[중복방지] 미체결 CSV 저장: {save_path}")

        result = {}
        df = None
        for enc in ['utf-8-sig', 'cp949', 'cp1252', 'utf-16']:
            try:
                import pandas as pd
                df = pd.read_csv(save_path, encoding=enc)
                break
            except Exception:
                continue
        if df is None:
            raise Exception("[중복방지] 방금 새로 저장된 미체결 CSV 파싱 실패 (인코딩 불일치).")
        if df.empty:
            # 방금 새로 저장된(확인된) 파일이 비어있는 것 — 진짜로 미체결이 없다고 봐도 안전.
            logging.info("[중복방지] 미체결 CSV 비어있음 — 미체결 주문 없음으로 확인.")
            return {}

        if '종목코드' not in df.columns or '매매구분' not in df.columns:
            raise Exception(f"[중복방지] 미체결 CSV 컬럼 이상 (엉뚱한 화면이 저장됐을 가능성): {list(df.columns)}")

        df['종목코드'] = df['종목코드'].astype(str).str.replace(r'\.\w+$', '', regex=True).str.strip()
        for _, row in df.iterrows():
            code = str(row.get('종목코드', '')).strip()
            gubun = str(row.get('매매구분', '')).strip()
            if not code or code == 'nan':
                continue
            if code not in result:
                result[code] = {"sell": False, "buy": False}
            if '매도' in gubun:
                result[code]["sell"] = True
            elif '매수' in gubun:
                result[code]["buy"] = True

        logging.info(f"[중복방지] 미체결 주문 조회 완료: {result}")
        return result

    except Exception as e:
        logging.error(f"[중복방지] 미체결 주문 조회 실패 — 호출부에서 안전하게 스킵되어야 함: {e}")
        raise
    finally:
        block_input(False)
        logging.info(">>>>> 미체결 주문 조회 완료 <<<<<")


if __name__ == "__main__":
    # ------------------------------------------------------------
    # 로컬 테스트용 실행 블록
    # - TEST_USER / TEST_ACCOUNT 를 직접 지정 가능. None 이면 Supabase 자동 로드.
    # ------------------------------------------------------------
    TEST_USER: str | None = None
    TEST_ACCOUNT: int | None = None

    from automation_target_store import resolve_first_user_account

    selected_user, account_index = resolve_first_user_account(TEST_USER, TEST_ACCOUNT)
    save_orders_history(selected_user, account_index)

