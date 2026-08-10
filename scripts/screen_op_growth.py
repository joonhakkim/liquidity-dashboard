"""
data/manual/*데이터 모음*.xlsm (에프앤가이드류 컨센서스 워크북)에서
- '시가총액' 시트: 종목별 시가총액
- '2026 영업이익 추정치' / '2027 영업이익 추정치' 시트: 종목별 영업이익 컨센서스(각 시트 최신일자 행)
을 읽어 시가총액이 있는 전체 상장사를 data/screening/op_growth_screen.csv 로 저장한다.
컨센서스(영업이익 추정치)는 애널리스트 커버리지가 있는 종목(~800개)만 있어서 없는 종목은
2026/2027 영업이익·증가율 컬럼이 비어있다 - 컨센서스가 없어도 DART 실적 트래킹은 하기 위해
시가총액만 있으면 포함시킨다(이름 기준 아우터조인).

주의: 이 워크북엔 매출액 컨센서스가 없어 영업이익만으로 스크리닝한다(사용자 확인 완료).
"""
import glob
import os
import re
import warnings

import openpyxl
import pandas as pd

warnings.filterwarnings("ignore")

EXCLUDE_NAME_RE = re.compile(r"스팩|기업인수목적|ETF|ETN$")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MANUAL_DIR = os.path.join(DATA_DIR, "manual")
OUT_PATH = os.path.join(DATA_DIR, "screening", "op_growth_screen.csv")

MIN_MARKET_CAP = 0  # 시총 제한 없음 - 컨센서스(영업이익 추정치) 있는 종목 전체(~740개)가 사실상 상한
MIN_OP_GROWTH = -10  # 사실상 무제한(적자전환 등 극단치만 배제) - 실제 필터링은 웹페이지에서
# DART 일일 API 호출 한도(2만 건) 때문에 하루에 전체를 다 못 돈다 - fetch_dart_quarterly.py /
# fetch_valuation_bands.py / fetch_dart_preliminary.py 가 이미 처리된 종목은 건너뛰므로,
# 하루 2회 자동 실행(run_pipeline.py)이 며칠 반복되면 전체가 자연스럽게 채워진다.


def find_workbook():
    candidates = glob.glob(os.path.join(MANUAL_DIR, "*데이터 모음*.xls*"))
    candidates = [c for c in candidates if not os.path.basename(c).startswith("~$")]
    if not candidates:
        raise SystemExit("data/manual/ 에 '데이터 모음' 워크북이 없습니다.")
    return max(candidates, key=os.path.getmtime)


def read_market_cap(wb):
    ws = wb["시가총액"]
    rows = list(ws.iter_rows(values_only=True))
    code_row_idx = next(
        i for i, r in enumerate(rows) if r[0] == "Code" and isinstance(r[1], str) and r[1].startswith("A")
    )
    names = rows[code_row_idx + 1]
    date_row_idx = next(i for i in range(code_row_idx + 2, len(rows)) if rows[i][0] == "D A T E") + 1
    latest = rows[date_row_idx]
    print(f"  시가총액 기준일: {latest[0]}")
    return {names[i]: latest[i] for i in range(1, len(names)) if names[i] and latest[i] is not None}


def read_op_estimate(wb, sheet_name):
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    names = rows[1]
    latest = rows[2]  # 날짜 내림차순 정렬이라 2행이 최신
    print(f"  {sheet_name} 기준일: {latest[0]}")
    return {
        names[i]: latest[i]
        for i in range(1, len(names))
        if names[i] and names[i] != "합계" and latest[i] is not None
    }


def main():
    path = find_workbook()
    print(f"워크북 로딩: {os.path.basename(path)}")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_vba=False)

    print("시가총액 읽는 중...")
    market_cap = read_market_cap(wb)
    print(f"  {len(market_cap)}개 종목")

    print("2026 영업이익 추정치 읽는 중...")
    op_2026 = read_op_estimate(wb, "2026 영업이익 추정치")
    print("2027 영업이익 추정치 읽는 중...")
    op_2027 = read_op_estimate(wb, "2027 영업이익 추정치")

    # 컨센서스(영업이익 추정치)가 없어도 시가총액만 있으면 포함한다 - 성장률/컨센서스 필터는
    # 페이지에서 걸고, 여기서는 "실적 트래킹 대상"을 최대한 넓게(전체 상장사) 잡는다.
    # 컨센서스가 있는 종목만 OP2026/OP2027/증가율이 채워지고, 없는 종목은 None으로 둔다.
    rows = []
    for name, cap in market_cap.items():
        if not isinstance(cap, (int, float)):
            continue
        if EXCLUDE_NAME_RE.search(name):
            continue  # 스팩(SPAC)·ETF·ETN 등 실적 추적 대상이 아닌 종목 제외
        v2026 = op_2026.get(name)
        v2027 = op_2027.get(name)
        growth = None
        if isinstance(v2026, (int, float)) and isinstance(v2027, (int, float)) and v2026 > 0:
            growth = round((v2027 - v2026) / v2026, 4)
        rows.append({
            "종목명": name,
            "시가총액(억원)": round(cap / 1e8, 1),
            "2026_영업이익(십억원)": round(v2026, 1) if isinstance(v2026, (int, float)) else None,
            "2027_영업이익(십억원)": round(v2027, 1) if isinstance(v2027, (int, float)) else None,
            "영업이익_증가율": growth,
        })

    df = pd.DataFrame(rows)
    screened = df[df["시가총액(억원)"] >= MIN_MARKET_CAP / 1e8]
    # 시가총액 큰 종목부터 먼저 처리되도록 정렬한다 - DART 백필이 며칠 걸리는데(2500여개),
    # 성장률 순으로 두면 삼성전자 같은 대형주가 한참 뒤로 밀려서 오래 기다려야 했다.
    # 페이지 자체의 기본 정렬(OP 증가율)은 화면(JS)에서 따로 적용되므로 순서를 바꿔도 무방.
    screened = screened.sort_values(
        "시가총액(억원)", ascending=False, na_position="last"
    ).reset_index(drop=True)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    screened.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    has_consensus = screened["영업이익_증가율"].notna().sum()
    print(f"\n전체 대상(시가총액 있는 전 종목): {len(screened)}개")
    print(f"이 중 영업이익 컨센서스(2026/2027 둘 다 흑자) 있는 종목: {has_consensus}개")
    print(f"저장 완료: {OUT_PATH}")


if __name__ == "__main__":
    main()
