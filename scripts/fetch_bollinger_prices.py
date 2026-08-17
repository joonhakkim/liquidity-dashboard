"""
코스피+코스닥 전종목 일별 종가를 KRX 공식 Open API에서 받아 data/bollinger_prices.csv 에
누적한다(볼린저밴드 상단 돌파 종목수 트래커의 원천 데이터).

fetch_adr.py와 동일한 API(stk_bydd_trd=코스피, ksq_bydd_trd=코스닥)를 쓰지만, 상승/하락
개수 대신 종목별 종가 자체를 저장한다(20일 이동평균/표준편차로 밴드를 계산해야 해서).
하루에 시장당 API 호출 1번이라 DART와 달리 한도 걱정 없이 통째로 백필 가능.
"""
import os
import time
from datetime import datetime, timedelta

import holidays
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

KRX_OPEN_API_KEY = os.environ.get("KRX_OPEN_API_KEY")
if not KRX_OPEN_API_KEY:
    raise SystemExit("KRX_OPEN_API_KEY가 .env에 설정되어 있지 않습니다.")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "bollinger_prices.csv")

STK_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
KSQ_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"

BACKFILL_START = "2010-01-04"  # KRX Open API(stk_bydd_trd/ksq_bydd_trd)로 실제 조회 가능한 최초일
# (2009년 이전 날짜는 직접 테스트해보니 빈 응답 - 2026-08-17 확인. 사용자가 "뽑을 수 있는
# 최대치"로 요청해서 이 API가 주는 한계까지 전부 백필한다.)


def fetch_market(url, bas_dd, market):
    headers = {"AUTH_KEY": KRX_OPEN_API_KEY}
    try:
        r = requests.get(url, params={"basDd": bas_dd}, headers=headers, timeout=20)
        data = r.json().get("OutBlock_1", []) if r.status_code == 200 else []
    except requests.RequestException:
        return []
    rows = []
    for item in data:
        code = item.get("ISU_CD")
        name = item.get("ISU_NM")
        close = pd.to_numeric(item.get("TDD_CLSPRC"), errors="coerce")
        if code and pd.notna(close):
            rows.append({"date": bas_dd, "market": market, "code": code, "name": name, "close": float(close)})
    return rows


def business_days(start, end):
    kr_holidays = holidays.KR(years=range(start.year, end.year + 1))
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in kr_holidays:
            yield d
        d += timedelta(days=1)


def main():
    existing = pd.read_csv(OUT_PATH, dtype={"date": str}) if os.path.exists(OUT_PATH) else pd.DataFrame(columns=["date"])
    done_dates = set(existing["date"]) if not existing.empty else set()

    today = datetime.today().date()
    start = datetime.strptime(BACKFILL_START, "%Y-%m-%d").date()
    todo = [d for d in business_days(start, today) if d.strftime("%Y%m%d") not in done_dates]
    print(f"수집 대상: {len(todo)}개 영업일 (기존 {len(done_dates)}개는 건너뜀)")

    all_rows = []
    for i, d in enumerate(todo, 1):
        bas_dd = d.strftime("%Y%m%d")
        rows = fetch_market(STK_URL, bas_dd, "KOSPI")
        time.sleep(0.1)
        rows += fetch_market(KSQ_URL, bas_dd, "KOSDAQ")
        time.sleep(0.1)
        if rows:
            all_rows.extend(rows)
        if i % 20 == 0:
            print(f"  진행 {i}/{len(todo)} ({bas_dd})")

    if not all_rows:
        print("새로 수집된 데이터가 없습니다.")
        return

    new_df = pd.DataFrame(all_rows)
    combined = pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df
    combined = combined.drop_duplicates(subset=["date", "market", "code"], keep="last")
    combined = combined.sort_values(["date", "market", "code"]).reset_index(drop=True)

    os.makedirs(DATA_DIR, exist_ok=True)
    combined.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n저장 완료: {OUT_PATH}")
    print(f"행 수: {len(combined)}, 영업일수: {combined['date'].nunique()}, 기간: {combined['date'].min()} ~ {combined['date'].max()}")


if __name__ == "__main__":
    main()
