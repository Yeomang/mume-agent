import pandas as pd
import os
import logging
from config import Config
from utils import save_csv


def order_history_data_preprocessing(selected_user, account_index):
    logging.info(">>>>> csv로 저장된 주문내역 raw data 읽어와 전처리 시작! <<<<<")
        
    # CSV 파일 불러오기
    file_path = f'./data/order_history_raw/order_history_raw_{selected_user}_{account_index}.csv'
    if not os.path.exists(file_path):
        logging.info(f"csv 파일이 존재하지 않습니다. 불러오기 시도한 csv 파일 경로 : {file_path}")
        return  # 파일이 없으면 함수 실행을 즉시 종료
    
    # 파일이 존재하면 CSV 파일 읽기
    df = pd.read_csv(file_path, encoding='cp949')
    logging.info("파일을 성공적으로 불러왔습니다.")
    
    logging.info("원본 테이블에서 필요한 컬럼만 추출하여 새로운 데이터프레임 만드는 중...")
            
    # [종목코드] 데이터 처리(. 뒤의 모든 문자 제거, ex. SOXL.AX -> SOXL)
    df['종목코드'] = df['종목코드'].astype(str).str.replace(r'\.\w+$', '', regex=True)
        
    # 필요한 컬럼만 선택
    columns_to_keep = ['주문#', '매매구분', '종목코드', '주문가', '주문량', '원주문', '주문시간', '주문유형', '상태']
    df_filtered = df[[col for col in df.columns if col in columns_to_keep]]
    logging.info("새로운 데이터프레임 생성 완료!")

    # 정정 건 제거
    logging.info("정정 주문 건 찾는 중...")
    # '정정/취소' 값이 '정정'인 행 찾기
    modified_rows = df_filtered[df_filtered['매매구분'].str.contains('정정', na=False)]
    # '정정'된 행의 '원주문' 값 리스트 만들기 (중복 제거)
    modified_original_no = modified_rows['원주문'].dropna().unique()
    # '정정'된 '원주문' 값과 같은 '주문#'를 가진 행을 찾기
    modified_rows_to_remove = df_filtered['주문#'].isin(modified_original_no)
    # '정정'된 행과 '주문#'가 일치하는 행 제거
    df_filtered = df_filtered[~(modified_rows_to_remove)]
    logging.info("정정 주문 건 제거 완료!")

    # 취소 건 제거
    logging.info("취소 주문 건 찾는 중...")
    # '정정/취소' 값이 '취소'인 행 찾기
    cancel_rows = df_filtered[df_filtered['매매구분'].str.contains('취소', na=False)]
    # '취소'된 행의 '원주문' 값 리스트 만들기 (중복 제거)
    cancel_original_no = cancel_rows['원주문'].dropna().unique()
    # '취소'된 '원주문' 값과 같은 '주문#'를 가진 행을 찾기
    cancel_rows_to_remove = df_filtered['주문#'].isin(cancel_original_no)
    # '취소'된 행 및 '주문#'가 일치하는 행 제거
    df_filtered = df_filtered[~(cancel_rows_to_remove | df_filtered['매매구분'].str.contains('취소', na=False))]
    logging.info("취소 주문 건 제거 완료!")
    
    logging.info("[전체 주문 내역]")
    logging.info(f"{df_filtered}")
    # 전체 주문 및 체결 내역 csv 저장
    path = "./data/order_history_processed"
    file_name = f"order_history_processed_{selected_user}_{account_index}.csv"
    save_csv(df_filtered, path, file_name)

    logging.info(">>>>> csv로 저장된 주문내역 raw data 읽어와 전처리 완료! <<<<<")
    
    return
    
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
        order_history_data_preprocessing(selected_user, account_index)