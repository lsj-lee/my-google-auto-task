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
COL_CATEGORY_IDX = 3      # D열 (0-based index: 3)
COL_PRODUCT_NAME_IDX = 5  # F열 (0-based index: 5)
COL_TAGS_IDX = 4          # E열 (0-based index: 4)
COL_DESC_IDX = 10         # K열 (0-based index: 10)

# 테스트 제한 해제 (무제한 실행)
MAX_UPDATES = float('inf') 
BATCH_SIZE = 5 # 한 번에 AI에게 물어볼 제품 수 (5~10 권장)

# [안전장치] 일일 요청 제한 (Gemini 무료: 하루 250회)
# 여유를 두고 240회에서 멈추도록 설정
MAX_DAILY_REQUESTS = 240 

AI_PROVIDER = os.environ.get("AI_PROVIDER", "openai").lower() # 'openai' or 'google'

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
    다음 {len(product_list)}개의 제품에 대해 '소분류 및 태그(열 E)'와 '친근하고 전문적인 설명(열 K)'을 한국어로 작성해주세요.
    
    [대상 제품 목록]
{names_text}
    
    [작성 규칙 1: 열 E (소분류/태그)]
    - 반드시 이 형식을 지키세요: [대표 용도] #핵심기능 #대상 또는 특징 #제형
    - 예시: [간건강] #지친하루활력 #직장인맞춤 #간편한정제
    
    [작성 규칙 2: 열 K (설명)]
    - 금지어: "과학적으로 입증", "확인되었습니다", "기반을 제공합니다", "증명되었습니다" 등 딱딱하고 직접적인 표현은 절대 쓰지 마세요.
    - 권장 표현: "~를 돕습니다", "~에 최적화된 배합입니다", "~를 위해 세심하게 설계되었습니다", "~를 선사합니다", "~를 경험해보세요".
    - 내용 구성: [핵심 성분/공법]이 [어떻게 작용]하여 [어떤 긍정적인 변화]를 주는지 부드러운 전문가의 말투로 한 문장 작성하세요.
    - 예시: "뉴트리라이트 농장의 엄선된 원료를 담아, 일상 속 지친 몸에 활기찬 에너지를 가득 채워주는 세심한 영양 설계가 돋보입니다."
    
    [출력 형식]
    반드시 다음 JSON 배열 형식으로만 출력하세요:
    [
        {{ "name": "제품명", "tags": "[용도] #기능 #특징 #제형", "description": "부드럽고 신뢰감 있는 설명" }},
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
                response_format={"type": "json_object"} # gpt-4o-mini supports json_object but usually for single object. for list, standard text is safer or wrapped in object
            )
            # OpenAI json_object mode requires "JSON" word in prompt and usually returns { ... }. 
            # Safe way: wrap list in a key
            prompt_text += "\n\nOutput format: { \"products\": [ ... ] }"
            
            # Re-call with wrapped structure instruction
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
            # 사용 가능한 모델 리스트 (우선순위 순)
            # 2.5-flash를 먼저 시도하고, 실패하면 2.0-flash로 전환 (Fallback)
            candidate_models = ['gemini-2.5-flash', 'gemini-2.0-flash']
            
            for model_name in candidate_models:
                try:
                    # print(f"  (모델 시도: {model_name})") # 디버깅용 (너무 시끄러울 수 있어 주석 처리)
                    model = genai.GenerativeModel(model_name)
                    
                    response = model.generate_content(
                        prompt_text, 
                        generation_config={"response_mime_type": "application/json"}
                    )

                    text = response.text.strip()
                    # Cleanup markdown
                    if text.startswith("```json"): text = text[7:]
                    if text.startswith("```"): text = text[3:]
                    if text.endswith("```"): text = text[:-3]
                    text = text.strip()
                    
                    # Check if wrapped or list
                    try:
                        data = json.loads(text)
                        if isinstance(data, list):
                            return data
                        elif isinstance(data, dict) and "products" in data:
                            return data["products"]
                        # Fallback: try to find list in dict values
                        for v in data.values():
                            if isinstance(v, list): return v
                        return []
                    except:
                        return []
                        
                except ResourceExhausted:
                    raise # 할당량 초과는 즉시 상위로 전파 (모델 바꿔도 소용없음)
                except Exception as e:
                    # 그 외 에러는 다음 모델 시도
                    # 마지막 모델이었다면 에러 출력
                    if model_name == candidate_models[-1]:
                        print(f"\n❌ [{AI_PROVIDER}] 모든 모델 요청 실패: {e}")
                        return None
                    else:
                        continue # 다음 모델로 재시도

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

    # 3. 데이터 로드 및 업데이트용 리스트 준비
    all_values = worksheet.get_all_values()
    start_index = START_ROW - 1 
    
    batch_data = [] # 시트에 한 번에 쓸 데이터 (range, values)
    pending_products = [] # AI에게 보낼 대기열 [{'row':..., 'name':...}]
    
    update_count = 0
    api_request_count = 0 # 실제 API 호출 횟수 카운트

    processed_start = None
    processed_end = None

    try:
        for i in range(start_index, len(all_values)):
            if update_count >= MAX_UPDATES:
                break
            
            # [안전장치] 일일 API 요청 한도 도달 시 중단
            if api_request_count >= MAX_DAILY_REQUESTS:
                print(f"\n✋ [안전장치 작동] 일일 최대 요청 횟수({MAX_DAILY_REQUESTS}회)에 도달했습니다.")
                print("   - 내일 오전 9시에 사용량이 초기화되면 다시 실행됩니다.")
                break

            row_num = i + 1
            row_values = all_values[i]
            
            if len(row_values) < 11:
                row_values += [''] * (11 - len(row_values))

            category = row_values[COL_CATEGORY_IDX].strip() if len(row_values) > COL_CATEGORY_IDX else ""
            product_name = row_values[COL_PRODUCT_NAME_IDX].strip()
            current_tags = row_values[COL_TAGS_IDX].strip()
            current_desc = row_values[COL_DESC_IDX].strip()

            # 이어하기 로직
            if product_name and not current_tags and not current_desc:
                # [예외] '이벤트' 카테고리 건너뜀
                if "이벤트" in category:
                     continue

                if processed_start is None:
                    processed_start = row_num
                processed_end = row_num

                # 대기열에 추가
                pending_products.append({'row': row_num, 'name': product_name})
                
                # 배치 사이즈가 차면 AI 요청
                if len(pending_products) >= BATCH_SIZE:
                    print(f"[{pending_products[0]['row']}행 ~ {pending_products[-1]['row']}행] {len(pending_products)}개 제품 일괄 처리 중...")
                    
                    try:
                        api_request_count += 1 # 요청 횟수 증가
                        results = get_ai_response_batch(pending_products)
                        
                        if results:
                            # 결과 매핑
                            # AI가 순서를 보장한다고 가정하지만, 이름으로 매칭하는 것이 더 안전함
                            # 여기서는 순서대로 매핑 (AI 프롬프트에서 순서 유지 요청함)
                            for idx, item in enumerate(results):
                                if idx < len(pending_products):
                                    target = pending_products[idx]
                                    tags = item.get("tags", "")
                                    desc = item.get("description", "")
                                    
                                    batch_data.append({'range': f'E{target["row"]}', 'values': [[tags]]})
                                    batch_data.append({'range': f'K{target["row"]}', 'values': [[desc]]})
                                    update_count += 1
                            
                            print(f"  -> {len(results)}건 처리 완료")
                            pending_products = [] # 초기화
                            
                            # [안전장치] RPM 제한 준수를 위한 60초 대기
                            # 대기하기 전에 현재까지 작업한 내용을 시트에 저장 (데이터 보호)
                            if batch_data:
                                try:
                                    print("  -> (60초 대기 전) 데이터 시트 저장 중...")
                                    worksheet.batch_update(batch_data)
                                    batch_data = [] # 저장 후 초기화
                                    print("  -> 저장 완료")
                                except Exception as e:
                                    print(f"  -> ⚠️ 중간 저장 실패: {e} (메모리에 보관 후 나중에 재시도)")

                            print("  -> 1분당 요청 제한(RPM) 준수를 위해 60초 대기합니다...")
                            time.sleep(60) 
                        else:
                            print("  -> AI 응답이 비어있습니다. (건너뜀)")
                            pending_products = [] 

                    except ResourceExhausted:
                        print("\n⚠️ [경고] 오늘의 무료 사용량을 모두 소모했습니다!")
                        # 현재 대기열 처리는 실패했으므로 저장하지 않음 (다음에 다시 시도)
                        pending_products = [] 
                        
                        reset_time_msg = calculate_time_until_reset()
                        print(f"🕒 {reset_time_msg} 후에 다시 실행 가능합니다.")
                        
                        # 지금까지 모은 batch_data는 저장
                        if batch_data:
                             try:
                                 worksheet.batch_update(batch_data)
                                 print("✅ 중간 데이터 저장 완료!")
                             except: pass
                        sys.exit(100)

        # 반복문 종료 후 남은 대기열 처리
        if pending_products:
            print(f"[{pending_products[0]['row']}행 ~ {pending_products[-1]['row']}행] 남은 {len(pending_products)}개 제품 처리 중...")
            try:
                results = get_ai_response_batch(pending_products)
                if results:
                    for idx, item in enumerate(results):
                        if idx < len(pending_products):
                            target = pending_products[idx]
                            tags = item.get("tags", "")
                            desc = item.get("description", "")
                            batch_data.append({'range': f'E{target["row"]}', 'values': [[tags]]})
                            batch_data.append({'range': f'K{target["row"]}', 'values': [[desc]]})
                            update_count += 1
                    print(f"  -> {len(results)}건 처리 완료")
            except ResourceExhausted:
                print("\n⚠️ [경고] 막바지 작업 중 할당량 소모!")
                pass # 그냥 저장 루틴으로 이동

    except KeyboardInterrupt:
        print("\n사용자에 의해 작업이 중단되었습니다.")
    except Exception as e:
        print(f"\n알 수 없는 오류 발생: {e}")
    finally:
        # 4. 모아둔 데이터를 한 번에 업데이트 (에러 발생 시에도 저장)
        # 이미 위에서 ResourceExhausted로 저장하고 나간 경우는 제외해야 하지만
        # batch_data가 남아있다면 저장 시도 (중복 저장 방지는 batch_data.clear() 등으로 가능하나 여기서는 간단히 처리)
        if batch_data:
            print(f"\n현재까지 작업한 {len(batch_data)//2}건(태그+설명)의 데이터를 시트에 안전하게 기록 중...")
            try:
                worksheet.batch_update(batch_data)
                print("✅ 시트 저장 완료!")
            except Exception as e:
                print(f"❌ 시트 저장 중 오류 발생: {e}")
            batch_data.clear() # 중복 방지

        if processed_start and processed_end:
            print(f"\n[AI 작업 요약]")
            print(f"   - 처리 범위: {processed_start}행 ~ {processed_end}행")
            print(f"   - 성공 건수: {update_count}건")
        else:
            print("\n[AI 작업 요약] 처리된 항목이 없습니다. (모두 완료되었거나 API가 제한됨)")

        print("프로그램을 종료합니다.")

if __name__ == "__main__":
    main()
