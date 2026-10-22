"""
바이오 텔레그램 다이제스트 실행기 - 작업 스케줄러 'Bio-TG-Digest'가 평일 08:00~18:00 2시간마다 pythonw.exe로 실행(창 안 뜸).
2026-10-22 사용자 "2시간 단위로 해볼래". 수집 -> 다이제스트 -> @Troyinvestbot으로 보냄. 로그는 logs/YYYY-MM-DD-bio-tg.log.
"""
import contextlib
import os
import sys
import traceback
from datetime import datetime

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(BASE, "logs", f"{datetime.now():%Y-%m-%d}-bio-tg.log")


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f, contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
        print(f"\n===== {datetime.now():%H:%M:%S} 시작")
        if datetime.now().weekday() >= 5:
            print("주말 - 건너뜀"); return
        try:
            import bio_tg_digest as b
            parts = b.digest()   # 2026-10-22 사용자 요청으로 주가 동향만 보내므로 채널 수집(b.collect)은 생략
            b.send(parts)
        except Exception:
            traceback.print_exc()
        print(f"===== {datetime.now():%H:%M:%S} 끝")


if __name__ == "__main__":
    main()
