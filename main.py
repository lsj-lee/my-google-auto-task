import os
import sys
import json
import time
import datetime
import pytz
import gspread
import warnings
from google.oauth2.service_account import Credentials
from google.api_core.exceptions import ResourceExhausted
from dotenv import load_dotenv

# OpenAI
from openai import OpenAI

# Google Generative AI 경고 숨기기 (모든 경고 무시)
warnings.filterwarnings("ignore", message="All support for the `google.generativeai` package has ended")
import google.generativeai as genai

# 환경 변수 로드
load_dotenv()

# 설정
SERVICE_ACCOUNT_FILE = 'service_account.json'
SHEET_NAME = '통합DB'
START_ROW = 6
# [최적화] D열부터 K열까지 가져오므로 인덱스가 변경됨 (D=0, E=1, F=2 ... K=7)
COL_CATEGORY_IDX = 0      # D열 (Relative 0)
COL_PRODUCT_NAME_IDX = 2  # F열 (Relative 2)
COL_TAGS_IDX = 1          # E열 (Relative 1)
COL_DESC_IDX = 7          # K열 (Relative 7)

# 테스트 제한 해제 (무제한 실행)
MAX_UPDATES = float('inf') 
BATCH_SIZE = 5 # 한 번에 AI에게 물어볼 제품 수 (5~10 권장)

# [안전장치] 일일 요청 제한 (Gemini 무료: 하루 250회)
# 여유를 두고 240회에서 멈추도록 설정
MAX_DAILY_REQUESTS = 240 

AI_PROVIDER = os.environ.get("AI_PROVIDER", "openai").lower() # 'openai' or 'google'

# [최적화] AI 공급자별 최소 요청 간격 설정 (초 단위)
# Google: 15 RPM = 4초 간격. (안전을 위해 5초 설정)
# OpenAI: 티어에 따라 다르지만 훨씬 빠름. (안전을 위해 1초 설정)
if AI_PROVIDER == 'google':
    MIN_REQUEST_INTERVAL = 5.0
else:
    MIN_REQUEST_INTERVAL = 1.0

# 클라이언트 초기화
openai_client = None
if AI_PROVIDER == 'openai':
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        openai_client = OpenAI(api_key=api_key)
    else:
        print("경고: OPENAI_API_KEY가 설정되지 않았습니다.")

elif AI_PROVIDER == 'google':
    api_key = os.environ.get("GOOGLE_API_KEY")
    if api_key:
        genai.configure(api_key=api_key)
    else:
        print("경고: GOOGLE_API_KEY가 설정되지 않았습니다.")
else:
    print(f"경고: 알 수 없는 AI_PROVIDER '{AI_PROVIDER}'. 'openai' 또는 'google'을 사용하세요.")

def calculate_time_until_reset():
    """
    KST 기준 다음 오전 9시까지 남은 시간을 계산하여 문자열로 반환
    """
    kst = pytz.timezone('Asia/Seoul')
    now_kst = datetime.datetime.now(kst)
    
    # 오늘 오전 9시
    target_time = now_kst.replace(hour=9, minute=0, second=0, microsecond=0)
    
    # 이미 9시가 지났으면 내일 9시로 설정
    if now_kst >= target_time:
        target_time += datetime.timedelta(days=1)
        
    remaining = target_time - now_kst
    hours, remainder = divmod(remaining.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    
    return f"약 {hours}시간 {minutes}분 (한국 시간 오전 9시 초기화)"

def get_ai_response_batch(product_list):
    """
    여러 제품(product_list)을 받아 한 번에 태그와 설명을 생성하는 AI 함수 (배치 처리)
    product_list: [{'name': '...', 'row': 10}, ...]
    """
    if not product_list:
        return []

    names_text = "\n".join([f"- {item['name']}" for item in product_list])
    
    prompt_text = f"""
    대상 제품: {names_text}
    
    [작성 규칙: 열 E (분류/성분) - 핵심 성분 및 구성 요소 상세화]
    1. 단순히 제품군만 적지 말고, 제품의 핵심 성분과 구성 요소를 상세히 포함하세요.
    2. 예시: '더블엑스' → 비타민 A, B, C, D, E, K, 엽산, 비오틴 및 20가지 식물 농축물 성분 포함.
    3. 예시: '화장품' → 살리실산(BHA), 히알루론산, 세라마이드 등 핵심 유효 성분 명시.
    4. 해시태그(#)는 절대 사용하지 마세요. 문장이나 쉼표로 구분된 성분 나열 형식을 사용하세요.
    
    [작성 규칙: 열 K (설명) - 2단락 구조 및 신중한 문체]
    1. 구조: 두 개의 단락으로 나누어 작성하세요. (줄바꿈 필수)
       - 첫 번째 단락: 제품에 대한 간결하고 매력적인 소개글 (2~3줄).
       - 두 번째 단락: 해당 성분이 작용하는 과학적 원리 및 논문적 근거를 요약하여 기술.
    2. 문체: AI 특유의 확정적 말투(예: ~이다, 확실하다)를 지양하고, 신중하고 객관적인 문체를 사용하세요.
       - 권장 표현: "~에 도움을 줄 수 있는 것으로 알려져 있다", "~한 원리가 보고된 바 있다", "~할 가능성이 있다", "~연구 결과가 있다"
    
    [출력 형식]
    반드시 다음 JSON 배열 형식으로만 출력하세요:
    [
        {{ "name": "제품명", "tags": "성분1, 성분2 및 성분3 포함...", "description": "첫번째 단락 소개글...\\n\\n두번째 단락 과학적 근거..." }},
        ...
    ]
    """

    try:
        if AI_PROVIDER == 'openai':
            if not openai_client: return None
            
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant. Output purely JSON."},
                    {"role": "user", "content": prompt_text}
                ],
                response_format={"type": "json_object"}
            )
            prompt_text += "\n\nOutput format: { \"products\": [ ... ] }"
            
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant. Output purely JSON."},
                    {"role": "user", "content": prompt_text}
                ],
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content
            data = json.loads(content)
            return data.get("products", [])

        elif AI_PROVIDER == 'google':
            candidate_models = ['gemini-2.5-flash', 'gemini-2.0-flash']
            
            for model_name in candidate_models:
                try:
                    model = genai.GenerativeModel(model_name)
                    
                    response = model.generate_content(
                        prompt_text, 
                        generation_config={"response_mime_type": "application/json"}
                    )

                    text = response.text.strip()
                    if text.startswith("```json"): text = text[7:]
                    if text.startswith("```"): text = text[3:]
                    if text.endswith("```"): text = text[:-3]
                    text = text.strip()
                    
                    try:
                        data = json.loads(text)
                        if isinstance(data, list):
                            return data
                        elif isinstance(data, dict) and "products" in data:
                            return data["products"]
                        for v in data.values():
                            if isinstance(v, list): return v
                        return []
                    except:
                        return []
                        
                except ResourceExhausted:
                    raise
                except Exception as e:
                    if model_name == candidate_models[-1]:
                        print(f"\n❌ [{AI_PROVIDER}] 모든 모델 요청 실패: {e}")
                        return None
                    else:
                        continue

    except ResourceExhausted:
        raise
    except Exception as e:
        print(f"\n❌ [{AI_PROVIDER}] AI 요청 실패 (배치): {e}")
        return None

def main():
    print("=== 구글 시트 AI 자동화 봇 실행 (스마트 할당량 관리) ===")
    print(f"AI 공급자: {AI_PROVIDER}")

    # 1. 구글 시트 인증
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"오류: '{SERVICE_ACCOUNT_FILE}' 파일을 찾을 수 없습니다.")
        return

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
    gc = gspread.authorize(creds)

    # 2. 스프레드시트 열기
    spreadsheet_name = os.environ.get("SPREADSHEET_NAME")
    if not spreadsheet_name:
        print("오류: .env 파일에 'SPREADSHEET_NAME'이 설정되지 않았습니다.")
        return

    print("📡 구글 시트에 접속 중입니다... (잠시만 기다려주세요)")
    try:
        sh = gc.open(spreadsheet_name)
        worksheet = sh.worksheet(SHEET_NAME)
    except Exception as e:
        print(f"❌ 접속 오류: {e}")
        print("팁: .env 파일의 SPREADSHEET_NAME이 정확한지, 서비스 계정이 공유되어 있는지 확인하세요.")
        return

    print(f"✅ '{spreadsheet_name}'의 '{SHEET_NAME}' 시트 작업을 시작합니다...")

    # 3. 데이터 로드
    range_query = f"D{START_ROW}:K"
    print(f"   - 데이터 읽는 중... ({range_query})")
    all_values = worksheet.get(range_query)
    
    # 4. 작업 분류 (채우기 vs 업데이트)
    fill_queue = []
    update_queue = []
    
    print("   - 데이터 분석 및 작업 분류 중...")
    for i, row_values in enumerate(all_values):
        row_num = START_ROW + i

        if len(row_values) < 8:
            row_values += [''] * (8 - len(row_values))

        category = row_values[COL_CATEGORY_IDX].strip()
        product_name = row_values[COL_PRODUCT_NAME_IDX].strip()
        current_tags = row_values[COL_TAGS_IDX].strip()
        current_desc = row_values[COL_DESC_IDX].strip()

        # [예외] '이벤트' 카테고리 건너뜀
        if "이벤트" in category:
             continue
        if not product_name:
             continue

        is_empty = not current_tags or not current_desc

        needs_update = False
        if not is_empty:
            # [조건] 해시태그(#)가 있으면 구버전 데이터 -> 업데이트 대상
            if '#' in current_tags:
                needs_update = True
            # [조건] 설명이 너무 짧거나 2단락(\n\n)이 아니면 -> 업데이트 대상 (휴리스틱)
            # 확실한 2단락 구분자가 없으면 업데이트 대상으로 간주
            elif '\n' not in current_desc: # 간단한 체크
                needs_update = True

        if is_empty:
            fill_queue.append({'row': row_num, 'name': product_name, 'type': 'new'})
        elif needs_update:
            update_queue.append({'row': row_num, 'name': product_name, 'type': 'update'})

    print(f"   - 신규 작성 필요: {len(fill_queue)}건")
    print(f"   - 업데이트 필요: {len(update_queue)}건")

    # 5. 작업 실행
    # 우선순위: 1. 빈칸 채우기 -> 2. 업데이트
    total_queues = [('신규 채우기', fill_queue), ('업데이트', update_queue)]

    api_request_count = 0
    new_filled_count = 0
    updated_count = 0
    last_request_time = 0

    batch_data = []

    try:
        for job_name, queue in total_queues:
            if not queue:
                continue
                
            print(f"\n>>> [{job_name}] 작업을 시작합니다. (대상: {len(queue)}건)")

            # Batch processing
            for i in range(0, len(queue), BATCH_SIZE):
                if api_request_count >= MAX_DAILY_REQUESTS:
                    print(f"\n✋ [안전장치 작동] 일일 최대 요청 횟수({MAX_DAILY_REQUESTS}회)에 도달했습니다.")
                    break
                    
                batch_items = queue[i : i + BATCH_SIZE]
                print(f"   [{job_name}] {batch_items[0]['row']}행 ~ {batch_items[-1]['row']}행 처리 중... ({len(batch_items)}개)")

                try:
                    api_request_count += 1
                    results = get_ai_response_batch(batch_items)

                    if results:
                        for idx, item in enumerate(results):
                            if idx < len(batch_items):
                                target = batch_items[idx]
                                tags = item.get("tags", "")
                                desc = item.get("description", "")

                                batch_data.append({'range': f'E{target["row"]}', 'values': [[tags]]})
                                batch_data.append({'range': f'K{target["row"]}', 'values': [[desc]]})

                                if target['type'] == 'new':
                                    new_filled_count += 1
                                else:
                                    updated_count += 1
                        
                        print(f"     -> 처리 완료")
                        
                        # 중간 저장
                        if batch_data:
                            try:
                                worksheet.batch_update(batch_data)
                                batch_data = []
                            except Exception as e:
                                print(f"     -> ⚠️ 중간 저장 실패: {e} (메모리 보관)")

                        # RPM 대기
                        elapsed = time.time() - last_request_time
                        wait_time = max(0, MIN_REQUEST_INTERVAL - elapsed)
                        if wait_time > 0:
                            time.sleep(wait_time)
                        last_request_time = time.time()

                    else:
                        print("     -> AI 응답 없음")

                except ResourceExhausted:
                    print("\n⚠️ [경고] 오늘의 무료 사용량을 모두 소모했습니다!")
                    raise # Loop 탈출

    except ResourceExhausted:
        # 종료 전 안내 메시지 계산
        reset_time_msg = calculate_time_until_reset()
        print(f"🕒 {reset_time_msg} 후에 다시 실행 가능합니다.")

    except KeyboardInterrupt:
        print("\n사용자에 의해 작업이 중단되었습니다.")
    except Exception as e:
        print(f"\n알 수 없는 오류 발생: {e}")
    finally:
        # 잔여 데이터 저장
        if batch_data:
            print(f"\n남은 {len(batch_data)//2}건의 데이터를 시트에 저장 중...")
            try:
                worksheet.batch_update(batch_data)
                print("✅ 저장 완료!")
            except Exception as e:
                print(f"❌ 저장 실패: {e}")

        print("\n[AI 작업 최종 보고]")
        print(f"   - 신규 채워진 행: {new_filled_count}건")
        print(f"   - 수정된 기존 행: {updated_count}건")
        print("프로그램을 종료합니다.")

if __name__ == "__main__":
    main()
