# -*- coding: utf-8 -*-
"""
섹터별 YoY 가속화 트래커 - docs/sector_yoy_accel_tracker.html (2026-09-29 사용자 요청
"섹터기준으로 한번 만들어볼건데 YOY를 %에 대한 평균으로 섹터별 YOY가속화 트래커를
만들어보자 인터페이스는 YOY 가속화 트래커 그대로").

build_yoy_accel_tracker.py와 같은 원본(data/manual/QoQ 계산.xlsx)에서 종목별 분기 YoY%를
뽑는 것까지는 동일하고, 그 다음이 다르다 - 종목별 행 대신 "사용자가 정한 42개 섹터"(data/manual/
섹터 정리.xlsx, load_custom_sector_map)로 묶어서 분기마다 그 섹터에 속한 종목들의 YoY%를
단순평균한 행을 만든다. 섹터 정리.xlsx에 없는(미분류) 종목은 어느 섹터 평균에도 넣지 않는다
(2026-09-29 "기존 내가 제시한 섹터가 아닌거는 지워버리자" 결정과 같은 원칙 - 사용자가 정한
분류 체계 밖의 것을 섞지 않는다).

평균은 그 분기에 "숫자"(%)가 있는 종목만 대상으로 한다 - 흑전/적전/적자(부호가 섞여 라벨만
있는 분기, compute_yoy 참고)인 종목은 그 분기 평균에서 빠진다(숫자가 아니라서 평균을 낼 수
없다, 지어내지 않는다). 그래서 각 섹터-분기 칸은 "몇 개 종목의 평균인지"(n)를 항상 같이
들고 있다.

가속도(%p)/세자리유지/가속화유지 - 개별 종목 페이지와 완전히 같은 정의를, 종목의 yoy 배열
대신 "섹터 평균 yoy 배열"에 그대로 적용한다(섹터를 마치 하나의 가상 종목처럼 다룬다). 그 중
실적/추정 구분(is_estimate)은 그 분기에 기여한 종목들의 다수결로 정한다 - 같은 달력분기라
거의 항상 전원 일치하지만, 실적 발표 시점이 종목마다 며칠씩 어긋나는 경우를 대비한 것.

밸류에이션 등급은 OP밴드의 "3년 하위 10% 바텀 대비 %"를 섹터 소속 종목들 평균으로 내고,
그 섹터 평균값들끼리(42개) 5분위로 나눈다.

상세 모달은 개별 종목 페이지의 OP밴드 차트 대신(섹터엔 단일 OP 시계열이 없어 밴드 개념이
안 맞는다), 더 이전 버전에 쓰던 "시가총액(선) + YoY%(막대)" 차트를 그대로 가져와 섹터
합산 시가총액 + 섹터 평균 YoY%로 그린다. 구성종목 명단도 같이 보여준다.
"""
import calendar
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
SUMMARY_PATH = os.path.join(SCREEN_DIR, "sector_yoy_accel_summary.csv")
PAGE_OUT_PATH = os.path.join(DOCS_DIR, "sector_yoy_accel_tracker.html")
DETAIL_OUT_DIR = os.path.join(DOCS_DIR, "sector_yoy_accel_tracker_data")

MIN_QUARTERS_FOR_YOY = 5


def slugify(sector):
    return "".join(c if c.isalnum() else "_" for c in sector)


def build_sector_row_and_detail(sector, members):
    """members: [{code, name, quarters, yoy, label, latest_mktcap, mc_by_month, gap}]"""
    all_periods = sorted({q["period"] for m in members for q in m["quarters"]})

    sector_yoy, sector_est, sector_n = [], [], []
    for p in all_periods:
        vals, est_votes = [], []
        for m in members:
            for i, q in enumerate(m["quarters"]):
                if q["period"] != p:
                    continue
                est_votes.append(q["is_estimate"])
                if m["yoy"][i] is not None:
                    vals.append(m["yoy"][i])
        sector_yoy.append(round(statistics.mean(vals), 1) if vals else None)
        sector_est.append((sum(est_votes) / len(est_votes)) >= 0.5 if est_votes else False)
        sector_n.append(len(vals))

    idxs_with_val = [i for i, v in enumerate(sector_yoy) if v is not None]
    if not idxs_with_val:
        return None, None
    latest_i = idxs_with_val[-1]
    latest_yoy = sector_yoy[latest_i]
    latest_period = all_periods[latest_i]
    latest_is_estimate = sector_est[latest_i]
    prev_yoy = sector_yoy[latest_i - 1] if latest_i - 1 >= 0 else None

    triple_digit_streak = 0
    for i in range(latest_i, -1, -1):
        if sector_yoy[i] is not None and sector_yoy[i] >= 100:
            triple_digit_streak += 1
        else:
            break

    accel_streak = 0
    i = latest_i
    while i - 1 >= 0 and sector_yoy[i] is not None and sector_yoy[i - 1] is not None and sector_yoy[i] > sector_yoy[i - 1]:
        accel_streak += 1
        i -= 1

    latest_actual_i = next((i for i in range(len(all_periods) - 1, -1, -1) if not sector_est[i]), None)
    accel = None
    if latest_actual_i is not None and latest_actual_i + 2 < len(all_periods):
        base_yoy, target_yoy = sector_yoy[latest_actual_i], sector_yoy[latest_actual_i + 2]
        if base_yoy is not None and target_yoy is not None:
            accel = round(target_yoy - base_yoy, 1)

    gaps = [m["gap"] for m in members if m["gap"] is not None]
    valuation_gap = round(statistics.mean(gaps), 1) if gaps else None

    mktcap_sum = sum(m["latest_mktcap"] for m in members if m["latest_mktcap"] is not None)

    yoy_by_period = {str(p): sector_yoy[i] for i, p in enumerate(all_periods) if sector_yoy[i] is not None}
    est_by_period = {str(p): sector_est[i] for i, p in enumerate(all_periods)}
    n_by_period = {str(p): sector_n[i] for i, p in enumerate(all_periods)}

    row = {
        "code": slugify(sector), "name": sector, "sector": sector,
        "latest_mktcap": mktcap_sum,
        "latest_period": latest_period, "latest_is_estimate": latest_is_estimate,
        "latest_yoy": latest_yoy, "latest_label": None, "prev_yoy": prev_yoy,
        "triple_digit_streak": triple_digit_streak, "accel_streak": accel_streak, "accel": accel,
        "yoy_by_period": yoy_by_period, "label_by_period": {}, "est_by_period": est_by_period,
        "n_by_period": n_by_period, "valuation_gap": valuation_gap, "n_stocks": len(members),
    }

    # 상세 모달 차트용 - 섹터 합산 시가총액(월별) + 섹터 평균 YoY%(분기, 실적발표 시점
    # 근사인 "분기말+1개월"에 배치). 개별 종목 하나가 그 달에 시총 데이터가 없어도(상장폐지
    # 직전 등) 마지막 값을 그대로 들고 가서(forward-fill) 합산해야 그 종목 하나 때문에
    # 섹터 합산 시총이 착시처럼 뚝 떨어지지 않는다(2026-09-25 테크윙 OP밴드 데이터 공백
    # 건과 같은 원칙).
    bars = [(month_end(*add_month(p // 100, p % 100)), sector_yoy[i], sector_est[i], sector_n[i])
            for i, p in enumerate(all_periods) if sector_yoy[i] is not None]
    grid_end = max([b[0] for b in bars], default=MKTCAP_CHART_START)
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
    bar_yoy_map = {d: v for d, v, _e, _n in bars}
    bar_est_map = {d: e for d, _v, e, _n in bars}
    bar_n_map = {d: n for d, _v, _e, n in bars}

    detail = {
        "sector": sector, "n_stocks": len(members),
        "member_names": sorted(m["name"] for m in members),
        "labels": [str(d) for d in grid_dates],
        "mc_eok": [mc_map.get(d) for d in grid_dates],
        "bar_yoy": [bar_yoy_map.get(d) for d in grid_dates],
        "bar_is_estimate": [bool(bar_est_map.get(d, False)) for d in grid_dates],
        "bar_n": [bar_n_map.get(d) for d in grid_dates],
        "quarters": [
            {"period": p, "yoy": sector_yoy[i], "n": sector_n[i], "is_estimate": sector_est[i]}
            for i, p in enumerate(all_periods) if sector_yoy[i] is not None
        ],
    }
    return row, detail


def main():
    wb_path = find_workbook()
    if not wb_path:
        print("data/manual/ 에 'QoQ 계산' 워크북이 없습니다.")
        return
    print(f"워크북 로드 중: {wb_path}")
    wb = openpyxl.load_workbook(wb_path, read_only=True, data_only=True)

    all_results = {}
    for sn in wb.sheetnames:
        res = process_sheet(wb[sn])
        for code, data in res.items():
            all_results.setdefault(code, data)

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
            "gap": gap_map.get(code),
        })
    print(f"섹터 배정 {sum(len(v) for v in sector_members.values())}종목 / 미분류 {n_unclassified}종목")

    os.makedirs(DETAIL_OUT_DIR, exist_ok=True)
    rows = []
    for sector, members in sector_members.items():
        row, detail = build_sector_row_and_detail(sector, members)
        if not row:
            continue
        rows.append(row)
        with open(os.path.join(DETAIL_OUT_DIR, f"{row['code']}.json"), "w", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False)

    print(f"결과 {len(rows)}개 섹터")

    gap_series = pd.Series({r["code"]: r["valuation_gap"] for r in rows if r["valuation_gap"] is not None})
    grade_labels = ["상", "중상", "중", "중하", "하"]
    if len(gap_series) >= 5:
        grade_cat = pd.qcut(gap_series, 5, labels=grade_labels, duplicates="drop")
        grade_by_code = {code: (str(g) if pd.notna(g) else None) for code, g in grade_cat.items()}
    else:
        grade_by_code = {}
    for r in rows:
        r["valuation_grade"] = grade_by_code.get(r["code"])

    all_periods = sorted({p for r in rows for p in r["yoy_by_period"]})

    os.makedirs(SCREEN_DIR, exist_ok=True)
    pd.DataFrame(rows).to_csv(SUMMARY_PATH, index=False, encoding="utf-8-sig")

    html = TEMPLATE.format(
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        n_sectors=len(rows),
        rows_json=json.dumps(rows, ensure_ascii=False),
        periods_json=json.dumps(all_periods),
    )
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(PAGE_OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"저장 완료: {SUMMARY_PATH}")
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
  <div class="updated">최종 갱신: {updated_at} &middot; {n_sectors}개 섹터</div>

  <div class="exp">
    <b>무엇을 보는 페이지인가</b><br>
    종목별 YoY 가속화 트래커와 같은 원본에서, 종목 대신 <b>사용자가 정한 42개 섹터</b>
    (섹터 정리.xlsx)로 묶어 분기마다 그 섹터 종목들의 YoY%를 <b>단순평균</b>한 것입니다.
    섹터 정리.xlsx에 없는(미분류) 종목은 어느 섹터 평균에도 들어가지 않습니다. 평균은 그
    분기에 <b>숫자(%)가 있는 종목만</b> 대상으로 합니다 - 흑전/적전/적자(부호가 뒤바뀌어
    라벨만 있는 분기)인 종목은 그 분기 평균에서 빠집니다(숫자를 지어내지 않습니다). 히트맵
    칸에 마우스를 올리면 몇 개 종목의 평균인지(n)가 표시됩니다.<br>
    <b>가속도(%p)</b>는 종목별 페이지와 같은 정의를 섹터 평균 YoY% 시계열에 그대로
    적용합니다 - (확정 실적 분기의 2분기 뒤 컨센서스 분기 평균YoY%) − (그 확정 실적
    분기의 평균YoY%). 실적/추정 구분은 그 분기에 기여한 종목들의 다수결로 정합니다.
    <b>밸류에이션 등급</b>은 OP밴드 "3년 하위 10% 바텀 대비 %"를 섹터 소속 종목 평균으로
    낸 뒤, 그 섹터 평균값끼리 5분위로 나눈 것입니다. <b>시가총액</b>은 섹터 소속 종목들의
    합산입니다.
  </div>

  <div class="filters">
    <label>검색 <input type="text" id="fSearch" placeholder="섹터명"></label>
    <label>최신 평균YoY% 최소 <input type="number" id="fYoyMin" step="10"></label>
    <label><input type="checkbox" id="fAccelOnly"> 가속 중인 섹터만(평균YoY가 전분기보다 상승)</label>
    <label><input type="checkbox" id="fTripleOnly"> 세자리 평균YoY(100%+) 3분기 이상 유지</label>
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
      <option value="yoy_desc">최신 평균YoY% 큰순</option>
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
        <span><span class="sw" style="background:#eb6834"></span>시가총액(조원, 섹터합산, 왼쪽 축)</span>
        <span><span class="sq" style="background:#2a78d6"></span>평균YoY%(실적, 오른쪽 축)</span>
        <span><span class="sq" style="background:#a9c8ec"></span>평균YoY%(추정, 오른쪽 축)</span>
      </div>
      <div class="chart-wrap"><canvas id="detailChart"></canvas></div>
      <table class="detail-table">
        <thead><tr><th style="text-align:left;">시기</th><th>기여종목수</th><th>평균YoY%</th></tr></thead>
        <tbody id="detailBody"></tbody>
      </table>
      <div class="members" id="detailMembers"></div>
    </div>
  </div>


<script>
const ROWS = {rows_json};
const PERIODS = {periods_json};

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
const POS_CAP = 500;
function posCellStyle(v, isEst) {{
  const t = Math.min(1, Math.log10(1 + Math.max(0, v)) / Math.log10(1 + POS_CAP));
  const light = isEst ? Math.round(85 - t * 40) : Math.round(60 - t * 40);
  const textColor = light < 50 ? '#eafff5' : '#0b3d24';
  return `background:hsl(150, 55%, ${{light}}%); color:${{textColor}}; border-radius:4px;`;
}}
function heatCell(r, period) {{
  const v = r.yoy_by_period[period];
  const est = r.est_by_period[period];
  const n = r.n_by_period[period];
  if (v == null) return '<td class="hm hm-empty">-</td>';
  const title = n ? ` title="${{n}}개 종목 평균"` : '';
  if (v >= 0) {{
    return `<td class="hm"${{title}} style="${{posCellStyle(v, est)}}">${{Math.round(v)}}%</td>`;
  }}
  const cls = est ? 'hm-neg-est' : 'hm-neg';
  return `<td class="hm ${{cls}}"${{title}}>${{Math.round(v)}}%</td>`;
}}

document.getElementById('headRow').innerHTML = '<th class="lbl">섹터</th>'
  + PERIODS.map(p => `<th>${{fmtPeriodShort(parseInt(p))}}</th>`).join('')
  + '<th>시가총액</th><th>가속도(%p)</th><th>밸류에이션</th><th>세자리 유지</th><th>가속화 유지</th><th>종목수</th>';

function applyFilters() {{
  const q = document.getElementById('fSearch').value.trim().toLowerCase();
  const yoyMin = parseFloat(document.getElementById('fYoyMin').value);
  const mktcapMin = parseFloat(document.getElementById('fMktcapMin').value);
  const accelOnly = document.getElementById('fAccelOnly').checked;
  const tripleOnly = document.getElementById('fTripleOnly').checked;
  const accelStreakOnly = document.getElementById('fAccelStreakOnly').checked;
  const grade = document.getElementById('fGrade').value;
  const sort = document.getElementById('fSort').value;

  let rows = ROWS.filter(r => {{
    if (q && !r.name.toLowerCase().includes(q)) return false;
    if (!isNaN(yoyMin) && (r.latest_yoy == null || r.latest_yoy < yoyMin)) return false;
    if (!isNaN(mktcapMin) && (r.latest_mktcap == null || r.latest_mktcap / 1e8 < mktcapMin)) return false;
    if (accelOnly && (r.accel == null || r.accel <= 0)) return false;
    if (tripleOnly && r.triple_digit_streak < 3) return false;
    if (accelStreakOnly && r.accel_streak < 3) return false;
    if (grade && r.valuation_grade !== grade) return false;
    return true;
  }});

  if (sort === 'accel2_desc') rows.sort((a, b) => (b.accel ?? -9e9) - (a.accel ?? -9e9));
  else if (sort === 'yoy_desc') rows.sort((a, b) => (b.latest_yoy ?? -9e9) - (a.latest_yoy ?? -9e9));
  else if (sort === 'streak_desc') rows.sort((a, b) => b.triple_digit_streak - a.triple_digit_streak);
  else if (sort === 'accel_streak_desc') rows.sort((a, b) => b.accel_streak - a.accel_streak);
  else if (sort === 'valuation_asc') rows.sort((a, b) => (a.valuation_gap ?? 9e9) - (b.valuation_gap ?? 9e9));
  else rows.sort((a, b) => (b.latest_mktcap ?? 0) - (a.latest_mktcap ?? 0));

  document.getElementById('count').textContent = rows.length + '개 섹터 (행 클릭하면 상세)';
  document.getElementById('tbody').innerHTML = rows.map(r => `
    <tr data-code="${{r.code}}">
      <td class="lbl">${{r.name}}</td>
      ${{PERIODS.map(p => heatCell(r, p)).join('')}}
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
function openDetail(code) {{
  fetch(`sector_yoy_accel_tracker_data/${{code}}.json`).then(r => r.json()).then(d => {{
    document.getElementById('detailName').textContent = `${{d.sector}} (${{d.n_stocks}}개 종목)`;
    document.getElementById('detailBody').innerHTML = d.quarters.slice().reverse().map(q => `
      <tr>
        <td style="text-align:left;">${{fmtPeriod(q.period)}}${{q.is_estimate ? ' <span class="sub">(추정)</span>' : ''}}</td>
        <td>${{q.n}}개</td>
        <td>${{pctSpan(q.yoy)}}</td>
      </tr>`).join('');
    document.getElementById('detailMembers').innerHTML = '<b>구성종목</b>: ' + d.member_names.join(', ');

    const MC = d.mc_eok.map(v => v == null ? null : v / 10000);
    const OP = d.bar_yoy;
    const COL = d.bar_is_estimate.map(e => e ? '#a9c8ec' : '#2a78d6');
    if (detailChart) detailChart.destroy();
    detailChart = new Chart(document.getElementById('detailChart').getContext('2d'), {{
      data: {{
        labels: d.labels,
        datasets: [
          {{ type: 'bar', label: '평균YoY%', data: OP, backgroundColor: COL, borderRadius: 4, maxBarThickness: 36, order: 2, yAxisID: 'y1' }},
          {{ type: 'line', label: '시가총액', data: MC, borderColor: '#eb6834', backgroundColor: 'rgba(235,104,52,0.08)', borderWidth: 2, pointRadius: 2, fill: true, spanGaps: false, tension: 0.15, order: 1, yAxisID: 'y' }},
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{ mode: 'index', intersect: false, filter: (c) => c.parsed.y != null, callbacks: {{ label: (c) => c.dataset.yAxisID === 'y' ? c.dataset.label + ': ' + c.parsed.y.toLocaleString() + '조원' : c.dataset.label + ': ' + (c.parsed.y >= 0 ? '+' : '') + c.parsed.y.toFixed(1) + '%' }} }}
        }},
        scales: {{
          x: {{ grid: {{ display: false }}, ticks: {{ color: '#9aa0a6', maxTicksLimit: 14, maxRotation: 45 }} }},
          y: {{ position: 'left', grid: {{ color: '#23262e' }}, ticks: {{ color: '#eb6834', callback: (v) => v.toLocaleString() + '조' }} }},
          y1: {{ position: 'right', grid: {{ display: false }}, ticks: {{ color: '#2a78d6', callback: (v) => v + '%' }} }},
        }},
        interaction: {{ mode: 'index', intersect: false }}
      }}
    }});
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
