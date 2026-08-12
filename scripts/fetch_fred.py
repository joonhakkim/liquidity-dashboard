"""
미국 M2 통화공급(M2SL, 월별)과 미국 10년물 실질금리(DFII10, 일별)를
FRED(세인트루이스 연은) 공식 API로 가져와 data/fred_raw.csv 로 저장한다.

기존엔 인증키 없이 fredgraph.csv 엔드포인트(비공식, 차트용 CSV 내보내기)를
긁어왔는데, 사용자가 공식 FRED_API_KEY를 주면서 "이 API로 가져오자"고
해서 api.stlouisfed.org의 공식 series/observations 엔드포인트로 전환했다.
(fredgraph.csv 방식은 User-Agent를 직접 지정하면 타임아웃되는 이상한 문제가
있었는데, 공식 API는 그런 문제 없이 바로 됐다.)

DFII10 = "Market Yield on U.S. Treasury Securities at 10-Year Constant
Maturity, Quoted on an Investment Basis, Inflation-Indexed" - 시장이 실제
거래로 반영한 기대 실질금리(10년, 일별). 값이 "."인 날(휴장일 등)은 결측으로 처리.
"""
import os
from datetime import datetime

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

FRED_API_KEY = os.environ.get("FRED_API_KEY")
if not FRED_API_KEY:
    raise SystemExit("FRED_API_KEY가 .env에 설정되어 있지 않습니다.")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "fred_raw.csv")
API_URL = "https://api.stlouisfed.org/fred/series/observations"

# (컬럼명, FRED series_id, 수집 시작일)
FRED_SERIES = [
    ("us_m2", "M2SL", "2000-01-01"),
    ("us_real_rate_10y", "DFII10", "2003-01-01"),  # DFII10은 2003년부터 존재
    # 미국 순유동성(Net Liquidity) = 연준 총자산 - TGA(재무부 일반계정) - ON RRP(익일역레포)
    # MacroMicro 등에서 흔히 쓰는 "Fed Net Liquidity" 정의와 동일. 단위는 build_dashboard.py에서 통일.
    ("fed_total_assets", "WALCL", "2003-01-01"),  # 연준 총자산(주간, 수요일 기준, 백만달러)
    ("us_treasury_tga", "WTREGEN", "2003-01-01"),  # 미 재무부 일반계정 TGA 잔액(주간, 백만달러)
    ("us_reverse_repo", "RRPONTSYD", "2003-01-01"),  # 익일 역레포(ON RRP) 잔액(일별, 십억달러)
    ("us_treasury_10y", "DGS10", "2000-01-01"),  # 미국채 10년물 명목금리(일별, %) - us_real_rate_10y(DFII10, 물가연동채 실질금리)와는 다름
    ("japan_ust_holdings", "FORTREASPOS42609", "2003-01-01"),  # 일본의 미국채(장단기 합산) 보유액(월간, 백만달러)
]


def fetch_series(series_id, start):
    session = requests.Session()
    session.trust_env = False  # Windows 시스템 프록시 설정 때문에 연결이 느려지는 문제 방지

    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": start,
    }
    last_err = None
    for attempt in range(4):
        try:
            r = session.get(API_URL, params=params, timeout=20)
            r.raise_for_status()
            last_err = None
            break
        except requests.RequestException as e:
            last_err = e
            print(f"  시도 {attempt + 1}/4 실패 ({e}), 재시도...")
    if last_err is not None:
        raise SystemExit(f"FRED 요청 실패 ({series_id}): {last_err}")

    obs = r.json().get("observations", [])
    df = pd.DataFrame(obs)[["date", "value"]]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")  # "." -> NaN
    return df.dropna(subset=["value"])


def main():
    merged = None
    for col, series_id, start in FRED_SERIES:
        print(f"수집 중: {col} (FRED {series_id})...")
        df = fetch_series(series_id, start)
        df = df.rename(columns={"value": col})
        print(f"  {len(df)}행")
        merged = df if merged is None else merged.merge(df, on="date", how="outer")

    merged = merged.sort_values("date").reset_index(drop=True)

    os.makedirs(DATA_DIR, exist_ok=True)
    merged.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"\n저장 완료: {OUT_PATH}")
    print(f"행 수: {len(merged)}, 기간: {merged['date'].min().date()} ~ {merged['date'].max().date()}")
    print(merged.tail(5).to_string(index=False))


if __name__ == "__main__":
    main()
