"""
한국은행 ECOS 뉴스심리지수(523Y001, 일별 실험적 통계, ITEM_CODE=A001)를 가져와
data/news_sentiment_raw.csv 로 저장한다.

2026-09-01, 한은이 "언어모형(AI/자연어처리)을 활용한 신뉴스심리지수"로 산출체계를 전면
개편해서 공표를 시작 - 기존에 쓰던 521Y001은 이날부로 API에서 완전히 사라졌고(과거
날짜를 조회해도 데이터 없음 응답), 그 대체가 이 523Y001이다. 값 자체도 다르다(예:
2026-08-30 기준 구 521Y001=107.38 vs 신 523Y001=100.15) - 같은 개념(뉴스 톤 심리지수)의
후속 시리즈일 뿐 서로 다른 산출 방식이라 시계열이 그 시점에서 완전히 이어지지 않는다.
column명(news_sentiment_index)은 build_dashboard.py 하위 호환을 위해 그대로 유지.

일부러 ecos_raw.csv(월별 지표들)와 분리했다 - us_real_rate_10y(일별)를 fred_raw.csv의
월별 지표들과 같이 병합했다가 YoY 계산이 깨졌던 전례가 있어서, 일별/월별 원본은 항상
따로 두고 build_dashboard.py의 병합 단계에서만 합친다.

100 = 중립(과거 뉴스 텍스트 감성분석의 평균), 100 초과 = 평소보다 긍정적 뉴스 톤,
100 미만 = 평소보다 부정적 톤.
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
OUT_PATH = os.path.join(DATA_DIR, "news_sentiment_raw.csv")

STAT_CODE, ITEM_CODE = "523Y001", "A001"
START_DATE = "20050101"


def main():
    end_date = datetime.today().strftime("%Y%m%d")
    print(f"수집 중: 뉴스심리지수 ({STAT_CODE}/{ITEM_CODE}, 일별) [{START_DATE} ~ {end_date}]")
    url = f"{BASE_URL}/StatisticSearch/{ECOS_API_KEY}/json/kr/1/10000/{STAT_CODE}/D/{START_DATE}/{end_date}/{ITEM_CODE}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()

    if "StatisticSearch" not in data:
        info = data.get("RESULT", {})
        raise SystemExit(f"조회 실패: {info.get('MESSAGE', data)}")

    rows = data["StatisticSearch"]["row"]
    df = pd.DataFrame(rows)[["TIME", "DATA_VALUE"]]
    df.columns = ["date", "news_sentiment_index"]
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df["news_sentiment_index"] = pd.to_numeric(df["news_sentiment_index"], errors="coerce")
    df = df.dropna().sort_values("date").reset_index(drop=True)

    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUT_PATH}")
    print(f"행 수: {len(df)}, 기간: {df['date'].min().date()} ~ {df['date'].max().date()}")
    print(df.tail(5).to_string(index=False))


if __name__ == "__main__":
    main()
