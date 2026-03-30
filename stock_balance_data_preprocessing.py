import pandas as pd
from pathlib import Path
import os
import logging
from config import Config
from utils import save_csv


def stock_balance_data_preprocessing(selected_user, account_index):
    logging.info(">>>>> csv로 저장된 해외주식 보유잔고 raw data 읽어와 전처리 시작! <<<<<")

    # 파일 경로 설정
    file_path = f'./data/stock_balance_raw/stock_balance_raw_{selected_user}_{account_index}.csv'
    
    # 파일이 없거나, 파일은 있으나 비어있을 경우
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        logging.warning(f"csv 파일이 없거나 비어있습니다. 빈 processed CSV를 저장합니다. 경로: {file_path}")
        
        # 빈 결과 CSV 저장
        df_empty = pd.DataFrame(columns=['종목코드', '보유수량', '현재가', '평균가', '평가손익', '수익률(%)', '평가금액(외화)', '매입금액(외화)'])
        path = "./data/stock_balance_processed"
        file_name = f"stock_balance_processed_{selected_user}_{account_index}.csv"
        Path(path).mkdir(parents=True, exist_ok=True)
        df_empty.to_csv(os.path.join(path, file_name), index=False, encoding="utf-8-sig")

        logging.info(f"[빈 processed CSV 저장 완료] → {os.path.join(path, file_name)}")
        return

    # 파일이 존재하고 내용이 있을 경우만 계속 진행
    df = pd.read_csv(file_path, encoding='cp949', header=[0, 1])
    logging.info("파일을 성공적으로 불러왔습니다.")
    
    logging.info("원본 테이블에서 필요한 컬럼만 추출하여 새로운 데이터프레임 만드는 중...")
    # 첫 번째와 두 번째 행의 데이터를 개별 컬럼으로 분리
    new_columns = []
    for col in df.columns:
        if col[0] != 'Unnamed':
            new_columns.append(col[0])  # 첫 번째 행의 컬럼명 추가
        if col[1] != 'Unnamed':
            new_columns.append(col[1])  # 두 번째 행의 컬럼명 추가
    
    # 새로운 데이터프레임 생성
    df_new = pd.DataFrame(columns=new_columns)

    # df에서 데이터가 시작되는 3번째 행(헤더 제외 index=0)부터 시작해 2개씩 건너뛰면서 데이터들을 새로운 컬럼의 데이터프레임에 매칭
    for i in range(0, len(df), 2):  # i : df에서의 행번호
        for j in range(0, len(df.columns)):  # j : df에서의 열번호
            df_new_row_num = (i+1)//2  # df_new에서의 행번호
            df_new_col_num = j*2  # df_new에서의 열번호            
            # 만약 행이 존재하지 않으면 새로운 행 추가
            if df_new_row_num >= len(df_new):
                # 새로운 빈 행 추가
                df_new.loc[df_new_row_num] = [None] * len(df_new.columns)
            # 데이터 복사
            df_new.iloc[df_new_row_num, df_new_col_num] = df.iloc[i, j]
    
    # df에서 4번째 행(헤더 제외 index=1)부터 시작해 2개씩 건너뛰면서 데이터들을 새로운 컬럼의 데이터프레임에 매칭
    for i in range(1, len(df), 2):  # i : df에서의 행번호
        for j in range(0, len(df.columns)):  # j : df에서의 열번호
            df_new_row_num = i//2  # df_new에 데이터를 추가할 행번호
            df_new_col_num = j*2+1  # df_new에 데이터를 추가할 열번호            
            # 만약 행이 존재하지 않으면 새로운 행 추가
            if df_new_row_num >= len(df_new):
                # 새로운 빈 행 추가
                df_new.loc[df_new_row_num] = [None] * len(df_new.columns)
            # 데이터 복사
            df_new.iloc[df_new_row_num, df_new_col_num] = df.iloc[i, j]
            
    # [종목코드] 데이터 처리(. 뒤의 모든 문자 제거, ex. SOXL.AX -> SOXL)
    df_new['종목코드'] = df_new['종목코드'].str.replace(r'\.\w+$', '', regex=True).str.strip()
    # [보유수량] 데이터 처리(추후 보유수량 비교 시 4자리수이상일 경우 에러 방지를 위해 콤마(,) 제거)
    df_new['보유수량'] = df_new['보유수량'].astype(str).str.replace(',', '', regex=False)
        
    # 필요한 컬럼만 선택
    columns_to_keep = ['종목코드', '보유수량', '현재가', '평균가', '평가손익', '수익률(%)', '평가금액(외화)', '매입금액(외화)']
    df_filtered = df_new[[col for col in df_new.columns if col in columns_to_keep]]
    logging.info("새로운 데이터프레임 생성 완료!")
    
    logging.info("[전체 보유 내역]")
    logging.info(f"{df_filtered}")
    # 전체 보유 내역 csv 저장
    path = "./data/stock_balance_processed"
    file_name = f"stock_balance_processed_{selected_user}_{account_index}.csv"
    save_csv(df_filtered, path, file_name)

    logging.info(">>>>> csv로 저장된 해외주식 보유잔고 raw data 읽어와 전처리 완료! <<<<<")
    
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
        stock_balance_data_preprocessing(selected_user, account_index)