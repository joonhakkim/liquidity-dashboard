"""섹터 ETF 공통 편입종목 전용 경량 파이프라인(화~토 08:00, 네이버 ETF 데이터가 밤사이 전일 기준으로 바뀜, 작업 'KRX-Sector-ETF').
빌드(build_sector_etf_common.py) -> 홈 빌드 -> 이 페이지 관련 파일만 커밋+푸시(git add -A 안 씀)."""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
LOGS_DIR = BASE_DIR / "logs"
PYTHON = sys.executable
PATHS = ["docs/sector_etf_common.html", "docs/index.html", "data/etf_sector_common"]


def main():
    LOGS_DIR.mkdir(exist_ok=True)
    log_path = LOGS_DIR / f"{datetime.now():%Y-%m-%d}-sector-etf.log"
    with open(log_path, "a", encoding="utf-8") as log:
        def write(s):
            print(s)
            log.write(s + "\n")
        write(f"===== 섹터 ETF 실행 {datetime.now().isoformat()} =====")
        for script in ("build_sector_etf_common.py", "build_home.py"):
            p = subprocess.run([PYTHON, str(SCRIPTS_DIR / script)], cwd=BASE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
            write(f"--- {script} rc={p.returncode}\n{p.stdout[-600:]}{p.stderr[-600:]}")
            if p.returncode != 0:
                write("실패 - 중단")
                return 1
        subprocess.run(["git", "add"] + PATHS, cwd=BASE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=BASE_DIR).returncode == 0:
            write("변경 없음")
            return 0
        msg = f"섹터 ETF 공통 편입종목 자동 갱신 {datetime.now():%Y-%m-%d}\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
        subprocess.run(["git", "commit", "-m", msg], cwd=BASE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
        subprocess.run(["git", "pull", "--rebase", "-q"], cwd=BASE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
        push = subprocess.run(["git", "push"], cwd=BASE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
        write("git push 완료" if push.returncode == 0 else f"경고: git push 실패\n{push.stderr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
