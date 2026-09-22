"""
MP(모델 포트폴리오) 트래커 전용 경량 파이프라인. 장마감(15:30) 후 종가가 확정되는 매일 17:30에
실행되도록 별도 스케줄(register_troy_mp_task_windows.ps1)로 등록한다.
fetch_troy_mp_prices.py/build_troy_mp_page.py 둘 다 mp_portfolios.PORTFOLIOS를 순회하므로
트로이 MP·모멘텀 MP 등 등록된 포트폴리오 전부가 이 파이프라인 한 번으로 같이 갱신된다.

원래는 run_pipeline.py(아침 07:30)에 같이 있었는데, 그러면 전날 종가로 하루 늦게
갱신되는 문제가 있었다(코스피 선행 PER 트래커가 겪었던 것과 동일한 문제 -
register_per_tracker_task_windows.ps1 참고). 그래서 이 트래커만 당일 종가가 나온
이후 시간대로 분리했다.
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
LOGS_DIR = BASE_DIR / "logs"
PYTHON = sys.executable

STEPS = [
    ("fetch_troy_mp_prices.py", "MP 트래커: 편입 종목 일별 종가 수집(네이버 차트 API)"),
    ("process_mp_orders.py", "MP 트래커: 사용자가 직접 적어둔 지시서(mp_orders.csv) 오늘자 처리 - "
     "그날 확정 종가로 정확한 수량 계산해서 매매일지에 기록(2026-09-16 추가)"),
    ("fetch_troy_mp_prices.py", "MP 트래커: 지시서로 신규 편입된 종목 가격 이력도 받기(재실행)"),
    ("fetch_market_sector_weights.py", "MP 트래커: 섹터별 OW/UW 비교용 코스피/코스닥 전종목 시총 수집"),
    ("build_troy_mp_page.py", "MP 트래커 페이지 빌드(트로이 MP/모멘텀 MP/코스닥 롱숏 2종)"),
    ("fetch_investor_flow.py", "유동성 대시보드: 수급주체(개인/외국인/기관) 오늘자 재수집 - "
     "이 소스(네이버 신버전 API)가 실시간 스냅샷이라 아침 07:30(장 시작 전) 실행분은 항상 0으로 "
     "찍혀서, 장 마감 이후인 이 시점에 한 번 더 받아 그날 확정치로 덮어씀(2026-09-22 추가)"),
    ("build_dashboard.py", "유동성 대시보드 재빌드(수급주체 확정치 반영)"),
    ("build_home.py", "홈페이지 빌드"),
]


def main():
    LOGS_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"{today}-troy-mp.log"

    overall_ok = True
    with open(log_path, "a", encoding="utf-8") as log_f:
        def write(line):
            try:
                print(line)
            except UnicodeEncodeError:
                enc = sys.stdout.encoding or "utf-8"
                print(line.encode(enc, errors="replace").decode(enc, errors="replace"))
            log_f.write(line + "\n")

        write(f"\n===== 트로이 MP 파이프라인 실행 시작: {datetime.now().isoformat()} =====")
        for script, label in STEPS:
            write(f"\n--- {label} ({script}) ---")
            result = subprocess.run(
                [PYTHON, str(SCRIPTS_DIR / script)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            write(result.stdout)
            if result.stderr:
                write("[STDERR]\n" + result.stderr)
            if result.returncode != 0:
                overall_ok = False
                write(f"경고: {script} 실패, 다음 단계는 계속 진행합니다.")

        write("\n--- GitHub 배포 (git commit + push) ---")
        try:
            subprocess.run(["git", "add", "-A"], cwd=BASE_DIR, check=True, capture_output=True, text=True)
            diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=BASE_DIR)
            if diff.returncode == 0:
                write("변경 사항 없음, 커밋 스킵")
            else:
                commit_msg = f"MP 트래커 자동 갱신 {today}"
                subprocess.run(["git", "commit", "-m", commit_msg], cwd=BASE_DIR, check=True, capture_output=True, text=True)
                push = subprocess.run(["git", "push"], cwd=BASE_DIR, capture_output=True, text=True)
                if push.returncode == 0:
                    write("git push 완료")
                else:
                    write(f"경고: git push 실패\n{push.stderr}")
        except Exception as e:
            write(f"경고: git 배포 단계 실패 ({e})")

        write(f"\n===== 트로이 MP 파이프라인 실행 종료: {datetime.now().isoformat()} (성공={overall_ok}) =====")

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
