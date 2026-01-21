# 암웨이 통합 자동화 시스템 (Amway Hybrid Automation)

이 프로젝트는 암웨이 상품 정보를 자동으로 수집(크롤링)하고, Google AI (Gemini) 또는 OpenAI를 사용하여 태그와 설명을 생성한 뒤 구글 스프레드시트에 동기화하는 통합 자동화 시스템입니다.

## 주요 기능

1.  **주간 전체 크롤링 (`run_all.py`)**
    *   매주 월요일 오전 10시 실행 (스케줄러 설정 시)
    *   암웨이 전체 카테고리 상품 정보(가격, PV/BV, 품절 상태 등) 수집
    *   구글 시트 '통합DB'에 실시간 동기화 (기존 AI 데이터 및 가격 변동 내역 보존)
    *   변경 내역은 '변경내역' 시트에 기록됨

2.  **일일 AI 봇 (`main.py`)**
    *   매일 오전 10시 실행 (스케줄러 설정 시)
    *   '통합DB' 시트의 빈칸(태그, 설명)을 찾아 AI로 자동 작성
    *   일일 API 할당량(Quota) 초과 시 자동 중단 및 다음 날 이어하기 지원

## 설치 방법

### 1. 필수 프로그램 설치
*   Python 3.8 이상
*   Chrome 브라우저

### 2. 패키지 설치
터미널에서 다음 명령어를 실행하여 필요한 라이브러리를 설치하세요.

```bash
pip install -r requirements.txt
playwright install
```

### 3. 설정 파일 준비

#### (1) `service_account.json`
*   구글 클라우드 콘솔에서 서비스 계정을 생성하고 키(JSON)를 다운로드하여 프로젝트 폴더에 `service_account.json` 이름으로 저장하세요.
*   해당 서비스 계정 이메일(`xxx@xxx.iam.gserviceaccount.com`)을 구글 스프레드시트의 '공유'에 추가(편집자 권한)해야 합니다.

#### (2) `.env` 파일
프로젝트 폴더에 `.env` 파일을 생성하고 다음 내용을 입력하세요.

```env
# 구글 시트 이름 (예: 통합DB)
SPREADSHEET_NAME=통합DB

# AI 공급자 선택 (google 또는 openai)
AI_PROVIDER=google

# Google Gemini API Key (AI_PROVIDER=google 일 때)
GOOGLE_API_KEY=your_google_api_key_here

# OpenAI API Key (AI_PROVIDER=openai 일 때)
OPENAI_API_KEY=your_openai_api_key_here
```

## 실행 방법

### 1. GitHub Actions 자동화 (클라우드 실행 - 권장)
컴퓨터가 꺼져 있어도 클라우드에서 자동으로 실행되도록 설정합니다.

1.  GitHub 저장소의 **Settings > Secrets and variables > Actions** 메뉴로 이동합니다.
2.  **New repository secret** 버튼을 눌러 다음 값들을 등록합니다.
    *   `GCP_SERVICE_ACCOUNT_JSON`: `service_account.json` 파일의 전체 내용
    *   `SPREADSHEET_NAME`: 구글 시트 이름 (예: `통합DB`)
    *   `AI_PROVIDER`: `google` 또는 `openai`
    *   `GOOGLE_API_KEY`: Google Gemini API 키
    *   `OPENAI_API_KEY`: OpenAI API 키 (필요한 경우)
3.  이제 매일 한국 시간 **오전 10시**에 자동으로 실행됩니다.
4.  실행 로그는 **Actions** 탭에서 확인할 수 있습니다.

### 2. Mac 로컬 자동화 (내 컴퓨터 실행)
내 컴퓨터(Mac)가 켜져 있을 때 백그라운드에서 실행되도록 설정합니다.

```bash
chmod +x setup_automation.sh
./setup_automation.sh
```
*   위 명령어를 실행하면 `launchd`에 작업이 등록됩니다.
*   로그는 `logs/` 폴더에 저장됩니다.

### 3. 수동 실행
언제든지 수동으로 전체 작업을 시작할 수 있습니다.

```bash
python run_all.py
```
*   크롤링 -> 시트 동기화 -> AI 태그 작업이 순차적으로 진행됩니다.

### 3. AI 봇만 실행
AI 태그 작업만 따로 돌리고 싶을 때 사용합니다.

```bash
python main.py
```

## 파일 구조

*   `run_all.py`: 전체 자동화 메인 실행 파일
*   `main.py`: AI 태그/설명 생성 봇
*   `amway_full_crawler.py`: Playwright 기반 전체 상품 크롤러
*   `sync_to_sheet.py`: 구글 시트 동기화 모듈 (스마트 업데이트)
*   `setup_automation.sh`: Mac 자동 실행 스케줄 설정 스크립트
*   `requirements.txt`: 파이썬 의존성 목록
*   `amway_crawling.py` / `update_sheet.py`: (보조) 단일 상품 검색 및 업데이트 도구

## 주의 사항
*   구글 시트의 열 구조(D열~N열)를 임의로 변경하면 데이터가 꼬일 수 있습니다.
*   Gemini API 무료 버전을 사용할 경우 분당 요청 제한이 있을 수 있으며, 스크립트가 이를 감지하여 자동으로 대기하거나 다음 주기에 이어합니다.
