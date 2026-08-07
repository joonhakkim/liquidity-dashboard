"""
원/달러 환율, 금, 은 시세를 네이버 금융에서 가져와 data/markets_raw.csv 로 저장한다.
(구리는 네이버에 없어서 fetch_fred.py에서 FRED 데이터로 따로 받는다.)

기존 fetch_krx.py의 코스피 지수 스크래핑과 동일한 패턴 - 페이지네이션되는
일별시세 표를 pandas.read_html로 파싱한다. 로그인/키 불필요.

발견한 엔드포인트:
  - 원/달러: https://finance.naver.com/marketindex/exchangeDailyQuote.naver?marketindexCd=FX_USDKRW&page=N
  - 금(국제): https://finance.naver.com/marketindex/worldDailyQuote.naver?marketindexCd=CMDT_GC&fdtc=2&page=N
  - 은(국제): https://finance.naver.com/marketindex/worldDailyQuote.naver?marketindexCd=CMDT_SI&fdtc=2&page=N
  (marketindexCd는 /marketindex/worldGoldDetail.naver 페이지에서 관련 시세 링크로 확인)
"""
import os
import time
from datetime import datetime, timedelta
from io import StringIO

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "markets_raw.csv")

EXCHANGE_URL = "https://finance.naver.com/marketindex/exchangeDailyQuote.naver"
WORLD_URL = "https://finance.naver.com/marketindex/worldDailyQuote.naver"

BACKFILL_YEARS = 7

# (컬럼명, 요청 함수용 파라미터)
SOURCES = [
    ("usd_krw", EXCHANGE_URL, {"marketindexCd": "FX_USDKRW"}),
    ("gold_usd", WORLD_URL, {"marketindexCd": "CMDT_GC", "fdtc": 2}),
    ("silver_usd", WORLD_URL, {"marketindexCd": "CMDT_SI", "fdtc": 2}),
]


def fetch_series(col, url, params, start):
    session = requests.Session()
    session.trust_env = False
    rows = []
    page = 1
    while True:
        try:
            r = session.get(url, params={**params, "page": page}, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            r.encoding = "euc-kr"
            tables = pd.read_html(StringIO(r.text))
        except Exception as e:
            print(f"  page {page}: 요청/파싱 실패 ({e})")
            break

        df = tables[0].dropna(how="all")
        if df.empty:
            break
        # 첫 컬럼=날짜, 둘째 컬럼=시세값. 나머지(변동폭/등락률/고가/저가 등)는 안 씀.
        df = df.iloc[:, :2]
        df.columns = ["date_str", col]
        df["date"] = pd.to_datetime(df["date_str"], format="%Y.%m.%d", errors="coerce")
        df = df.dropna(subset=["date"])
        if df.empty:
            break

        rows.append(df[["date", col]])

        if df["date"].min().date() < start:
            break
        page += 1
        if page > 600:  # 안전장치: 약 16년치
            break
        time.sleep(0.15)

    if not rows:
        return pd.DataFrame(columns=["date", col])
    out = pd.concat(rows, ignore_index=True)
    out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out[out["date"].dt.date >= start]
    return out.drop_duplicates(subset="date")


def determine_start():
    today = datetime.today().date()
    existing = pd.read_csv(OUT_PATH, parse_dates=["date"]) if os.path.exists(OUT_PATH) else None
    if existing is not None and len(existing) > 0:
        last_date = existing["date"].max().date()
        start = last_date - timedelta(days=3)  # 며칠 겹치게 재수집(수정치 반영용)
        print(f"기존 데이터 발견: {last_date} 부근부터 다시 수집")
    else:
        start = today - timedelta(days=365 * BACKFILL_YEARS)
        print(f"기존 데이터 없음: 최근 {BACKFILL_YEARS}년 백필")
    return existing, start


def main():
    existing, start = determine_start()

    merged = None
    for col, url, params in SOURCES:
        print(f"수집 중: {col} ...")
        df = fetch_series(col, url, params, start)
        print(f"  {len(df)}행")
        merged = df if merged is None else merged.merge(df, on="date", how="outer")

    if merged is None or merged.empty:
        print("수집된 데이터가 없습니다.")
        return

    merged = merged.sort_values("date").reset_index(drop=True)

    if existing is not None and len(existing) > 0:
        combined = pd.concat([existing, merged], ignore_index=True)
        combined = combined.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
    else:
        combined = merged

    os.makedirs(DATA_DIR, exist_ok=True)
    combined.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"\n저장 완료: {OUT_PATH}")
    print(f"행 수: {len(combined)}, 기간: {combined['date'].min().date()} ~ {combined['date'].max().date()}")
    print(combined.tail(3).to_string(index=False))


if __name__ == "__main__":
    main()
