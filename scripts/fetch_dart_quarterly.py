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
import json
import os
import time

import pandas as pd

from dart_client import DartQuotaExceeded, extract_account, extract_account_cumulative, get_financials, load_corp_code_map

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SCREEN_PATH = os.path.join(DATA_DIR, "screening", "op_growth_screen.csv")
OUT_PATH = os.path.join(DATA_DIR, "screening", "dart_quarterly.csv")
# 종목별로 "어느 연도까지 (한도초과 없이) 끝까지 시도했는지"를 별도로 기록한다. 예전엔
# dart_quarterly.csv에 이름이 한 번이라도 나오면 무조건 "완료"로 치고 영원히 건너뛰었는데,
# 한도 초과(status 020) 도중 일부 연도만 채워진 회사도 똑같이 "완료"로 오판되는 버그가 있었다
# (예: 케이엠더블유가 2026 Q1만 있고 2023~2025가 통째로 빠짐). 이제는 연도 단위로 "성공적으로
# 시도 완료"한 것만 기록해서, 한도초과로 못 채운 연도는 다음 실행에서 이어서 재시도한다.
YEARS_DONE_PATH = os.path.join(DATA_DIR, "screening", "dart_quarterly_years_done.json")

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


def load_years_done():
    if os.path.exists(YEARS_DONE_PATH):
        with open(YEARS_DONE_PATH, "r", encoding="utf-8") as f:
            return {name: set(years) for name, years in json.load(f).items()}
    return {}


def save_years_done(years_done):
    with open(YEARS_DONE_PATH, "w", encoding="utf-8") as f:
        json.dump({name: sorted(years) for name, years in years_done.items()}, f, ensure_ascii=False, indent=1)


def main():
    screened = pd.read_csv(SCREEN_PATH)
    names = screened["종목명"].tolist()
    print(f"대상 종목: {len(names)}개, 연도: {QUARTERLY_YEARS}")

    name_to_code, _, _ = load_corp_code_map()

    rows_out = []
    if os.path.exists(OUT_PATH):
        rows_out = pd.read_csv(OUT_PATH).to_dict("records")

    years_done = load_years_done()
    # years_done.json이 아직 없는 첫 실행이면, 기존 dart_quarterly.csv에 있는 연도는 전부
    # "성공적으로 시도된" 연도로 간주해 부트스트랩한다(연도 내 4개 분기를 순서대로 다 채운
    # 뒤에야 다음 연도로 넘어가는 구조라, 한도초과는 항상 처리 도중이던 연도에서 끊기고 그
    # 뒤 연도는 행 자체가 없으므로 - 존재하는 연도=완료, 없는 연도=미완료로 안전하게 구분된다).
    # 4개 연도 미만인 종목도 이미 있는 연도는 재조회하지 않고 없는 연도만 이어서 채운다
    # (연도 단위로 다시 긁으면 중복 행이 생기므로).
    if not years_done and rows_out:
        existing_df = pd.DataFrame(rows_out)
        for name, g in existing_df.groupby("종목명"):
            years_done[name] = set(int(y) for y in g["연도"].unique())
        n_complete = sum(1 for yrs in years_done.values() if yrs >= set(QUARTERLY_YEARS))
        print(f"years_done.json 부트스트랩: {len(years_done)}개 종목(완료 {n_complete}개, 부분완료 {len(years_done)-n_complete}개)")

    todo = [n for n in names if set(years_done.get(n, set())) < set(QUARTERLY_YEARS)]
    print(f"오늘 처리 대상: {len(todo)}개 (전체 {len(names)}개 중, 완료 {len(names) - len(todo)}개 제외)")

    call_budget = 8000  # 이 스크립트 하나가 하루 API 한도를 다 쓰지 않도록 상한(나머지 스크립트 몫 남겨둠)
    calls_made = 0
    quota_hit = False

    for i, name in enumerate(todo, 1):
        if calls_made >= call_budget:
            print(f"이번 실행 호출 한도({call_budget}) 도달 - 나머지는 다음 실행에서 이어서 처리합니다.")
            break
        corp_code = name_to_code.get(name)
        remaining_years = [y for y in QUARTERLY_YEARS if y not in years_done.get(name, set())]
        print(f"[{i}/{len(todo)}] {name} (남은 연도: {remaining_years})")
        if not corp_code:
            years_done[name] = set(QUARTERLY_YEARS)  # corp_code 자체가 없으면 영원히 못 채우니 완료 처리
            continue
        for year in remaining_years:
            calls_made += 16  # fetch_year_quarters 1회 호출당 4개 reprt_code 조회
            try:
                quarters = fetch_year_quarters(corp_code, year)
            except DartQuotaExceeded:
                print(f"  DART 일일 호출 한도 초과 - {name} {year}은(는) 다음 실행에서 재시도합니다.")
                quota_hit = True
                break
            except Exception as e:
                print(f"  경고: {name} {year} 조회 실패 ({e}) - 다음 실행에서 재시도")
                continue
            for q, (rev, op) in quarters.items():
                if rev is None and op is None:
                    continue
                margin = (op / rev) if (op is not None and rev not in (None, 0)) else None
                rows_out.append({
                    "종목명": name, "연도": year, "분기": q,
                    "매출액": rev, "영업이익": op, "영업이익률": margin,
                })
            years_done.setdefault(name, set()).add(year)

        if quota_hit:
            break

        if i % 10 == 0 or i == len(todo):
            pd.DataFrame(rows_out).to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
            save_years_done(years_done)
            print(f"  중간 저장 완료 ({i}/{len(todo)}, 누적 {len(rows_out)}행)")

    final_df = pd.DataFrame(rows_out).drop_duplicates(subset=["종목명", "연도", "분기"], keep="last")
    final_df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    save_years_done(years_done)
    print(f"\n최종 저장 완료: {OUT_PATH} ({len(final_df)}행)")


if __name__ == "__main__":
    main()
