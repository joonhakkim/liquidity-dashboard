"""
코스피/코스닥 투자자별(개인/기관/외국인) 순매수를 네이버 금융 신버전 모바일 API에서
받아와 data/investor_flow_raw.csv 로 저장한다.

  https://m.stock.naver.com/api/index/{KOSPI|KOSDAQ}/integration
  응답의 "dealTrendInfo" 필드: {bizdate, personalValue, foreignValue, institutionalValue}
  단위 억원(기존 구형 페이지와 동일 스케일로 추정 - 억원 x 100 = 백만원 저장, 기존 xlsm
  산출값과 스케일 맞춤).

과거 경위(중요):
- 원래는 KRX data.krx.co.kr 투자자별 API를 썼는데 로그인 세션이 필요해지면서 죽었고,
  그 뒤 사용자가 데이터터미널에서 내려받는 "수급정리*.xlsm"을 수동 파싱했다. 이건
  파일을 사람이 직접 갱신해야 해서 자주 밀렸다(2026-09, 8/10에서 한 달 정지).
- 2026-09-11: 네이버 구형 집계 페이지(investorDealTrendDay.naver)로 교체.
- 2026-09-22: 그 구형 페이지도 네이버가 서비스 종료(HTTP 410, "이 페이지는 더 이상
  제공되지 않습니다")해서 조용히 며칠째 갱신이 멈춰있던 걸 발견(사용자 지적 -
  "유동성지표 갱신이 안되는데"). 신버전 모바일 API(m.stock.naver.com)로 교체.
  **중요한 제약**: 이 신버전 API는 "오늘" 스냅샷만 주고 과거 날짜 조회 파라미터가
  없다(bizdate/date 파라미터를 줘도 무시하고 항상 오늘 값만 돌려줌 - 확인됨). 그래서
  구형 페이지처럼 몇 년치를 한 번에 백필하는 건 이제 불가능하고, 매일 실행 시점의
  "오늘" 값만 하루하루 누적해서 쌓는 방식으로 바뀌었다. 이 스크립트가 끊겨있던 기간
  (약 2026-09-17~21)은 구조적으로 못 채운다 - 필요하면 그 구간만 수동으로 다른
  소스(KOFIA, 데이터터미널 등)에서 채워야 한다.

외국인 보유비중(%)은 이 소스에도 없어 계속 비워둔다.
"""
import os
from datetime import datetime

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "investor_flow_raw.csv")

URL_TMPL = "https://m.stock.naver.com/api/index/{code}/integration"
HEADERS = {"User-Agent": "Mozilla/5.0"}
EOK_TO_MILLION = 100.0  # 억원 -> 백만원


def fetch_today(index_code):
    """{code}/integration의 dealTrendInfo에서 오늘자 (날짜, 개인, 외국인, 기관) 백만원 단위로."""
    r = requests.get(URL_TMPL.format(code=index_code), headers=HEADERS, timeout=15)
    r.raise_for_status()
    info = r.json().get("dealTrendInfo") or {}
    bizdate = info.get("bizdate")
    if not bizdate:
        return None
    d = datetime.strptime(bizdate, "%Y%m%d").date()

    def to_num(v):
        if v is None:
            return None
        try:
            return float(str(v).replace(",", "")) * EOK_TO_MILLION
        except ValueError:
            return None

    return d, {
        "indiv": to_num(info.get("personalValue")),
        "foreign": to_num(info.get("foreignValue")),
        "inst": to_num(info.get("institutionalValue")),
    }


def main():
    existing = pd.read_csv(OUT_PATH, parse_dates=["date"]) if os.path.exists(OUT_PATH) else None

    print("네이버(신버전) 투자자별 매매동향 수집 중(오늘자 스냅샷만 제공)...")
    kospi = fetch_today("KOSPI")
    kosdaq = fetch_today("KOSDAQ")
    if kospi is None and kosdaq is None:
        print("수집 실패 - API 응답에 dealTrendInfo 없음")
        return

    d = (kospi or kosdaq)[0]
    k = kospi[1] if kospi else {}
    q = kosdaq[1] if kosdaq else {}
    rec = {"date": pd.Timestamp(d)}
    for pfx, key in (("indiv", "indiv"), ("inst", "inst"), ("foreign", "foreign")):
        kv, qv = k.get(key), q.get(key)
        rec[f"{pfx}_net_kospi"] = kv
        rec[f"{pfx}_net_kosdaq"] = qv
        rec[f"{pfx}_net_total"] = (kv or 0) + (qv or 0) if (kv is not None or qv is not None) else None
    merged = pd.DataFrame([rec])

    if existing is not None and len(existing):
        combined = pd.concat([existing, merged], ignore_index=True)
        combined = combined.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
    else:
        combined = merged

    os.makedirs(DATA_DIR, exist_ok=True)
    combined.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n저장 완료: {OUT_PATH}")
    print(f"행 수: {len(combined)}, 기간: {combined['date'].min().date()} ~ {combined['date'].max().date()}")
    print(combined.tail(4).to_string(index=False))


if __name__ == "__main__":
    main()
