"""
코스피 -15%+ 급락(고점->저점) 구간을 전부 찾는 zigzag 기반 탐지 로직.
build_dashboard.py(차트 세로선 표시)와 analysis_leading_indicators.py /
optimize_percentile_window.py(선행지표 상관관계 분석)가 공용으로 쓴다.

이전 버전(단순 롤링고점 대비 낙폭)은 "이전 전고점을 다시 넘어야만" 다음 급락을 인식해서,
하락장 안에서 저점 대비 추가로 15%+ 빠지는 구간(예: 2024-08 발작적 하락)을 놓쳤다.
zigzag는 저점에서 +REBOUND_THRESHOLD 이상 반등하면 그 하락 구간을 종료하고 새 고점 추적을
시작하므로, 큰 하락장 안에 여러 개의 -15%+ 구간이 있어도 다 잡는다.
"""
import pandas as pd

CRASH_DRAWDOWN_THRESHOLD = -0.15
REBOUND_THRESHOLD = 0.10  # 저점 대비 이만큼 반등하면 그 하락 구간을 "끝"으로 보고 새 고점 탐색 재개


def detect_crashes(kospi_df):
    """kospi_df: date, kospi_close 컬럼. 반환: [(peak_date, peak_price, trough_date, trough_price, drawdown), ...]"""
    kospi = kospi_df[["date", "kospi_close"]].dropna().sort_values("date")
    if kospi.empty:
        return []

    state = "up"
    extreme_price, extreme_date = None, None
    pending_peak = None
    crashes = []

    for price, date in zip(kospi["kospi_close"], kospi["date"]):
        if extreme_price is None:
            extreme_price, extreme_date = price, date
            continue
        if state == "up":
            if price >= extreme_price:
                extreme_price, extreme_date = price, date
            else:
                dd = price / extreme_price - 1
                if dd <= CRASH_DRAWDOWN_THRESHOLD:
                    pending_peak = (extreme_date, extreme_price)
                    state = "down"
                    extreme_price, extreme_date = price, date
        else:  # down
            if price <= extreme_price:
                extreme_price, extreme_date = price, date
            else:
                rebound = price / extreme_price - 1
                if rebound >= REBOUND_THRESHOLD:
                    crashes.append((pending_peak[0], pending_peak[1], extreme_date, extreme_price, extreme_price / pending_peak[1] - 1))
                    pending_peak = None
                    state = "up"
                    extreme_price, extreme_date = price, date

    if state == "down" and pending_peak:
        crashes.append((pending_peak[0], pending_peak[1], extreme_date, extreme_price, extreme_price / pending_peak[1] - 1))

    return crashes


def detect_crash_start_dates(kospi_df):
    """차트 세로선 표시용 - 고점(=급락 시작) 날짜 문자열 리스트만."""
    return sorted(set(pd.Timestamp(c[0]).strftime("%Y-%m-%d") for c in detect_crashes(kospi_df)))
