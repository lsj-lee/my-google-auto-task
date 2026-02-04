import gspread
import time
import random
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials
import os

# 구글 시트 설정
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

SERVICE_ACCOUNT_FILE = 'service_account.json'

class SheetManager:
    def __init__(self):
        print("구글 시트 연결 중...")

        # Retry logic for connection
        max_retries = 5
        for i in range(max_retries):
            try:
                self.creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
                self.gc = gspread.authorize(self.creds)
                self.sh = self.gc.open("통합DB")
                self.worksheet = self.sh.get_worksheet(0)
                
                # 변경 내역 기록을 위한 시트
                try:
                    self.history_sheet = self.sh.worksheet("변경내역")
                except:
                    self.history_sheet = self.sh.add_worksheet(title="변경내역", rows=1000, cols=10)
                    self.history_sheet.append_row(["날짜", "유형", "제품명", "세부내용", "기존값", "변경값"])

                break # Success
            except Exception as e:
                if i == max_retries - 1:
                    print(f"시트 연결 오류 (최종 실패): {e}")
                    raise e

                wait_time = (2 ** i) + random.uniform(0, 1)
                print(f"시트 연결 실패 ({e}). {wait_time:.1f}초 후 재시도 ({i+1}/{max_retries})...")
                time.sleep(wait_time)

        # 1. 기존 데이터 백업 및 AI 데이터(태그/설명) 보존
        print("기존 데이터 분석 중... (데이터 양에 따라 1~2분 이상 소요될 수 있습니다. 잠시만 기다려주세요...)")
        self.old_data = {}
        self.ai_data = {}

        try:
            # D6:N 범위 읽기 최적화
            # get_all_values() 대신 필요한 범위만 가져와서 메모리와 네트워크 대역폭을 절약합니다.
            # D열(index 0) ~ N열(index 10)까지가 반환됩니다.
            all_rows = self.worksheet.get('D6:N')
            
            for row in all_rows:
                # D열(index 0) ~ N열(index 10)까지가 유효 데이터 범위
                # row 길이가 충분한지 확인. F열(Name)은 반환된 row의 index 2에 위치합니다.
                if len(row) > 2 and row[2]: # F열(index 2, Name)이 존재해야 함
                    # D6:N 기준 인덱스: D=0, E=1, F=2, ... K=7, L=8 ...
                    name = row[2] # F열

                    tags = row[1] if len(row) > 1 else ""  # E열
                    desc = row[7] if len(row) > 7 else "" # K열

                    self.ai_data[name] = {
                        "tags": tags,
                        "desc": desc
                    }

                    price = row[8] if len(row) > 8 else "0" # L열
                    self.old_data[name] = {
                        "price": price
                    }
        except Exception as e:
            print(f"  (기존 데이터 읽기 실패: {e})")

        # 2. Start Row: D6
        self.current_row = 6

        # 3. Initialization: Clear data (D~N열) -> Skip for safety (Incremental Overwrite)
        # print("시트 초기화 중 (D6:N5000)...")
        # try:
        #     self.worksheet.batch_clear(["D6:N5000"])
        # except:
        #     print("  (초기화 건너뜀)")

        # 4. New Data Accumulator
        self.new_data_check = {}

    def append_data(self, data_dict):
        """
        data_dict: { 'id': {info}, ... }
        """
        if not data_dict:
            return

        rows = []
        # Sort by name
        sorted_items = sorted(data_dict.values(), key=lambda x: x['name'])
        
        for item in sorted_items:
            name = item.get('name', '')
            if name:
                self.new_data_check[name] = item

            # Restore AI Data if exists
            tags = self.ai_data.get(name, {}).get('tags', '')
            desc = self.ai_data.get(name, {}).get('desc', '')

            # Price/PV/BV cleanup
            price_raw = str(item.get('price','0')).replace('원', '').replace(',', '').strip()
            pv_raw = str(item.get('pv','0')).replace('PV','').replace(':','').replace(',', '').strip()
            bv_raw = str(item.get('bv','0')).replace('BV','').replace(':','').replace(',', '').strip()
            
            # Formula row index adjustment
            this_row_num = self.current_row + len(rows)
            
            # New Mapping:
            # D: 분류 (category) - Korean
            # E: 태그 (tags) - Restored from AI
            # F: 제품명 (name)
            # G: 사진URL (image)
            # H: 사진보기 (=IMAGE)
            # I: 상품링크 (link)
            # J: (Empty)
            # K: 설명 (description) - Restored from AI
            # L: 가격 (price)
            # M: PV (pv)
            # N: BV (bv)
            
            row_data = [
                item.get('category',''),       # D
                tags,                          # E (Preserved)
                name,                          # F
                item.get('image',''),          # G
                f'=IMAGE(G{this_row_num})',    # H
                item.get('link',''),           # I
                "",                            # J
                desc,                          # K (Preserved)
                price_raw,                     # L
                pv_raw,                        # M
                bv_raw                         # N
            ]
            rows.append(row_data)

        if not rows:
            return

        # Batch update (D~N)
        end_row = self.current_row + len(rows) - 1
        range_str = f"D{self.current_row}:N{end_row}"
        
        # Retry logic for writing
        max_write_retries = 5
        for i in range(max_write_retries):
            try:
                # gspread v6 compatibility: Use keyword arguments
                self.worksheet.update(range_name=range_str, values=rows, value_input_option='USER_ENTERED')
                print(f"  -> {len(rows)}개 데이터 입력 완료 ({range_str})")

                self.current_row += len(rows)
                break
            except Exception as e:
                # 503 Service Unavailable or others
                if i == max_write_retries - 1:
                    print(f"  !!! 시트 쓰기 실패 (최종): {e}")
                    print("  !!! 데이터가 누락되었습니다.")
                else:
                    wait_time = (2 ** i) + random.uniform(0, 1)
                    print(f"  !!! 시트 쓰기 오류: {e}")
                    print(f"  -> {wait_time:.1f}초 후 재시도 ({i+1}/{max_write_retries})...")
                    time.sleep(wait_time)

    def finalize_and_report_changes(self):
        """
        크롤링 완료 후 변경 사항을 '변경내역' 시트에 기록
        """
        # Cleanup remaining rows (if any)
        if self.current_row < 5000:
            print(f"\n>>> 잔여 데이터 정리 중 ({self.current_row}행 이후)...")
            try:
                self.worksheet.batch_clear([f"D{self.current_row}:N5000"])
            except Exception as e:
                print(f"  (잔여 데이터 삭제 실패: {e})")

        print("\n>>> 변경 사항 분석 중...")
        changes = []
        today = time.strftime("%Y-%m-%d %H:%M")
        
        # 1. 신규 상품 (New)
        for name, info in self.new_data_check.items():
            if name not in self.old_data:
                price = info.get('price','').replace('원','').replace(',','').strip()
                changes.append([today, "신규", name, "신제품 추가됨", "", f"{price}원"])

        # 2. 삭제된 상품 (Removed)
        for name, info in self.old_data.items():
            if name not in self.new_data_check:
                changes.append([today, "삭제", name, "목록에서 사라짐 (단종)", f"{info['price']}원", "-"])

        # 3. 가격/정보 변경 (Changed)
        for name, new_info in self.new_data_check.items():
            if name in self.old_data:
                old = self.old_data[name]
                
                # Compare Price
                new_price = str(new_info.get('price','0')).replace('원','').replace(',','').strip()
                old_price = str(old['price']).replace(',','').strip()
                
                if new_price != old_price and new_price != "0" and old_price != "0":
                     changes.append([today, "가격변경", name, "가격이 변경됨", f"{old_price}원", f"{new_price}원"])

        if changes:
            print(f"총 {len(changes)}건의 변경사항을 발견했습니다.")
            try:
                self.history_sheet.append_rows(changes)
                print(">>> '변경내역' 시트에 저장 완료!")
            except Exception as e:
                print(f"변경내역 저장 실패: {e}")
        else:
            print(">>> 변경 사항이 없습니다.")

if __name__ == "__main__":
    print("This module is intended to be imported by run_all.py")
