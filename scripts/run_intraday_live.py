"""
장중 실시간 갱신 실행기(작업 스케줄러 'KRX-Intraday-Live'가 평일 09:05~15:25 15분마다 pythonw.exe로 실행 - 창 안 뜸, 2026-10-15).
live_intraday.py로 docs/live/intraday.json을 만들고 그 파일 하나만 커밋·푸시한다(다른 변경 파일은 건드리지 않음).
장이 열리지 않은 날(주말·휴장)이나 시세 시각이 오늘이 아니면 아무것도 올리지 않는다.
"""
import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from keep_awake import keep_awake  # noqa: E402

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REL = "docs/live/intraday.json"
LOG = os.path.join(BASE, "logs", f"{datetime.now():%Y-%m-%d}-intraday.log")


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%H:%M:%S}] {msg}\n")


def git(*args):
    # pythonw.exe(창 없음)로 돌므로 git도 콘솔 창을 띄우지 않게 한다
    return subprocess.run(["git", *args], cwd=BASE, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main():
    keep_awake()
    now = datetime.now()
    if now.weekday() >= 5:
        log("주말 - 건너뜀"); return
    try:
        import live_intraday
        out = live_intraday.build()
    except Exception as e:  # noqa: BLE001
        log(f"계산 실패: {e}"); return
    if out.get("date") != now.strftime("%Y-%m-%d"):
        log(f"시세 날짜 {out.get('date')} != 오늘 - 휴장으로 보고 올리지 않음"); return
    p = out["phase"]
    log(f"{out['ts']} 점수 {p['raw']} 꺾임 {p['caution']} 목표 {p['target']}% | 코스피 {out['index']['KOSPI']} 코스닥 {out['index']['KOSDAQ']}")
    for attempt in range(4):
        r1 = git("add", REL)
        if r1.returncode != 0:
            log(f"git add 실패({r1.stderr.strip()[:200]}) - 재시도"); time.sleep(3); continue
        r2 = git("commit", "-m", f"장중 실시간 갱신 {now:%Y-%m-%d %H:%M}", "--", REL)
        if r2.returncode != 0 and "nothing to commit" not in (r2.stdout + r2.stderr):
            log(f"git commit 실패({(r2.stdout + r2.stderr).strip()[:200]})"); time.sleep(3); continue
        r3 = git("push", "-q")
        if r3.returncode == 0:
            log("푸시 완료"); return
        log(f"push 실패({r3.stderr.strip()[:200]}) - pull --rebase 후 재시도")
        git("pull", "--rebase", "--autostash", "-q")
        time.sleep(3)
    log("푸시 최종 실패")


if __name__ == "__main__":
    main()
