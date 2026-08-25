"""
전체 파이프라인(ECOS/KRX/KOFIA 수집 -> 대시보드 빌드)을 순서대로 실행한다.
스케줄러(Windows 작업 스케줄러 / macOS launchd)가 매일 이 스크립트를 호출한다.

- 성공/실패와 관계없이 logs/YYYY-MM-DD.log 에 전체 출력을 남긴다.
- 하나라도 실패하면 logs/error.log 에 이어서(append) 에러를 기록하고,
  나머지 단계는 계속 진행한다 (한 소스가 막혀도 나머지는 갱신되도록).
- git commit + push는 세 번 한다: (1) 유동성 대시보드/트로이 MP/크랙스프레드처럼 빠른 핵심
  단계들이 끝난 직후 한 번, (2) DART 스크리닝처럼 종목이 577개라 API 레이트리밋에 걸려 몇 시간씩
  걸리기도 하는 느린 단계들까지 다 끝난 뒤 한 번 더, (3) KRX 재시도(CATCHUP_STEPS) 이후 마지막
  한 번. 원래는 맨 끝에 한 번만 커밋했는데, Windows 작업 스케줄러의 실행시간 제한(1시간)에
  걸려 DART 단계가 안 끝나면 프로세스가 강제 종료(SCHED_S_TASK_TERMINATED)되면서 유동성
  대시보드 배포까지 며칠씩 안 되는 문제가 있었다(2026-08-13, 사용자가 "금투협 데이터가
  8/10에서 멈춰있다"고 알려줘서 발견). 중간 체크포인트를 추가하면 뒤쪽이 잘려도 앞쪽 핵심
  배포는 이미 끝난 상태라 이 문제가 사라진다.

- CATCHUP_STEPS(2026-08-19 추가): ADR/볼린저밴드/PER트래커는 KRX Open API(stk_bydd_trd/
  ksq_bydd_trd)의 당일 데이터에 의존하는데, KRX가 가끔 아침 7시반(CORE_STEPS 시점)까지도
  발표를 안 한다(사용자 PC가 07:20~18:30에만 켜져 있어서 저녁 늦은 재시도는 불가능). 마침
  DART 스크리닝(SLOW_STEPS)이 끝나는 늦은 오전엔 KRX가 발표를 끝낸 경우가 많아서, 이 시점에
  ADR/볼린저밴드도 한 번 더 재시도한다(PER트래커는 이미 SLOW_STEPS에 있었고 이 재시도 덕분에
  같은 날 우연히 살아난 적이 있어서 - 그게 "중복"이 아니라 의도된 안전장치였다는 걸 알게 됨).
  fetch_adr.py/fetch_bollinger_prices.py 둘 다 이미 받은 날짜는 건너뛰는 멱등적 로직이라
  하루 두 번 돌아도 안전하다.
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
LOGS_DIR = BASE_DIR / "logs"
PYTHON = sys.executable

# 빠른 핵심 단계 - 유동성 대시보드/트로이 MP/크랙스프레드처럼 몇 분이면 끝나는 것들.
# 여기까지 끝나면 바로 한 번 커밋+푸시해서, 뒤쪽 느린 단계가 시간제한에 걸려도 이미 배포돼 있다.
CORE_STEPS = [
    ("fetch_ecos.py", "ECOS 수집"),
    ("fetch_krx.py", "KRX/네이버 수집"),
    ("fetch_kofia.py", "KOFIA 수집(수동 파일 병합)"),
    ("fetch_fred.py", "FRED(미국 M2/실질금리/구리) 수집"),
    ("fetch_bitcoin.py", "CoinGecko(비트코인 시가총액) 수집"),
    ("fetch_markets.py", "네이버 금융(원/달러 환율, 금, 은, 엔/달러) 수집"),
    ("fetch_adr.py", "코스피/코스닥 등락비율(ADR) 계산용 상승/하락 종목수 수집(KRX Open API)"),
    ("fetch_bollinger_prices.py", "볼린저밴드 돌파종목수용 전종목 종가 수집(KRX Open API, 최초 1회만 오래 걸림)"),
    ("build_bollinger_breakout.py", "볼린저밴드 상단 돌파 종목수 트래커 페이지 빌드"),
    ("fetch_gdx.py", "금광기업 ETF(GDX) 수집(Yahoo Finance, 유동성 선행성 검증됨)"),
    ("fetch_crack_spread.py", "정유화학 정제마진(3:2:1 크랙 스프레드) 수집(EIA API)"),
    ("build_crack_spread_page.py", "정유화학 정제마진 트래커 페이지 + 엑셀 다운로드 빌드"),
    ("fetch_investor_flow.py", "수급주체(data/manual/수급정리*.xlsm 병합, 코스피/코스닥 분류)"),
    ("fetch_news_sentiment.py", "한국은행 뉴스심리지수(ECOS 521Y001, 일별) 수집"),
    ("optimize_percentile_window.py", "통합차트 백분위 지표 최적 기간 재탐색(데이터 누적에 따라 자동 조정)"),
    ("build_dashboard.py", "유동성 대시보드 빌드"),
    ("fetch_naver_sector.py", "네이버 업종분류 수집(OP밴드/스크리닝 섹터 필터용, 종목코드 기준 전종목)"),
    ("build_op_band.py", "OP밴드 트래커 빌드(data/manual/*기업*밴드*.xlsx 기반, 영업이익 x N배 밴드)"),
    ("build_risk_signals.py", "조정 경고 신호 페이지 빌드(유동성 지표 + 돌파종목수 조합, 지표별 개별 발동 표시)"),
    ("build_home.py", "홈페이지 빌드"),
]

# 느린 단계 - DART 스크리닝은 종목이 577개라 API 레이트리밋(하루 13000회)에 걸리면 다음날로
# 이어서 처리되고, 전체가 끝나기까지 1시간을 넘기는 날도 있다. CORE_STEPS 배포 이후에 실행한다.
SLOW_STEPS = [
    ("screen_op_growth.py", "주식 스크리닝: 전체 상장사 목록 + 영업이익 컨센서스 매칭"),
    ("fetch_dart_quarterly.py", "주식 스크리닝: DART 분기별 매출/영업이익"),
    ("fetch_dart_preliminary.py", "주식 스크리닝: DART 잠정실적(2분기 YoY 우선 소스)"),
    ("fetch_valuation_bands.py", "주식 스크리닝: PER/PBR 밴드"),
    ("fetch_stock_issues.py", "주식 스크리닝: 시총상위50 관련 이슈·뉴스(DART 공시 + 네이버 종목뉴스)"),
    ("build_screening_page.py", "주식 스크리닝 페이지 빌드"),
    ("fetch_kospi_per_tracker.py", "코스피 선행 PER 트래커: 시총상위50 컨센서스 PER 집계(하루 1행 누적)"),
    ("fetch_kosdaq_per_tracker.py", "코스닥 선행 PER 트래커: 시총상위50 컨센서스 PER 집계(하루 1행 누적)"),
    ("build_per_tracker_page.py", "코스피·코스닥 선행 PER 트래커 페이지 빌드"),
    ("fetch_op_band_consensus.py", "OP밴드: 전종목 FnGuide 컨센서스 교차검증 수집(종목당 API 호출, 느림)"),
    ("build_op_band.py", "OP밴드 트래커 재빌드(FnGuide 교차검증 반영)"),
    ("build_home.py", "홈페이지 빌드(스크리닝/PER 트래커 최신 링크 반영용으로 한 번 더)"),
]

# KRX 재시도 단계 - CORE_STEPS(아침 7시반) 시점엔 KRX가 당일 데이터를 아직 안 줬을 수 있는데,
# SLOW_STEPS(DART 등)가 끝나는 늦은 오전엔 발표가 끝나있는 경우가 많다. fetch_adr.py/
# fetch_bollinger_prices.py 둘 다 이미 받은 날짜는 건너뛰므로 하루 두 번 돌아도 안전(멱등).
CATCHUP_STEPS = [
    ("fetch_adr.py", "[재시도] 코스피/코스닥 ADR (KRX 발표 지연 대비)"),
    ("fetch_bollinger_prices.py", "[재시도] 볼린저밴드 돌파종목수용 종가 (KRX 발표 지연 대비)"),
    ("build_bollinger_breakout.py", "[재시도] 볼린저밴드 돌파 종목수 트래커 재빌드"),
    ("build_dashboard.py", "[재시도] 유동성 대시보드 재빌드(ADR 반영)"),
    ("build_risk_signals.py", "[재시도] 조정 경고 신호 재빌드"),
    ("build_home.py", "[재시도] 홈페이지 재빌드"),
]


def run_steps(steps, write, error_log_path):
    overall_ok = True
    for script, label in steps:
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
    return overall_ok


def git_deploy(write, commit_msg):
    write(f"\n--- GitHub 배포 (git commit + push): {commit_msg} ---")
    try:
        subprocess.run(["git", "add", "-A"], cwd=BASE_DIR, check=True, capture_output=True, text=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=BASE_DIR)
        if diff.returncode == 0:
            write("변경 사항 없음, 커밋 스킵")
            return
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=BASE_DIR, check=True, capture_output=True, text=True)
        push = subprocess.run(["git", "push"], cwd=BASE_DIR, capture_output=True, text=True)
        if push.returncode == 0:
            write("git push 완료")
        else:
            write(f"경고: git push 실패\n{push.stderr}")
    except Exception as e:
        write(f"경고: git 배포 단계 실패 ({e})")


def main():
    LOGS_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"{today}.log"
    error_log_path = LOGS_DIR / "error.log"

    with open(log_path, "a", encoding="utf-8") as log_f:
        def write(line):
            try:
                print(line)
            except UnicodeEncodeError:
                # 콘솔 코드페이지(cp949)가 못 그리는 문자가 섞이면 print()가 죽어서 파이프라인
                # 전체가 중단된다 - 로그 파일(UTF-8)에는 항상 정상 기록되니 콘솔 출력만 깨진
                # 문자를 물음표로 바꿔서 죽지 않게 한다.
                enc = sys.stdout.encoding or "utf-8"
                print(line.encode(enc, errors="replace").decode(enc, errors="replace"))
            log_f.write(line + "\n")

        write(f"\n===== 파이프라인 실행 시작: {datetime.now().isoformat()} =====")

        core_ok = run_steps(CORE_STEPS, write, error_log_path)
        git_deploy(write, f"데이터 자동 갱신 {today}")

        slow_ok = run_steps(SLOW_STEPS, write, error_log_path)
        git_deploy(write, f"데이터 자동 갱신(스크리닝/PER) {today}")

        catchup_ok = run_steps(CATCHUP_STEPS, write, error_log_path)
        git_deploy(write, f"데이터 자동 갱신(KRX 재시도) {today}")

        overall_ok = core_ok and slow_ok and catchup_ok
        write(f"\n===== 파이프라인 실행 종료: {datetime.now().isoformat()} (성공={overall_ok}) =====")

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
