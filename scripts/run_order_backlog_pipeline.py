"""
수주잔고(주문잔고) 전용 경량 파이프라인. 전체 종목(~740개) DART 정기보고서를 훑는 무거운
스캔이라 매일 돌리면 fetch_dart_quarterly.py 등 다른 단계와 DART API 하루 호출 한도(2만 건)를
다투게 된다. 분기보고서 기반이라 매일 바뀔 데이터도 아니라서, 주 1회(일요일, 별도 스케줄:
register_order_backlog_task_windows.ps1)로 분리해서 실행한다.
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
    ("fetch_order_backlog.py", "수주잔고 1단계(scan): 전종목 최신 정기보고서에서 수주잔고 탐색"),
    ("fetch_order_backlog_history.py", "수주잔고 2단계(backfill): 발견된 종목만 시계열 백필"),
    ("build_screening_page.py", "주식 스크리닝 페이지 재빌드(수주잔고 반영)"),
]


def main():
    LOGS_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"{today}-order-backlog.log"

    overall_ok = True
    with open(log_path, "a", encoding="utf-8") as log_f:
        def write(line):
            try:
                print(line)
            except UnicodeEncodeError:
                # 콘솔 코드페이지(cp949)가 못 그리는 문자(예: UTF-8 디코딩시 생긴 대체문자 U+FFFD)가
                # 섞이면 print() 자체가 죽어서 파이프라인 전체가 중단된다 - 로그 파일(UTF-8)에는
                # 항상 정상 기록되니, 콘솔 출력만 깨진 문자를 물음표로 바꿔서 죽지 않게 한다.
                enc = sys.stdout.encoding or "utf-8"
                print(line.encode(enc, errors="replace").decode(enc, errors="replace"))
            log_f.write(line + "\n")

        write(f"\n===== 수주잔고 파이프라인 실행 시작: {datetime.now().isoformat()} =====")
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
                commit_msg = f"수주잔고 자동 갱신 {today}"
                subprocess.run(["git", "commit", "-m", commit_msg], cwd=BASE_DIR, check=True, capture_output=True, text=True)
                push = subprocess.run(["git", "push"], cwd=BASE_DIR, capture_output=True, text=True)
                if push.returncode == 0:
                    write("git push 완료")
                else:
                    write(f"경고: git push 실패\n{push.stderr}")
        except Exception as e:
            write(f"경고: git 배포 단계 실패 ({e})")

        write(f"\n===== 수주잔고 파이프라인 실행 종료: {datetime.now().isoformat()} (성공={overall_ok}) =====")

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
