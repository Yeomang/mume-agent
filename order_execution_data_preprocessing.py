import pandas as pd
import os
import logging
import datetime as dt
import numpy as np
from config import Config
from utils import save_csv


def order_execution_data_preprocessing(selected_user, account_index, inquiry_start_date=None, inquiry_end_date=None):
    logging.info(">>>>> csv로 저장된 주문체결내역 raw data 읽어와 전처리 시작! <<<<<")
    # 조회기간 매개변수를 별도로 지정하지 않은 경우, default로 어제 날짜로 지정
    if inquiry_start_date is None:
        inquiry_start_date = (dt.date.today()-dt.timedelta(days=1)).strftime('%Y%m%d')  # 조회기간 시작일을 어제로 지정 (yyyymmdd)
    if inquiry_end_date is None:
        inquiry_end_date = (dt.date.today()-dt.timedelta(days=1)).strftime('%Y%m%d')  # 조회기간 종료일로 어제로 지정 (yyyymmdd)
        
    # CSV 파일 불러오기
    file_path = f'./data/all_order_execution_raw/all_order_execution_raw_{selected_user}_{account_index}_{inquiry_start_date}-{inquiry_end_date}.csv'
    if not os.path.exists(file_path):
        logging.info(f"csv 파일이 존재하지 않습니다. 불러오기 시도한 csv 파일 경로 : {file_path}")
        return  # 파일이 없으면 함수 실행을 즉시 종료
    
    # 파일이 존재하면 CSV 파일 읽기 (이중 헤더 처리)
    df = pd.read_csv(file_path, encoding='cp949', header=[0, 1])  # 첫 번째와 두 번째 줄을 헤더로 읽기
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
    df_new['종목코드'] = df_new['종목코드'].str.replace(r'\.\w+$', '', regex=True)
        
    # 필요한 컬럼만 선택
    columns_to_keep = ['주문일자', '주문No', '원No', '주문구분', '정정/취소', '종목코드', '주문수량', '주문단가', '체결수량', '체결단가', '주문조건', '주문시간', '체결시간', '주문상태']
    df_filtered = df_new[[col for col in df_new.columns if col in columns_to_keep]]
    logging.info("새로운 데이터프레임 생성 완료!")

    # 숫자처럼 보여도 문자열로 들어온 값들 정규화: 콤마/공백/통화기호/유니코드 마이너스/괄호음수 처리
    def _clean_numeric_series(s: pd.Series) -> pd.Series:
        # 모두 문자열화
        s = s.astype(str)

        # 괄호 음수 표시 (e.g., "(1,234.56)") → "-1234.56" 처리를 위해 플래그 컬럼 생성
        is_paren_neg = s.str.fullmatch(r"\(\s*.*\s*\)", na=False)
        s = s.str.replace(r"^\(\s*(.*)\s*\)$", r"\1", regex=True)

        # NBSP 제거, 앞뒤/중간 공백 정리
        s = s.str.replace("\u00A0", " ", regex=False).str.strip()

        # 통화기호/여분 공백 제거 (필요 시 여기에 다른 기호도 추가)
        s = s.str.replace(r"[\s\$₩€¥￦]", "", regex=True)

        # 유니코드 마이너스(−, U+2212) → ASCII 하이픈(-)
        s = s.str.replace("−", "-", regex=False)

        # 천 단위 콤마 제거
        s = s.str.replace(",", "", regex=False)

        # 빈값/대시류/None류는 NA로
        s = s.replace({"": np.nan, "-": np.nan, "—": np.nan, "None": np.nan,
                    "nan": np.nan, "NaN": np.nan, "#N/A": np.nan}, regex=False)

        # 숫자 변환
        out = pd.to_numeric(s, errors="coerce")

        # 괄호 음수 적용
        out[is_paren_neg & out.notna()] = -out[is_paren_neg & out.notna()]
        return out

    # 정규화가 필요한 숫자 컬럼 목록 (존재하는 컬럼만 적용)
    num_quantity_cols = [c for c in ['주문수량','체결수량','미체결수량'] if c in df_filtered.columns]
    num_price_cols    = [c for c in ['주문단가','체결단가','체결금액'] if c in df_filtered.columns]

    # 1) 수량 계열: 콤마 제거 → 숫자화 → 반올림(0) → Nullable Int
    for col in num_quantity_cols:
        ser = _clean_numeric_series(df_filtered[col])
        df_filtered.loc[:, col] = ser.round(0).astype('Int64')

    # 2) 단가/금액 계열: 콤마 제거 → 숫자화 → 소수 4자리 반올림(필요 시 조정)
    for col in num_price_cols:
        ser = _clean_numeric_series(df_filtered[col])
        df_filtered.loc[:, col] = ser.round(4)

    # (선택) 변환 결과 점검 로그
    for col in num_quantity_cols + num_price_cols:
        na_cnt = df_filtered[col].isna().sum()
        logging.info(f"[정규화 후 점검] {col} NA 개수 = {na_cnt}")



    # 정정 건 제거
    logging.info("정정 주문 건 찾는 중...")
    # '정정/취소' 값이 '정정'인 행 찾기
    modified_rows = df_filtered[df_filtered['정정/취소'] == '정정']
    # '정정'된 행의 '원No' 값 리스트 만들기 (중복 제거)
    modified_original_no = modified_rows['원No'].dropna().unique()
    # '정정'된 '원No' 값과 같은 '주문No'를 가진 행을 찾기
    modified_rows_to_remove = df_filtered['주문No'].isin(modified_original_no)
    # '정정'된 행과 '주문No'가 일치하는 행 제거
    df_filtered = df_filtered[~(modified_rows_to_remove)]
    logging.info("정정 주문 건 제거 완료!")

    # 취소 건 제거
    logging.info("취소 주문 건 찾는 중...")
    # '정정/취소' 값이 '취소'인 행 찾기
    cancel_rows = df_filtered[df_filtered['정정/취소'] == '취소']
    # '취소'된 행의 '원No' 값 리스트 만들기 (중복 제거)
    cancel_original_no = cancel_rows['원No'].dropna().unique()
    # '취소'된 '원No' 값과 같은 '주문No'를 가진 행을 찾기
    cancel_rows_to_remove = df_filtered['주문No'].isin(cancel_original_no)
    # '취소'된 행 및 '주문No'가 일치하는 행 제거
    df_filtered = df_filtered[~(cancel_rows_to_remove | df_filtered['정정/취소'].eq('취소'))]
    logging.info("취소 주문 건 제거 완료!")
    
    logging.info("[전체 주문 및 체결 내역]")
    logging.info(f"{df_filtered}")
    # 전체 주문 및 체결 내역 csv 저장
    path = "./data/all_order_execution_processed"
    file_name = f"all_order_execution_processed_{selected_user}_{account_index}_{inquiry_start_date}-{inquiry_end_date}.csv"
    save_csv(df_filtered, path, file_name)

    logging.info(">>>>> csv로 저장된 주문체결내역 raw data 읽어와 전처리 완료! <<<<<")
    
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
        order_execution_data_preprocessing(selected_user, account_index)

    # inquiry_start_date = "20250318"  # 조회기간 시작일 직접 설정 (yyyymmdd)
    # inquiry_end_date = "20250325"  # 조회기간 종료일 직접 설정 (yyyymmdd)    
    # order_execution_data_preprocessing(selected_user, account_index, inquiry_start_date, inquiry_end_date)