"""
OP밴드 트래커의 전종목을 네이버 WiseReport(FnGuide 소스) 컨센서스 API로 교차검증한다.

경위: data/manual/*기업*밴드*.xlsx(데이터터미널 export)는 종목에 따라 먼 미래(FY+1~+2)
분기별 영업이익 컨센서스가 아예 비어있는 경우가 있다(애널리스트 커버리지 부족). 사용자가
세경하이테크로 확인해보니 엑셀엔 FY2027이 비어있는데 네이버 WiseReport(FnGuide)에는
연간 영업이익 컨센서스가 있었다 - 그래서 전종목을 이 API로도 받아와서, build_op_band.py가
현재 회계연도(사용중인 use_year) 구간에 대해 엑셀값과 대조/보정할 수 있게 한다.

API는 종목별 연간 실적표(YYMM별 매출/영업이익/순이익/EPS/BPS 등)를 반환한다. 여기서는
영업이익(OP, 억원 단위)만 뽑아서 저장한다. 과거로 되돌릴 수 없는 컨센서스라(그 시점 값을
알 방법이 없음) 오늘 시점 값만 의미가 있다 - 코스피 선행 PER 트래커와 같은 제약.
"""
import os
import time
from datetime import datetime

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SCREEN_DIR = os.path.join(DATA_DIR, "screening")
SUMMARY_PATH = os.path.join(SCREEN_DIR, "op_band_summary.csv")
OUT_PATH = os.path.join(DATA_DIR, "op_band_fnguide.csv")

WISEREPORT_URL = "https://navercomp.wisereport.co.kr/company/ajax/c1050001_data.aspx"


def fetch_op_by_year(code):
    """반환: {연도(int): 영업이익(억원)} - (E) 추정치와 (A) 확정치 모두 포함(연도가 같으면 마지막 걸로 덮어씀)."""
    try:
        r = requests.get(
            WISEREPORT_URL,
            params={"flag": "2", "cmp_cd": code, "finGubun": "MAIN", "frq": "0",
                    "sDT": datetime.today().strftime("%Y%m%d"), "chartType": "svg"},
            headers={"User-Agent": "Mozilla/5.0",
                     "Referer": f"https://navercomp.wisereport.co.kr/v2/company/c1050001.aspx?cmp_cd={code}"},
            timeout=15,
        )
        rows = r.json().get("JsonData", [])
    except Exception:
        return {}

    result = {}
    for row in rows:
        yymm = row.get("YYMM", "")
        if not yymm[:4].isdigit():
            continue
        year = int(yymm[:4])
        op = row.get("OP")
        if op in (None, ""):
            continue
        try:
            result[year] = float(str(op).replace(",", ""))
        except ValueError:
            continue
    return result


def main():
    if not os.path.exists(SUMMARY_PATH):
        print("op_band_summary.csv가 없습니다. build_op_band.py를 먼저 실행하세요.")
        return
    summary = pd.read_csv(SUMMARY_PATH, dtype={"code": str})
    codes = summary["code"].tolist()
    print(f"대상 종목: {len(codes)}개")

    rows = []
    for i, code in enumerate(codes, 1):
        naver_code = code[1:] if code.startswith("A") else code  # "A005930" -> "005930"
        op_by_year = fetch_op_by_year(naver_code)
        for year, op_100mil in op_by_year.items():
            rows.append({"code": code, "year": year, "op_100mil": op_100mil})
        if i % 100 == 0:
            print(f"  진행 {i}/{len(codes)}")
        time.sleep(0.12)

    if not rows:
        print("수집된 데이터가 없습니다.")
        return

    df = pd.DataFrame(rows)
    df["fetched_date"] = datetime.today().strftime("%Y-%m-%d")
    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n저장 완료: {OUT_PATH} ({len(df)}행, {df['code'].nunique()}종목)")


if __name__ == "__main__":
    main()
