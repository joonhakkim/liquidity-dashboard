"""
MP(모델 포트폴리오) 트래커 전용 경량 파이프라인. 매일 20:05(register_troy_mp_task_windows.ps1
스케줄)에 자동 실행된다 - 수동으로 돌릴 땐 반드시 이 시각 이후에 돌릴 것(아래 설명).
fetch_troy_mp_prices.py/build_troy_mp_page.py 둘 다 mp_portfolios.PORTFOLIOS를 순회하므로
트로이 MP·모멘텀 MP 등 등록된 포트폴리오 전부가 이 파이프라인 한 번으로 같이 갱신된다.

원래는 run_pipeline.py(아침 07:30)에 같이 있었는데, 그러면 전날 종가로 하루 늦게
갱신되는 문제가 있었다(코스피 선행 PER 트래커가 겪었던 것과 동일한 문제 -
register_per_tracker_task_windows.ps1 참고). 그래서 당일 종가가 나온 이후 시간대로
분리했다 - 처음엔 정규장 마감(15:30) 직후인 17:30이었는데, 2026-09-14부터 KRX가
애프터마켓(16:00~20:00 접속매매)을 도입한 뒤로 네이버 일별시세의 "당일 종가"가 20:00까지
계속 갱신되는 값이라, 17:30에 받으면 애프터마켓 중간 스냅샷이라 나중에 또 바뀌는 문제를
겪었다(같은 날 두 번 정정). 그래서 "애프터마켓 마감가(20:00)를 그날 종가로 쓴다"로
정하고 20:05로 재조정했다(2026-09-15). **장중은 물론 15:30~20:00 애프터마켓 시간대에
수동 실행해도 아직 확정 전이라 잘못된 가격으로 체결된다** - 이 파이프라인을 수동으로
당겨 돌리려면 20:05 이후에만 할 것(2026-09-23, 트로이MP 4건이 이 실수로 09:23 장중가에
체결됐다가 되돌려짐).
"""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
LOGS_DIR = BASE_DIR / "logs"
PYTHON = sys.executable

# 자식 파이썬 프로세스가 콘솔 코드페이지(cp949)가 아니라 UTF-8로 stdout/stderr를 쓰게 강제한다.
# 부모 쪽은 encoding="utf-8"/errors="replace"로 받고 있었는데도, 자식이 실제로는 cp949 바이트를
# 내보내던 경우가 있어서 디코딩 스레드(_readerthread)가 드물게 UnicodeDecodeError로 죽었다
# (2026-09-23, MP 트래커 자동 갱신 로그에서 발견 - 파이프라인 자체는 계속 진행됐지만 로그가
# 지저분해짐). 환경변수로 자식의 출력 인코딩 자체를 UTF-8로 고정하는 게 근본 수정.
CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}

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
                env=CHILD_ENV,
            )
            write(result.stdout)
            if result.stderr:
                write("[STDERR]\n" + result.stderr)
            if result.returncode != 0:
                overall_ok = False
                write(f"경고: {script} 실패, 다음 단계는 계속 진행합니다.")

        write("\n--- GitHub 배포 (git commit + push) ---")
        try:
            # 아래 git 호출들엔 원래 encoding/errors가 없어서 text=True가 시스템 로케일
            # (cp949)로 디코딩을 시도했다 - git 자체 출력(변경된 한글 파일명 등)은 UTF-8이라
            # 여기서 진짜 UnicodeDecodeError가 났다(_readerthread에서 스레드가 죽는 형태라
            # 파이프라인 자체는 안 멈추고 로그만 깨졌음 - 2026-09-23에 STEPS 루프의
            # subprocess.run은 이미 encoding="utf-8" 지정돼 있는 걸 재확인하고서야 진짜
            # 원인이 여기였다는 걸 찾았다). 동일하게 encoding/errors를 맞춰준다.
            g_kw = dict(encoding="utf-8", errors="replace")
            subprocess.run(["git", "add", "-A"], cwd=BASE_DIR, check=True, capture_output=True, text=True, **g_kw)
            diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=BASE_DIR)
            if diff.returncode == 0:
                write("변경 사항 없음, 커밋 스킵")
            else:
                commit_msg = f"MP 트래커 자동 갱신 {today}"
                subprocess.run(["git", "commit", "-m", commit_msg], cwd=BASE_DIR, check=True, capture_output=True, text=True, **g_kw)
                push = subprocess.run(["git", "push"], cwd=BASE_DIR, capture_output=True, text=True, **g_kw)
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
