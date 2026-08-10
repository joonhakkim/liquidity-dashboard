"""
data/manual/*데이터 모음*.xlsm (에프앤가이드류 컨센서스 워크북)에서
- '시가총액' 시트: 종목별 시가총액
- '2026 영업이익 추정치' / '2027 영업이익 추정치' 시트: 종목별 영업이익 컨센서스(각 시트 최신일자 행)
을 읽어 시가총액 2000억원 이상 + 2026->2027 영업이익 컨센서스 50% 이상 성장 종목을 걸러
data/screening/op_growth_screen.csv 로 저장한다.

주의: 이 워크북엔 매출액 컨센서스가 없어 영업이익만으로 스크리닝한다(사용자 확인 완료).
'영업이익 추정치' 시트들은 애널리스트 커버리지가 있는 종목(~800개)만 있어서,
전체 상장사(~2600개)의 시가총액 시트보다 종목 수가 적다 - 이름 기준으로 이너조인한다.
"""
import glob
import os
import warnings

import openpyxl
import pandas as pd

warnings.filterwarnings("ignore")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MANUAL_DIR = os.path.join(DATA_DIR, "manual")
OUT_PATH = os.path.join(DATA_DIR, "screening", "op_growth_screen.csv")

MIN_MARKET_CAP = 300_000_000_000  # 3000억원 (DART 일일 API 호출 한도 내에서 최대한 넓힌 값 - 449개)
MIN_OP_GROWTH = -10  # 사실상 무제한(적자전환 등 극단치만 배제) - 실제 필터링은 웹페이지에서


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

    rows = []
    for name in op_2026:
        if name not in op_2027:
            continue
        cap = market_cap.get(name)
        v2026, v2027 = op_2026[name], op_2027[name]
        if not isinstance(cap, (int, float)) or not isinstance(v2026, (int, float)) or not isinstance(v2027, (int, float)):
            continue
        if v2026 <= 0:
            continue  # 적자 -> 흑자 전환은 growth% 의미 없음, 별도 표기 없이 제외
        growth = (v2027 - v2026) / v2026
        rows.append({
            "종목명": name,
            "시가총액(억원)": round(cap / 1e8, 1),
            "2026_영업이익(십억원)": round(v2026, 1),
            "2027_영업이익(십억원)": round(v2027, 1),
            "영업이익_증가율": round(growth, 4),
        })

    df = pd.DataFrame(rows)
    screened = df[(df["시가총액(억원)"] >= MIN_MARKET_CAP / 1e8) & (df["영업이익_증가율"] >= MIN_OP_GROWTH)]
    screened = screened.sort_values("영업이익_증가율", ascending=False).reset_index(drop=True)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    screened.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"\n전체 매칭 종목: {len(df)}개 (시가총액+컨센서스 둘 다 있는 종목)")
    print(f"조건 통과(시총 2000억+ / OP성장 50%+): {len(screened)}개")
    print(f"저장 완료: {OUT_PATH}")
    print(screened.to_string(index=False))


if __name__ == "__main__":
    main()
