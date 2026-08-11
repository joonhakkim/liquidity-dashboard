"""
한국은행 ECOS 국고채(3년) 시장금리(817Y002, 일별, ITEM_CODE=010200000)를 가져와
data/kr_bond_yield_raw.csv 로 저장한다.

news_sentiment_raw.csv와 같은 이유로 ecos_raw.csv(월별 지표들)와 분리 - 일별 원본을
월별 지표들과 같이 병합하면 YoY 등 shift(12) 계산이 깨진다.
"""
import os
from datetime import datetime

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

ECOS_API_KEY = os.environ.get("ECOS_API_KEY")
if not ECOS_API_KEY:
    raise SystemExit("ECOS_API_KEY가 .env에 설정되어 있지 않습니다.")

BASE_URL = "https://ecos.bok.or.kr/api"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "kr_bond_yield_raw.csv")

STAT_CODE, ITEM_CODE = "817Y002", "010200000"
START_DATE = "20000101"


def main():
    end_date = datetime.today().strftime("%Y%m%d")
    print(f"수집 중: 국고채(3년) 금리 ({STAT_CODE}/{ITEM_CODE}, 일별) [{START_DATE} ~ {end_date}]")
    url = f"{BASE_URL}/StatisticSearch/{ECOS_API_KEY}/json/kr/1/10000/{STAT_CODE}/D/{START_DATE}/{end_date}/{ITEM_CODE}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()

    if "StatisticSearch" not in data:
        info = data.get("RESULT", {})
        raise SystemExit(f"조회 실패: {info.get('MESSAGE', data)}")

    rows = data["StatisticSearch"]["row"]
    df = pd.DataFrame(rows)[["TIME", "DATA_VALUE"]]
    df.columns = ["date", "kr_bond_3y"]
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df["kr_bond_3y"] = pd.to_numeric(df["kr_bond_3y"], errors="coerce")
    df = df.dropna().sort_values("date").reset_index(drop=True)

    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUT_PATH}")
    print(f"행 수: {len(df)}, 기간: {df['date'].min().date()} ~ {df['date'].max().date()}")
    print(df.tail(5).to_string(index=False))


if __name__ == "__main__":
    main()
