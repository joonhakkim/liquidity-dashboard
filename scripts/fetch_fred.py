"""
미국 M2 통화공급(M2SL, 월별)을 FRED(세인트루이스 연은)에서 가져와 data/fred_raw.csv 로 저장한다.

FRED는 인증키 없이도 fredgraph.csv 엔드포인트로 시계열을 바로 받을 수 있다.
https://fred.stlouisfed.org/graph/fredgraph.csv?id=M2SL

주의: User-Agent 헤더를 "Mozilla/5.0" 같은 값으로 직접 지정하면 이 환경에서
fred.stlouisfed.org 연결이 거의 항상 타임아웃됐다 (WAF가 너무 단순한 UA 문자열을
막는 것으로 추정). requests 기본 UA(python-requests/x.x)를 쓰면 정상 동작하므로
헤더를 따로 지정하지 않는다.
"""
import os
from io import StringIO

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "fred_raw.csv")

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=M2SL"


def main():
    print("미국 M2(M2SL, FRED) 수집 중...")
    session = requests.Session()
    session.trust_env = False  # Windows 시스템 프록시 설정 때문에 연결이 느려지는 문제 방지

    r = None
    last_err = None
    for attempt in range(4):
        try:
            r = session.get(FRED_CSV_URL, timeout=20)
            r.raise_for_status()
            last_err = None
            break
        except requests.RequestException as e:
            last_err = e
            print(f"  시도 {attempt + 1}/4 실패 ({e}), 재시도...")
    if last_err is not None:
        raise SystemExit(f"FRED 요청 실패: {last_err}")

    df = pd.read_csv(StringIO(r.text))
    df.columns = ["date", "us_m2"]
    df["date"] = pd.to_datetime(df["date"])
    df["us_m2"] = pd.to_numeric(df["us_m2"], errors="coerce")
    df = df.dropna(subset=["us_m2"])

    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"저장 완료: {OUT_PATH}")
    print(f"행 수: {len(df)}, 기간: {df['date'].min().date()} ~ {df['date'].max().date()}")
    print(df.tail(3).to_string(index=False))


if __name__ == "__main__":
    main()
