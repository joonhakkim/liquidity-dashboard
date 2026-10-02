# -*- coding: utf-8 -*-
"""
섹터별 YoY 가속화 트래커 - docs/sector_yoy_accel_tracker.html (2026-09-29 사용자 요청
"섹터기준으로 한번 만들어볼건데 YOY를 %에 대한 평균으로 섹터별 YOY가속화 트래커를
만들어보자 인터페이스는 YOY 가속화 트래커 그대로").

build_yoy_accel_tracker.py와 같은 원본(data/manual/QoQ 계산.xlsx)에서 종목별 분기 YoY%를
뽑는 것까지는 동일하고, 그 다음이 다르다 - 종목별 행 대신 "사용자가 정한 42개 섹터"(data/manual/
섹터 정리.xlsx, load_custom_sector_map)로 묶어서 분기마다 그 섹터에 속한 종목들의 YoY%를
집계한 행을 만든다. 섹터 정리.xlsx에 없는(미분류) 종목은 어느 섹터에도 넣지 않는다
(2026-09-29 "기존 내가 제시한 섹터가 아닌거는 지워버리자" 결정과 같은 원칙).

집계 방식 3가지를 같이 계산해서 페이지 안에서 토글로 비교할 수 있게 했다(2026-09-29
사용자 요청 "여러가지 버전을 만들어보고 비교해보자" - 단순평균은 종목 하나의 극단치
(특히 적자 근처에서 조금만 좋아져도 %가 수천%로 튀는 베이스효과)에 너무 휘둘린다는
문제를 같이 짚은 뒤 나온 요청):
  1) simple  - 그 분기에 숫자(%)가 있는 종목 전체의 단순평균(기존 방식)
  2) trimmed - 그 분기 값들을 정렬해서 아래 5% / 위 15%를 잘라내고 남은 것만 평균(절사평균,
     비대칭) - 표본이 작으면 그만큼 덜(또는 안) 잘리므로 사실상 simple과 같아질 수 있다
     (지어내지 않음). 왜 대칭이 아닌 5%/15%인지는 trimmed_mean() 주석 참고.
  3) opsum   - "그 분기 섹터 소속 종목들의 영업이익(원) 합계"끼리 전년동기대비 - 개별
     종목 YoY%를 평균 내는 대신, 금액을 먼저 합산한 뒤 딱 한 번만 비율을 낸다. 큰 회사의
     실제 이익 기여도가 자연히 반영되고(암묵적 시총/이익 가중), 작은 회사의 베이스효과
     극단치에 덜 휘둘린다. 부호가 섞이면(합계가 적자<->흑자 전환) 개별 종목과 같은 원칙으로
     숫자 대신 흑전/적전/적자 라벨을 쓴다. 두 분기 모두 컨센서스가 있는 종목만 골라 그
     종목들끼리만 두 분기를 합산한다(2026-09-29 사용자 지적 - "분기추정치가 없어지면서
     음수로 변하는게 너무 크다") - 먼 미래 분기일수록 컨센서스 있는 종목 수가 줄어드는데,
     이번 분기/작년 동기를 각각 "그때 있던 종목 전부"로 따로 합산하면 모집단 자체가
     달라져서 실제로는 안 줄었는데 합계가 확 줄어드는 착시가 생겼었다.

세 방식 모두 가속도(%p)/세자리유지/가속화유지는 섹터를 하나의 가상 종목처럼 취급해서
종목별 페이지와 동일한 정의를 그 방식의 YoY 시계열에 그대로 적용한다. 실적/추정 구분은
그 분기에 기여한 종목들의 다수결로 정한다.

밸류에이션 등급(OP밴드 바텀 대비 %)은 집계 방식과 무관하게 섹터당 하나만 계산한다 -
집계 방식이 바뀌어도 "그 섹터가 싼지"는 안 바뀌어야 정상이라서.
"""
import json
import os
import statistics
from datetime import datetime

import openpyxl
import pandas as pd

from build_yoy_accel_tracker import (
    MKTCAP_CHART_START, add_month, compute_yoy, find_workbook, load_custom_sector_map,
    load_op_band_gaps, month_end, process_sheet,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
SCREEN_DIR = os.path.join(DATA_DIR, "screening")
PAGE_OUT_PATH = os.path.join(DOCS_DIR, "sector_yoy_accel_tracker.html")
DETAIL_OUT_DIR = os.path.join(DOCS_DIR, "sector_yoy_accel_tracker_data")

METHODS = ["simple", "trimmed", "opsum"]
METHOD_LABEL = {"simple": "단순평균", "trimmed": "절사평균(아래5%/위15% 제외)", "opsum": "섹터합산 OP YoY"}


def slugify(sector):
    return "".join(c if c.isalnum() else "_" for c in sector)


# 아래 5% / 위 15% 비대칭 트림(2026-09-29 결정) - 전체 654종목×12분기 4,837개 (종목,분기)
# YoY% 값을 다 모아서 분포를 실제로 조사해보니 skewness=16.3으로 극심한 우측 비대칭이었다.
# 아래쪽은 -100%(흑자->적자 직전)에서 구조적으로 막혀 있는데(중앙값 -99.3%가 최솟값과 거의
# 같음) 위쪽은 +20,000%대까지 사실상 무한히 뻗어있어서(베이스효과), 평균(+116%)이 중앙값
# (+23%)의 5배나 튀었다. 댈러스연은 Trimmed Mean PCE가 대칭이 아니라 아래 24%/위 31%로
# 위를 더 많이 자르는 것과 같은 이유 - 위로 길게 뻗은 분포는 대칭 트림으로 균형이 안 맞는다.
TRIM_LO, TRIM_HI = 0.05, 0.15


def trimmed_mean(values, trim_lo=TRIM_LO, trim_hi=TRIM_HI):
    if not values:
        return None
    vals = sorted(values)
    n = len(vals)
    k_lo, k_hi = int(n * trim_lo), int(n * trim_hi)
    core = vals[k_lo:n - k_hi] if (n - k_lo - k_hi) >= 1 else vals
    return statistics.mean(core)


def sign_aware_ratio(cur, prev):
    """개별 종목 compute_yoy와 같은 원칙 - 부호가 섞이면 숫자 대신 라벨."""
    if cur is None or prev is None or prev == 0 or cur == 0:
        return None, None
    if cur > 0 and prev > 0:
        return round((cur / prev - 1) * 100, 1), None
    if cur > 0 and prev < 0:
        return None, "흑전"
    if cur < 0 and prev > 0:
        return None, "적전"
    return None, "적자"


def series_stats(all_periods, yoy_arr, label_arr, est_arr):
    """개별 종목 build_row_and_detail과 동일한 정의(최신/전분기/스트릭/가속도)를 임의의
    (섹터 집계) yoy/label/is_estimate 시계열에 그대로 적용하는 공용 버전."""
    idxs = [i for i, (v, l) in enumerate(zip(yoy_arr, label_arr)) if v is not None or l is not None]
    if not idxs:
        return None
    latest_i = idxs[-1]

    triple_digit_streak = 0
    for i in range(latest_i, -1, -1):
        if yoy_arr[i] is not None and yoy_arr[i] >= 100:
            triple_digit_streak += 1
        else:
            break

    accel_streak = 0
    i = latest_i
    while i - 1 >= 0 and yoy_arr[i] is not None and yoy_arr[i - 1] is not None and yoy_arr[i] > yoy_arr[i - 1]:
        accel_streak += 1
        i -= 1

    latest_actual_i = next((i for i in range(len(all_periods) - 1, -1, -1) if not est_arr[i]), None)
    accel = None
    if latest_actual_i is not None and latest_actual_i + 2 < len(all_periods):
        base_yoy, target_yoy = yoy_arr[latest_actual_i], yoy_arr[latest_actual_i + 2]
        if base_yoy is not None and target_yoy is not None:
            accel = round(target_yoy - base_yoy, 1)

    return {
        "latest_i": latest_i, "latest_period": all_periods[latest_i],
        "latest_is_estimate": est_arr[latest_i], "latest_yoy": yoy_arr[latest_i],
        "latest_label": label_arr[latest_i],
        "prev_yoy": yoy_arr[latest_i - 1] if latest_i - 1 >= 0 else None,
        "triple_digit_streak": triple_digit_streak, "accel_streak": accel_streak, "accel": accel,
    }


def build_sector(sector, members, gap_map):
    all_periods = sorted({q["period"] for m in members for q in m["quarters"]})

    # 종목별 op_won을 {code: {period: op_won}}로도 따로 잡아둔다 - opsum 방식에서 "이번
    # 분기 있는 종목 전부 합"과 "작년 동기 있는 종목 전부 합"을 각각 따로 더하면, 먼 미래
    # 분기일수록 컨센서스 있는 종목 수가 줄면서 모집단 자체가 달라져(예: 작년엔 10개 종목
    # 합계, 올해는 컨센서스 있는 2개 종목 합계) 실제로는 줄지 않았는데 합계가 확 줄어드는
    # 착시가 생긴다(2026-09-29 사용자 지적 - "분기추정치가 없어지면서 음수로 변하는게 너무
    # 크다"). 그래서 비교하는 두 분기 모두에 데이터가 있는 종목만 골라 그 종목들끼리만
    # 두 분기를 더하는 "동일 종목 집합" 방식으로 고친다.
    op_by_member_period = {m["code"]: {q["period"]: q["op_won"] for q in m["quarters"]} for m in members}

    vals_by_period, est_votes_by_period = {}, {}
    for p in all_periods:
        vals, est_votes = [], []
        for m in members:
            for i, q in enumerate(m["quarters"]):
                if q["period"] != p:
                    continue
                est_votes.append(q["is_estimate"])
                if m["yoy"][i] is not None:
                    vals.append(m["yoy"][i])
        vals_by_period[p] = vals
        est_votes_by_period[p] = est_votes

    est_arr = [(sum(est_votes_by_period[p]) / len(est_votes_by_period[p])) >= 0.5 if est_votes_by_period[p] else False
               for p in all_periods]
    n_by_period = {str(p): len(vals_by_period[p]) for p in all_periods}

    yoy_simple = [round(statistics.mean(vals_by_period[p]), 1) if vals_by_period[p] else None for p in all_periods]
    yoy_trimmed = [round(trimmed_mean(vals_by_period[p]), 1) if vals_by_period[p] else None for p in all_periods]
    label_none = [None] * len(all_periods)

    yoy_opsum, label_opsum, n_opsum = [], [], []
    for p in all_periods:
        common_codes = [code for code, by_p in op_by_member_period.items() if p in by_p and (p - 100) in by_p]
        if common_codes:
            cur = sum(op_by_member_period[code][p] for code in common_codes)
            prev = sum(op_by_member_period[code][p - 100] for code in common_codes)
        else:
            cur = prev = None
        v, lbl = sign_aware_ratio(cur, prev)
        yoy_opsum.append(v)
        label_opsum.append(lbl)
        n_opsum.append(len(common_codes))

    # 맨 앞쪽 분기(들)는 YoY 계산에 4분기치 이전 데이터가 필요해서(compute_yoy) 세 방식
    # 모두 값이 하나도 없는 "워밍업" 구간일 수 있다 - 원본 워크북 히스토리가 2024년부터라
    # 그 빈 2024년 칸들이 차트 앞에 계속 나와서 불편하다는 요청(2026-09-29 "차트에서
    # 24년도 나오니까 불편해"). 세 방식 중 하나라도 값(숫자 또는 흑전/적전/적자 라벨)이
    # 있는 첫 분기부터만 남긴다 - 지어내는 게 아니라 정말 아무 값도 없는 구간을 그냥 자르는
    # 것뿐이다.
    start_idx = next((i for i in range(len(all_periods))
                       if vals_by_period[all_periods[i]] or yoy_opsum[i] is not None or label_opsum[i] is not None),
                      0)
    if start_idx > 0:
        all_periods = all_periods[start_idx:]
        yoy_simple, yoy_trimmed, label_none = yoy_simple[start_idx:], yoy_trimmed[start_idx:], label_none[start_idx:]
        est_arr = est_arr[start_idx:]
        yoy_opsum, label_opsum, n_opsum = yoy_opsum[start_idx:], label_opsum[start_idx:], n_opsum[start_idx:]
        n_by_period = {str(p): len(vals_by_period[p]) for p in all_periods}

    series_by_method = {
        "simple": (yoy_simple, label_none, n_by_period),
        "trimmed": (yoy_trimmed, label_none, n_by_period),
        "opsum": (yoy_opsum, label_opsum, {str(p): n_opsum[i] for i, p in enumerate(all_periods)}),
    }

    gaps = [gap_map.get(m["code"]) for m in members if gap_map.get(m["code"]) is not None]
    valuation_gap = round(statistics.mean(gaps), 1) if gaps else None
    mktcap_sum = sum(m["latest_mktcap"] for m in members if m["latest_mktcap"] is not None)

    rows_by_method, detail_methods = {}, {}
    for method in METHODS:
        yoy_arr, label_arr, n_map = series_by_method[method]
        stats = series_stats(all_periods, yoy_arr, label_arr, est_arr)
        if stats is None:
            continue
        yoy_by_period = {str(p): yoy_arr[i] for i, p in enumerate(all_periods) if yoy_arr[i] is not None}
        label_by_period = {str(p): label_arr[i] for i, p in enumerate(all_periods) if label_arr[i] is not None}
        est_by_period = {str(p): est_arr[i] for i, p in enumerate(all_periods)}
        rows_by_method[method] = {
            "code": slugify(sector), "name": sector, "sector": sector,
            "latest_mktcap": mktcap_sum,
            "latest_period": stats["latest_period"], "latest_is_estimate": stats["latest_is_estimate"],
            "latest_yoy": stats["latest_yoy"], "latest_label": stats["latest_label"], "prev_yoy": stats["prev_yoy"],
            "triple_digit_streak": stats["triple_digit_streak"], "accel_streak": stats["accel_streak"],
            "accel": stats["accel"],
            "yoy_by_period": yoy_by_period, "label_by_period": label_by_period, "est_by_period": est_by_period,
            "n_by_period": n_map, "valuation_gap": valuation_gap, "n_stocks": len(members),
        }
        detail_methods[method] = {
            "quarters": [
                {"period": p, "yoy": yoy_arr[i], "label": label_arr[i],
                 "n": n_map.get(str(p), 0), "is_estimate": est_arr[i]}
                for i, p in enumerate(all_periods) if yoy_arr[i] is not None or label_arr[i] is not None
            ],
        }

    if not rows_by_method:
        return None, None

    # 상세 모달 차트용 - 섹터 합산 시가총액(월별, forward-fill) + 각 방식의 YoY 막대.
    # 집계 방식이 바뀌어도 시가총액 선은 동일하므로 한 번만 계산해서 공유한다.
    bar_dates_by_method = {
        method: [month_end(*add_month(q["period"] // 100, q["period"] % 100)) for q in detail_methods[method]["quarters"]]
        for method in detail_methods
    }
    grid_end = MKTCAP_CHART_START
    for dates in bar_dates_by_method.values():
        if dates:
            grid_end = max(grid_end, max(dates))
    for m in members:
        if m["mc_by_month"]:
            grid_end = max(grid_end, month_end(*max(m["mc_by_month"])))
    y, mo = MKTCAP_CHART_START.year, MKTCAP_CHART_START.month
    grid = []
    while (y, mo) <= (grid_end.year, grid_end.month):
        grid.append((y, mo))
        y, mo = add_month(y, mo)

    last_vals = {m["code"]: None for m in members}
    mc_sum_by_month = {}
    for (y2, m2) in grid:
        total, any_val = 0, False
        for m in members:
            v = m["mc_by_month"].get((y2, m2))
            if v is not None:
                last_vals[m["code"]] = v
            lv = last_vals[m["code"]]
            if lv is not None:
                total += lv
                any_val = True
        mc_sum_by_month[(y2, m2)] = total if any_val else None

    grid_dates = [month_end(y2, m2) for (y2, m2) in grid]
    mc_map = {month_end(y2, m2): (round(v / 1e8, 1) if v is not None else None) for (y2, m2), v in mc_sum_by_month.items()}

    for method in METHODS:
        if method not in detail_methods:
            continue
        bar_dates = bar_dates_by_method[method]
        qs = detail_methods[method]["quarters"]
        bar_yoy_map = {d: q["yoy"] for d, q in zip(bar_dates, qs)}
        bar_label_map = {d: q["label"] for d, q in zip(bar_dates, qs)}
        bar_est_map = {d: q["is_estimate"] for d, q in zip(bar_dates, qs)}
        detail_methods[method]["bar_yoy"] = [bar_yoy_map.get(d) for d in grid_dates]
        detail_methods[method]["bar_label"] = [bar_label_map.get(d) for d in grid_dates]
        detail_methods[method]["bar_is_estimate"] = [bool(bar_est_map.get(d, False)) for d in grid_dates]

    # 종목별 분포(분산형 점 + min~max 범위) - "섹터 평균이 실제로 대표성 있는지" 눈으로
    # 바로 보기 위해(2026-09-29 사용자 요청 "1번[종목별 분포]을 한번 해볼까"). 집계 방식과
    # 무관하게 개별 종목의 실제 YoY%(sign_aware_ratio 라벨이 아니라 숫자가 있는 것만)를
    # 그대로 내보낸다 - 집계 방식 버튼을 바꿔도 이 점들은 안 움직이고, 그 위에 겹쳐 그리는
    # 굵은 평균선만 방식에 따라 움직인다.
    members_series = [
        {"name": m["name"], "yoy_by_period": {str(q["period"]): m["yoy"][i] for i, q in enumerate(m["quarters"]) if m["yoy"][i] is not None}}
        for m in members
    ]

    detail = {
        "sector": sector, "n_stocks": len(members),
        "member_names": sorted(m["name"] for m in members),
        "quarter_periods": all_periods,
        "members_series": members_series,
        "labels": [str(d) for d in grid_dates],
        "mc_eok": [mc_map.get(d) for d in grid_dates],
        "methods": detail_methods,
    }
    return rows_by_method, detail


def main():
    wb_path = find_workbook()
    if not wb_path:
        print("data/manual/ 에 'QoQ 계산' 워크북이 없습니다.")
        return
    print(f"워크북 로드 중: {wb_path}")
    wb = openpyxl.load_workbook(wb_path, read_only=True, data_only=True)

    all_results = {}
    data_as_of = None
    for sn in wb.sheetnames:
        res, sheet_latest = process_sheet(wb[sn])
        for code, data in res.items():
            all_results.setdefault(code, data)
        if sheet_latest is not None and (data_as_of is None or sheet_latest > data_as_of):
            data_as_of = sheet_latest

    custom_sector_map = load_custom_sector_map()
    gap_map = load_op_band_gaps()
    print(f"섹터 정리.xlsx 종목 {len(custom_sector_map)}개 로드")

    sector_members = {}
    n_unclassified = 0
    for code, data in all_results.items():
        sector = custom_sector_map.get(data["name"])
        if sector is None:
            n_unclassified += 1
            continue
        quarters = data["quarters"]
        yoy, label = compute_yoy(quarters)
        if not any(v is not None for v in yoy) and not any(label):
            continue
        sector_members.setdefault(sector, []).append({
            "code": code, "name": data["name"], "quarters": quarters, "yoy": yoy, "label": label,
            "latest_mktcap": data["latest_mktcap"], "mc_by_month": data["mc_by_month"],
        })
    print(f"섹터 배정 {sum(len(v) for v in sector_members.values())}종목 / 미분류 {n_unclassified}종목")

    os.makedirs(DETAIL_OUT_DIR, exist_ok=True)
    rows_by_method = {m: [] for m in METHODS}
    for sector, members in sector_members.items():
        result, detail = build_sector(sector, members, gap_map)
        if not result:
            continue
        for method, row in result.items():
            rows_by_method[method].append(row)
        with open(os.path.join(DETAIL_OUT_DIR, f"{slugify(sector)}.json"), "w", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False)

    print(f"결과 {len(rows_by_method['simple'])}개 섹터")

    # 밸류에이션 등급(집계 방식과 무관, 섹터당 1개) - simple 리스트 기준으로 한 번만 계산해서
    # 세 방식 모두에 복사한다.
    gap_series = pd.Series({r["code"]: r["valuation_gap"] for r in rows_by_method["simple"] if r["valuation_gap"] is not None})
    grade_labels = ["상", "중상", "중", "중하", "하"]
    if len(gap_series) >= 5:
        grade_cat = pd.qcut(gap_series, 5, labels=grade_labels, duplicates="drop")
        grade_by_code = {code: (str(g) if pd.notna(g) else None) for code, g in grade_cat.items()}
    else:
        grade_by_code = {}
    for method in METHODS:
        for r in rows_by_method[method]:
            r["valuation_grade"] = grade_by_code.get(r["code"])

    periods_by_method = {
        method: sorted({p for r in rows_by_method[method] for p in r["yoy_by_period"]} |
                        {p for r in rows_by_method[method] for p in r["label_by_period"]})
        for method in METHODS
    }

    os.makedirs(SCREEN_DIR, exist_ok=True)
    for method in METHODS:
        pd.DataFrame(rows_by_method[method]).to_csv(
            os.path.join(SCREEN_DIR, f"sector_yoy_accel_summary_{method}.csv"), index=False, encoding="utf-8-sig")

    html = TEMPLATE.format(
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        data_as_of=data_as_of.strftime("%Y-%m-%d") if data_as_of is not None else "알 수 없음",
        n_sectors=len(rows_by_method["simple"]),
        rows_by_method_json=json.dumps(rows_by_method, ensure_ascii=False),
        periods_by_method_json=json.dumps(periods_by_method),
        method_label_json=json.dumps(METHOD_LABEL, ensure_ascii=False),
    )
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(PAGE_OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"저장 완료: {PAGE_OUT_PATH}")


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>섹터별 YoY 가속화 트래커</title>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  a.back {{ color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }}
  h1 {{ font-size:20px; margin:8px 0 4px 0; }}
  .updated {{ color:#9aa0a6; font-size:13px; margin-bottom:16px; }}
  .exp {{ background:#1a1d24; border-radius:10px; padding:14px 18px; font-size:12px; color:#9aa0a6; line-height:1.8; max-width:1000px; margin-bottom:18px; }}
  .exp b {{ color:#ffa94d; }}
  .method-bar {{ display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }}
  .method-btn {{ background:#1a1d24; border:1px solid #2a2e37; color:#9aa0a6; padding:7px 14px; border-radius:999px; cursor:pointer; font-size:13px; font-family:inherit; }}
  .method-btn.active {{ background:#4dabf7; color:#0f1115; border-color:#4dabf7; font-weight:bold; }}
  .filters {{ display:flex; gap:12px; flex-wrap:wrap; align-items:center; margin-bottom:14px; font-size:13px; color:#9aa0a6; }}
  .filters input, .filters select {{ background:#1a1d24; border:1px solid #2a2e37; color:#e6e6e6; border-radius:6px; padding:6px 10px; font-size:13px; }}
  .filters input[type="number"] {{ width:80px; }}
  .table-wrap {{ overflow-x:auto; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  th, td {{ padding:8px 10px; text-align:right; border-bottom:1px solid #23262e; white-space:nowrap; }}
  th.lbl, td.lbl {{ text-align:left; }}
  th {{ color:#9aa0a6; font-weight:normal; font-size:12px; position:sticky; top:0; background:#0f1115; }}
  .up {{ color:#63e6be; }}
  .down {{ color:#ff8787; }}
  td.hm {{ padding:4px 6px; text-align:center; font-size:11px; }}
  .hm-pos {{ background:hsl(150,45%,28%); color:#c8ecd9; border-radius:4px; }}
  .hm-pos-est {{ background:hsl(150,45%,55%); color:#0b3d24; border-radius:4px; }}
  .hm-neg {{ background:hsl(0,45%,28%); color:#f0c9c9; border-radius:4px; }}
  .hm-neg-est {{ background:hsl(0,45%,55%); color:#4a0f0f; border-radius:4px; }}
  .hm-empty {{ color:#3a3d45; }}
  .hm-lown {{ opacity:0.55; }}
  .grade-badge {{ display:inline-block; border-radius:4px; font-size:11px; padding:2px 7px; font-weight:bold; }}
  .grade-상 {{ background:#1e6b4655; color:#8fd9b8; }}
  .grade-중상 {{ background:#1e6b4630; color:#a8d9c2; }}
  .grade-중 {{ background:#2a2e37; color:#9aa0a6; }}
  .grade-중하 {{ background:#8a2f2f30; color:#e0b3b3; }}
  .grade-하 {{ background:#8a2f2f55; color:#f0b3b3; }}
  .sub {{ color:#6b7280; font-size:11px; }}
  .count {{ color:#63e6be; font-size:12px; margin-bottom:8px; }}
  tbody tr {{ cursor:pointer; }}
  tbody tr:hover {{ background:#1a1d24; }}
  .overlay {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,0.75); z-index:200; overflow:auto; padding:40px 20px; }}
  .overlay.open {{ display:block; }}
  .modal {{ background:#12151b; border:1px solid #23262e; border-radius:14px; max-width:900px; margin:0 auto; padding:22px 26px; }}
  .modal h2 {{ font-size:17px; margin:0 0 12px 0; }}
  .close-btn {{ float:right; background:none; border:none; color:#9aa0a6; font-size:22px; cursor:pointer; line-height:1; }}
  .detail-table {{ width:100%; margin-top:16px; }}
  .detail-table th {{ position:static; }}
  .legend {{ display:flex; gap:16px; flex-wrap:wrap; font-size:12px; color:#9aa0a6; margin-bottom:8px; }}
  .legend span {{ display:flex; align-items:center; gap:4px; }}
  .sw {{ width:12px; height:2px; display:inline-block; }}
  .sq {{ width:10px; height:10px; border-radius:2px; display:inline-block; }}
  .chart-wrap {{ height:320px; position:relative; }}
  .members {{ margin-top:14px; font-size:12px; color:#9aa0a6; line-height:1.7; }}
  .members b {{ color:#e6e6e6; }}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <a class="back" href="yoy_accel_tracker.html">종목별 YoY 가속화 트래커 &rarr;</a>
  <h1>섹터별 YoY 가속화 트래커</h1>
  <div class="updated">최종 갱신: {updated_at} &middot; 데이터 기준일: {data_as_of} &middot; {n_sectors}개 섹터</div>

  <div class="exp">
    <b>무엇을 보는 페이지인가</b><br>
    종목별 YoY 가속화 트래커와 같은 원본에서, 종목 대신 <b>사용자가 정한 42개 섹터</b>
    (섹터 정리.xlsx)로 묶은 것입니다. 섹터 정리.xlsx에 없는(미분류) 종목은 어느 섹터에도
    들어가지 않습니다. 집계 방식을 3가지 중 골라 비교할 수 있습니다 - <b>단순평균</b>은
    그 분기 숫자(%)가 있는 종목 전체를 그냥 평균(작은 회사의 베이스효과 극단치에 취약).
    <b>절사평균</b>은 아래 5% / 위 15%를 잘라내고 평균 - 대칭이 아닌 이유는 실제 종목별
    YoY% 분포를 조사해보니 아래쪽은 -100%에서 막혀있는데 위쪽은 베이스효과로 수만%까지
    뻗어있어서(우측 극단치가 훨씬 많음), 대칭으로 자르면 균형이 안 맞기 때문입니다(표본이
    작으면 그만큼 덜 잘림). <b>섹터합산 OP YoY</b>는 종목별 %를 평균 내는 대신 영업이익 금액을 먼저 합산한
    뒤 딱 한 번 전년동기대비를 계산(큰 회사 비중이 자연히 반영, 부호가 섞이면 흑전/적전/적자
    라벨) - 이번 분기와 작년 동기 <b>둘 다 컨센서스가 있는 종목만</b> 골라 그 종목들끼리만
    합산합니다(먼 미래 분기일수록 컨센서스 있는 종목이 줄어드는데, 각 분기를 "그때 있던
    종목 전부"로 따로 더하면 모집단이 달라져 실제로는 안 줄었는데 합계가 확 줄어드는 착시가
    생기기 때문). 히트맵 칸에 마우스를 올리면 몇 개 종목이 들어갔는지(n) 나오고, <b>n이 3
    미만인 칸은 흐리게</b> 표시해 표본이 적어 신뢰도가 낮다는 걸 표시합니다.<br>
    가속도(%p)/세자리유지/가속화유지는 섹터를 하나의 가상 종목처럼 취급해 종목별 페이지와
    같은 정의를 그대로 적용합니다. <b>밸류에이션 등급</b>(OP밴드 바텀 대비 %, 섹터 소속
    종목 평균을 다시 섹터끼리 5분위)과 <b>시가총액</b>(섹터 합산)은 집계 방식과 무관하게
    동일합니다.
  </div>

  <div class="method-bar" id="methodBar"></div>

  <div class="filters">
    <label>검색 <input type="text" id="fSearch" placeholder="섹터명"></label>
    <label>최신 YoY% 최소 <input type="number" id="fYoyMin" step="10"></label>
    <label><input type="checkbox" id="fAccelOnly"> 가속 중인 섹터만</label>
    <label><input type="checkbox" id="fTripleOnly"> 세자리 YoY(100%+) 3분기 이상 유지</label>
    <label><input type="checkbox" id="fAccelStreakOnly"> 가속화 3분기 이상 유지</label>
    <label>시총 최소(억, 섹터합산) <input type="number" id="fMktcapMin" step="100"></label>
    <label>밸류에이션 등급 <select id="fGrade">
      <option value="">전체</option>
      <option value="상">상(바텀 근접)</option>
      <option value="중상">중상</option>
      <option value="중">중</option>
      <option value="중하">중하</option>
      <option value="하">하(바텀에서 멀음)</option>
    </select></label>
    <label>정렬 <select id="fSort">
      <option value="accel2_desc" selected>가속도(%p) 큰순</option>
      <option value="yoy_desc">최신 YoY% 큰순</option>
      <option value="streak_desc">세자리 유지 분기수 큰순</option>
      <option value="accel_streak_desc">가속화 유지 분기수 큰순</option>
      <option value="mktcap_desc">시가총액(섹터합산) 큰순</option>
      <option value="valuation_asc">밸류에이션(바텀 대비 %) 낮은순</option>
    </select></label>
  </div>
  <div class="count" id="count"></div>

  <div class="table-wrap">
  <table>
    <thead><tr id="headRow"></tr></thead>
    <tbody id="tbody"></tbody>
  </table>
  </div>

  <div class="overlay" id="overlay">
    <div class="modal">
      <button class="close-btn" id="closeBtn">&times;</button>
      <h2 id="detailName"></h2>
      <div class="legend">
        <span><span class="sq" style="background:rgba(77,171,247,0.5)"></span>개별 종목 YoY%(점)</span>
        <span><span class="sw" style="background:#4a4e58"></span>최소~최대 범위</span>
        <span><span class="sw" style="background:#ff8f3f"></span>현재 집계방식 평균선</span>
      </div>
      <div class="sub" id="detailMethodSub" style="margin-bottom:6px;"></div>
      <div class="chart-wrap"><canvas id="detailChart"></canvas></div>
      <table class="detail-table">
        <thead><tr><th style="text-align:left;">시기</th><th>기여종목수</th><th>YoY%</th></tr></thead>
        <tbody id="detailBody"></tbody>
      </table>
      <div class="members" id="detailMembers"></div>
    </div>
  </div>


<script>
const ROWS_BY_METHOD = {rows_by_method_json};
const PERIODS_BY_METHOD = {periods_by_method_json};
const METHOD_LABEL = {method_label_json};
const METHODS = ['trimmed', 'simple', 'opsum'];
let currentMethod = 'trimmed';
let currentDetail = null;

const methodBar = document.getElementById('methodBar');
METHODS.forEach(m => {{
  const b = document.createElement('button');
  b.className = 'method-btn' + (m === currentMethod ? ' active' : '');
  b.textContent = METHOD_LABEL[m];
  b.dataset.method = m;
  b.onclick = () => {{
    currentMethod = m;
    document.querySelectorAll('.method-btn').forEach(x => x.classList.toggle('active', x.dataset.method === m));
    applyFilters();
    if (currentDetail && document.getElementById('overlay').classList.contains('open')) renderDetailChart();
  }};
  methodBar.appendChild(b);
}});

function fmtMktcap(v) {{
  if (v == null) return '-';
  const eok = v / 1e8;
  return eok >= 10000 ? (eok / 10000).toFixed(2) + '조' : Math.round(eok).toLocaleString() + '억';
}}
function fmtGrade(r) {{
  if (r.valuation_grade == null) return '<span class="sub">-</span>';
  const gapTxt = r.valuation_gap == null ? '' : ` (${{r.valuation_gap > 0 ? '+' : ''}}${{r.valuation_gap.toFixed(0)}}%)`;
  return `<span class="grade-badge grade-${{r.valuation_grade}}">${{r.valuation_grade}}</span><span class="sub">${{gapTxt}}</span>`;
}}
function fmtPeriod(p) {{
  const y = Math.floor(p / 100), m = p % 100;
  const q = {{3:'Q1',6:'Q2',9:'Q3',12:'Q4'}}[m] || m;
  return y + ' ' + q;
}}
function fmtPeriodShort(p) {{
  const y = Math.floor(p / 100) % 100, m = p % 100;
  const q = {{3:'Q1',6:'Q2',9:'Q3',12:'Q4'}}[m] || m;
  return "'" + y + ' ' + q;
}}
function pctSpan(v) {{
  if (v == null) return '<span class="sub">-</span>';
  const cls = v > 0 ? 'up' : v < 0 ? 'down' : '';
  return `<span class="${{cls}}">${{v > 0 ? '+' : ''}}${{v.toFixed(1)}}%</span>`;
}}
function pctOrLabelSpan(v, lbl) {{
  if (lbl != null) return `<span class="${{lbl === '흑전' ? 'up' : 'down'}}">${{lbl}}</span>`;
  return pctSpan(v);
}}
const POS_CAP = 500;
function posCellStyle(v, isEst) {{
  const t = Math.min(1, Math.log10(1 + Math.max(0, v)) / Math.log10(1 + POS_CAP));
  const light = isEst ? Math.round(85 - t * 40) : Math.round(60 - t * 40);
  const textColor = light < 50 ? '#eafff5' : '#0b3d24';
  return `background:hsl(150, 55%, ${{light}}%); color:${{textColor}}; border-radius:4px;`;
}}
function heatCell(r, period) {{
  const v = r.yoy_by_period[period];
  const lbl = r.label_by_period[period];
  const est = r.est_by_period[period];
  const n = r.n_by_period[period];
  const lown = n != null && n > 0 && n < 3 ? ' hm-lown' : '';
  const title = n ? ` title="${{n}}개 종목"` : '';
  if (v == null && lbl == null) return '<td class="hm hm-empty">-</td>';
  if (lbl != null) {{
    const cls = lbl === '흑전' ? (est ? 'hm-pos-est' : 'hm-pos') : (est ? 'hm-neg-est' : 'hm-neg');
    return `<td class="hm ${{cls}}${{lown}}"${{title}}>${{lbl}}</td>`;
  }}
  if (v >= 0) {{
    return `<td class="hm${{lown}}"${{title}} style="${{posCellStyle(v, est)}}">${{Math.round(v)}}%</td>`;
  }}
  const cls = est ? 'hm-neg-est' : 'hm-neg';
  return `<td class="hm ${{cls}}${{lown}}"${{title}}>${{Math.round(v)}}%</td>`;
}}

function applyFilters() {{
  const rows = ROWS_BY_METHOD[currentMethod];
  const periods = PERIODS_BY_METHOD[currentMethod];
  document.getElementById('headRow').innerHTML = '<th class="lbl">섹터</th>'
    + periods.map(p => `<th>${{fmtPeriodShort(parseInt(p))}}</th>`).join('')
    + '<th>시가총액</th><th>가속도(%p)</th><th>밸류에이션</th><th>세자리 유지</th><th>가속화 유지</th><th>종목수</th>';

  const q = document.getElementById('fSearch').value.trim().toLowerCase();
  const yoyMin = parseFloat(document.getElementById('fYoyMin').value);
  const mktcapMin = parseFloat(document.getElementById('fMktcapMin').value);
  const accelOnly = document.getElementById('fAccelOnly').checked;
  const tripleOnly = document.getElementById('fTripleOnly').checked;
  const accelStreakOnly = document.getElementById('fAccelStreakOnly').checked;
  const grade = document.getElementById('fGrade').value;
  const sort = document.getElementById('fSort').value;

  let filtered = rows.filter(r => {{
    if (q && !r.name.toLowerCase().includes(q)) return false;
    if (!isNaN(yoyMin) && (r.latest_yoy == null || r.latest_yoy < yoyMin)) return false;
    if (!isNaN(mktcapMin) && (r.latest_mktcap == null || r.latest_mktcap / 1e8 < mktcapMin)) return false;
    if (accelOnly && (r.accel == null || r.accel <= 0)) return false;
    if (tripleOnly && r.triple_digit_streak < 3) return false;
    if (accelStreakOnly && r.accel_streak < 3) return false;
    if (grade && r.valuation_grade !== grade) return false;
    return true;
  }});

  if (sort === 'accel2_desc') filtered.sort((a, b) => (b.accel ?? -9e9) - (a.accel ?? -9e9));
  else if (sort === 'yoy_desc') filtered.sort((a, b) => (b.latest_yoy ?? -9e9) - (a.latest_yoy ?? -9e9));
  else if (sort === 'streak_desc') filtered.sort((a, b) => b.triple_digit_streak - a.triple_digit_streak);
  else if (sort === 'accel_streak_desc') filtered.sort((a, b) => b.accel_streak - a.accel_streak);
  else if (sort === 'valuation_asc') filtered.sort((a, b) => (a.valuation_gap ?? 9e9) - (b.valuation_gap ?? 9e9));
  else filtered.sort((a, b) => (b.latest_mktcap ?? 0) - (a.latest_mktcap ?? 0));

  document.getElementById('count').textContent = filtered.length + '개 섹터 (' + METHOD_LABEL[currentMethod] + ', 행 클릭하면 상세)';
  document.getElementById('tbody').innerHTML = filtered.map(r => `
    <tr data-code="${{r.code}}">
      <td class="lbl">${{r.name}}</td>
      ${{periods.map(p => heatCell(r, p)).join('')}}
      <td>${{fmtMktcap(r.latest_mktcap)}}</td>
      <td>${{pctSpan(r.accel)}}</td>
      <td>${{fmtGrade(r)}}</td>
      <td class="sub">${{r.triple_digit_streak > 0 ? r.triple_digit_streak + '분기' : '-'}}</td>
      <td class="sub">${{r.accel_streak > 0 ? r.accel_streak + '분기' : '-'}}</td>
      <td class="sub">${{r.n_stocks}}개</td>
    </tr>`).join('');
  document.querySelectorAll('#tbody tr').forEach(tr =>
    tr.addEventListener('click', () => openDetail(tr.dataset.code)));
}}

['fSearch','fYoyMin','fMktcapMin','fAccelOnly','fTripleOnly','fAccelStreakOnly','fGrade','fSort'].forEach(id => {{
  document.getElementById(id).addEventListener('input', applyFilters);
  document.getElementById(id).addEventListener('change', applyFilters);
}});

let detailChart = null;
function renderDetailChart() {{
  const d = currentDetail;
  const md = d.methods[currentMethod];
  document.getElementById('detailMethodSub').textContent = '집계 방식: ' + METHOD_LABEL[currentMethod] + ' (굵은 선만 이동, 점/범위는 방식과 무관하게 항상 실제 개별 종목 YoY%)';
  document.getElementById('detailBody').innerHTML = md.quarters.slice().reverse().map(q => `
    <tr>
      <td style="text-align:left;">${{fmtPeriod(q.period)}}${{q.is_estimate ? ' <span class="sub">(추정)</span>' : ''}}</td>
      <td>${{q.n}}개</td>
      <td>${{pctOrLabelSpan(q.yoy, q.label)}}</td>
    </tr>`).join('');

  const periods = d.quarter_periods;
  const labels = periods.map(p => fmtPeriodShort(p));

  // 종목별 분포 - 그 분기에 그 섹터 종목들의 실제 YoY%가 흩어진 정도를 점으로, 범위를
  // 옅은 막대로 같이 보여준다(2026-09-29 "1번[종목별 분포]을 한번 해볼까"). 평균 하나만
  // 보면 극단치 하나 때문에 숫자가 튀는 건지 다들 고르게 좋은 건지 구분이 안 됐다.
  const byPeriod = periods.map(p => d.members_series.map(m => m.yoy_by_period[String(p)]).filter(v => v != null));
  const rangeData = byPeriod.map(vs => vs.length ? [Math.min(...vs), Math.max(...vs)] : null);

  const pointDatasets = d.members_series.map(m => ({{
    type: 'line', label: m.name, showLine: false,
    data: periods.map(p => {{ const v = m.yoy_by_period[String(p)]; return v == null ? null : v; }}),
    pointRadius: 4, pointHoverRadius: 6,
    pointBackgroundColor: 'rgba(77,171,247,0.45)', pointBorderWidth: 0,
    order: 2,
  }}));

  const avgByPeriod = {{}};
  md.quarters.forEach(q => {{ if (q.yoy != null) avgByPeriod[q.period] = q.yoy; }});
  const avgLine = periods.map(p => avgByPeriod[p] ?? null);

  if (detailChart) detailChart.destroy();
  detailChart = new Chart(document.getElementById('detailChart').getContext('2d'), {{
    data: {{
      labels,
      datasets: [
        {{ type: 'bar', label: '최소~최대', data: rangeData, backgroundColor: 'rgba(74,78,88,0.35)', borderRadius: 3, maxBarThickness: 28, order: 3 }},
        ...pointDatasets,
        {{ type: 'line', label: '평균(' + METHOD_LABEL[currentMethod] + ')', data: avgLine, borderColor: '#ff8f3f', backgroundColor: 'transparent', borderWidth: 3, pointRadius: 0, tension: 0.1, spanGaps: false, order: 1 }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            label: (c) => {{
              if (c.dataset.label === '최소~최대') {{ const [lo, hi] = c.raw ?? [null, null]; return lo == null ? null : `범위: ${{lo.toFixed(1)}}% ~ ${{hi.toFixed(1)}}%`; }}
              if (c.parsed.y == null) return null;
              return c.dataset.label + ': ' + (c.parsed.y >= 0 ? '+' : '') + c.parsed.y.toFixed(1) + '%';
            }}
          }}
        }}
      }},
      scales: {{
        x: {{ grid: {{ display: false }}, ticks: {{ color: '#9aa0a6', maxTicksLimit: 14, maxRotation: 45 }} }},
        y: {{ grid: {{ color: '#23262e' }}, ticks: {{ color: '#9aa0a6', callback: (v) => v + '%' }} }},
      }},
      interaction: {{ mode: 'nearest', intersect: true }}
    }}
  }});
}}
function openDetail(code) {{
  fetch(`sector_yoy_accel_tracker_data/${{code}}.json`).then(r => r.json()).then(d => {{
    currentDetail = d;
    document.getElementById('detailName').textContent = `${{d.sector}} (${{d.n_stocks}}개 종목)`;
    document.getElementById('detailMembers').innerHTML = '<b>구성종목</b>: ' + d.member_names.join(', ');
    renderDetailChart();
    document.getElementById('overlay').classList.add('open');
  }});
}}
document.getElementById('closeBtn').addEventListener('click', () => document.getElementById('overlay').classList.remove('open'));
document.getElementById('overlay').addEventListener('click', e => {{
  if (e.target.id === 'overlay') document.getElementById('overlay').classList.remove('open');
}});

applyFilters();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
