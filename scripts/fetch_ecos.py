"""
한국은행 ECOS Open API에서 유동성 관련 통계를 가져와 data/ecos_raw.csv 로 저장한다.

사용된 통계표 코드는 ECOS Open API의 StatisticTableList / StatisticItemList
엔드포인트로 직접 검색하여 확인했다 (아래 STAT_ITEMS 딕셔너리 및 discover_candidates()
참고). '정부예금 잔액'은 ECOS 내에 월별 이상 주기의 독립 시계열이 존재하지 않아
(가장 근접한 '중앙은행 개관표' 101Y010/161Y016 은 2001~2003년까지만 존재하는
레거시 연간 통계) net_liquidity 계산에서 제외했다.

실행:
    python scripts/fetch_ecos.py            # 데이터 수집 후 CSV 저장
    python scripts/fetch_ecos.py --discover  # 통계표 코드를 다시 검색만 하고 종료
"""
import argparse
import os
import sys
from datetime import datetime

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

ECOS_API_KEY = os.environ.get("ECOS_API_KEY")
if not ECOS_API_KEY:
    sys.exit("ECOS_API_KEY가 .env에 설정되어 있지 않습니다.")

BASE_URL = "https://ecos.bok.or.kr/api"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "ecos_raw.csv")

START_DATE = "200001"  # YYYYMM, M2/총자산 등은 이보다 앞서도 있지만 2000년부터로 충분
END_DATE = datetime.today().strftime("%Y%m")

# STAT_CODE / ITEM_CODE는 StatisticTableList, StatisticItemList API로 확인한 값.
# (컬럼명, STAT_CODE, ITEM_CODE, 주기, 설명)
STAT_ITEMS = [
    ("bok_total_assets", "103Y002", "BCAA1", "M", "한국은행 주요계정(말잔) - 자산합계(총자산)"),
    ("msb_balance", "103Y002", "BCAA215", "M", "한국은행 주요계정(말잔) - 통화안정증권발행"),
    ("rp_sale_balance", "103Y002", "BCAA216", "M", "한국은행 주요계정(말잔) - 환매조건부채권매각(RP매각)"),
    ("m2", "161Y008", "BBGA00", "M", "M2(말잔, 원계열) [신지표, 2003.10~]"),
    ("mmf", "161Y008", "BBGA04", "M", "M2 구성항목 중 MMF(말잔, 원계열)"),
]

# 분기(Q) 주기 통계 - 대출행태서베이(대출수요), BOK 확산지수(-100~100)
QUARTERLY_STAT_ITEMS = [
    ("credit_card_loan_demand", "514Y003", "PP", "Q", "대출행태서베이(대출수요) - 신용카드회사"),
]

START_DATE_Q = "2013Q4"  # 신용카드회사 항목 수록 시작
END_DATE_Q = None  # main()에서 현재 분기로 채움


def discover_candidates():
    """전체 통계표 목록을 받아 키워드로 후보를 찾아 콘솔에 출력한다 (확인용)."""
    print("전체 통계표 목록 조회 중...")
    rows = []
    start = 1
    page = 1000
    while True:
        end = start + page - 1
        url = f"{BASE_URL}/StatisticTableList/{ECOS_API_KEY}/json/kr/{start}/{end}/"
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        block = r.json().get("StatisticTableList")
        if not block:
            print("응답 오류:", r.json())
            return
        rows.extend(block["row"])
        total = block["list_total_count"]
        if end >= total:
            break
        start = end + 1
    print(f"총 {len(rows)}개 통계표")

    keywords = ["총자산", "대차대조표", "통화안정증권", "환매조건부", "정부예금", "국고금", "M2", "한국은행"]
    for kw in keywords:
        print(f"\n=== 키워드: '{kw}' ===")
        for m in rows:
            if kw in (m.get("STAT_NAME") or "") and m.get("SRCH_YN") == "Y":
                print(f"  {m['STAT_CODE']:>12} | {m['CYCLE']:>3} | {m['STAT_NAME']}")

    print("\n현재 스크립트가 실제로 사용하는 코드:")
    for col, stat_code, item_code, cycle, desc in STAT_ITEMS:
        print(f"  [{col}] STAT_CODE={stat_code} ITEM_CODE={item_code} CYCLE={cycle} - {desc}")
    print("  [government_deposit] 없음 - ECOS에 월별 이상 주기의 정부예금 잔액 시계열이 존재하지 않아 제외")


def fetch_series(stat_code, item_code, cycle, start_date, end_date, parse_dates=True):
    url = (
        f"{BASE_URL}/StatisticSearch/{ECOS_API_KEY}/json/kr/1/2000/"
        f"{stat_code}/{cycle}/{start_date}/{end_date}/{item_code}"
    )
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()

    if "StatisticSearch" not in data:
        info = data.get("RESULT", {})
        print(f"  경고: {stat_code}/{item_code} 조회 실패 - {info.get('MESSAGE', data)}")
        return pd.DataFrame(columns=["date", "value"])

    rows = data["StatisticSearch"]["row"]
    df = pd.DataFrame(rows)[["TIME", "DATA_VALUE"]]
    df.columns = ["date", "value"]
    if parse_dates:
        df["date"] = pd.to_datetime(df["date"], format="%Y%m")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df


def fetch_quarterly_series(stat_code, item_code, start_date, end_date):
    df = fetch_series(stat_code, item_code, "Q", start_date, end_date, parse_dates=False)
    if df.empty:
        return df
    # "2026Q3" -> 2026-07-01 (해당 분기 첫 달) : 월별 그리드와 병합하기 위함
    quarter_start_month = {"Q1": 1, "Q2": 4, "Q3": 7, "Q4": 10}
    years = df["date"].str[:4].astype(int)
    quarters = df["date"].str[4:]
    months = quarters.map(quarter_start_month)
    df["date"] = pd.to_datetime(dict(year=years, month=months, day=1))
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--discover", action="store_true", help="통계표 코드 검색만 수행하고 종료")
    args = parser.parse_args()

    if args.discover:
        discover_candidates()
        return

    print("사용 중인 ECOS 통계표 코드:")
    for col, stat_code, item_code, cycle, desc in STAT_ITEMS + QUARTERLY_STAT_ITEMS:
        print(f"  [{col}] STAT_CODE={stat_code} ITEM_CODE={item_code} CYCLE={cycle} - {desc}")
    print("  [government_deposit] 미포함 (ECOS에 해당 월별 시계열 없음, --discover 로 검색 내역 확인 가능)\n")

    merged = None
    for col, stat_code, item_code, cycle, desc in STAT_ITEMS:
        print(f"수집 중: {col} ({stat_code}/{item_code})")
        df = fetch_series(stat_code, item_code, cycle, START_DATE, END_DATE)
        df = df.rename(columns={"value": col})
        merged = df if merged is None else merged.merge(df, on="date", how="outer")

    end_date_q = f"{datetime.today().year}Q{(datetime.today().month - 1) // 3 + 1}"
    for col, stat_code, item_code, cycle, desc in QUARTERLY_STAT_ITEMS:
        print(f"수집 중: {col} ({stat_code}/{item_code}, 분기)")
        df = fetch_quarterly_series(stat_code, item_code, START_DATE_Q, end_date_q)
        df = df.rename(columns={"value": col})
        merged = df if merged is None else merged.merge(df, on="date", how="outer")

    merged = merged.sort_values("date").reset_index(drop=True)
    merged["government_deposit"] = pd.NA

    merged["net_liquidity"] = (
        merged["bok_total_assets"] - (merged["msb_balance"] + merged["rp_sale_balance"])
    )

    merged = merged.set_index("date")
    yoy = merged["net_liquidity"] / merged["net_liquidity"].shift(12) * 100 - 100
    merged["net_liquidity_yoy"] = yoy
    merged = merged.reset_index()

    os.makedirs(DATA_DIR, exist_ok=True)
    merged.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"\n저장 완료: {OUT_PATH}")
    print(f"행 수: {len(merged)}, 기간: {merged['date'].min().date()} ~ {merged['date'].max().date()}")
    print(merged.tail(3).to_string(index=False))


if __name__ == "__main__":
    main()
