"""
홈페이지 요약 텔레그램 실행기 - 작업 스케줄러 'Homepage-TG'가 평일 11·13·15·17·19시에 pythonw.exe로 실행(창 안 뜸).
2026-10-22 사용자 "2시간 간격으로 1) 섹터별 수익률 2) 섹터 4사분면 3) 19시 이후에는 시장 국면".
19시 이후 실행은 섹터 + 시장 국면, 그 전에는 섹터만. 휴장일(장중 데이터가 오늘 날짜가 아님)은 건너뜀. 로그 logs/YYYY-MM-DD-homepage-tg.log.
"""
import contextlib
import os
import sys
import traceback
from datetime import datetime

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(BASE, "logs", f"{datetime.now():%Y-%m-%d}-homepage-tg.log")


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f, contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
        now = datetime.now()
        print(f"\n===== {now:%H:%M:%S} 시작")
        if now.weekday() >= 5:
            print("주말 - 건너뜀"); return
        try:
            import homepage_tg as h
            h.run("all" if now.hour >= 19 else "sector")
        except Exception:
            traceback.print_exc()
        print(f"===== {datetime.now():%H:%M:%S} 끝")


if __name__ == "__main__":
    main()
