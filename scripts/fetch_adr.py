"""
코스피/코스닥 등락비율(ADR, Advance Decline Ratio) 계산용 원천 데이터를 KRX Open API에서
가져와 data/adr_raw.csv 로 저장한다.

ADR = 최근 20거래일 상승종목수 누계 / 최근 20거래일 하락종목수 누계 x 100(%)
  120% 이상 = 과열권, 75% 이하 = 바닥권으로 보는 게 일반적.

KRX Open API(stk_bydd_trd=코스피, ksq_bydd_trd=코스닥)가 종목별 CMPPREVDD_PRC(전일대비
등락)를 이미 제공해서, 그날 전종목을 한 번에 받아 양수/음수 개수만 세면 된다(종목별로
전일 종가를 따로 비교할 필요 없음). 하루에 시장당 API 호출 1번이라 DART와 달리 한도
걱정 없이 통째로 백필 가능.

20일 롤링 합산(kospi_adr 등)은 build_dashboard.py에서 계산한다 - 여기서는 raw
상승/하락 종목수만 저장(거래일에만 값 있음, 주말·휴장일은 행 자체가 없음 - ffill해서
롤링하면 왜곡되므로 build_dashboard.py에서 거래일 기준으로만 롤링해야 함).
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
OUT_PATH = os.path.join(DATA_DIR, "adr_raw.csv")

STK_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
KSQ_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"

BACKFILL_YEARS = 3  # 3년치면 20일 롤링 ADR + UI의 "3년" 구간까지 충분히 커버


def fetch_counts(url, bas_dd):
    headers = {"AUTH_KEY": KRX_OPEN_API_KEY}
    r = requests.get(url, params={"basDd": bas_dd}, headers=headers, timeout=20)
    if r.status_code != 200:
        return None
    data = r.json().get("OutBlock_1", [])
    if not data:
        return None
    adv = dec = 0
    for item in data:
        try:
            chg = float(item.get("CMPPREVDD_PRC", 0))
        except (TypeError, ValueError):
            continue
        if chg > 0:
            adv += 1
        elif chg < 0:
            dec += 1
    return adv, dec


def business_days(start, end):
    kr_holidays = holidays.KR(years=range(start.year, end.year + 1))
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in kr_holidays:
            yield d
        d += timedelta(days=1)


def main():
    existing = pd.read_csv(OUT_PATH, parse_dates=["date"]) if os.path.exists(OUT_PATH) else pd.DataFrame(columns=["date"])
    done_dates = set(existing["date"].dt.strftime("%Y%m%d")) if not existing.empty else set()

    today = datetime.today().date()
    start = today - timedelta(days=365 * BACKFILL_YEARS)
    todo = [d for d in business_days(start, today) if d.strftime("%Y%m%d") not in done_dates]
    print(f"수집 대상: {len(todo)}개 영업일 (기존 {len(done_dates)}개는 건너뜀)")

    rows = []
    for i, d in enumerate(todo, 1):
        bas_dd = d.strftime("%Y%m%d")
        kospi = fetch_counts(STK_URL, bas_dd)
        time.sleep(0.1)
        kosdaq = fetch_counts(KSQ_URL, bas_dd)
        time.sleep(0.1)
        if kospi is None and kosdaq is None:
            continue  # 공휴일 등 데이터 없는 날(holidays 라이브러리가 못 잡은 임시휴장 등)
        rows.append({
            "date": d,
            "kospi_adv": kospi[0] if kospi else None, "kospi_dec": kospi[1] if kospi else None,
            "kosdaq_adv": kosdaq[0] if kosdaq else None, "kosdaq_dec": kosdaq[1] if kosdaq else None,
        })
        if i % 100 == 0:
            print(f"  진행 {i}/{len(todo)} ({bas_dd})")

    if not rows:
        print("새로 수집된 데이터가 없습니다.")
        return

    new_df = pd.DataFrame(rows)
    new_df["date"] = pd.to_datetime(new_df["date"])
    combined = pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df
    combined["date"] = pd.to_datetime(combined["date"])
    combined = combined.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    combined.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n저장 완료: {OUT_PATH} ({len(combined)}행, {combined['date'].min().date()} ~ {combined['date'].max().date()})")
    print(combined.tail(3).to_string(index=False))


if __name__ == "__main__":
    main()
