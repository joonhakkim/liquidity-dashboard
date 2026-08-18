"""
트로이 MP 트래커 전용 경량 파이프라인. 장마감(15:30) 후 종가가 확정되는 매일 17:30에
실행되도록 별도 스케줄(register_troy_mp_task_windows.ps1)로 등록한다.

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
    ("fetch_troy_mp_prices.py", "트로이 MP: 편입 종목 일별 종가 수집(네이버 차트 API)"),
    ("build_troy_mp_page.py", "트로이 MP 트래커 페이지 빌드"),
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
                commit_msg = f"트로이 MP 자동 갱신 {today}"
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
