"""
시장 국면 페이지 '대응 단계'(보조경고) 계산용 외부 시계열을 모아 data/phase_warning_inputs.csv로 저장한다(2026-10-14 신규).

- kosdaq_close : 코스닥 지수 종가(네이버 차트 API) - 코스닥 변동성 확대(20일/120일 변동성) 계산용
- sox_close    : 미국 필라델피아 반도체지수 ^SOX(야후) - 50일선 아래 여부
- baa_spread   : 무디스 Baa 회사채 - 미국채 10년 스프레드(FRED BAA10Y, %p) - 20일 급등 여부
- ice_*_oas    : ICE BofA 등급별 회사채 스프레드(FRED, %p). FRED가 최근 3년치만 제공해서
                 판정에는 안 쓰고 페이지에 참고 값으로만 보여준다(CCC/B/BB 하이일드, BBB/A 투자등급).
소스 하나가 실패해도 나머지는 갱신하고, 실패한 열은 이전 파일 값을 그대로 둔다.
"""
import os
import re

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "phase_warning_inputs.csv")
FRED_SERIES = [
    ("baa_spread", "BAA10Y", "2010-01-01"),
    ("ice_ccc_oas", "BAMLH0A3HYC", "2020-01-01"),
    ("ice_b_oas", "BAMLH0A2HYB", "2020-01-01"),
    ("ice_bb_oas", "BAMLH0A1HYBB", "2020-01-01"),
    ("ice_bbb_oas", "BAMLC0A4CBBB", "2020-01-01"),
    ("ice_a_oas", "BAMLC0A3CA", "2020-01-01"),
]


def session():
    s = requests.Session()
    s.trust_env = False   # Windows 시스템 프록시 때문에 느려지는 문제 방지(fetch_fred.py와 같은 이유)
    return s


def fetch_kosdaq():
    r = session().get("https://fchart.stock.naver.com/sise.nhn",
                      params={"symbol": "KOSDAQ", "timeframe": "day", "count": 4000, "requestType": 0}, timeout=30)
    r.raise_for_status()
    rows = re.findall(r'data="(\d{8})\|[^|]*\|[^|]*\|[^|]*\|([\d.]+)', r.text)
    return pd.Series({pd.Timestamp(a): float(b) for a, b in rows}).sort_index()


def fetch_yahoo(sym):
    r = session().get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"range": "20y", "interval": "1d"},
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    j = r.json()["chart"]["result"][0]
    s = pd.Series(j["indicators"]["quote"][0]["close"], index=pd.to_datetime(j["timestamp"], unit="s").normalize()).dropna()
    return s[~s.index.duplicated(keep="last")]


def fetch_fred(series_id, start):
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY 없음")
    r = session().get("https://api.stlouisfed.org/fred/series/observations",
                      params={"series_id": series_id, "api_key": key, "file_type": "json", "observation_start": start}, timeout=30)
    r.raise_for_status()
    df = pd.DataFrame(r.json()["observations"])
    return pd.Series(pd.to_numeric(df["value"], errors="coerce").values, index=pd.to_datetime(df["date"])).dropna()


def main():
    old = pd.read_csv(OUT_PATH, parse_dates=["date"]).set_index("date") if os.path.exists(OUT_PATH) else pd.DataFrame()
    jobs = [("kosdaq_close", fetch_kosdaq), ("sox_close", lambda: fetch_yahoo("^SOX"))]
    jobs += [(col, (lambda sid=sid, st=st: fetch_fred(sid, st))) for col, sid, st in FRED_SERIES]
    cols = {}
    for col, fn in jobs:
        try:
            s = fn()
            cols[col] = s
            print(f"  {col}: {len(s)}행 ({s.index.min().date()} ~ {s.index.max().date()}, 최근 {s.iloc[-1]:.2f})")
        except Exception as e:  # noqa: BLE001 - 소스별로 실패를 격리
            print(f"  {col}: 실패({e}) - 이전 값 유지")
            if col in old.columns:
                cols[col] = old[col].dropna()
    out = pd.DataFrame(cols).sort_index()
    out.index.name = "date"
    os.makedirs(DATA_DIR, exist_ok=True)
    out.to_csv(OUT_PATH, encoding="utf-8-sig", float_format="%.4f")
    print(f"저장 완료: {OUT_PATH} ({len(out)}행)")


if __name__ == "__main__":
    main()
