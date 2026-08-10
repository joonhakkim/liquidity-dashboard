"""
op_growth_screen.csv에 있는 116개 종목에 대해 DART에서:
1) 2분기(Q2) 실적 YoY 성장률 - 반기누적(11012) - 1분기누적(11013) = 개별 Q2, 전년 동기 대비 비교
2) 최근 6개 연도 사업보고서(11011)의 매출액/영업이익 - 매출·영업이익 추이 차트용
를 받아 data/screening/dart_financials.csv, data/screening/dart_annual_trend.csv 로 저장한다.

DART 재무제표는 회사마다 계정명 표기가 달라(매출액/수익(매출액)/영업수익 등) 여러 후보명을 시도한다.
호출량이 많아(회사당 ~10회) 시간이 걸린다 - 진행 상황을 계속 출력한다.
"""
import os
import time

import pandas as pd

from dart_client import extract_account, get_financials, load_corp_code_map

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SCREEN_PATH = os.path.join(DATA_DIR, "screening", "op_growth_screen.csv")
FIN_OUT_PATH = os.path.join(DATA_DIR, "screening", "dart_financials.csv")
TREND_OUT_PATH = os.path.join(DATA_DIR, "screening", "dart_annual_trend.csv")

REVENUE_NAMES = ["매출액", "수익(매출액)", "영업수익", "매출"]
OP_NAMES = ["영업이익", "영업이익(손실)"]

CURRENT_YEAR = 2026
ANNUAL_YEARS = list(range(CURRENT_YEAR - 6, CURRENT_YEAR))  # 최근 6개년 사업보고서


def q2_standalone(corp_code, year):
    """해당 연도 개별(discrete) 2분기 매출/영업이익. 반기누적 - 1분기누적."""
    q1 = get_financials(corp_code, year, "11013")
    time.sleep(0.15)
    half = get_financials(corp_code, year, "11012")
    time.sleep(0.15)
    if not q1 or not half:
        return None, None
    rev_q1 = extract_account(q1, REVENUE_NAMES)
    rev_half = extract_account(half, REVENUE_NAMES)
    op_q1 = extract_account(q1, OP_NAMES)
    op_half = extract_account(half, OP_NAMES)
    rev_q2 = rev_half - rev_q1 if rev_half is not None and rev_q1 is not None else None
    op_q2 = op_half - op_q1 if op_half is not None and op_q1 is not None else None
    return rev_q2, op_q2


def main():
    screened = pd.read_csv(SCREEN_PATH)
    names = screened["종목명"].tolist()
    print(f"대상 종목: {len(names)}개")

    name_to_code, _, _ = load_corp_code_map()

    fin_rows = []
    trend_rows = []

    for i, name in enumerate(names, 1):
        corp_code = name_to_code.get(name)
        print(f"[{i}/{len(names)}] {name} ({'corp_code 못찾음' if not corp_code else corp_code})")
        if not corp_code:
            fin_rows.append({"종목명": name, "상태": "corp_code_not_found"})
            continue

        try:
            rev_q2_this, op_q2_this = q2_standalone(corp_code, CURRENT_YEAR)
            rev_q2_prev, op_q2_prev = q2_standalone(corp_code, CURRENT_YEAR - 1)
        except Exception as e:
            print(f"  경고: {name} Q2 조회 실패 ({e})")
            fin_rows.append({"종목명": name, "상태": f"error: {e}"})
            continue

        rev_yoy = (rev_q2_this / rev_q2_prev - 1) if rev_q2_this and rev_q2_prev else None
        op_yoy = (op_q2_this / op_q2_prev - 1) if op_q2_this and op_q2_prev else None

        fin_rows.append({
            "종목명": name,
            "상태": "ok" if (rev_q2_this is not None or op_q2_this is not None) else "no_data",
            f"{CURRENT_YEAR}Q2_매출액": rev_q2_this,
            f"{CURRENT_YEAR-1}Q2_매출액": rev_q2_prev,
            "매출액_YoY": rev_yoy,
            f"{CURRENT_YEAR}Q2_영업이익": op_q2_this,
            f"{CURRENT_YEAR-1}Q2_영업이익": op_q2_prev,
            "영업이익_YoY": op_yoy,
        })

        # 연간 추이(최근 6개년 사업보고서)
        for year in ANNUAL_YEARS:
            try:
                rows = get_financials(corp_code, year, "11011")
                time.sleep(0.15)
            except Exception as e:
                print(f"  경고: {name} {year} 사업보고서 조회 실패 ({e})")
                continue
            if not rows:
                continue
            rev = extract_account(rows, REVENUE_NAMES)
            op = extract_account(rows, OP_NAMES)
            if rev is None and op is None:
                continue
            trend_rows.append({"종목명": name, "연도": year, "매출액": rev, "영업이익": op})

        # 중간 저장(오래 걸리는 작업이라 중단돼도 여기까지는 남도록)
        if i % 10 == 0 or i == len(names):
            pd.DataFrame(fin_rows).to_csv(FIN_OUT_PATH, index=False, encoding="utf-8-sig")
            pd.DataFrame(trend_rows).to_csv(TREND_OUT_PATH, index=False, encoding="utf-8-sig")
            print(f"  중간 저장 완료 ({i}/{len(names)})")

    pd.DataFrame(fin_rows).to_csv(FIN_OUT_PATH, index=False, encoding="utf-8-sig")
    pd.DataFrame(trend_rows).to_csv(TREND_OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n최종 저장 완료: {FIN_OUT_PATH}, {TREND_OUT_PATH}")


if __name__ == "__main__":
    main()
