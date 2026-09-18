"""
해외MP(미국주식) 트래커용 일별 종가를 Yahoo Finance 차트 API에서 가져와
data/overseas_mp_prices.csv 로 저장한다. 로그인/키 불필요(fetch_gdx.py와 동일한 방식 - 이미
이 레포에서 검증됨).

국내 MP들과 달리 종목코드가 6자리 숫자가 아니라 영문 티커라서 fetch_troy_mp_prices.py(네이버
차트 API, 6자리 코드 전제)를 그대로 못 쓰고 이 스크립트를 따로 둔다. 벤치마크(S&P500=^GSPC,
나스닥100=^NDX)도 같은 파일에 code="^GSPC"/"^NDX"로 같이 저장해서 build_overseas_mp_page.py가
한 파일만 읽으면 되게 한다.
"""
import os
from datetime import datetime, timedelta

import pandas as pd
import requests

from mp_portfolios import OVERSEAS_PORTFOLIOS

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
BACKFILL_YEARS = 2
BENCHMARK_TICKERS = ["^GSPC"]  # 벤치마크는 S&P500 지수 하나만 쓴다(사용자 요청, 2026-09-18)


def fetch_yahoo_history(ticker, years=BACKFILL_YEARS):
    p1 = int((datetime.today() - timedelta(days=365 * years)).timestamp())
    p2 = int(datetime.today().timestamp())
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
        params={"period1": p1, "period2": p2, "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    r.raise_for_status()
    result = r.json()["chart"]["result"][0]
    ts = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    df = pd.DataFrame({"date": pd.to_datetime(ts, unit="s").normalize(), "close": closes}).dropna()
    return df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)


def main():
    portfolio = OVERSEAS_PORTFOLIOS[0]
    trades_path, out_path = portfolio["trades_path"], portfolio["prices_path"]
    if not os.path.exists(trades_path):
        print(f"매매일지 파일이 없습니다: {trades_path}")
        return

    trades = pd.read_csv(trades_path, dtype={"code": str}).dropna(subset=["code"])
    codes = sorted(trades["code"].unique())
    all_tickers = codes + BENCHMARK_TICKERS

    frames = []
    for ticker in all_tickers:
        print(f"  수집 중: {ticker} ...")
        try:
            df = fetch_yahoo_history(ticker)
        except Exception as e:
            print(f"    경고: {ticker} 조회 실패 ({e})")
            continue
        if df.empty:
            print(f"    경고: {ticker} 가격 데이터 없음")
            continue
        df["code"] = ticker
        frames.append(df)

    if not frames:
        print("수집된 가격 데이터가 없습니다.")
        return

    combined = pd.concat(frames, ignore_index=True).sort_values(["code", "date"]).reset_index(drop=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    combined.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {out_path} (티커 수: {len(all_tickers)}, 행 수: {len(combined)})")


if __name__ == "__main__":
    main()
