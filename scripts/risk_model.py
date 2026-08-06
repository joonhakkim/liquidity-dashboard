"""
코스피 하락 예측 '위험지수' 모델 (build_risk_index.py의 탐색적 분석에서 나온
결론을 프로덕션에서도 그대로 쓰기 위해 분리한 공용 모듈).

analyze_leading_indicators.py / analyze_leading_indicators2.py로 2021~2026년
데이터 전체를 스캔해서, 코스피 시가총액과 그냥 동행하는 지표(예탁금, CMA잔고
등 레벨 지표 대부분)를 제외하고 실제로 향후 수익률을 유의하게 선행 예측하는
지표 2개를 추렸다:

  1. m2_to_marketcap_ratio 레벨의 60일 z-score
     - M2/코스피시가총액 비율이 (평소보다) 낮음 = 유동성 대비 밸류에이션 과열
       → 향후 수익률에 유의한 음의 영향 (전체 기간 5~40거래일 뒤 수익률 전부
       p<0.01)
  2. credit_loan_kospi(코스피 신용거래융자 잔고)의 20일 변화율(모멘텀)의
     60일 z-score
     - 빚투 잔고가 20일 만에 급격히 늘어나는 속도 → 20/40거래일 뒤 수익률에
       강한 음의 영향(p<0.001)

두 피처를 표준화해 향후 20거래일 코스피 수익률에 대해 OLS(Newey-West HAC
표준오차)로 가중치를 추정하고, 예측값의 부호를 뒤집어 과거 분포 기준
0~100 백분위로 스케일링한 게 위험지수(risk_index)다. 높을수록 "이 조합이
나타났을 때 과거엔 실제로 한 달 안에 코스피가 빠지는 경우가 많았다"는 뜻.

한계: 처음엔 credit_loan_kospi 자료가 있는 기간(2024.8~)으로 표본이 약 450
거래일뿐이었는데, R²=0.156으로 꽤 높게 나왔었다. 이후 KRX 시가총액과 KOFIA
신용융자 원본 데이터를 2020년까지로 백필해서 표본을 1560거래일로 4배 가까이
늘려 재검증한 결과, 두 계수 모두 여전히 유의(p<0.05)하지만 R²는 0.046으로
크게 낮아졌다 - 즉 신호 자체는 실재하지만, 짧은 표본에서 나온 첫 추정은
과대평가였다는 뜻. 이게 표본이 짧을 때 흔히 생기는 착시라서, 데이터가
쌓일수록 다시 재적합해서 확인하는 게 중요하다.
"""
import numpy as np
import pandas as pd
import statsmodels.api as sm

FEATURES = {
    "m2_to_marketcap_ratio": ("level", 60),
    "credit_loan_kospi": ("momentum", 20),
}
FWD_DAYS = 20
ZWIN = 60
THRESHOLD_WARN = 80
THRESHOLD_DANGER = 95


def zscore(s, win):
    return (s - s.rolling(win, min_periods=win // 3).mean()) / s.rolling(win, min_periods=win // 3).std()


def build_features(df):
    feat_cols = []
    for col, (kind, win) in FEATURES.items():
        if kind == "level":
            f = zscore(df[col], win)
        else:
            f = zscore(df[col].pct_change(win), ZWIN)
        fname = f"z_{col}"
        df[fname] = f
        feat_cols.append(fname)
    return df, feat_cols


def signal_for(v):
    if pd.isna(v):
        return None
    if v >= THRESHOLD_DANGER:
        return "위험"
    if v >= THRESHOLD_WARN:
        return "경고"
    return "안전"


def fit_and_score(merged_daily, trading_dates):
    """merged_daily: build_merged()이 만든 일별(ffill) 데이터프레임 전체.
    trading_dates: 실제 코스피 거래일(주말/공휴일 제외) 집합.

    z-score/모멘텀의 60일·20일 창은 반드시 '거래일 기준'으로 계산해야 한다
    (주말까지 포함된 달력일 기준으로 rolling을 돌리면 60개 창에 실제로는
    거래일이 42개 정도만 들어가서 창 길이가 왜곡된다). 그래서 먼저 거래일만
    추려서 피처와 모델을 만들고, 마지막에 전체 달력일 프레임에 date로 붙여서
    (거래일이 아닌 날은 ffill) 반환한다.
    """
    trading = merged_daily[merged_daily["date"].isin(trading_dates)].sort_values("date").reset_index(drop=True)
    trading, feat_cols = build_features(trading)
    trading["fwd_ret_20"] = trading["kospi_close"].shift(-FWD_DAYS) / trading["kospi_close"] - 1

    fit_df = trading.dropna(subset=feat_cols + ["fwd_ret_20"])
    full_dates = merged_daily[["date"]].copy()
    if len(fit_df) < 60:
        full_dates["predicted_fwd_ret_20"] = np.nan
        full_dates["risk_index"] = np.nan
        full_dates["signal"] = None
        return full_dates, None

    X = sm.add_constant(fit_df[feat_cols])
    y = fit_df["fwd_ret_20"]
    model = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": FWD_DAYS})

    predict_mask = trading[feat_cols].notna().all(axis=1)
    X_all = sm.add_constant(trading.loc[predict_mask, feat_cols], has_constant="add")
    trading.loc[predict_mask, "predicted_fwd_ret_20"] = model.predict(X_all)

    risk_raw = -trading["predicted_fwd_ret_20"]
    trading["risk_index"] = risk_raw.rank(pct=True) * 100
    trading["signal"] = trading["risk_index"].apply(signal_for)

    out = full_dates.merge(
        trading[["date", "predicted_fwd_ret_20", "risk_index", "signal"]], on="date", how="left"
    ).sort_values("date")
    out[["predicted_fwd_ret_20", "risk_index"]] = out[["predicted_fwd_ret_20", "risk_index"]].ffill()
    out["signal"] = out["signal"].ffill()
    return out.reset_index(drop=True), model
