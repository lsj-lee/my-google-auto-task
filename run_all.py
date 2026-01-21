import sys
import time
import subprocess
import os
import datetime

class Logger:
    """화면 출력과 파일 저장을 동시에 하는 로거"""
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "a", encoding="utf-8", buffering=1) # buffering=1 (Line buffering)

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.terminal.flush() # 즉시 화면 출력

    def flush(self):
        self.terminal.flush()
        self.log.flush()

def main():
    # 로그 디렉토리 생성
    if not os.path.exists("logs"):
        os.makedirs("logs")

    # stdout을 로거로 교체하여 print() 내용이 파일에도 저장되도록 함
    sys.stdout = Logger(f"logs/monitor.log")
    
    # 에러도 별도 파일에 기록
    # sys.stderr = Logger(f"logs/monitor_error.log") # stderr까지 잡으면 복잡할 수 있어 stdout만 처리

    print(f"\n[{datetime.datetime.now()}] 작업 시작")
    print("==========================================")
    print("   암웨이 통합 자동화 시스템 (실시간 저장)")
    print("   1. 카테고리 자동 탐색")
    print("   2. 크롤링 즉시 구글 시트 저장")
    print("   3. AI 태그/설명 자동 채우기")
    print("==========================================\n")

    # 의존성 확인 및 모듈 로드
    print(">>> 시스템 점검 중...")
    
    try:
        # 의존성이 없으면 여기서 에러 발생
        import amway_full_crawler
        from sync_to_sheet import SheetManager
    except ImportError as e:
        print(f"\n!!! 필수 모듈을 불러올 수 없습니다: {e}")
        print("필요한 패키지가 설치되었는지 확인해주세요.")
        print("설치 명령어:")
        print(f"  {sys.executable} -m pip install -r requirements.txt")
        print(f"  {sys.executable} -m playwright install")
        sys.exit(1)

    # 1. 시트 매니저 초기화 (연결 및 기존 데이터 삭제)
    print("\n>>> [1/3] 구글 시트 연결 및 초기화...")
    try:
        sheet_manager = SheetManager()
    except Exception as e:
        print(f"\n!!! 시트 연결 실패: {e}")
        print("service_account.json 파일을 확인해주세요.")
        sys.exit(1)

    # 2. 크롤링 실행 (콜백으로 시트 저장 함수 전달)
    print("\n>>> [2/3] 전체 상품 크롤링 시작 (실시간 저장)...")
    start_time = time.time()
    
    try:
        # sheet_manager.append_data 함수를 콜백으로 넘김
        amway_full_crawler.run_full_crawl(data_callback=sheet_manager.append_data)
            
    except Exception as e:
        print(f"\n!!! 크롤링 중 오류 발생: {e}")
        # 오류가 나도 이미 저장된 데이터는 시트에 남아있음 (안전함)
        sys.exit(1)
    
    # 변경 사항 리포트 생성
    print("\n>>> 크롤링 데이터 정리 및 리포트 생성...")
    try:
        sheet_manager.finalize_and_report_changes()
    except Exception as e:
        print(f"!!! 변경 내역 기록 중 오류: {e}")

    # 3. AI 태그 채우기 (main.py)
    print("\n>>> [3/3] AI 빈칸 채우기 (main.py 실행)...")
    if os.path.exists("main.py"):
        try:
            print("   -> main.py 실행 시작...")
            # main.py 실행 (출력을 실시간으로 보여줌)
            result = subprocess.run([sys.executable, "main.py"], check=False)
            
            if result.returncode == 0:
                print("   -> AI 작업 완료.")
            else:
                print(f"   !!! AI 작업 중 오류 발생 (종료 코드: {result.returncode})")
        except Exception as e:
            print(f"   !!! main.py 실행 실패: {e}")
    else:
        print("   -> 'main.py' 파일을 찾을 수 없습니다. (AI 채우기 건너뜀)")
        print("      현재 폴더에 main.py 파일이 있는지 확인해주세요.")

    elapsed_total = time.time() - start_time
    
    end_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{end_time_str}] 모든 작업 완료!")
    print(f"   - 총 소요시간: {int(elapsed_total)}초")
    print("==========================================")

if __name__ == "__main__":
    main()
