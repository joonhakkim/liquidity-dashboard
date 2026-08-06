"""
비트코인 시가총액(USD)을 가져와 data/bitcoin_raw.csv 로 저장한다.

CoinGecko 무료 공개 API(키 불필요)를 쓴다. 단, days=max(전체 히스토리)는
401(인증 필요)이 떠서 무료로는 안 되고, 실험해보니 무료로는 최근 365일까지만
허용된다 (그 이상은 CoinGecko 유료/Demo API 키 필요).

.env의 COINMARKETCAP_API_KEY는 대체용으로 남겨뒀지만, CoinMarketCap 무료
플랜은 과거 시계열(historical) 엔드포인트를 지원하지 않아(최신가만 제공)
지금은 사용하지 않는다.
"""
import os
import time

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "bitcoin_raw.csv")

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"


def main():
    print("비트코인 시가총액(CoinGecko) 수집 중...")
    session = requests.Session()
    session.trust_env = False

    r = None
    last_err = None
    for attempt in range(4):
        try:
            r = session.get(COINGECKO_URL, params={"vs_currency": "usd", "days": "365", "interval": "daily"}, timeout=30)
            r.raise_for_status()
            last_err = None
            break
        except requests.RequestException as e:
            last_err = e
            print(f"  시도 {attempt + 1}/4 실패 ({e}), 재시도...")
            time.sleep(5)
    if last_err is not None:
        raise SystemExit(f"CoinGecko 요청 실패: {last_err}")

    data = r.json()
    market_caps = data.get("market_caps", [])
    if not market_caps:
        raise SystemExit("market_caps 데이터가 비어 있습니다.")

    df = pd.DataFrame(market_caps, columns=["ts_ms", "btc_market_cap_usd"])
    df["date"] = pd.to_datetime(df["ts_ms"], unit="ms").dt.floor("D")
    df = df.drop(columns=["ts_ms"])
    df = df.drop_duplicates(subset="date", keep="last").sort_values("date")

    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"저장 완료: {OUT_PATH}")
    print(f"행 수: {len(df)}, 기간: {df['date'].min().date()} ~ {df['date'].max().date()}")
    print(df.tail(3).to_string(index=False))


if __name__ == "__main__":
    main()
