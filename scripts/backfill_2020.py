"""
위험지수 모델 학습 표본을 늘리기 위한 일회성 과거 데이터 백필(2020년부터).
fetch_krx.py/fetch_kofia.py는 기존 파일이 있으면 마지막 날짜 다음부터만
증분 수집하므로(일일 자동 갱신용 설계), 과거로 더 파고드는 이 작업은 별도로
한 번 실행한다. 완료 후엔 지울 수 있는 스크립트.
"""
import os
import sys
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import fetch_krx
import fetch_kofia

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
BACKFILL_START = date(2020, 1, 1)


def backfill_krx():
    existing = pd.read_csv(fetch_krx.OUT_PATH, parse_dates=["date"])
    earliest = existing["date"].min().date()
    backfill_end = earliest - timedelta(days=1)
    if BACKFILL_START > backfill_end:
        print("KRX: 이미 2020년 이전까지 있음, 스킵")
        return
    print(f"KRX 백필: {BACKFILL_START} ~ {backfill_end}")

    index_df = fetch_krx.fetch_kospi_index(BACKFILL_START, backfill_end)
    market_cap_df = fetch_krx.fetch_kospi_market_cap(BACKFILL_START, backfill_end)

    merged = index_df.merge(market_cap_df, on="date", how="outer") if not market_cap_df.empty else index_df
    if "kospi_market_cap" not in merged.columns:
        merged["kospi_market_cap"] = pd.NA
    for col in fetch_krx.UNAVAILABLE_COLUMNS:
        merged[col] = pd.NA

    combined = pd.concat([merged, existing], ignore_index=True)
    combined = combined.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
    combined.to_csv(fetch_krx.OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"KRX 백필 완료: {len(combined)}행, {combined['date'].min().date()} ~ {combined['date'].max().date()}")


def backfill_kofia():
    existing = pd.read_csv(fetch_kofia.OUT_PATH, parse_dates=["date"])
    earliest = existing["date"].min().date()
    backfill_end = earliest - timedelta(days=1)
    if BACKFILL_START > backfill_end:
        print("KOFIA: 이미 2020년 이전까지 있음, 스킵")
        return
    print(f"KOFIA 백필: {BACKFILL_START} ~ {backfill_end}")

    start_s, end_s = BACKFILL_START.strftime("%Y%m%d"), backfill_end.strftime("%Y%m%d")
    merged = None
    for columns, obj_nm, date_params, fixed_params in fetch_kofia.SOURCES:
        print(f"  수집 중: {obj_nm}")
        df = fetch_kofia.fetch_source(columns, obj_nm, date_params, fixed_params, start_s, end_s)
        print(f"    {len(df)}행")
        merged = df if merged is None else merged.merge(df, on="date", how="outer")

    merged = merged.sort_values("date").reset_index(drop=True)
    combined = pd.concat([merged, existing], ignore_index=True)
    combined = combined.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
    combined.to_csv(fetch_kofia.OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"KOFIA 백필 완료: {len(combined)}행, {combined['date'].min().date()} ~ {combined['date'].max().date()}")


if __name__ == "__main__":
    backfill_krx()
    backfill_kofia()
