"""
탐색용 스크립트(대시보드에는 포함 안 됨): merged.csv의 각 지표가 코스피보다
'먼저' 움직이는지(선행성)를 과거 전체 기간에 대해 검증한다.

방법: 각 후보 지표 X에 대해, X(t)의 60일 롤링 z-score와 코스피의 향후 N거래일
수익률(fwd_ret_N) 사이의 피어슨 상관계수를 구한다. 상관계수가 유의하게
음수면 "X가 오르면/커지면 앞으로 N일간 코스피가 빠질 가능성이 크다"는 뜻이고,
반대로 X가 코스피와 동행/후행하는지도 구분하기 위해 X(t)와 kospi_close(t)의
동시 상관도 같이 본다 (동행성이 너무 높으면 선행지표가 아니라 그냥 코스피
자체의 다른 표현일 수 있음).

6월말 급락 한 사건에만 맞추면 과적합이므로, 전체 기간 상관계수로 먼저 후보를
추리고 마지막에 6월 급락 구간에서 실제로 신호가 떴는지 별도로 검증한다.
"""
import pandas as pd
import numpy as np
from scipy.stats import pearsonr

MERGED_PATH = "data/merged.csv"

CANDIDATES = [
    "kospi_turnover_ratio", "m2_to_marketcap_ratio",
    "credit_loan_kospi", "credit_loan_kosdaq",
    "credit_short_kospi", "credit_short_kosdaq",
    "margin_liquidation_ratio", "margin_call_liquidation", "margin_call_unpaid",
    "deposit_minus_rp", "dry_powder", "investor_deposit", "broker_rp_balance",
    "foreign_net_kospi", "foreign_net_kosdaq",
    "inst_net_kospi", "inst_net_kosdaq",
    "indiv_net_kospi", "indiv_net_kosdaq",
    "leading_index_cycle", "btc_market_cap_usd",
    "us_m2_yoy", "korea_m2_yoy", "mmf_total",
    "bok_total_assets", "msb_balance", "rp_sale_balance",
    "export_amount_daily_avg", "cma_balance",
]

FWD_DAYS = 20  # 향후 20거래일(약 1개월) 수익률
ZWIN = 60


def main():
    df = pd.read_csv(MERGED_PATH, parse_dates=["date"])
    krx = pd.read_csv("data/krx_raw.csv", parse_dates=["date"]).dropna(subset=["kospi_close"])
    trading_dates = set(krx["date"])
    df = df[df["date"].isin(trading_dates)].sort_values("date").reset_index(drop=True)

    df["fwd_ret"] = df["kospi_close"].shift(-FWD_DAYS) / df["kospi_close"] - 1

    results = []
    for col in CANDIDATES:
        if col not in df.columns:
            continue
        sub = df[["date", col, "kospi_close", "fwd_ret"]].dropna()
        if len(sub) < 80:
            continue
        z = (sub[col] - sub[col].rolling(ZWIN, min_periods=20).mean()) / sub[col].rolling(ZWIN, min_periods=20).std()
        valid = z.notna() & sub["fwd_ret"].notna()
        if valid.sum() < 60:
            continue
        r_fwd, p_fwd = pearsonr(z[valid], sub.loc[valid, "fwd_ret"])
        r_coincident, p_co = pearsonr(sub.loc[valid, col], sub.loc[valid, "kospi_close"])
        results.append({
            "indicator": col, "n": int(valid.sum()),
            "corr_vs_fwd20d_return": round(r_fwd, 3), "p_value": round(p_fwd, 4),
            "coincident_corr_with_kospi_level": round(r_coincident, 3),
        })

    res_df = pd.DataFrame(results).sort_values("corr_vs_fwd20d_return")
    pd.set_option("display.width", 140)
    print(res_df.to_string(index=False))
    res_df.to_csv("leading_indicator_scan.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
