"""
코스피/코스닥 전종목 시가총액을 섹터(네이버 업종분류, data/sector_map.csv)별로 합산해서
"지금 시장 전체가 섹터별로 얼마씩 들고 있는지"를 담은 벤치마크 테이블을 만든다.

MP 트래커의 섹터별 OW/UW(오버웨이트/언더웨이트) 비교(build_troy_mp_page.py)가 이 파일을
쓴다 - MP가 특정 섹터를 시장 평균보다 많이/적게 들고 있는지 보려면 "시장 평균 섹터 비중"이
있어야 하는데, 그게 이 파일이다(2026-09-15 사용자 요청).

섹터 분류는 fetch_naver_sector.py가 만드는 sector_map.csv를 그대로 쓴다 - MP 종목의 섹터도
같은 소스(load_naver_sector_map)로 조회해야 두 쪽 라벨이 일치해서 비교가 의미 있다(매매일지에
직접 써넣는 sector 컬럼은 사람이 자유롭게 붙인 라벨이라 79개 업종 분류와 이름이 안 맞을 수 있음
- 그래서 OW/UW 비교에는 안 씀).

시가총액은 KRX Open API 종목별 일별매매정보(stk_bydd_trd=코스피, ksq_bydd_trd=코스닥)에서
받는다(fetch_krx.py/fetch_investor_flow.py와 동일 소스) - 최근 며칠은 KRX가 아직 발표 전일
수 있어서 당일부터 최대 10일 전까지 값이 나올 때까지 하루씩 물러난다.

출력: data/market_sector_weights.csv
  sector, kospi_mktcap, kospi_weight_pct, kosdaq_mktcap, kosdaq_weight_pct,
  combined_mktcap, combined_weight_pct, asof_date
"""
import os
from datetime import datetime, timedelta

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "market_sector_weights.csv")
SECTOR_MAP_PATH = os.path.join(DATA_DIR, "sector_map.csv")

KRX_OPEN_API_KEY = os.environ.get("KRX_OPEN_API_KEY")
STK_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
KSQ_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"


def fetch_latest(url, max_lookback=10):
    if not KRX_OPEN_API_KEY:
        return None, []
    headers = {"AUTH_KEY": KRX_OPEN_API_KEY}
    day = datetime.today().date()
    for _ in range(max_lookback):
        try:
            r = requests.get(url, params={"basDd": day.strftime("%Y%m%d")}, headers=headers, timeout=20)
        except requests.RequestException:
            day -= timedelta(days=1)
            continue
        if r.status_code == 200:
            data = r.json().get("OutBlock_1", [])
            if data:
                return day, data
        day -= timedelta(days=1)
    return None, []


def aggregate(rows, code_to_sector):
    sums = {}
    for r in rows:
        code = r.get("ISU_CD")
        mktcap = pd.to_numeric(r.get("MKTCAP"), errors="coerce")
        if not code or mktcap is None or pd.isna(mktcap):
            continue
        sector = code_to_sector.get(code, "미분류")
        sums[sector] = sums.get(sector, 0) + mktcap
    return sums


def main():
    if not os.path.exists(SECTOR_MAP_PATH):
        print("data/sector_map.csv가 없습니다. fetch_naver_sector.py를 먼저 실행하세요.")
        return
    sector_map = pd.read_csv(SECTOR_MAP_PATH, dtype={"code": str})
    code_to_sector = dict(zip(sector_map["code"], sector_map["sector"]))

    kospi_date, kospi_rows = fetch_latest(STK_URL)
    kosdaq_date, kosdaq_rows = fetch_latest(KSQ_URL)
    if not kospi_rows and not kosdaq_rows:
        print("KRX 전종목 시가총액을 받지 못했습니다(KRX_OPEN_API_KEY 확인 필요).")
        return
    dates = [d for d in (kospi_date, kosdaq_date) if d is not None]
    asof = max(dates)
    print(f"기준일: 코스피 {kospi_date}({len(kospi_rows)}종목), 코스닥 {kosdaq_date}({len(kosdaq_rows)}종목)")

    kospi_sums = aggregate(kospi_rows, code_to_sector)
    kosdaq_sums = aggregate(kosdaq_rows, code_to_sector)
    all_sectors = sorted(set(kospi_sums) | set(kosdaq_sums))

    kospi_total = sum(kospi_sums.values()) or 1
    kosdaq_total = sum(kosdaq_sums.values()) or 1
    combined_total = kospi_total + kosdaq_total

    rows = []
    for sector in all_sectors:
        k = kospi_sums.get(sector, 0)
        q = kosdaq_sums.get(sector, 0)
        rows.append({
            "sector": sector,
            "kospi_mktcap": round(k, 0), "kospi_weight_pct": round(k / kospi_total * 100, 3),
            "kosdaq_mktcap": round(q, 0), "kosdaq_weight_pct": round(q / kosdaq_total * 100, 3),
            "combined_mktcap": round(k + q, 0), "combined_weight_pct": round((k + q) / combined_total * 100, 3),
            "asof_date": asof.strftime("%Y-%m-%d"),
        })
    df = pd.DataFrame(rows).sort_values("combined_weight_pct", ascending=False)

    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n저장 완료: {OUT_PATH} ({len(df)}개 섹터)")
    print(df.head(10)[["sector", "kospi_weight_pct", "kosdaq_weight_pct", "combined_weight_pct"]].to_string(index=False))


if __name__ == "__main__":
    main()
