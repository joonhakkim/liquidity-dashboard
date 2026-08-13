"""
정유화학 업황 트래킹: EIA(미 에너지정보청) 공식 API에서 WTI 원유/휘발유/경유 현물가를
받아 표준 3:2:1 크랙 스프레드(정제마진 프록시)를 계산해 data/crack_spread_raw.csv 로 저장한다.

참고: https://github.com/mohamedmadkour62005-beep/crack-spread-tracker (EIA APIv2 호출 방식,
갤런->배럴 환산, 3:2:1 공식을 그대로 가져오되 이 리포의 fetch_* 스크립트 패턴(증분 갱신,
data/*.csv 누적 저장)에 맞게 재작성함).

3:2:1 크랙 스프레드($/bbl) = [(2 x 휘발유 $/bbl) + (1 x 경유 $/bbl) - (3 x WTI $/bbl)] / 3
원유 3배럴을 정제해서 휘발유 2배럴 + 경유 1배럴을 만든다고 가정한 정유사 정제마진 프록시.
정제마진이 넓어질수록(스프레드 확대) 정유사 마진이 개선되는 신호로 흔히 쓰인다.
휘발유/경유는 EIA가 갤런당 달러로 발표해서 1배럴=42갤런으로 환산 후 계산한다.
"""
import os
from datetime import datetime, timedelta

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

EIA_API_KEY = os.environ.get("EIA_API_KEY")
if not EIA_API_KEY:
    raise SystemExit("EIA_API_KEY가 .env에 설정되어 있지 않습니다.")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "crack_spread_raw.csv")

API_BASE_URL = "https://api.eia.gov/v2"
ROUTE = "petroleum/pri/spt"
GALLONS_PER_BARREL = 42
PAGE_SIZE = 5000
BACKFILL_YEARS = 20

# (컬럼명, EIA series_id, 단위)
SERIES = [
    ("wti_usd_per_bbl", "RWTC", "$/BBL"),
    ("gasoline_usd_per_gal", "EER_EPMRU_PF4_Y35NY_DPG", "$/GAL"),
    ("diesel_usd_per_gal", "EER_EPD2DXL0_PF4_Y35NY_DPG", "$/GAL"),
]


def fetch_series(series_id, start_date):
    endpoint = f"{API_BASE_URL}/{ROUTE}/data/"
    rows = []
    offset = 0
    expected_total = None
    while True:
        params = [
            ("api_key", EIA_API_KEY),
            ("frequency", "daily"),
            ("data[0]", "value"),
            ("facets[series][]", series_id),
            ("start", start_date),
            ("length", str(PAGE_SIZE)),
            ("offset", str(offset)),
            ("sort[0][column]", "period"),
            ("sort[0][direction]", "asc"),
        ]
        r = requests.get(endpoint, params=params, timeout=30)
        r.raise_for_status()
        payload = r.json()
        if payload.get("error"):
            raise SystemExit(f"EIA 요청 실패 ({series_id}): {payload['error']}")
        api_response = payload.get("response", {})
        page_rows = api_response.get("data", [])
        if expected_total is None:
            expected_total = int(api_response.get("total", 0))
        rows.extend(page_rows)
        if not page_rows or len(rows) >= expected_total or len(page_rows) < PAGE_SIZE:
            break
        offset += len(page_rows)

    if not rows:
        return pd.DataFrame(columns=["date", series_id])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["period"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["date", "value"]).drop_duplicates(subset="date", keep="last")
    return df[["date", "value"]].rename(columns={"value": series_id})


def determine_start():
    today = datetime.today().date()
    existing = pd.read_csv(OUT_PATH, parse_dates=["date"]) if os.path.exists(OUT_PATH) else None
    if existing is not None and len(existing) > 0:
        start = (existing["date"].max().date() - timedelta(days=5)).strftime("%Y-%m-%d")
        print(f"기존 데이터 발견: {existing['date'].max().date()} 부근부터 다시 수집")
    else:
        start = (today - timedelta(days=365 * BACKFILL_YEARS)).strftime("%Y-%m-%d")
        print(f"기존 데이터 없음: 최근 {BACKFILL_YEARS}년 백필")
    return existing, start


def main():
    existing, start = determine_start()

    merged = None
    for col, series_id, unit in SERIES:
        print(f"수집 중: {col} (EIA {series_id})...")
        df = fetch_series(series_id, start).rename(columns={series_id: col})
        print(f"  {len(df)}행")
        merged = df if merged is None else merged.merge(df, on="date", how="outer")

    if merged is None or merged.empty:
        print("수집된 데이터가 없습니다.")
        return

    merged = merged.dropna().sort_values("date").reset_index(drop=True)
    merged["gasoline_usd_per_bbl"] = merged["gasoline_usd_per_gal"] * GALLONS_PER_BARREL
    merged["diesel_usd_per_bbl"] = merged["diesel_usd_per_gal"] * GALLONS_PER_BARREL
    merged["crack_spread_321_usd_per_bbl"] = (
        2 * merged["gasoline_usd_per_bbl"] + merged["diesel_usd_per_bbl"] - 3 * merged["wti_usd_per_bbl"]
    ) / 3

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
