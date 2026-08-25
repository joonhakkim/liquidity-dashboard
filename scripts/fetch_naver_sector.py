"""
네이버 금융의 업종(WICS 기반) 분류를 종목코드 기준으로 받아와 data/sector_map.csv로 저장한다.

기존에는 data/manual/*데이터 모음*.xlsm의 "섹터별 구성 종목" 시트(팀이 직접 관리하는 커스텀
섹터, 종목명 기준 매핑)만 썼는데 이건 913개 종목만 커버해서(테마성 큐레이션이라 전체 상장사를
다루지 않음) OP밴드처럼 종목이 2500개 넘는 페이지에서는 커버리지가 35%밖에 안 됐다
(2026-08-25, 사용자가 "섹터에 안 걸리는 게 많다"고 알려줘서 발견). 네이버가 자체적으로 관리하는
업종 분류(sise_group.naver?type=upjong)는 상장사 대부분을 다루는 79개 업종 그룹이라 이걸 종목
코드 기준으로 긁어오면 훨씬 넓은 커버리지를 얻을 수 있다.
"""
import os
import re

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "sector_map.csv")

BASE = "https://finance.naver.com/sise"


def fetch_sector_list():
    r = requests.get(f"{BASE}/sise_group.naver", params={"type": "upjong"}, timeout=20)
    r.encoding = "euc-kr"
    return re.findall(r'no=(\d+)">([^<]+)</a>', r.text)


def fetch_sector_members(no):
    r = requests.get(f"{BASE}/sise_group_detail.naver", params={"type": "upjong", "no": no}, timeout=20)
    r.encoding = "euc-kr"
    return re.findall(r'code=(\d{6})[^"]*">([^<]+)</a>', r.text)


def main():
    sectors = fetch_sector_list()
    print(f"업종 {len(sectors)}개 발견")

    rows = []
    for no, sector_name in sectors:
        members = fetch_sector_members(no)
        for code, name in members:
            rows.append({"code": code, "name": name, "sector": sector_name})
        print(f"  {sector_name}: {len(members)}종목")

    df = pd.DataFrame(rows).drop_duplicates(subset="code", keep="first")
    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n저장 완료: {OUT_PATH} ({len(df)}종목, 업종 {len(sectors)}개)")


if __name__ == "__main__":
    main()
