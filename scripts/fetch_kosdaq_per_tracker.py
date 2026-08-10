"""코스닥 선행 PER 트래커 - 실행마다 data/kosdaq_per_tracker.csv 에 한 행씩 누적한다. 방법론은 per_tracker_common.py 참고."""
from per_tracker_common import run_tracker

KSQ_BYDD_TRD_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"

if __name__ == "__main__":
    run_tracker(KSQ_BYDD_TRD_URL, "KOSDAQ", 50, "kosdaq_per_tracker.csv", "코스닥")
