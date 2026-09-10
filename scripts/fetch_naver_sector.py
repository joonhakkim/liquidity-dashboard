"""
네이버 금융의 업종(WICS 기반) 분류를 종목코드 기준으로 받아와 data/sector_map.csv로 저장한다.

기존에는 data/manual/*데이터 모음*.xlsm의 "섹터별 구성 종목" 시트(팀이 직접 관리하는 커스텀
섹터, 종목명 기준 매핑)만 썼는데 이건 913개 종목만 커버해서(테마성 큐레이션이라 전체 상장사를
다루지 않음) OP밴드처럼 종목이 2500개 넘는 페이지에서는 커버리지가 35%밖에 안 됐다
(2026-08-25, 사용자가 "섹터에 안 걸리는 게 많다"고 알려줘서 발견). 네이버가 자체적으로 관리하는
업종 분류(79개 업종 그룹)는 상장사 대부분을 다뤄서 이걸 종목코드 기준으로 긁어오면 훨씬 넓은
커버리지를 얻는다.

2026-09-11: 네이버가 finance.naver.com/sise/sise_group.naver 를 SPA(Next.js)로 개편하면서
기존 HTML 정규식 스크래핑이 깨졌다(sector_map.csv가 빈 파일이 돼서 OP밴드/이익추정치/OP밴드v2
빌드가 전부 실패). 새 데이터 API(m.stock.naver.com/api/stocks/industry)로 교체한다:
  - 업종 목록: GET /api/stocks/industry?page=1&pageSize=100  -> {"groups":[{"no":..,"name":..}, ...]}
  - 업종별 종목: GET /api/stocks/industry/{no}?page=N&pageSize=100 -> {"stocks":[{"itemCode":..,"stockName":..}], "totalCount":..}
"""
import os
import time

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "sector_map.csv")

API = "https://m.stock.naver.com/api/stocks/industry"
HEADERS = {"User-Agent": "Mozilla/5.0"}
PAGE_SIZE = 100


def get_json(url, params=None, tries=3):
    last = None
    for _ in range(tries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.0)
    raise SystemExit(f"네이버 API 요청 실패 ({url}): {last}")


def fetch_sector_list():
    d = get_json(API, {"page": 1, "pageSize": PAGE_SIZE})
    return [(g["no"], g["name"]) for g in d.get("groups", [])]


def fetch_sector_members(no):
    """업종 하나의 전 종목. totalCount를 보고 페이지를 끝까지 넘긴다."""
    members, page = [], 1
    while True:
        d = get_json(f"{API}/{no}", {"page": page, "pageSize": PAGE_SIZE})
        stocks = d.get("stocks", [])
        for s in stocks:
            code = s.get("itemCode")
            name = s.get("stockName")
            if code and name:
                members.append((code, name))
        total = d.get("totalCount", len(members))
        if len(members) >= total or not stocks:
            break
        page += 1
        time.sleep(0.15)
    return members


def main():
    sectors = fetch_sector_list()
    if not sectors:
        raise SystemExit("업종 목록이 비어 있습니다 - 네이버 API 응답 형식이 또 바뀌었을 수 있습니다.")
    print(f"업종 {len(sectors)}개 발견")

    rows = []
    for no, sector_name in sectors:
        members = fetch_sector_members(no)
        for code, name in members:
            rows.append({"code": code, "name": name, "sector": sector_name})
        print(f"  {sector_name}: {len(members)}종목")
        time.sleep(0.1)

    df = pd.DataFrame(rows).drop_duplicates(subset="code", keep="first")
    if len(df) < 1000:
        raise SystemExit(f"수집된 종목이 {len(df)}개뿐입니다 - 비정상. 기존 파일을 덮어쓰지 않습니다.")

    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n저장 완료: {OUT_PATH} ({len(df)}종목, 업종 {len(sectors)}개)")


if __name__ == "__main__":
    main()
