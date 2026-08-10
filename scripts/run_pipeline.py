"""
전체 파이프라인(ECOS/KRX/KOFIA 수집 -> 대시보드 빌드)을 순서대로 실행한다.
스케줄러(Windows 작업 스케줄러 / macOS launchd)가 매일 이 스크립트를 호출한다.

- 성공/실패와 관계없이 logs/YYYY-MM-DD.log 에 전체 출력을 남긴다.
- 하나라도 실패하면 logs/error.log 에 이어서(append) 에러를 기록하고,
  나머지 단계는 계속 진행한다 (한 소스가 막혀도 나머지는 갱신되도록).
- 대시보드 빌드까지 끝나면 git commit + push까지 자동으로 해서 GitHub Pages가 갱신되게 한다.
- 마지막에 docs/index.html 빌드까지 성공하면 exit code 0, 아니면 1 (git push 실패는 경고만).
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
    ("fetch_ecos.py", "ECOS 수집"),
    ("fetch_krx.py", "KRX/네이버 수집"),
    ("fetch_kofia.py", "KOFIA 수집(수동 파일 병합)"),
    ("fetch_fred.py", "FRED(미국 M2/실질금리/구리) 수집"),
    ("fetch_bitcoin.py", "CoinGecko(비트코인 시가총액) 수집"),
    ("fetch_markets.py", "네이버 금융(원/달러 환율, 금, 은) 수집"),
    ("fetch_investor_flow.py", "수급주체(data/manual/수급정리*.xlsm 병합, 코스피/코스닥 분류)"),
    ("fetch_news_sentiment.py", "한국은행 뉴스심리지수(ECOS 521Y001, 일별) 수집"),
    ("optimize_percentile_window.py", "통합차트 백분위 지표 최적 기간 재탐색(데이터 누적에 따라 자동 조정)"),
    ("build_dashboard.py", "유동성 대시보드 빌드"),
    ("screen_op_growth.py", "주식 스크리닝: 전체 상장사 목록 + 영업이익 컨센서스 매칭"),
    ("fetch_dart_quarterly.py", "주식 스크리닝: DART 분기별 매출/영업이익"),
    ("fetch_dart_preliminary.py", "주식 스크리닝: DART 잠정실적(2분기 YoY 우선 소스)"),
    ("fetch_valuation_bands.py", "주식 스크리닝: PER/PBR 밴드"),
    ("fetch_stock_issues.py", "주식 스크리닝: 시총상위50 관련 이슈·뉴스(DART 공시 + 네이버 종목뉴스)"),
    ("build_screening_page.py", "주식 스크리닝 페이지 빌드"),
    ("fetch_kospi_per_tracker.py", "코스피 선행 PER 트래커: 시총상위50 컨센서스 PER 집계(하루 1행 누적)"),
    ("fetch_kosdaq_per_tracker.py", "코스닥 선행 PER 트래커: 시총상위50 컨센서스 PER 집계(하루 1행 누적)"),
    ("build_per_tracker_page.py", "코스피·코스닥 선행 PER 트래커 페이지 빌드"),
    ("build_home.py", "홈페이지 빌드"),
]


def main():
    LOGS_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"{today}.log"
    error_log_path = LOGS_DIR / "error.log"

    overall_ok = True

    with open(log_path, "a", encoding="utf-8") as log_f:
        def write(line):
            print(line)
            log_f.write(line + "\n")

        write(f"\n===== 파이프라인 실행 시작: {datetime.now().isoformat()} =====")

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
                error_msg = (
                    f"[{datetime.now().isoformat()}] {script} 실패 "
                    f"(exit code {result.returncode})\n{result.stderr}\n"
                )
                write(f"경고: {script} 실패, 다음 단계는 계속 진행합니다.")
                with open(error_log_path, "a", encoding="utf-8") as err_f:
                    err_f.write(error_msg)

        write("\n--- GitHub 배포 (git commit + push) ---")
        try:
            subprocess.run(["git", "add", "-A"], cwd=BASE_DIR, check=True, capture_output=True, text=True)
            diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=BASE_DIR)
            if diff.returncode == 0:
                write("변경 사항 없음, 커밋 스킵")
            else:
                commit_msg = f"데이터 자동 갱신 {today}"
                subprocess.run(["git", "commit", "-m", commit_msg], cwd=BASE_DIR, check=True, capture_output=True, text=True)
                push = subprocess.run(["git", "push"], cwd=BASE_DIR, capture_output=True, text=True)
                if push.returncode == 0:
                    write("git push 완료")
                else:
                    write(f"경고: git push 실패\n{push.stderr}")
        except Exception as e:
            write(f"경고: git 배포 단계 실패 ({e})")

        write(f"\n===== 파이프라인 실행 종료: {datetime.now().isoformat()} (성공={overall_ok}) =====")

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
