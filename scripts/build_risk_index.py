"""
risk_model.py에 있는 위험지수 모델을 실제로 학습시켜서 회귀 결과와 6월 급락
구간 백테스트를 콘솔에 출력하는 연구용 스크립트. build_dashboard.py도 내부적으로
risk_model.fit_and_score()를 그대로 호출하므로, 여기서 보는 수치가 대시보드에
실제로 반영되는 수치와 항상 일치한다 (모델이 두 군데서 따로 관리되며 어긋나는
걸 막기 위해 fit_and_score 하나로 통일).

실행: python scripts/build_risk_index.py
"""
import pandas as pd
import statsmodels.api as sm

from risk_model import FEATURES, FWD_DAYS, build_features, fit_and_score

MERGED_PATH = "data/merged.csv"
KRX_PATH = "data/krx_raw.csv"


def main():
    merged = pd.read_csv(MERGED_PATH, parse_dates=["date"])
    krx = pd.read_csv(KRX_PATH, parse_dates=["date"])
    trading_dates = set(krx["date"])

    # 회귀 요약(계수/유의성)을 보여주기 위해 fit_and_score와 동일한 방식으로 한 번 더 학습
    trading = merged[merged["date"].isin(trading_dates)].sort_values("date").reset_index(drop=True)
    trading, feat_cols = build_features(trading)
    trading["fwd_ret_20"] = trading["kospi_close"].shift(-FWD_DAYS) / trading["kospi_close"] - 1
    fit_df = trading.dropna(subset=feat_cols + ["fwd_ret_20"])
    print(f"회귀 학습 표본 수: {len(fit_df)} (기간: {fit_df['date'].min().date()} ~ {fit_df['date'].max().date()})")

    X = sm.add_constant(fit_df[feat_cols])
    y = fit_df["fwd_ret_20"]
    model = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": FWD_DAYS})
    print("\n=== 회귀 결과 (종속변수: 향후 20거래일 코스피 수익률, Newey-West HAC 표준오차) ===")
    print(model.summary())

    score_df, _ = fit_and_score(merged, trading_dates)
    df = merged[["date", "kospi_close"]].merge(score_df, on="date", how="left")

    print("\n=== 6월 급락 구간 백테스트 (2026-06-01 ~ 2026-07-15) ===")
    crash_window = df[(df["date"] >= "2026-06-01") & (df["date"] <= "2026-07-15") & df["date"].isin(trading_dates)]
    print(crash_window[["date", "kospi_close", "risk_index", "signal"]].to_string(index=False))

    print("\n=== 신호별 실제 향후 20일 수익률 분포 (신호의 실전 의미 검증) ===")
    scored_trading_days = score_df[score_df["date"].isin(trading_dates)][["date", "signal"]]
    valid = trading[["date", "fwd_ret_20"]].merge(scored_trading_days, on="date", how="inner").dropna(subset=["signal", "fwd_ret_20"])
    print(valid.groupby("signal")["fwd_ret_20"].describe().to_string())

    print("\n=== 최근 10거래일 ===")
    recent_trading_dates = sorted(trading_dates)[-10:]
    print(df[df["date"].isin(recent_trading_dates)][["date", "kospi_close", "risk_index", "signal"]].to_string(index=False))


if __name__ == "__main__":
    main()
