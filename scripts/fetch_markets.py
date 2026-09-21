"""
원/달러 환율, 금, 은, 달러/엔, 구리 시세를 Yahoo Finance 차트 API에서 가져와
data/markets_raw.csv 로 저장한다. 로그인/키 불필요(fetch_gdx.py와 동일한 패턴).

원래는 네이버 금융의 exchangeDailyQuote/worldDailyQuote 일별시세 페이지를 파싱했는데,
2026-09월 중순 네이버가 이 구형 페이지들을 전부 서비스 종료했다("이 페이지는 더 이상
제공되지 않습니다" - stock.naver.com 신형 페이지로 통합). HTTP는 200/410으로 응답하고
표는 파싱되지만 안내 문구만 들어있어서 조용히 빈 결과만 쌓이고 있었다(크래시가 안 나서
한동안 못 알아챔 - 2026-09-21, "유동성 지표 갱신 안되는 게 많다"는 지적으로 발견). 다섯
시세 전부 Yahoo Finance로 교체.
"""
import os
from datetime import datetime, timedelta

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "markets_raw.csv")

BACKFILL_YEARS = 7

# (컬럼명, Yahoo Finance 티커)
SOURCES = [
    ("usd_krw", "KRW=X"),
    ("gold_usd", "GC=F"),
    ("silver_usd", "SI=F"),
    ("usd_jpy", "JPY=X"),
    ("copper_usd", "HG=F"),
]


def fetch_series(col, ticker, start, end):
    p1 = int(datetime.combine(start, datetime.min.time()).timestamp())
    p2 = int((datetime.combine(end, datetime.min.time()) + timedelta(days=1)).timestamp())
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={"period1": p1, "period2": p2, "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=20,
        )
        r.raise_for_status()
        result = r.json()["chart"]["result"][0]
        ts = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except Exception as e:
        print(f"  {col}({ticker}) 요청/파싱 실패 ({e})")
        return pd.DataFrame(columns=["date", col])

    df = pd.DataFrame({"date": pd.to_datetime(ts, unit="s").normalize(), col: closes}).dropna()
    return df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)


def determine_start():
    today = datetime.today().date()
    existing = pd.read_csv(OUT_PATH, parse_dates=["date"]) if os.path.exists(OUT_PATH) else None
    if existing is not None and len(existing) > 0:
        last_date = existing["date"].max().date()
        start = last_date - timedelta(days=7)  # 며칠 겹치게 재수집(수정치 반영용)
        print(f"기존 데이터 발견: {last_date} 부근부터 다시 수집")
    else:
        start = today - timedelta(days=365 * BACKFILL_YEARS)
        print(f"기존 데이터 없음: 최근 {BACKFILL_YEARS}년 백필")
    return existing, start


def main():
    existing, start = determine_start()
    end = datetime.today().date()

    merged = None
    for col, ticker in SOURCES:
        print(f"수집 중: {col} ({ticker}) ...")
        df = fetch_series(col, ticker, start, end)
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
