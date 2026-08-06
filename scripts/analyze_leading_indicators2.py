"""
1차 스캔(analyze_leading_indicators.py)에서 대부분의 지표가 '레벨'로는 코스피
시가총액과 그냥 동행(coincident)한다는 걸 확인했다 (예: 예탁금, CMA잔고, 신용
융자 잔고는 시장이 커지면 같이 커지는 게 당연함). 레벨이 아니라 '변화 속도'
(모멘텀)가 진짜 위험 신호일 수 있으므로 이번엔:
  - 레벨 z-score (60일 롤링)
  - 20일 변화율(모멘텀)의 z-score
두 가지 피처 버전을 만들고, 향후 5/10/20/40거래일 코스피 수익률과의 상관계수를
전부 계산해서 어느 조합이 가장 일관되게(여러 forward window에서 동일한 부호로,
유의하게) 코스피 하락을 선행하는지 찾는다.
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

FWD_WINDOWS = [5, 10, 20, 40]
ZWIN = 60
MOM_WIN = 20


def zscore(s, win):
    return (s - s.rolling(win, min_periods=win // 3).mean()) / s.rolling(win, min_periods=win // 3).std()


def main():
    df = pd.read_csv(MERGED_PATH, parse_dates=["date"])
    krx = pd.read_csv("data/krx_raw.csv", parse_dates=["date"]).dropna(subset=["kospi_close"])
    trading_dates = set(krx["date"])
    df = df[df["date"].isin(trading_dates)].sort_values("date").reset_index(drop=True)

    for w in FWD_WINDOWS:
        df[f"fwd_ret_{w}"] = df["kospi_close"].shift(-w) / df["kospi_close"] - 1

    rows = []
    for col in CANDIDATES:
        if col not in df.columns:
            continue
        level_z = zscore(df[col], ZWIN)
        mom = df[col].pct_change(MOM_WIN)
        mom_z = zscore(mom, ZWIN)

        for feat_name, feat in [("level_z", level_z), ("momentum20d_z", mom_z)]:
            for w in FWD_WINDOWS:
                sub = pd.DataFrame({"f": feat, "fwd": df[f"fwd_ret_{w}"]}).dropna()
                if len(sub) < 60:
                    continue
                r, p = pearsonr(sub["f"], sub["fwd"])
                rows.append({
                    "indicator": col, "feature": feat_name, "fwd_days": w,
                    "n": len(sub), "corr": round(r, 3), "p": round(p, 4),
                })

    res = pd.DataFrame(rows)
    # 여러 forward window에서 얼마나 일관되게(부호 같고 유의) 나오는지로 정리
    summary = (
        res[res["p"] < 0.05]
        .groupby(["indicator", "feature"])
        .agg(n_sig_windows=("fwd_days", "count"), avg_corr=("corr", "mean"))
        .reset_index()
        .sort_values(["n_sig_windows", "avg_corr"])
    )
    pd.set_option("display.width", 140)
    print("=== 유의(p<0.05)한 forward window 개수 기준 요약 (음수=하락 선행 신호) ===")
    print(summary.to_string(index=False))
    print("\n=== 전체 상세 ===")
    print(res.sort_values(["indicator", "feature", "fwd_days"]).to_string(index=False))
    res.to_csv("leading_indicator_scan2.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
