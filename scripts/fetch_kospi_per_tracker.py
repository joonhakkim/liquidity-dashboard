"""코스피 선행 PER 트래커 - 실행마다 data/kospi_per_tracker.csv 에 한 행씩 누적한다. 방법론은 per_tracker_common.py 참고."""
from per_tracker_common import run_tracker

STK_BYDD_TRD_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"

if __name__ == "__main__":
    run_tracker(STK_BYDD_TRD_URL, "KOSPI", 50, "kospi_per_tracker.csv", "코스피")
