"""
data/ecos_raw.csv, data/krx_raw.csv, data/kofia_raw.csv 를 날짜 기준으로 병합해
data/merged.csv 를 만들고, Chart.js 기반 정적 HTML 대시보드를 docs/index.html 로 생성한다
(GitHub Pages가 /docs 폴더를 소스로 쓰는 관례에 맞춤).

패널 구성:
  1. 수급주체            - 투자자별(개인/외국인/기관/기타법인) 순매매대금
  2. 유동성지표          - 한국은행 총자산 / 통안증권잔액 / RP매각잔고 / M2 (각각 별도 차트)
  3. 실탄게이지           - 투자자예탁금 + CMA잔고 (KOFIA) [계산식 표시]
  4. 예수금·신용거래 현황 - 파생상품거래예수금/RP매도잔고/미수금/반대매매 (KOFIA)
  6. M2 통화공급(한국·미국) - 한국 ECOS M2 vs 미국 FRED M2SL

ECOS는 월별이라 일별 그래프에서 자연스럽게 이어지도록 forward-fill해서
merged.csv에 daily 인덱스로 저장한다 (원본 raw csv들은 그대로 둠).
전체 기간 데이터를 클라이언트로 다 보내고, 화면엔 기본으로 최근 구간
(RECENT_WINDOW_DAYS)만 보여준다 - 전체를 한번에 그리면 최근 몇 달이 20년치
축에 눌려서 안 보이기 때문. 프리셋 버튼(1개월~전체) 또는 날짜 직접 입력으로
과거 임의 구간을 볼 수 있다.
"""
import json
import os
from datetime import datetime

import pandas as pd

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(BASE_DIR, "data")
DIST_DIR = os.path.join(BASE_DIR, "docs")  # GitHub Pages 관례상 /docs 폴더를 소스로 씀

ECOS_PATH = os.path.join(DATA_DIR, "ecos_raw.csv")
KRX_PATH = os.path.join(DATA_DIR, "krx_raw.csv")
KOFIA_PATH = os.path.join(DATA_DIR, "kofia_raw.csv")
FRED_PATH = os.path.join(DATA_DIR, "fred_raw.csv")
BITCOIN_PATH = os.path.join(DATA_DIR, "bitcoin_raw.csv")
INVESTOR_FLOW_PATH = os.path.join(DATA_DIR, "investor_flow_raw.csv")
MARKETS_PATH = os.path.join(DATA_DIR, "markets_raw.csv")
NEWS_SENTIMENT_PATH = os.path.join(DATA_DIR, "news_sentiment_raw.csv")
MERGED_PATH = os.path.join(DATA_DIR, "merged.csv")
DASHBOARD_PATH = os.path.join(DIST_DIR, "liquidity.html")

RECENT_WINDOW_DAYS = 1825  # 대시보드 기본 표시 구간 (~5년)


def load_csv(path):
    if os.path.exists(path):
        return pd.read_csv(path, parse_dates=["date"])
    return pd.DataFrame(columns=["date"])


def build_merged():
    ecos = load_csv(ECOS_PATH)
    krx = load_csv(KRX_PATH)
    kofia = load_csv(KOFIA_PATH)
    fred = load_csv(FRED_PATH)
    bitcoin = load_csv(BITCOIN_PATH)
    investor_flow = load_csv(INVESTOR_FLOW_PATH)
    markets = load_csv(MARKETS_PATH)
    news_sentiment = load_csv(NEWS_SENTIMENT_PATH)  # 이미 일별이라 ffill 대상 외 별도 처리 불필요

    # M2 YoY(전년동월대비 %)는 각자 월별 원본에서 12개월 shift로 계산한다
    # (일별로 ffill된 시계열에서 12"일" shift하면 틀리므로 ffill 전에 계산).
    if not ecos.empty and "m2" in ecos.columns:
        ecos = ecos.sort_values("date").reset_index(drop=True)
        ecos["korea_m2_yoy"] = ecos["m2"] / ecos["m2"].shift(12) * 100 - 100
    if not fred.empty and "us_m2" in fred.columns:
        fred = fred.sort_values("date").reset_index(drop=True)
        # fred_raw.csv에 us_real_rate_10y(일별)가 섞여 있어 fred 전체가 일별 그리드가
        # 됐다. us_m2(월별) 값이 있는 행만 뽑아 12개월 shift로 계산한 뒤 다시 합친다.
        m2_monthly = fred.loc[fred["us_m2"].notna(), ["date", "us_m2"]].sort_values("date").reset_index(drop=True)
        m2_monthly["us_m2_yoy"] = m2_monthly["us_m2"] / m2_monthly["us_m2"].shift(12) * 100 - 100
        fred = fred.merge(m2_monthly[["date", "us_m2_yoy"]], on="date", how="left")

    all_dates = pd.concat(
        [ecos["date"], krx["date"], kofia["date"], fred["date"], bitcoin["date"], investor_flow["date"], markets["date"], news_sentiment["date"]],
        ignore_index=True,
    ).dropna()
    if all_dates.empty:
        raise SystemExit("병합할 데이터가 없습니다. fetch_*.py 스크립트를 먼저 실행하세요.")

    # ffill 전, 컬럼별 실제 마지막 관측일을 따로 기록해둔다 (대시보드의 "최신" 배지가
    # ffill로 늘어난 날짜가 아니라 진짜 관측된 마지막 날짜를 보여주도록).
    raw_latest = {}
    for df in (ecos, krx, kofia, fred, bitcoin, investor_flow, markets, news_sentiment):
        if df.empty:
            continue
        for col in df.columns:
            if col == "date":
                continue
            sub = df.dropna(subset=[col])
            if not sub.empty:
                raw_latest[col] = sub["date"].max()

    full_range = pd.date_range(all_dates.min(), all_dates.max(), freq="D")
    merged = pd.DataFrame({"date": full_range})

    for df in (ecos, krx, kofia, fred, bitcoin, investor_flow, markets, news_sentiment):
        if df.empty or len(df.columns) <= 1:
            continue
        df = df.drop_duplicates(subset="date").sort_values("date")
        df = df.set_index("date").reindex(full_range).ffill().reset_index()
        df = df.rename(columns={"index": "date"})
        merged = merged.merge(df, on="date", how="left")

    if {"investor_deposit", "cma_balance"}.issubset(merged.columns):
        merged["dry_powder"] = merged["investor_deposit"] + merged["cma_balance"]
        raw_latest["dry_powder"] = min(raw_latest.get("investor_deposit", pd.Timestamp.min),
                                        raw_latest.get("cma_balance", pd.Timestamp.min))

    if {"investor_deposit", "broker_rp_balance"}.issubset(merged.columns):
        merged["deposit_minus_rp"] = merged["investor_deposit"] - merged["broker_rp_balance"]
        if "investor_deposit" in raw_latest and "broker_rp_balance" in raw_latest:
            raw_latest["deposit_minus_rp"] = min(raw_latest["investor_deposit"], raw_latest["broker_rp_balance"])

    if {"kospi_trading_value", "kospi_market_cap"}.issubset(merged.columns):
        # kospi_trading_value 단위는 백만원(네이버), kospi_market_cap 단위는 원(KRX Open API) - 단위 맞춰서 계산
        merged["kospi_turnover_ratio"] = (merged["kospi_trading_value"] * 1_000_000) / merged["kospi_market_cap"] * 100
        if "kospi_trading_value" in raw_latest and "kospi_market_cap" in raw_latest:
            raw_latest["kospi_turnover_ratio"] = min(raw_latest["kospi_trading_value"], raw_latest["kospi_market_cap"])

    if {"m2", "kospi_market_cap"}.issubset(merged.columns):
        # m2 단위는 십억원(ECOS), kospi_market_cap 단위는 원(KRX Open API)
        merged["m2_to_marketcap_ratio"] = (merged["m2"] * 1_000_000_000) / merged["kospi_market_cap"] * 100
        if "m2" in raw_latest and "kospi_market_cap" in raw_latest:
            raw_latest["m2_to_marketcap_ratio"] = min(raw_latest["m2"], raw_latest["kospi_market_cap"])

    if "kospi_close" in merged.columns:
        # 코스피 YoY(%) = (오늘 종가 / 365일 전 종가 - 1) x 100. merged는 1일 간격 daily grid라
        # 365행 shift가 정확히 1년 전과 대응한다.
        merged["kospi_yoy"] = merged["kospi_close"] / merged["kospi_close"].shift(365) * 100 - 100
        if "kospi_close" in raw_latest:
            raw_latest["kospi_yoy"] = raw_latest["kospi_close"]

    if {"fed_total_assets", "us_treasury_tga", "us_reverse_repo"}.issubset(merged.columns):
        # 미국 순유동성(Fed Net Liquidity) = 연준 총자산 - TGA - ON RRP.
        # WALCL/WTREGEN은 백만달러, RRPONTSYD는 십억달러 단위라 RRP에 x1000해서 맞춘다.
        merged["us_net_liquidity"] = (
            merged["fed_total_assets"] - merged["us_treasury_tga"] - merged["us_reverse_repo"] * 1000
        )
        merged["us_net_liquidity_bil"] = merged["us_net_liquidity"] / 1000  # 백만달러 -> 십억달러(가독성)
        merged["fed_total_assets_bil"] = merged["fed_total_assets"] / 1000
        merged["us_treasury_tga_bil"] = merged["us_treasury_tga"] / 1000
        keys = ("fed_total_assets", "us_treasury_tga", "us_reverse_repo")
        if all(k in raw_latest for k in keys):
            raw_latest["us_net_liquidity"] = min(raw_latest[k] for k in keys)
            raw_latest["us_net_liquidity_bil"] = raw_latest["us_net_liquidity"]
            raw_latest["fed_total_assets_bil"] = raw_latest["fed_total_assets"]
            raw_latest["us_treasury_tga_bil"] = raw_latest["us_treasury_tga"]

    os.makedirs(DATA_DIR, exist_ok=True)
    merged.to_csv(MERGED_PATH, index=False, encoding="utf-8-sig")
    print(f"병합 완료: {MERGED_PATH} ({len(merged)}행, {merged['date'].min().date()} ~ {merged['date'].max().date()})")
    return merged, raw_latest


def col_or_none(df, col):
    return df[col].where(pd.notna(df[col]), None).tolist() if col in df.columns else [None] * len(df)


def has_data(df, col):
    return bool(col in df.columns and df[col].notna().any())


def latest_date_str(raw_latest, cols):
    dates = [raw_latest[c] for c in cols if c in raw_latest]
    if not dates:
        return None
    return max(dates).strftime("%Y-%m-%d")


PANEL_SOURCES = {
    "수급주체": "데이터 터미널에서 받은 수급정리 엑셀(data/manual/수급정리*.xlsm, 종목별 개인/기관/외국인 순매수)을 KRX Open API 상장종목 목록으로 코스피/코스닥 분류해 합산 (파일은 사용자가 매일 직접 갱신)",
    "코스피 선행지수 vs YoY": "선행종합지수 YoY: 한국은행 ECOS(901Y067/I16A 원지수, 국가데이터처 작성, 월간) 전년동월대비(%) / 코스피 YoY: 네이버 금융 코스피 종가 기준 전년동일대비(%)",
    "수출금액 (일간)": "관세청 통관기준 수출금액(한국은행 ECOS 901Y118/T002, 월간 합계) ÷ 조업일수. 조업일수는 산업통상부 방식(평일 1일 + 토요일 0.5일 + 공휴일·일요일 0일)으로 자체 계산",
    "수출금액 YoY vs 코스피": "위 수출금액(일간) 패널과 동일 소스, 전년동월대비(%)로 변환해 코스피와 겹쳐본 것",
    "뉴스심리지수 vs 코스피": "한국은행 ECOS 521Y001(뉴스심리지수, 실험적 통계, 일별) - 뉴스 텍스트 감성분석 기반, 100=중립, 100 초과면 평소보다 긍정적 톤",
    "소비자심리지수(CCSI) vs 코스피": "한국은행 ECOS 511Y002(소비자동향조사, 월간) - 소비자심리지수(CCSI), 100=중립",
    "경제심리지수(ESI) vs 코스피": "한국은행 ECOS 513Y001(경제심리지수, 원계열, 월간) - 기업+소비자 심리 종합, 100=중립",
    "전산업 업황BSI vs 코스피": "한국은행 ECOS 512Y013(기업경기조사-실적, 월간) - 전산업 업황실적BSI, 100=중립",
    "미국 10년물 실질금리": "FRED(세인트루이스 연은) DFII10 - Market Yield on U.S. Treasury Securities at 10-Year Constant Maturity, Quoted on an Investment Basis, Inflation-Indexed (일별)",
    "미국 유동성 지수 vs 코스피": "FRED(세인트루이스 연은) WALCL(연준 총자산, 주간) - WTREGEN(재무부 TGA, 주간) - RRPONTSYD(익일역레포, 일별) x 1000. MacroMicro 'Fed Net Liquidity'와 동일 정의",
    "미국 연준 유동성 구성요소": "FRED WALCL(연준 총자산) / WTREGEN(재무부 일반계정 TGA) / RRPONTSYD(익일역레포 ON RRP) - 전부 십억달러, 순유동성 = 총자산-TGA-RRP",
    "연준 총자산 vs 코스피": "FRED WALCL(연준 총자산, 주간, 십억달러)",
    "재무부 TGA vs 코스피": "FRED WTREGEN(재무부 일반계정 TGA, 주간, 십억달러) - 늘면 시중에서 자금을 흡수(유동성 축소), 줄면 반대(유동성 공급)",
    "익일역레포(ON RRP) vs 코스피": "FRED RRPONTSYD(익일역레포, 일별, 십억달러) - 늘면 자금이 연준에 묶임(유동성 축소), 줄면 시중에 풀림(유동성 공급)",
    "환율·귀금속·구리": "원/달러·금·은: 네이버 금융(marketindex, 일별) / 구리: FRED PCOPPUSDM(IMF 발표, 월간, 네이버엔 구리 시세 없어 대체)",
    "유동성지표": "한국은행 ECOS Open API - 한국은행 주요계정(103Y002), M2(161Y008)",
    "실탄게이지": "KOFIA FreeSIS Open API(자동 수집) 기반 계산",
    "예수금·신용거래 현황": "KOFIA FreeSIS (meta/getMetaDataList.do, 자동 수집)",
    "신용거래융자 · MMF 현황": "KOFIA FreeSIS (meta/getMetaDataList.do, 자동 수집)",
    "M2 통화공급 (한국·미국)": "한국: ECOS Open API(161Y008) / 미국: FRED M2SL (둘 다 월별, 단위 다름 - 각각 자체 축)",
    "시가총액 회전율 · M2 비율": "코스피 거래대금: 네이버 금융 / 코스피 시가총액: KRX Open API(stk_bydd_trd, MKTCAP 합산) / M2: ECOS",
    "신용카드 대출수요 vs 코스피": "코스피: 네이버 금융 / 대출수요: 한국은행 ECOS 대출행태서베이(514Y003, 신용카드회사, 분기)",
    "비트코인 시가총액 vs 코스피": "비트코인: CoinGecko (무료 API, 최근 365일만 제공) / 코스피: 네이버 금융",
}

PANEL_FORMULAS = {
    "실탄게이지": "계산식: 실탄(dry powder) = 투자자예탁금 + CMA잔고",
    "시가총액 회전율 · M2 비율": "계산식: 회전율(%) = 코스피 거래대금 / 코스피 상장시가총액 × 100  |  M2/시가총액(%) = 한국 M2 / 코스피 상장시가총액 × 100",
    "M2 통화공급 (한국·미국)": "계산식: M2 YoY(%) = (M2(t) / M2(t-12개월) - 1) × 100",
}


def trim_to_data(df, cols):
    """cols 중 하나라도 값이 있는 첫 날짜 ~ 마지막 날짜로 잘라낸다 (앞뒤 빈 구간 제거)."""
    cols_present = [c for c in cols if c in df.columns]
    if not cols_present:
        return df.iloc[0:0]
    mask = df[cols_present].notna().any(axis=1)
    if not mask.any():
        return df.iloc[0:0]
    idx = mask[mask].index
    return df.loc[idx.min():idx.max()]


def trim_to_common_data(df, cols):
    """cols가 전부(교집합) 값을 갖는 구간으로 잘라낸다 - 두 지표를 겹쳐볼 때
    한쪽 데이터가 짧으면(예: 비트코인 1년치) 그 시작 시점부터만 비교하도록."""
    cols_present = [c for c in cols if c in df.columns]
    if len(cols_present) < len(cols):
        return df.iloc[0:0]
    mask = df[cols_present].notna().all(axis=1)
    if not mask.any():
        return df.iloc[0:0]
    idx = mask[mask].index
    return df.loc[idx.min():idx.max()]


def build_panel(merged, window_df, panel_type, series_map):
    cols = list(series_map.values())
    trimmed = trim_to_data(window_df, cols)
    dates = trimmed["date"].dt.strftime("%Y-%m-%d").tolist()
    return {
        "type": panel_type,
        "labels": dates,
        "series": {name: col_or_none(trimmed, col) for name, col in series_map.items()},
        "has_data": any(has_data(trimmed, c) for c in cols),
    }


def build_dual_panel(merged, window_df, left_map, right_map):
    """왼쪽/오른쪽 축이 다른 두 지표를 한 차트에 겹쳐 그린다 (예: 코스피 vs 분기 지수).
    두 지표가 모두 존재하는 교집합 구간만 보여준다 (한쪽이 짧으면 그 시작점부터)."""
    cols = list(left_map.values()) + list(right_map.values())
    trimmed = trim_to_common_data(window_df, cols)
    dates = trimmed["date"].dt.strftime("%Y-%m-%d").tolist()
    return {
        "type": "dual",
        "labels": dates,
        "left": {name: col_or_none(trimmed, col) for name, col in left_map.items()},
        "right": {name: col_or_none(trimmed, col) for name, col in right_map.items()},
        "has_data": any(has_data(trimmed, c) for c in cols),
    }


MASTER_SERIES = [
    # 코스피 -15%+ 급락 4개 구간(2020-01/2021-07/2026-02/2026-06, 뒤 2개는 미래 합성구간이라
    # 신뢰도 낮음) 이전 데이터로 level/MoM/QoQ/YoY x 21~252일 선행폭을 다 테스트해서, "향후 126일
    # 최대낙폭"과의 상관관계가 뚜렷했던 지표만 남겼다(2026-08 분석, scratchpad/leading_indicator_analysis.py).
    # 표본이 4개뿐이라 확정 법칙은 아니고 "이 데이터에서 방향성이 일관된 지표" 정도로 취급할 것.
    # 개별 패널(수급주체/유동성지표/실탄게이지 등)은 이 리스트와 무관하게 그대로 유지된다.
    # 네번째 값 = analysis_leading_indicators.py에서 찾은 최적 선행일수. 그래프에 그릴 때 이
    # 일수만큼 시계열을 앞으로 밀어서(shift) 표시한다 - 각 지표가 자기 축으로 자동 스케일링돼서
    # "다 똑같이 시작하는 것처럼" 보이는 착시를 없애고, 실제로 급락 시점과 시간축이 맞는지
    # (선행성이 진짜인지) 눈으로 검증할 수 있게 하기 위함.
    # 다섯번째 값(transform) = None(원값) / "percentile"(0~100, 역대 상위 몇%) / "zscore"(이동평균
    # 대비 표준편차 단위 괴리) / "ma_gap"(이동평균 대비 괴리율 %). 레벨 자체가 추세적으로 계속
    # 우상향하는 지표(신용융자·예탁금류)를 원값 그대로 그리면 "그냥 계속 오르는 선"으로만 보여
    # 위험 구간 판단이 안 돼서 percentile로, YoY/금리처럼 원래도 오르내리지만 최적 변환이 따로
    # 있는 지표는 analysis_transform_search.py 탐색 결과로 zscore/ma_gap을 골랐다.
    # 여섯번째 값(선택) = zscore/ma_gap 계산용 롤링 윈도우를 기본값(TRANSFORM_WINDOW=756=3년) 대신
    # 다른 값으로 쓰고 싶을 때만 지정. None이면 기본값 사용.
    ("코스피 종가", "kospi_close", True, 0, None, None),
    ("신용거래융자 백분위(252일 선행정렬)", "credit_loan_total", False, 252, "percentile", None),  # corr -0.73(level)/-0.54(pct)
    ("미국 M2 YoY z-score(252일 선행정렬)", "us_m2_yoy", False, 252, "zscore", None),  # corr 0.70(zscore_756) > 0.68(level)
    ("예탁금-RP실탄 백분위(252일 선행정렬)", "deposit_minus_rp", False, 252, "percentile", None),  # corr -0.61(level)
    ("M2/시총%(252일 선행정렬)", "m2_to_marketcap_ratio", False, 252, None, None),  # corr 0.54
    ("한국 M2 YoY 이평괴리율(42일 선행정렬)", "korea_m2_yoy", False, 42, "ma_gap", None),  # corr 0.51(ma_gap_756) > 0.41(level)
    ("실탄합계 백분위(252일 선행정렬)", "dry_powder", False, 252, "percentile", None),  # corr -0.47(level, 표본 늘면 불안정)
    ("투자자예탁금 백분위(252일 선행정렬)", "investor_deposit", False, 252, "percentile", None),  # corr -0.55(level)
    ("미국 10년물 실질금리 z-score(21일 선행정렬)", "us_real_rate_10y", False, 21, "zscore", None),  # corr -0.65(zscore_756) > -0.48(level)
    ("신용카드 대출수요BSI(126일 선행정렬)", "credit_card_loan_demand", False, 126, None, None),  # corr -0.31(QoQ 기준, 표시는 level)
    ("소비자심리지수 이평괴리율(252일 선행정렬)", "ccsi", False, 252, "ma_gap", 1260),  # corr -0.60(ma_gap_1260)
    ("경제심리지수(252일 선행정렬)", "esi", False, 252, None, None),  # corr -0.59(level)
    ("전산업 업황BSI(252일 선행정렬)", "bsi_all_industry", False, 252, None, None),  # corr -0.50(level)
    ("뉴스심리지수 이평괴리율(21일 선행정렬)", "news_sentiment_index", False, 21, "ma_gap", None),  # corr 0.48(ma_gap_756)
]
TRANSFORM_WINDOW = 756  # zscore/ma_gap 계산용 이동평균·표준편차 창(3년)

# 위 MASTER_SERIES는 분석해서 "선행성이 뚜렷했던" 것만 고른 큐레이션 목록이다.
# 아래는 사용자가 직접 눈으로 다 훑어보고 싶다고 해서, 지금까지 조사한 후보 지표 전부에
# level/WoW/MoM/QoQ/YoY 다섯 변환을 기계적으로 다 만들어 통합차트에 추가한다(전부 기본
# 숨김 - 범례에서 원하는 것만 켜서 봄). 선행폭 정렬(shift)은 안 하니 순수 원시 비교용.
BULK_CANDIDATES = [
    ("net_liquidity", "순유동성"), ("korea_m2_yoy", "한국 M2 YoY"), ("us_m2_yoy", "미국 M2 YoY"),
    ("m2", "한국 M2"), ("bok_total_assets", "한국은행 총자산"), ("msb_balance", "통안증권잔액"),
    ("rp_sale_balance", "RP매각잔고"), ("mmf", "MMF"), ("dry_powder", "실탄합계"),
    ("investor_deposit", "투자자예탁금"), ("cma_balance", "CMA잔고"), ("deriv_deposit", "파생상품거래예수금"),
    ("broker_rp_balance", "대고객RP매도잔고"), ("margin_call_unpaid", "위탁매매미수금"),
    ("margin_call_liquidation", "반대매매금액"), ("margin_liquidation_ratio", "반대매매비중"),
    ("credit_card_loan_demand", "신용카드대출수요BSI"), ("credit_loan_total", "신용거래융자합계"),
    ("credit_loan_kospi", "신용거래융자(코스피)"), ("credit_loan_kosdaq", "신용거래융자(코스닥)"),
    ("credit_short_total", "신용대주잔고"), ("leading_index_yoy", "선행종합지수YoY"),
    ("export_amount_daily_avg", "일평균수출금액"), ("us_real_rate_10y", "미국10년실질금리"),
    ("m2_to_marketcap_ratio", "M2/코스피시총비율"), ("kospi_turnover_ratio", "코스피회전율"),
    ("foreign_net_total", "외국인순매수"), ("indiv_net_total", "개인순매수"), ("inst_net_total", "기관순매수"),
    ("deposit_minus_rp", "예탁금-RP"), ("usd_krw", "원달러환율"), ("copper_usd", "구리가격"),
    ("ccsi", "소비자심리지수"), ("esi", "경제심리지수"), ("bsi_all_industry", "전산업업황BSI"),
    ("news_sentiment_index", "뉴스심리지수"), ("us_net_liquidity_bil", "미국유동성지수"),
    ("fed_total_assets_bil", "연준총자산"), ("us_treasury_tga_bil", "재무부TGA"), ("us_reverse_repo", "ON RRP"),
]
BULK_TRANSFORMS = [
    ("level", "원값", lambda s: s),
    ("wow", "WoW", lambda s: s.pct_change(5)),
    ("mom", "MoM", lambda s: s.pct_change(21)),
    ("qoq", "QoQ", lambda s: s.pct_change(63)),
    ("yoy", "YoY", lambda s: s.pct_change(252)),
]

# 이미 YoY·비율·%로 변환된 컬럼("한국 M2 YoY"의 QoQ 같은 "변화율의 변화율"은 의미가 불분명함) -
# 이런 건 원값(level)만 남기고 WoW/MoM/QoQ/YoY 재변환은 안 한다.
BULK_ALREADY_RATE_COLS = {
    "korea_m2_yoy", "us_m2_yoy", "margin_liquidation_ratio", "leading_index_yoy",
    "m2_to_marketcap_ratio", "kospi_turnover_ratio", "us_real_rate_10y",
}


def build_bulk_items(window_df):
    items = []
    for col, kor_label in BULK_CANDIDATES:
        if col not in window_df.columns or not has_data(window_df, col):
            continue
        transforms = BULK_TRANSFORMS[:1] if col in BULK_ALREADY_RATE_COLS else BULK_TRANSFORMS
        for tkey, tlabel, tfunc in transforms:
            series = tfunc(window_df[col]).replace([float("inf"), float("-inf")], None)
            if not series.notna().any():
                continue
            items.append({
                "label": f"[전체후보] {kor_label} {tlabel}",
                "column": f"{col}_{tkey}",
                "data": [None if pd.isna(v) else round(float(v), 4) for v in series],
                "default_visible": False,
            })
    return items

PERCENTILE_WINDOW_DEFAULT = 756  # 3년(거래일) - optimize_percentile_window.py가 종목별 최적값을
# data/percentile_windows.json 에 저장하면 그쪽을 우선 쓰고, 없는 지표는 이 기본값을 쓴다.
PERCENTILE_WINDOWS_PATH = os.path.join(DATA_DIR, "percentile_windows.json")


def load_percentile_windows():
    if os.path.exists(PERCENTILE_WINDOWS_PATH):
        with open(PERCENTILE_WINDOWS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def detect_crash_starts(merged):
    """코스피 -15%+ 급락 구간(고점일) 리스트. 통합차트에 세로선으로 표시해서, MASTER_SERIES로
    고른 지표들이 실제로 그 시점 이전에 먼저 움직였는지 눈으로 확인할 수 있게 한다.
    (crash_detection.py의 zigzag 탐지 - 하락장 안의 저점 대비 추가 급락도 놓치지 않는다.)"""
    from crash_detection import detect_crash_start_dates
    return detect_crash_start_dates(merged)


def build_combined(merged, window_df):
    items = []
    percentile_windows = load_percentile_windows()
    for label, col, default_visible, lead_days, transform, transform_window in MASTER_SERIES:
        if col not in window_df.columns or not has_data(window_df, col):
            continue
        series = window_df[col]
        window_n = transform_window or TRANSFORM_WINDOW
        if transform == "percentile":
            window = percentile_windows.get(col, {}).get("window_days", PERCENTILE_WINDOW_DEFAULT)
            if window == "expanding":
                series = series.expanding(min_periods=250).apply(lambda x: (x.iloc[-1] > x).mean() * 100, raw=False)
            else:
                series = series.rolling(window, min_periods=min(250, window)).apply(
                    lambda x: (x.iloc[-1] > x).mean() * 100, raw=False
                )
        elif transform == "zscore":
            roll = series.rolling(window_n, min_periods=60)
            series = (series - roll.mean()) / roll.std()
        elif transform == "ma_gap":
            series = series / series.rolling(window_n, min_periods=60).mean() - 1
        if lead_days:
            # shift(양수) = lead_days일 전 값을 오늘 자리로 당겨온다 -> 이 지표가 "예측하는"
            # 미래 시점과 같은 x좌표에 그려져서, 급락 세로선과 시간축이 맞는지 바로 비교 가능.
            series = series.shift(lead_days)
        items.append({
            "label": label,
            "column": col,
            "data": [None if pd.isna(v) else round(float(v), 4) for v in series],
            "default_visible": default_visible,
        })
    dates = window_df["date"].dt.strftime("%Y-%m-%d").tolist()
    crash_starts = [d for d in detect_crash_starts(merged) if d in set(dates)]
    items.extend(build_bulk_items(window_df))
    return {"labels": dates, "items": items, "crashStarts": crash_starts}


def build_dashboard(merged, raw_latest):
    # 예전엔 여기서 최근 RECENT_WINDOW_DAYS(5년)만 잘라서 클라이언트로 보냈는데,
    # 사용자가 임의 과거 구간을 직접 골라서 비교하고 싶다고 해서 전체 기간을 다
    # 보낸다. 초기 화면은 JS 쪽에서 기본값으로 최근 구간만 보여주고, 날짜 선택
    # UI로 그 이전 구간도 자유롭게 볼 수 있게 한다.
    recent = merged
    combined = build_combined(merged, recent)

    panels = {
        "수급주체": build_panel(merged, recent, "multi", {
            "개인(코스피)": "indiv_net_kospi", "개인(코스닥)": "indiv_net_kosdaq",
            "외국인(코스피)": "foreign_net_kospi", "외국인(코스닥)": "foreign_net_kosdaq",
            "기관(코스피)": "inst_net_kospi", "기관(코스닥)": "inst_net_kosdaq",
        }),
        "유동성지표": build_panel(merged, recent, "split", {
            "한국은행 총자산": "bok_total_assets", "통안증권잔액": "msb_balance",
            "RP매각잔고": "rp_sale_balance", "M2": "m2",
        }),
        "실탄게이지": build_panel(merged, recent, "split", {
            "실탄 합계(예탁금+CMA)": "dry_powder", "투자자예탁금": "investor_deposit", "CMA잔고": "cma_balance",
        }),
        "예수금·신용거래 현황": build_panel(merged, recent, "split", {
            "장내파생상품 거래예수금": "deriv_deposit",
            "대고객 RP매도잔고": "broker_rp_balance",
            "위탁매매 미수금": "margin_call_unpaid",
            "반대매매금액": "margin_call_liquidation",
            "반대매매비중(%)": "margin_liquidation_ratio",
        }),
        "M2 통화공급 (한국·미국)": build_panel(merged, recent, "split", {
            "한국 M2 (십억원)": "m2", "미국 M2 (십억달러, M2SL)": "us_m2",
            "한국 M2 YoY(%)": "korea_m2_yoy", "미국 M2 YoY(%)": "us_m2_yoy",
        }),
        "신용거래융자 · MMF 현황": build_panel(merged, recent, "split", {
            "신용거래융자(코스피)": "credit_loan_kospi", "신용거래융자(코스닥)": "credit_loan_kosdaq",
            "MMF 개인": "mmf_indiv", "MMF 법인": "mmf_corp",
        }),
        "시가총액 회전율 · M2 비율": build_panel(merged, recent, "split", {
            "코스피 회전율(%)": "kospi_turnover_ratio", "M2/코스피 시가총액(%)": "m2_to_marketcap_ratio",
        }),
        "신용카드 대출수요 vs 코스피": build_dual_panel(
            merged, recent,
            {"코스피 종가": "kospi_close"},
            {"신용카드 대출수요(BSI)": "credit_card_loan_demand"},
        ),
        "비트코인 시가총액 vs 코스피": build_dual_panel(
            merged, recent,
            {"코스피 종가": "kospi_close"},
            {"비트코인 시가총액(USD)": "btc_market_cap_usd"},
        ),
        "코스피 선행지수 vs YoY": build_dual_panel(
            merged, recent,
            {"코스피 YoY(%)": "kospi_yoy"},
            {"선행종합지수 YoY(%)": "leading_index_yoy"},
        ),
        "수출금액 (일간)": build_dual_panel(
            merged, recent,
            {"코스피 종가": "kospi_close"},
            {"수출금액(천불/일)": "export_amount_daily_avg"},
        ),
        "수출금액 YoY vs 코스피": build_dual_panel(
            merged, recent,
            {"코스피 종가": "kospi_close"},
            {"수출금액 YoY(%)": "export_amount_daily_avg_yoy"},
        ),
        "뉴스심리지수 vs 코스피": build_dual_panel(
            merged, recent,
            {"코스피 종가": "kospi_close"},
            {"뉴스심리지수": "news_sentiment_index"},
        ),
        "소비자심리지수(CCSI) vs 코스피": build_dual_panel(
            merged, recent,
            {"코스피 종가": "kospi_close"},
            {"소비자심리지수(CCSI)": "ccsi"},
        ),
        "경제심리지수(ESI) vs 코스피": build_dual_panel(
            merged, recent,
            {"코스피 종가": "kospi_close"},
            {"경제심리지수(ESI)": "esi"},
        ),
        "전산업 업황BSI vs 코스피": build_dual_panel(
            merged, recent,
            {"코스피 종가": "kospi_close"},
            {"전산업 업황실적BSI": "bsi_all_industry"},
        ),
        "미국 10년물 실질금리": build_panel(merged, recent, "multi", {
            "미국 10년물 실질금리(%)": "us_real_rate_10y",
        }),
        "미국 유동성 지수 vs 코스피": build_dual_panel(
            merged, recent,
            {"코스피 종가": "kospi_close"},
            {"미국 유동성 지수(십억달러)": "us_net_liquidity_bil"},
        ),
        "미국 연준 유동성 구성요소": build_panel(merged, recent, "split", {
            "연준 총자산(십억달러)": "fed_total_assets_bil",
            "재무부 TGA(십억달러)": "us_treasury_tga_bil",
            "익일역레포 ON RRP(십억달러)": "us_reverse_repo",
            "미국 유동성 지수(십억달러)": "us_net_liquidity_bil",
        }),
        "연준 총자산 vs 코스피": build_dual_panel(
            merged, recent,
            {"코스피 종가": "kospi_close"},
            {"연준 총자산(십억달러)": "fed_total_assets_bil"},
        ),
        "재무부 TGA vs 코스피": build_dual_panel(
            merged, recent,
            {"코스피 종가": "kospi_close"},
            {"재무부 TGA(십억달러)": "us_treasury_tga_bil"},
        ),
        "익일역레포(ON RRP) vs 코스피": build_dual_panel(
            merged, recent,
            {"코스피 종가": "kospi_close"},
            {"ON RRP(십억달러)": "us_reverse_repo"},
        ),
        "환율·귀금속·구리": build_panel(merged, recent, "split", {
            "원/달러 환율": "usd_krw",
            "금 가격(USD)": "gold_usd",
            "은 가격(USD)": "silver_usd",
            "구리 가격(USD/MT)": "copper_usd",
        }),
    }

    panels["수급주체"]["latest"] = latest_date_str(raw_latest, [
        "indiv_net_kospi", "indiv_net_kosdaq", "foreign_net_kospi", "foreign_net_kosdaq", "inst_net_kospi", "inst_net_kosdaq",
    ])
    panels["유동성지표"]["latest"] = latest_date_str(raw_latest, ["bok_total_assets", "m2"])
    panels["실탄게이지"]["latest"] = latest_date_str(raw_latest, ["investor_deposit", "cma_balance"])
    panels["M2 통화공급 (한국·미국)"]["latest"] = latest_date_str(raw_latest, ["m2", "us_m2"])
    panels["예수금·신용거래 현황"]["latest"] = latest_date_str(raw_latest, [
        "deriv_deposit", "broker_rp_balance", "margin_call_unpaid", "margin_call_liquidation", "margin_liquidation_ratio",
    ])
    panels["시가총액 회전율 · M2 비율"]["latest"] = latest_date_str(raw_latest, ["kospi_turnover_ratio", "m2_to_marketcap_ratio"])
    panels["신용거래융자 · MMF 현황"]["latest"] = latest_date_str(raw_latest, ["credit_loan_kospi", "credit_loan_kosdaq", "mmf_indiv", "mmf_corp"])
    panels["신용카드 대출수요 vs 코스피"]["latest"] = latest_date_str(raw_latest, ["kospi_close", "credit_card_loan_demand"])
    panels["비트코인 시가총액 vs 코스피"]["latest"] = latest_date_str(raw_latest, ["kospi_close", "btc_market_cap_usd"])
    panels["코스피 선행지수 vs YoY"]["latest"] = latest_date_str(raw_latest, ["kospi_yoy", "leading_index_yoy"])
    panels["수출금액 (일간)"]["latest"] = latest_date_str(raw_latest, ["kospi_close", "export_amount_daily_avg"])
    panels["수출금액 YoY vs 코스피"]["latest"] = latest_date_str(raw_latest, ["kospi_close", "export_amount_daily_avg_yoy"])
    panels["뉴스심리지수 vs 코스피"]["latest"] = latest_date_str(raw_latest, ["kospi_close", "news_sentiment_index"])
    panels["소비자심리지수(CCSI) vs 코스피"]["latest"] = latest_date_str(raw_latest, ["kospi_close", "ccsi"])
    panels["경제심리지수(ESI) vs 코스피"]["latest"] = latest_date_str(raw_latest, ["kospi_close", "esi"])
    panels["전산업 업황BSI vs 코스피"]["latest"] = latest_date_str(raw_latest, ["kospi_close", "bsi_all_industry"])
    panels["미국 10년물 실질금리"]["latest"] = latest_date_str(raw_latest, ["us_real_rate_10y"])
    panels["미국 유동성 지수 vs 코스피"]["latest"] = latest_date_str(raw_latest, ["kospi_close", "us_net_liquidity_bil"])
    panels["미국 연준 유동성 구성요소"]["latest"] = latest_date_str(raw_latest, ["fed_total_assets_bil", "us_treasury_tga_bil", "us_reverse_repo", "us_net_liquidity_bil"])
    panels["연준 총자산 vs 코스피"]["latest"] = latest_date_str(raw_latest, ["kospi_close", "fed_total_assets_bil"])
    panels["재무부 TGA vs 코스피"]["latest"] = latest_date_str(raw_latest, ["kospi_close", "us_treasury_tga_bil"])
    panels["익일역레포(ON RRP) vs 코스피"]["latest"] = latest_date_str(raw_latest, ["kospi_close", "us_reverse_repo"])
    panels["환율·귀금속·구리"]["latest"] = latest_date_str(raw_latest, ["usd_krw", "gold_usd", "silver_usd", "copper_usd"])

    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    panels_json = json.dumps(panels, ensure_ascii=False)
    sources_json = json.dumps(PANEL_SOURCES, ensure_ascii=False)
    formulas_json = json.dumps(PANEL_FORMULAS, ensure_ascii=False)
    combined_json = json.dumps(combined, ensure_ascii=False)

    html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>한국 증시 유동성 스크리닝 대시보드</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script><style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  h1 {{ font-size:20px; margin-bottom:4px; }}
  .updated {{ color:#9aa0a6; font-size:13px; margin-bottom:20px; }}
  .grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(440px, 1fr)); gap:20px; }}
  .card {{ background:#1a1d24; border-radius:12px; padding:16px; }}
  .card.wide {{ grid-column: 1 / -1; }}
  .card h2 {{ font-size:15px; margin:0 0 4px 0; display:inline-block; }}
  .latest {{ color:#63e6be; font-size:11px; float:right; }}
  .source {{ color:#7a8290; font-size:11px; margin-bottom:4px; clear:both; }}
  .formula {{ color:#ffa94d; font-size:11px; margin-bottom:10px; font-family:monospace; }}
  .nodata {{ color:#7a8290; font-size:13px; padding:40px 0; text-align:center; }}
  canvas {{ max-height:420px; }}
  .split-grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap:20px; }}
  .split-item h3 {{ font-size:13px; color:#c7cbd1; margin:6px 0 6px 0; font-weight:normal; }}
  .split-item .chart-canvas-wrap {{ height:280px; position:relative; }}
  .combined-canvas-wrap {{ height:420px; position:relative; }}
  .chart-canvas-wrap {{ height:380px; position:relative; }}
  .combined-hint {{ color:#7a8290; font-size:11px; margin-bottom:10px; }}
  .combined-legend {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:14px; }}
  .combined-legend-item {{ display:flex; align-items:center; gap:6px; background:#12141a; border:1px solid #2a2e37;
    border-radius:6px; padding:5px 10px; cursor:pointer; font-size:12px; color:#e6e6e6; user-select:none; }}
  .combined-legend-item:hover {{ border-color:#4dabf7; }}
  .combined-legend-item.inactive {{ opacity:0.35; }}
  .combined-legend-item .swatch {{ width:10px; height:10px; border-radius:2px; flex-shrink:0; }}
  .combined-legend-item .val.negative {{ color:#ff6b6b; font-weight:bold; }}
  .layout {{ display:flex; align-items:flex-start; gap:20px; }}
  .sidebar {{ width:220px; flex-shrink:0; position:sticky; top:24px; max-height:calc(100vh - 48px); overflow-y:auto; }}
  .nav-btn {{ display:block; width:100%; text-align:left; background:none; border:none; color:#9aa0a6;
    padding:10px 12px; border-radius:8px; cursor:pointer; font-size:13px; margin-bottom:2px; font-family:inherit; }}
  .nav-btn:hover {{ background:#1a1d24; color:#c7cbd1; }}
  .nav-btn.active {{ background:#1a1d24; color:#4dabf7; font-weight:bold; }}
  .content {{ flex:1; min-width:0; }}
  .section {{ display:none; }}
  .section.active {{ display:block; }}
  .range-bar {{ display:flex; gap:6px; margin-bottom:16px; flex-wrap:wrap; }}
  .range-btn {{ background:#1a1d24; border:1px solid #2a2e37; color:#9aa0a6; padding:6px 14px;
    border-radius:999px; cursor:pointer; font-size:12px; font-family:inherit; }}
  .range-btn:hover {{ color:#c7cbd1; border-color:#4dabf7; }}
  .range-btn.active {{ background:#4dabf7; color:#0f1115; border-color:#4dabf7; font-weight:bold; }}
  .custom-range-bar {{ display:flex; align-items:center; gap:10px; margin-bottom:20px; flex-wrap:wrap; font-size:13px; color:#9aa0a6; }}
  .custom-range-bar label {{ display:flex; align-items:center; gap:6px; }}
  .custom-range-bar input[type="date"] {{
    background:#1a1d24; border:1px solid #2a2e37; color:#e6e6e6; border-radius:6px; padding:5px 8px; font-size:13px;
  }}
  .custom-range-hint {{ color:#63e6be; }}
</style>
</head>
<body>
  <h1>한국 증시 유동성 스크리닝 대시보드</h1>
  <div class="updated">최종 갱신일시: {updated_at} (기본은 최근 구간만 표시 - 아래 버튼이나 날짜 직접 지정으로 과거 구간도 조절 가능) &middot; <a href="index.html" style="color:#4dabf7;">&larr; 홈</a> &middot; <a href="screening.html" style="color:#4dabf7;">주식 스크리닝 &rarr;</a></div>

  <div class="layout">
    <nav class="sidebar" id="sidebar"></nav>
    <main class="content">
      <div class="range-bar" id="rangeBar"></div>
      <div class="custom-range-bar">
        <label>시작 <input type="date" id="rangeStart"></label>
        <label>종료 <input type="date" id="rangeEnd"></label>
        <button id="rangeApplyBtn" class="range-btn">이 구간 적용</button>
        <span id="rangeCustomHint" class="custom-range-hint"></span>
      </div>
      <div id="sectionContainer"></div>
    </main>
  </div>

<script>
const PANELS = {panels_json};
const SOURCES = {sources_json};
const FORMULAS = {formulas_json};
const COMBINED = {combined_json};
const COLORS = ["#4dabf7","#f783ac","#69db7c","#ffa94d","#b197fc","#63e6be","#ff8787","#66d9e8","#eebefa","#a9e34b","#ffd43b","#e599f7"];

const content = document.getElementById('sectionContainer');
const sidebar = document.getElementById('sidebar');
const rangeBar = document.getElementById('rangeBar');
const rangeStartInput = document.getElementById('rangeStart');
const rangeEndInput = document.getElementById('rangeEnd');
const rangeCustomHint = document.getElementById('rangeCustomHint');
const sections = [];
const activeCharts = [];
// mode 'preset': 각 차트 자신의 최신일 기준 최근 N일. mode 'custom': 전 차트 공통의
// 절대 날짜 구간(YYYY-MM-DD) - 과거 특정 시기를 딱 잘라서 비교하고 싶을 때 씀.
let currentRange = {{ mode: 'preset', days: {RECENT_WINDOW_DAYS} }};

const RANGE_OPTIONS = [
  {{ label: '1개월', days: 30 }},
  {{ label: '3개월', days: 90 }},
  {{ label: '6개월', days: 180 }},
  {{ label: '1년', days: 365 }},
  {{ label: '3년', days: 1095 }},
  {{ label: '5년', days: {RECENT_WINDOW_DAYS} }},
  {{ label: '전체', days: null }},
];

function registerChart(chart) {{
  const fullLabels = chart.data.labels.slice();
  chart.data.datasets.forEach(ds => {{ ds._fullData = ds.data.slice(); }});
  const entry = {{ chart, fullLabels }};
  activeCharts.push(entry);
  applyCurrentRangeToEntry(entry);
}}

function lastNonNull(arr) {{
  for (let i = arr.length - 1; i >= 0; i--) {{
    if (arr[i] !== null && arr[i] !== undefined) return arr[i];
  }}
  return null;
}}

function formatLatestSuffix(v) {{
  if (v === null || v === undefined) return '';
  const formatted = Number(v).toLocaleString(undefined, {{ maximumFractionDigits: 1 }});
  return ' : ' + formatted;
}}

function computeRangeIndices(fullLabels, range) {{
  if (!fullLabels.length) return [0, -1];
  if (range.mode === 'custom') {{
    let startIdx = 0;
    if (range.start) {{
      const found = fullLabels.findIndex(d => d >= range.start);
      startIdx = found < 0 ? fullLabels.length : found;
    }}
    let endIdx = fullLabels.length - 1;
    if (range.end) {{
      endIdx = -1;
      for (let i = fullLabels.length - 1; i >= 0; i--) {{
        if (fullLabels[i] <= range.end) {{ endIdx = i; break; }}
      }}
    }}
    return [startIdx, endIdx];
  }}
  let startIdx = 0;
  if (range.days !== null) {{
    const cutoff = new Date(fullLabels[fullLabels.length - 1]);
    cutoff.setDate(cutoff.getDate() - range.days);
    const found = fullLabels.findIndex(d => new Date(d) >= cutoff);
    startIdx = found < 0 ? 0 : found;
  }}
  return [startIdx, fullLabels.length - 1];
}}

function applyCurrentRangeToEntry(entry) {{
  const {{ chart, fullLabels }} = entry;
  const [startIdx, endIdx] = computeRangeIndices(fullLabels, currentRange);
  chart.data.labels = fullLabels.slice(startIdx, endIdx + 1);
  chart.data.datasets.forEach(ds => {{ ds.data = ds._fullData.slice(startIdx, endIdx + 1); }});
  chart.update();
}}

function applyRange(days) {{
  currentRange = {{ mode: 'preset', days }};
  activeCharts.forEach(applyCurrentRangeToEntry);
  document.querySelectorAll('.range-btn').forEach(btn => {{
    btn.classList.toggle('active', btn.dataset.days === String(days));
  }});
  rangeStartInput.value = '';
  rangeEndInput.value = '';
  rangeCustomHint.textContent = '';
}}

function applyCustomRange() {{
  const start = rangeStartInput.value || null;
  const end = rangeEndInput.value || null;
  if (!start && !end) return;
  if (start && end && start > end) {{
    rangeCustomHint.textContent = '시작일이 종료일보다 늦습니다.';
    return;
  }}
  currentRange = {{ mode: 'custom', start, end }};
  activeCharts.forEach(applyCurrentRangeToEntry);
  document.querySelectorAll('.range-btn').forEach(btn => btn.classList.remove('active'));
  rangeCustomHint.textContent = `${{start || '처음'}} ~ ${{end || '최신'}} 구간 적용됨`;
}}

document.getElementById('rangeApplyBtn').onclick = applyCustomRange;
if (COMBINED.labels.length) {{
  const minD = COMBINED.labels[0], maxD = COMBINED.labels[COMBINED.labels.length - 1];
  [rangeStartInput, rangeEndInput].forEach(el => {{ el.min = minD; el.max = maxD; }});
}}

RANGE_OPTIONS.forEach(opt => {{
  const btn = document.createElement('button');
  btn.className = 'range-btn';
  btn.textContent = opt.label;
  btn.dataset.days = String(opt.days);
  btn.onclick = () => applyRange(opt.days);
  rangeBar.appendChild(btn);
}});
applyRange({RECENT_WINDOW_DAYS});

function addSection(id, title, buildFn) {{
  const section = document.createElement('div');
  section.className = 'section';
  section.id = 'section-' + id;
  content.appendChild(section);
  // 차트는 탭이 열릴 때 처음 한 번만 그린다 (숨겨진 상태에서 그리면 Chart.js가
  // 캔버스 크기를 0으로 잡아버려서 나중에 탭을 열어도 안 보이는 문제가 있음).
  sections.push({{ id, title, section, buildFn, built: false }});
}}

function activateSection(id) {{
  const target = sections.find(s => s.id === id);
  if (target && !target.built) {{
    target.buildFn(target.section);
    target.built = true;
  }}
  document.querySelectorAll('.section').forEach(el => el.classList.toggle('active', el.id === 'section-' + id));
  document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.id === id));
}}

function renderCombined(section) {{
  section.innerHTML = `<div class="card wide">
    <h2>통합 차트 (전체 지표 겹쳐보기)</h2>
    <div class="source">모든 지표를 한 차트에 모았습니다. 아래 범례를 클릭하면 해당 지표를 켜고 끌 수 있습니다.</div>
    <div class="combined-hint">지표마다 단위가 달라 각자 숨겨진 축(스케일)을 따로 씁니다 - 절대값보다는 시점/추세 비교용입니다.</div>
    <div class="combined-canvas-wrap"><canvas id="combinedChart"></canvas></div>
    <div class="combined-legend" id="combinedLegend"></div>
  </div>`;
  if (!COMBINED.items.length) return;
  const datasets = COMBINED.items.map((item, i) => ({{
    label: item.label,
    data: item.data,
    yAxisID: 'axis_' + i,
    borderColor: COLORS[i % COLORS.length],
    backgroundColor: COLORS[i % COLORS.length] + '33',
    spanGaps: true,
    pointRadius: 0,
    borderWidth: 1.5,
    hidden: !item.default_visible,
  }}));
  const scales = {{
    x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 10 }}, grid: {{ color:'#2a2e37' }} }},
  }};
  COMBINED.items.forEach((item, i) => {{
    scales['axis_' + i] = {{ display: false }};
  }});
  const chart = new Chart(document.getElementById('combinedChart'), {{
    type: 'line',
    data: {{ labels: COMBINED.labels, datasets }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      scales,
      plugins: {{ legend: {{ display: false }} }},
    }},
  }});
  registerChart(chart);

  // 캔버스 범례 대신 진짜 HTML 범례를 그린다 (항목이 많으면 캔버스 범례는
  // 글자가 겹쳐서 안 보이는 문제가 있었음). 클릭하면 해당 데이터셋을 토글.
  const legendEl = document.getElementById('combinedLegend');
  datasets.forEach((ds, i) => {{
    const v = lastNonNull(ds.data);
    const item = document.createElement('div');
    item.className = 'combined-legend-item' + (ds.hidden ? ' inactive' : '');
    const valClass = (v !== null && v < 0) ? 'val negative' : 'val';
    item.innerHTML = `<span class="swatch" style="background:${{ds.borderColor}}"></span>` +
                      `<span>${{ds.label}}</span><span class="${{valClass}}">${{formatLatestSuffix(v)}}</span>`;
    item.onclick = () => {{
      const nowVisible = chart.isDatasetVisible(i);
      chart.setDatasetVisibility(i, !nowVisible);
      chart.update();
      item.classList.toggle('inactive', nowVisible);
    }};
    legendEl.appendChild(item);
  }});
}}

function makeLineChart(canvas, labels, datasets) {{
  const chart = new Chart(canvas, {{
    type: 'line',
    data: {{ labels, datasets }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      scales: {{
        x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 6 }}, grid: {{ color:'#2a2e37' }} }},
        y: {{ ticks: {{ color: '#9aa0a6' }}, grid: {{ color:'#2a2e37' }} }},
      }},
      plugins: {{ legend: {{ labels: {{ color:'#e6e6e6', boxWidth: 12, font: {{ size: 11 }} }} }} }},
    }},
  }});
  registerChart(chart);
}}

function makeDualAxisChart(canvas, labels, leftSeries, rightSeries) {{
  const datasets = [
    ...Object.entries(leftSeries).map(([name, data], i) => ({{
      label: name, data, yAxisID: 'y', borderColor: COLORS[i % COLORS.length],
      backgroundColor: COLORS[i % COLORS.length] + '33',
      spanGaps: true, pointRadius: 0, borderWidth: 1.5,
    }})),
    ...Object.entries(rightSeries).map(([name, data], i) => ({{
      label: name, data, yAxisID: 'y1', borderColor: COLORS[(i + 3) % COLORS.length],
      backgroundColor: COLORS[(i + 3) % COLORS.length] + '33',
      spanGaps: true, pointRadius: 0, borderWidth: 1.5, borderDash: [5, 3],
    }})),
  ];
  const chart = new Chart(canvas, {{
    type: 'line',
    data: {{ labels, datasets }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      scales: {{
        x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 6 }}, grid: {{ color:'#2a2e37' }} }},
        y: {{ position: 'left', ticks: {{ color: '#9aa0a6' }}, grid: {{ color:'#2a2e37' }} }},
        y1: {{ position: 'right', ticks: {{ color: '#9aa0a6' }}, grid: {{ drawOnChartArea: false }} }},
      }},
      plugins: {{ legend: {{ labels: {{ color:'#e6e6e6', boxWidth: 12, font: {{ size: 11 }} }} }} }},
    }},
  }});
  registerChart(chart);
}}

function renderPanel(title, panel, section) {{
  const card = document.createElement('div');
  card.className = 'card wide';
  const source = SOURCES[title] || '';
  const formula = FORMULAS[title];
  const latest = panel.latest ? `<span class="latest">최신: ${{panel.latest}}</span>` : '';
  card.innerHTML = `<h2>${{title}}</h2>${{latest}}<div class="source">데이터 출처: ${{source}}</div>` +
                    (formula ? `<div class="formula">${{formula}}</div>` : '');
  section.appendChild(card);

  if (!panel.has_data) {{
    const div = document.createElement('div');
    div.className = 'nodata';
    div.textContent = '아직 수집된 데이터가 없습니다.';
    card.appendChild(div);
    return;
  }}

  if (panel.type === 'dual') {{
    const wrap = document.createElement('div');
    wrap.className = 'chart-canvas-wrap';
    const canvas = document.createElement('canvas');
    wrap.appendChild(canvas);
    card.appendChild(wrap);
    makeDualAxisChart(canvas, panel.labels, panel.left, panel.right);
    return;
  }}

  if (panel.type === 'split') {{
    const splitGrid = document.createElement('div');
    splitGrid.className = 'split-grid';
    Object.entries(panel.series).forEach(([name, data], i) => {{
      const item = document.createElement('div');
      item.className = 'split-item';
      item.innerHTML = `<h3>${{name}}</h3>`;
      const itemWrap = document.createElement('div');
      itemWrap.className = 'chart-canvas-wrap';
      const canvas = document.createElement('canvas');
      itemWrap.appendChild(canvas);
      item.appendChild(itemWrap);
      splitGrid.appendChild(item);
      makeLineChart(canvas, panel.labels, [{{
        label: name, data, borderColor: COLORS[i % COLORS.length],
        backgroundColor: COLORS[i % COLORS.length] + '33',
        spanGaps: true, pointRadius: 0, borderWidth: 1.5,
      }}]);
    }});
    card.appendChild(splitGrid);
    return;
  }}

  const wrap = document.createElement('div');
  wrap.className = 'chart-canvas-wrap';
  const canvas = document.createElement('canvas');
  wrap.appendChild(canvas);
  card.appendChild(wrap);
  const datasets = Object.entries(panel.series).map(([name, data], i) => ({{
    label: name, data, borderColor: COLORS[i % COLORS.length],
    backgroundColor: COLORS[i % COLORS.length] + '33',
    spanGaps: true, pointRadius: 0, borderWidth: 1.5,
  }}));
  makeLineChart(canvas, panel.labels, datasets);
}}

addSection('combined', '통합 차트', renderCombined);
Object.entries(PANELS).forEach(([title, panel], i) => {{
  addSection('panel' + i, title, (section) => renderPanel(title, panel, section));
}});

sections.forEach(({{ id, title }}) => {{
  const btn = document.createElement('button');
  btn.className = 'nav-btn';
  btn.textContent = title;
  btn.dataset.id = id;
  btn.onclick = () => activateSection(id);
  sidebar.appendChild(btn);
}});
if (sections.length) activateSection(sections[0].id);
</script>
</body>
</html>
"""
    os.makedirs(DIST_DIR, exist_ok=True)
    with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"대시보드 생성 완료: {DASHBOARD_PATH}")


def main():
    merged, raw_latest = build_merged()
    build_dashboard(merged, raw_latest)


if __name__ == "__main__":
    main()
