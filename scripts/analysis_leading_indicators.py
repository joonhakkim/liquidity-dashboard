"""
유동성 지표 중 코스피 급락 선행지표로 쓸만한 것을 찾는 분석(2026-08 실행, 1회성 조사용 - 파이프라인에
자동으로 물리지 않는다). 결과는 build_dashboard.py의 MASTER_SERIES(통합차트 전용)를 고르는 데 썼다.

방법:
1) 코스피가 롤링 고점 대비 -15% 이상 빠지기 시작한 시점을 "붕괴 시작"으로 감지.
2) 후보 유동성 지표들을 level/MoM/QoQ/YoY로 변환하고, 21~252거래일 선행폭으로 밀어서
   "향후 126거래일 내 최대낙폭"과의 상관관계를 계산.
3) 지표별 최고 상관관계(어떤 변환·선행폭에서 가장 셌는지)를 정리.

한계: 이 데이터셋에서 감지된 -15%+ 붕괴는 4건뿐이고(2020-01, 2021-07, 2026-02, 2026-06),
뒤 2건은 미래 합성구간이라 신뢰도가 낮다. 표본이 작아 "확정된 법칙"이 아니라 "이 데이터에서
방향성이 일관됐던 지표" 정도로만 취급해야 한다.
"""
import numpy as np
import pandas as pd

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
]
TRANSFORMS = {
    "level": lambda s: s,
    "MoM": lambda s: s.pct_change(21),
    "QoQ": lambda s: s.pct_change(63),
    "YoY": lambda s: s.pct_change(252),
}
LEADS = [21, 42, 63, 90, 126, 189, 252]
FUTURE_WINDOW = 126
CRASH_DRAWDOWN_THRESHOLD = -0.15


def detect_crash_starts(kospi):
    kospi = kospi.copy()
    kospi["roll_max"] = kospi["kospi_close"].cummax()
    kospi["drawdown"] = kospi["kospi_close"] / kospi["roll_max"] - 1
    starts, in_crash, peak_price, peak_date = [], False, None, None
    for _, row in kospi.iterrows():
        if row["kospi_close"] >= (peak_price or -np.inf):
            peak_price, peak_date = row["kospi_close"], row["date"]
            in_crash = False
        if row["drawdown"] <= CRASH_DRAWDOWN_THRESHOLD and not in_crash:
            starts.append(peak_date)
            in_crash = True
    return sorted(set(starts)), kospi


def main():
    df = pd.read_csv(MERGED_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    candidates = [c for c in CANDIDATES if c in df.columns]

    kospi = df[["date", "kospi_close"]].dropna().reset_index(drop=True)
    crash_starts, kospi = detect_crash_starts(kospi)
    print(f"감지된 급락({CRASH_DRAWDOWN_THRESHOLD:.0%}+ 낙폭) 시작점: {len(crash_starts)}개")
    for d in crash_starts:
        print(" ", d.date())

    dd_vals = kospi["drawdown"].values
    n = len(kospi)
    kospi["future_min_dd"] = [
        dd_vals[i:min(n, i + FUTURE_WINDOW)].min() if i < n else np.nan for i in range(n)
    ]
    label = kospi.set_index("date")["future_min_dd"]

    results = []
    dfi = df.set_index("date")
    for col in candidates:
        s = dfi[col]
        if s.notna().sum() < 500:
            continue
        for tname, tfunc in TRANSFORMS.items():
            ts = tfunc(s).replace([np.inf, -np.inf], np.nan)
            for lead in LEADS:
                joined = pd.concat([ts.shift(lead), label], axis=1).dropna()
                if len(joined) < 300:
                    continue
                corr = joined.iloc[:, 0].corr(joined.iloc[:, 1])
                if pd.isna(corr):
                    continue
                results.append({"indicator": col, "transform": tname, "lead_days": lead, "corr": corr, "n": len(joined)})

    res_df = pd.DataFrame(results)
    res_df["abs_corr"] = res_df["corr"].abs()
    best = res_df.sort_values("abs_corr", ascending=False).groupby("indicator").first().sort_values("abs_corr", ascending=False)
    pd.set_option("display.width", 160)
    print("\n=== 지표별 최고 상관관계(선행 N일 앞선 지표 변화 vs 향후126일 최대낙폭) ===")
    print(best[["transform", "lead_days", "corr", "n"]].round(3))


if __name__ == "__main__":
    main()
