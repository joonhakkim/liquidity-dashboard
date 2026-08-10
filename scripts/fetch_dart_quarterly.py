"""
op_growth_screen.csv 116개 종목의 최근 분기별(개별 3개월) 매출액/영업이익을 DART에서 가져와
data/screening/dart_quarterly.csv 로 저장한다.

DART 재무제표 API의 thstrm_amount 필드는 reprt_code에 따라 의미가 다르다(실제 응답으로 확인함):
  11013(1분기보고서) -> thstrm_amount = 1분기 단독(3개월)
  11012(반기보고서)   -> thstrm_amount = 2분기 단독(3개월), thstrm_add_amount = 반기 누적(6개월)
  11014(3분기보고서)  -> thstrm_amount = 3분기 단독(3개월), thstrm_add_amount = 3분기 누적(9개월)
  11011(사업보고서)   -> thstrm_amount = 연간 합계(12개월), thstrm_add_amount 없음
Q1~Q3는 thstrm_amount를 그대로 쓰면 되고, Q4만 연간합계 - 3분기누적으로 계산한다.
"""
import os
import time

import pandas as pd

from dart_client import extract_account, extract_account_cumulative, get_financials, load_corp_code_map

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SCREEN_PATH = os.path.join(DATA_DIR, "screening", "op_growth_screen.csv")
OUT_PATH = os.path.join(DATA_DIR, "screening", "dart_quarterly.csv")

REVENUE_NAMES = ["매출액", "수익(매출액)", "영업수익", "매출"]
OP_NAMES = ["영업이익", "영업이익(손실)"]

QUARTERLY_YEARS = [2023, 2024, 2025, 2026]


def fetch_year_quarters(corp_code, year):
    """해당 연도의 Q1~Q4 개별(3개월) 매출·영업이익. 미공시 분기는 None."""
    result = {}

    q1 = get_financials(corp_code, year, "11013")
    time.sleep(0.15)
    result["Q1"] = (extract_account(q1, REVENUE_NAMES), extract_account(q1, OP_NAMES))

    h1 = get_financials(corp_code, year, "11012")
    time.sleep(0.15)
    result["Q2"] = (extract_account(h1, REVENUE_NAMES), extract_account(h1, OP_NAMES))

    q3rep = get_financials(corp_code, year, "11014")
    time.sleep(0.15)
    result["Q3"] = (extract_account(q3rep, REVENUE_NAMES), extract_account(q3rep, OP_NAMES))
    q3cum_rev = extract_account_cumulative(q3rep, REVENUE_NAMES)
    q3cum_op = extract_account_cumulative(q3rep, OP_NAMES)

    fy = get_financials(corp_code, year, "11011")
    time.sleep(0.15)
    fy_rev, fy_op = extract_account(fy, REVENUE_NAMES), extract_account(fy, OP_NAMES)
    q4_rev = fy_rev - q3cum_rev if fy_rev is not None and q3cum_rev is not None else None
    q4_op = fy_op - q3cum_op if fy_op is not None and q3cum_op is not None else None
    result["Q4"] = (q4_rev, q4_op)

    return result


def main():
    screened = pd.read_csv(SCREEN_PATH)
    names = screened["종목명"].tolist()
    print(f"대상 종목: {len(names)}개, 연도: {QUARTERLY_YEARS}")

    name_to_code, _, _ = load_corp_code_map()

    # 이미 처리된 종목은 건너뛴다(DART 확정 실적은 과거로 안 바뀌므로 재조회 불필요) -
    # DART 일일 API 한도(2만 건)에 걸려도 다음 실행(하루 2회 자동)에서 이어서 처리되게 하기 위함.
    rows_out = []
    done_names = set()
    if os.path.exists(OUT_PATH):
        existing = pd.read_csv(OUT_PATH)
        rows_out = existing.to_dict("records")
        done_names = set(existing["종목명"].unique())
        print(f"기존에 처리된 종목 {len(done_names)}개는 건너뜁니다.")

    todo = [n for n in names if n not in done_names]
    print(f"오늘 처리 대상: {len(todo)}개 (전체 {len(names)}개 중)")

    call_budget = 8000  # 이 스크립트 하나가 하루 API 한도를 다 쓰지 않도록 상한(나머지 스크립트 몫 남겨둠)
    calls_made = 0

    for i, name in enumerate(todo, 1):
        if calls_made >= call_budget:
            print(f"이번 실행 호출 한도({call_budget}) 도달 - 나머지는 다음 실행에서 이어서 처리합니다.")
            break
        corp_code = name_to_code.get(name)
        print(f"[{i}/{len(todo)}] {name}")
        if not corp_code:
            continue
        for year in QUARTERLY_YEARS:
            calls_made += 16  # fetch_year_quarters 1회 호출당 4개 reprt_code 조회
            try:
                quarters = fetch_year_quarters(corp_code, year)
            except Exception as e:
                print(f"  경고: {name} {year} 조회 실패 ({e})")
                continue
            for q, (rev, op) in quarters.items():
                if rev is None and op is None:
                    continue
                margin = (op / rev) if (op is not None and rev not in (None, 0)) else None
                rows_out.append({
                    "종목명": name, "연도": year, "분기": q,
                    "매출액": rev, "영업이익": op, "영업이익률": margin,
                })

        if i % 10 == 0 or i == len(todo):
            pd.DataFrame(rows_out).to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
            print(f"  중간 저장 완료 ({i}/{len(todo)}, 누적 {len(rows_out)}행)")

    pd.DataFrame(rows_out).to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n최종 저장 완료: {OUT_PATH} ({len(rows_out)}행)")


if __name__ == "__main__":
    main()
