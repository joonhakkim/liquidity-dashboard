"""
백분위 하나로는 안 맞는 지표가 많다는 문제의식으로, 유동성 지표들에 더 다양한 변환을 적용해서
코스피 급락(zigzag 6개 구간, crash_detection.py) 예측력을 비교한다.

기존(level/MoM/QoQ/YoY/percentile)에 추가한 변환:
- zscore: (현재값 - 롤링평균) / 롤링표준편차 - 이동평균 대비 몇 표준편차 벗어났는지
- ma_gap: 현재값 / 롤링이동평균 - 1 (%) - 이동평균 대비 괴리율
- pct_of_yoy: YoY 변화율 자체의 롤링 백분위 - "그 지표의 변화 속도가 역사적으로 얼마나 빠른가"
- accel: YoY 변화율의 변화(2차 미분류) - 증가/감소 속도가 가속/감속하는지
각 변환 x 여러 윈도우(126~1512거래일) x 여러 lead(21~252일)를 다 테스트해서 최고 조합을 찾는다.
"""
import numpy as np
import pandas as pd

from crash_detection import detect_crashes

MERGED_PATH = "../data/merged.csv"

CANDIDATES = [
    "net_liquidity", "korea_m2_yoy", "us_m2_yoy", "m2", "bok_total_assets", "msb_balance",
    "rp_sale_balance", "mmf", "dry_powder", "investor_deposit", "cma_balance",
    "deriv_deposit", "broker_rp_balance", "margin_call_unpaid", "margin_call_liquidation",
    "margin_liquidation_ratio", "credit_card_loan_demand", "credit_loan_total",
    "credit_loan_kospi", "credit_loan_kosdaq", "credit_short_total", "leading_index_yoy",
    "export_amount_daily_avg", "us_real_rate_10y", "m2_to_marketcap_ratio",
    "kospi_turnover_ratio", "foreign_net_total", "indiv_net_total", "inst_net_total",
    "deposit_minus_rp", "usd_krw", "copper_usd",
    "ccsi", "esi", "bsi_all_industry", "news_sentiment_index", "us_net_liquidity_bil",
]
WINDOWS = [126, 252, 504, 756, 1260]
LEADS = [21, 42, 63, 90, 126, 189, 252]
FUTURE_WINDOW = 126
MIN_OBS = 300


def make_transforms(s):
    out = {"level": s, "MoM": s.pct_change(21), "QoQ": s.pct_change(63), "YoY": s.pct_change(252)}
    yoy = s.pct_change(252)
    for w in WINDOWS:
        out[f"pctile_{w}"] = s.rolling(w, min_periods=min(250, w)).apply(lambda x: (x.iloc[-1] > x).mean() * 100, raw=False)
        out[f"zscore_{w}"] = (s - s.rolling(w, min_periods=min(60, w)).mean()) / s.rolling(w, min_periods=min(60, w)).std()
        out[f"ma_gap_{w}"] = s / s.rolling(w, min_periods=min(60, w)).mean() - 1
        out[f"pct_of_yoy_{w}"] = yoy.rolling(w, min_periods=min(250, w)).apply(lambda x: (x.iloc[-1] > x).mean() * 100, raw=False)
    out["accel"] = yoy.diff(63)
    return out


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
    print(f"급락 구간 {len(crashes)}개 기준 분석")

    label = future_min_drawdown_label(kospi)
    dfi = df.set_index("date")

    results = []
    for col in CANDIDATES:
        if col not in dfi.columns or dfi[col].notna().sum() < 500:
            continue
        transforms = make_transforms(dfi[col])
        for tname, ts in transforms.items():
            ts = ts.replace([np.inf, -np.inf], np.nan)
            for lead in LEADS:
                joined = pd.concat([ts.shift(lead), label], axis=1).dropna()
                if len(joined) < MIN_OBS:
                    continue
                corr = joined.iloc[:, 0].corr(joined.iloc[:, 1])
                if pd.isna(corr):
                    continue
                results.append({"indicator": col, "transform": tname, "lead": lead, "corr": corr, "n": len(joined)})

    res = pd.DataFrame(results)
    res["abs_corr"] = res["corr"].abs()
    best = res.sort_values("abs_corr", ascending=False).groupby("indicator").first().sort_values("abs_corr", ascending=False)
    pd.set_option("display.width", 160)
    print("\n=== 지표별 최고 상관관계 (전체 변환 후보 포함) ===")
    print(best[["transform", "lead", "corr", "n"]].round(3).head(20))


if __name__ == "__main__":
    main()
