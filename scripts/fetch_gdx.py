"""
금광기업 ETF(GDX, VanEck Gold Miners ETF)를 Yahoo Finance 차트 API에서 가져와
data/gdx_raw.csv 로 저장한다. 로그인/키 불필요, 무료.

GDX를 추가한 이유: 대신증권 원자재 리포트가 "금은 유동성 프록시라 위험자산보다
먼저 반응한다"고 주장했는데, 검증해보니 순수 금현물(gold_usd)보다 금광기업 ETF가
코스피 급락에 대해 더 나은 선행성을 보였다(2000~2026년 17개 급락 이벤트 중 GDX
65% 적중 vs 금현물 53% 적중, 특히 2008년 금융위기 때 4연속 60~139일 선행 적중).
채굴기업 주가가 금값 헷지 성격 + 주식시장 리스크심리를 동시에 반영해서 그런 것으로
추정(2026-08-12 분석, 대화 기록 참고).
"""
import os
from datetime import datetime, timedelta

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "gdx_raw.csv")

TICKER = "GDX"
BACKFILL_YEARS = 25  # GDX 자체가 2006-05-22 상장이라 이보다 훨씬 이전으로 잡아도 상장일부터 잡힘


def main():
    p1 = int((datetime.today() - timedelta(days=365 * BACKFILL_YEARS)).timestamp())
    p2 = int(datetime.today().timestamp())
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{TICKER}",
        params={"period1": p1, "period2": p2, "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    r.raise_for_status()
    result = r.json()["chart"]["result"][0]
    ts = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    df = pd.DataFrame({"date": pd.to_datetime(ts, unit="s").normalize(), "gdx_close": closes}).dropna()
    df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)

    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUT_PATH}")
    print(f"행 수: {len(df)}, 기간: {df['date'].min().date()} ~ {df['date'].max().date()}")
    print(df.tail(3).to_string(index=False))


if __name__ == "__main__":
    main()
