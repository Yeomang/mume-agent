from utils import setup_window, get_window_handle, find_control_by_criteria, set_focus_and_type, block_input, with_desktop_retry
from hts_stock_balance_save_to_csv import _export_table_to_csv
from pywinauto import Application
import time
from pywinauto.keyboard import send_keys
import logging
from pathlib import Path
import os

# 상수 정의
AUTO_ID_SCREEN_SEARCH_INPUT = "1000"  # 화면검색 번호 입력 필드 automation_id
SCREEN_NUM_DAILY_RETURN = "2363"  # 일별 계좌수익률 화면번호
AUTO_ID_DROPDOWN_ACCOUNT = "3785"  # 계좌번호 드롭다운 필드 automation_id (6104와 동일)
AUTO_ID_INQUIRY_START_DATE = "3835"  # 조회기간 시작일 입력 필드 automation_id (MaskEdit)
AUTO_ID_INQUIRY_END_DATE = "3840"  # 조회기간 종료일 입력 필드 automation_id (MaskEdit)
AUTO_ID_INQUIRY_BUTTON = "3810"  # 조회 버튼 automation_id (6104의 3815와 다름! 3815는 '다음' 버튼)
AUTO_ID_NEXT_BUTTON = "3815"  # '다음' 버튼 automation_id — 조회 결과가 화면 최대치를 넘으면 눌러야 추가 데이터가 이어붙여짐
AUTO_ID_TABLE_DAILY_RETURN = "3825"  # 일별 계좌수익률 데이터 테이블 automation_id
MAX_NEXT_CLICKS = 60  # '다음' 버튼 무한루프 방지용 안전 상한. 최근 2년 이내만 조회 가능하므로 충분한 값.


@with_desktop_retry
def save_data_daily_return(selected_user, account_index, inquiry_start_date=None, inquiry_end_date=None):
    logging.info(">>>>> HTS 일별 계좌수익률 데이터 csv파일로 저장하기 시작! <<<<<")

    # 마우스 및 키보드 잠금 시작
    block_input(True)

    current_dir = os.path.dirname(os.path.abspath(__file__))
    save_dir = Path("./data/daily_return_raw")
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = Path(current_dir) / "data" / "daily_return_raw" / f"daily_return_raw_{selected_user}_{account_index}.csv"

    if save_path.exists():
        os.remove(save_path)

    hwnd = get_window_handle("iMeritz")
    setup_window(hwnd)

    app = Application(backend="uia").connect(handle=hwnd)
    main_window = app.window(handle=hwnd)

    # 화면 검색 입력
    logging.info(f"화면번호 [{SCREEN_NUM_DAILY_RETURN}]를 입력하여 '일별 계좌수익률' 창을 띄우는 중...")
    search_input = find_control_by_criteria(main_window, "Edit", automation_id=AUTO_ID_SCREEN_SEARCH_INPUT)
    set_focus_and_type(search_input, SCREEN_NUM_DAILY_RETURN)
    logging.info("'일별 계좌수익률' 창을 띄웠습니다.")

    report_window = find_control_by_criteria(main_window, "Window", title="[02363] 일별 계좌수익률", delay=2, retries=5)
    if not report_window:
        raise Exception("[02363] 일별 계좌수익률 창을 찾을 수 없습니다.")

    # 이 화면은 탭 5개를 가진 다목적 리포트 창이라, 다른 탭이 남아있을 수 있으므로
    # '일별 계좌수익률' 탭을 명시적으로 클릭
    daily_tab = find_control_by_criteria(report_window, "TabItem", title="일별 계좌수익률", delay=0.5, retries=5)
    if not daily_tab:
        raise Exception("'일별 계좌수익률' 탭을 찾을 수 없습니다.")
    daily_tab.click_input()
    logging.info("'일별 계좌수익률' 탭을 클릭하였습니다.")
    time.sleep(1)

    # 계좌 선택
    dropdown = find_control_by_criteria(report_window, "Pane", automation_id=AUTO_ID_DROPDOWN_ACCOUNT)
    if not dropdown:
        raise Exception("계좌 드롭다운을 찾을 수 없습니다.")
    dropdown.click_input()
    send_keys(f"{{PGUP}}{{DOWN {account_index}}}{{ENTER}}")
    logging.info(f"{selected_user}님의 {account_index}번째 계좌번호를 선택하였습니다.")
    time.sleep(3)

    # 조회기간 입력 — 지정하지 않으면 화면 기본값(최근 1개월)을 그대로 사용.
    # 매일 실행하는 정상 케이스는 겹치는 날짜를 콘솔에서 upsert로 덮어쓰므로 날짜 범위를 조작할 필요 없음.
    # 과거 데이터를 직접 보충하고 싶을 때(콘솔 수동 실행)만 inquiry_start_date/inquiry_end_date를 지정.
    if inquiry_start_date and inquiry_end_date:
        start_date_input = find_control_by_criteria(report_window, "Pane", automation_id=AUTO_ID_INQUIRY_START_DATE)
        if not start_date_input:
            raise Exception("조회기간 시작일 입력 필드를 찾을 수 없습니다.")
        set_focus_and_type(start_date_input, inquiry_start_date)
        end_date_input = find_control_by_criteria(report_window, "Pane", automation_id=AUTO_ID_INQUIRY_END_DATE)
        if not end_date_input:
            raise Exception("조회기간 종료일 입력 필드를 찾을 수 없습니다.")
        set_focus_and_type(end_date_input, inquiry_end_date)
        logging.info(f"조회기간 입력 : {inquiry_start_date}-{inquiry_end_date}")

    # '조회' 버튼 클릭
    inquiry_btn = find_control_by_criteria(main_window, "Button", automation_id=AUTO_ID_INQUIRY_BUTTON)
    if not inquiry_btn:
        raise Exception("조회 버튼을 찾을 수 없습니다.")
    inquiry_btn.click_input()
    logging.info("'조회' 버튼을 클릭하였습니다.")
    time.sleep(4)  # 조회 결과 로딩 대기

    # 조회 결과가 한 번에 다 안 나오면(오랫동안 미실행 후 첫 실행 등) '다음' 버튼이 활성화됨.
    # 비활성화될 때까지 계속 눌러서 더 오래된 데이터를 이어붙인다 (최대 2년치까지 조회 가능).
    # 매일 실행하는 정상 케이스는 데이터가 적어 '다음'이 처음부터 비활성화 상태라 곧바로 빠져나온다.
    for i in range(MAX_NEXT_CLICKS):
        next_btn = find_control_by_criteria(main_window, "Button", automation_id=AUTO_ID_NEXT_BUTTON, silent=True)
        if not next_btn or not next_btn.is_enabled():
            logging.info(f"'다음' 버튼이 더 이상 없거나 비활성화됨 (누적 클릭 {i}회). 데이터 로딩 완료.")
            break
        next_btn.click_input()
        logging.info(f"'다음' 버튼 클릭 ({i + 1}번째) — 추가 데이터 로딩 중...")
        time.sleep(2)
    else:
        logging.warning(f"'다음' 버튼을 {MAX_NEXT_CLICKS}회 눌렀는데도 비활성화되지 않음. 데이터가 잘렸을 수 있습니다.")

    # 데이터 테이블 위치 찾기
    table_pane = find_control_by_criteria(main_window, "Pane", automation_id=AUTO_ID_TABLE_DAILY_RETURN, delay=2, retries=5)
    if not table_pane:
        raise Exception("일별 계좌수익률 데이터 테이블을 찾을 수 없습니다.")

    # 일별 계좌수익률 테이블 CSV 저장
    # export_menu_index=6: 우클릭 메뉴에서 '파일로 보내기'가 6번째
    # (연결화면 편집 → 화면을 툴바에 등록 → 타이틀바 보기 → 화면인쇄 → 화면초기화 → 파일로 보내기)
    _export_table_to_csv(main_window, table_pane, save_path, label="일별 계좌수익률", export_menu_index=6)

    logging.info(">>>>> HTS 일별 계좌수익률 데이터 csv파일로 저장하기 완료! <<<<<")

    # 마우스 및 키보드 잠금 해제
    block_input(False)

    report_window.close()
    logging.info("'일별 계좌수익률' 창을 닫았습니다.")


if __name__ == "__main__":
    # ------------------------------------------------------------
    # 로컬 테스트용 실행 블록
    # - 조회 기간을 직접 지정하려면 INQUIRY_START_DATE / INQUIRY_END_DATE 에
    #   yyyymmdd 문자열 입력 (예: "20250318"). None이면 화면 기본값(최근 1개월).
    # ------------------------------------------------------------
    TEST_USER: str | None = "홍승표"
    TEST_ACCOUNT: int | None = 3
    INQUIRY_START_DATE: str | None = None
    INQUIRY_END_DATE: str | None = None

    from automation_target_store import resolve_first_user_account

    selected_user, account_index = resolve_first_user_account(TEST_USER, TEST_ACCOUNT)
    save_data_daily_return(selected_user, account_index, INQUIRY_START_DATE, INQUIRY_END_DATE)
