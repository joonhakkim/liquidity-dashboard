"""
통합차트에서 백분위로 표시하는 지표들(신용융자·예탁금-RP·실탄합계·투자자예탁금)에 대해,
"몇 년 치 데이터를 기준으로 백분위를 매겨야 코스피 급락 예측력이 가장 좋은가"를 탐색해서
data/percentile_windows.json 에 저장한다. build_dashboard.py가 이 파일을 읽어서 지표별
최적 롤링 기간을 쓴다.

데이터가 누적될수록(특히 급락 이벤트가 새로 생길 때마다) 최적 기간이 바뀔 수 있어서,
run_pipeline.py에 편입해 매일 자동으로 재탐색하게 한다 - "지속적으로 재평가"를 코드로 구현.
"""
import json
import os

import numpy as np
import pandas as pd

from crash_detection import detect_crashes

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MERGED_PATH = os.path.join(DATA_DIR, "merged.csv")
OUT_PATH = os.path.join(DATA_DIR, "percentile_windows.json")

# (컬럼명, 이미 찾아둔 선행일수) - build_dashboard.py의 MASTER_SERIES와 맞춰둔다.
PERCENTILE_INDICATORS = [
    ("credit_loan_total", 252),
    ("deposit_minus_rp", 252),
    ("dry_powder", 252),
    ("investor_deposit", 252),
]

# 테스트할 롤링 윈도우(거래일 기준): 6개월 ~ 16년. EXPANDING은 "전체 누적 기간"(고정폭 없이
# 시작일부터 계속 늘어나는 창) - 데이터가 쌓일수록 백분위 기준 자체가 넓어지는 방식.
EXPANDING = "expanding"
WINDOW_CANDIDATES = [126, 189, 252, 378, 504, 630, 756, 1008, 1260, 1512, 2016, 2520, 3024, 4032, EXPANDING]
FUTURE_WINDOW = 126
MIN_OBS = 300


def future_min_drawdown_label(kospi):
    kospi = kospi.copy()
    roll_max = kospi["kospi_close"].cummax()
    kospi["drawdown"] = kospi["kospi_close"] / roll_max - 1
    dd = kospi["drawdown"].values
    n = len(kospi)
    kospi["future_min_dd"] = [dd[i:min(n, i + FUTURE_WINDOW)].min() for i in range(n)]
    return kospi.set_index("date")["future_min_dd"]


def main():
    df = pd.read_csv(MERGED_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    kospi = df[["date", "kospi_close"]].dropna().reset_index(drop=True)

    crashes = detect_crashes(kospi)
    print(f"감지된 -15%+ 급락 구간: {len(crashes)}개")
    for peak_d, peak_p, trough_d, trough_p, dd in crashes:
        print(f"  고점 {pd.Timestamp(peak_d).date()} -> 저점 {pd.Timestamp(trough_d).date()}  낙폭 {dd:.1%}")

    label = future_min_drawdown_label(kospi)
    dfi = df.set_index("date")

    results = {}
    for col, lead in PERCENTILE_INDICATORS:
        if col not in dfi.columns:
            continue
        s = dfi[col]
        best = None
        for window in WINDOW_CANDIDATES:
            if window == EXPANDING:
                pct = s.expanding(min_periods=250).apply(lambda x: (x.iloc[-1] > x).mean() * 100, raw=False)
            else:
                pct = s.rolling(window, min_periods=min(250, window)).apply(
                    lambda x: (x.iloc[-1] > x).mean() * 100, raw=False
                )
            joined = pd.concat([pct.shift(lead), label], axis=1).dropna()
            if len(joined) < MIN_OBS:
                continue
            corr = joined.iloc[:, 0].corr(joined.iloc[:, 1])
            if pd.isna(corr):
                continue
            if best is None or abs(corr) > abs(best["corr"]):
                best = {"window_days": window, "corr": round(float(corr), 3), "n": len(joined)}
        if best:
            results[col] = best
            window_desc = "전체기간(확장)" if best["window_days"] == EXPANDING else f"{best['window_days']}일(~{best['window_days']/252:.1f}년)"
            print(f"{col}: 최적 윈도우 {window_desc}, corr={best['corr']}")
        else:
            print(f"{col}: 유효한 윈도우를 못 찾음 (데이터 부족)")

    results["_meta"] = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "n_crash_events": len(crashes),
        "future_window_days": FUTURE_WINDOW,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n저장 완료: {OUT_PATH}")


if __name__ == "__main__":
    main()
