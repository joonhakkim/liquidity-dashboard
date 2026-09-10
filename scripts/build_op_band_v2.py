"""
OP밴드 v2 (시험용, 2026-09-10) - docs/op_band_v2.html

기존 OP밴드(build_op_band.py)와 두 가지가 다르다:

1) 분모를 "12개월 선행 보간(12M forward)"으로 계산한다.
   기존은 6월 스위칭(1~6월=당해, 7~12월=차년)이라 7/1에 분모가 한꺼번에 갈아끼워지면서
   배수가 계단으로 뚝 떨어졌다(2026-09-10 실측: 6/30->7/1 하루에 중앙값 -7.3%, 597종목 중
   56%가 5% 넘게 급락). 그래서 "역대 최저 배수"가 죄다 7월에 찍히는 착시가 생겼다.
       선행OP(t) = w x 당해연도(NFY1) + (1-w) x 차년도(NFY2),  w = (13 - 월) / 12
   1월이면 w=1(당해 100%), 7월이면 w=0.5(반반), 12월이면 w=1/12(거의 차년).
   이렇게 하면 분모가 매일 조금씩 굴러가서 절벽이 사라지고, "OP가 늘어서 배수가 낮아지는"
   흐름이 특정 하루에 몰리지 않고 자연스럽게 반영된다.
   (NFY2가 없으면 NFY1만, 둘 다 없으면 TTM - 기존 우선순위와 동일)

2) "바텀"을 단일 최저값이 아니라 최근 N년 하위 퍼센타일로 잡는다.
   하루짜리 이상치(거래정지 후 재개, 데이터 오류)에 최저값이 좌우되는 걸 막고, 실제로 여러 번
   눌렸던 구간의 평균적 하단을 잡기 위함. 10/15/20 퍼센타일을 다 계산해서 화면에서 고를 수 있게 한다.

화면 정렬 기준은 "현재 배수가 바텀 대비 몇 % 위인가" - 0%에 가까울수록 바텀권.
"""
import json
import math
import os
from datetime import datetime

import openpyxl
import pandas as pd

from build_op_band import (
    MKTCAP_ITEM, TTM_ITEM, QUARTER_ITEM,
    HEADER_CODE_ROW, HEADER_NAME_ROW, HEADER_ITEM_ROW, HEADER_BASEDATE_ROW, DATA_START_ROW,
    find_workbook, detect_blocks, load_naver_sector_map, load_sector_map, pick_band_multiples,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
SCREEN_DIR = os.path.join(DATA_DIR, "screening")
SUMMARY_PATH = os.path.join(SCREEN_DIR, "op_band_v2_summary.csv")
PAGE_OUT_PATH = os.path.join(DOCS_DIR, "op_band_v2.html")
DETAIL_OUT_DIR = os.path.join(DOCS_DIR, "op_band_v2_data")

BOTTOM_WINDOW_YEARS = 3   # 바텀 계산에 쓰는 최근 기간
PERCENTILES = [10, 15, 20]


def forward_weight(date):
    """12개월 선행 보간 가중치 - 그 달 기준 '올해가 차지하는 비율'."""
    return (13 - date.month) / 12.0


def process_sheet_v2(ws):
    max_col = ws.max_column
    max_row = ws.max_row

    def read_row(r):
        return next(ws.iter_rows(min_row=r, max_row=r, max_col=max_col, values_only=True))

    row_codes = read_row(HEADER_CODE_ROW)
    row_names = read_row(HEADER_NAME_ROW)
    row_items = read_row(HEADER_ITEM_ROW)
    row_basedate = read_row(HEADER_BASEDATE_ROW)

    blocks = detect_blocks(row_codes)

    dates, data_rows = [], []
    for row in ws.iter_rows(min_row=DATA_START_ROW, max_row=max_row, max_col=max_col, values_only=True):
        d = row[0]
        if d is None or not isinstance(d, datetime):
            continue
        dates.append(d)
        data_rows.append(row)

    results = {}
    for code, start, end in blocks:
        mktcap_idx = ttm_idx = nfy1_idx = nfy2_idx = None
        for k in range(start, end):
            item, base = row_items[k], row_basedate[k]
            if item == MKTCAP_ITEM:
                mktcap_idx = k
            elif item == TTM_ITEM:
                ttm_idx = k
            elif item == QUARTER_ITEM:
                if base == "NFY1":
                    nfy1_idx = k
                elif base == "NFY2":
                    nfy2_idx = k
        # v2는 신형(NFY1/NFY2 직접값) 파일만 대상 - 구형 분기합산 파일은 보간 자체가 불가능
        if mktcap_idx is None or (nfy1_idx is None and nfy2_idx is None):
            continue

        name = row_names[start] if start < len(row_names) else code
        series_dates, series_mult, series_op, series_mktcap = [], [], [], []
        prev_op = None
        for row_vals, d in zip(data_rows, dates):
            mktcap = row_vals[mktcap_idx]
            if mktcap is None:
                continue
            fy1 = row_vals[nfy1_idx] if nfy1_idx is not None else None
            fy2 = row_vals[nfy2_idx] if nfy2_idx is not None else None
            w = forward_weight(d)
            if fy1 is not None and fy2 is not None:
                if (fy1 > 0) != (fy2 > 0):
                    # 적자->흑자(또는 그 반대) 전환 구간: 섞으면 분모가 0을 통과하면서 배수가
                    # 수억 배로 폭발한다(무의미). 이럴 땐 보간하지 않고 기존 6월 스위칭 룰대로
                    # 그 시점의 목표 회계연도 값 하나만 쓴다.
                    op = fy1 if d.month <= 6 else fy2
                else:
                    op = w * fy1 + (1 - w) * fy2
            elif fy1 is not None:
                op = fy1
            elif fy2 is not None:
                op = fy2
            elif ttm_idx is not None:
                op = row_vals[ttm_idx]
            else:
                op = None
            if op is None:
                op = prev_op
            if op is None or op == 0:
                continue
            prev_op = op
            op_won = op * 1000
            series_dates.append(d.strftime("%Y-%m-%d"))
            series_op.append(round(op_won, 0))
            series_mktcap.append(round(mktcap, 0))
            series_mult.append(round(mktcap / op_won, 4))

        if series_dates:
            results[code] = {"name": name, "dates": series_dates, "mult": series_mult,
                              "op": series_op, "mktcap": series_mktcap}
    return results


def percentile(sorted_vals, pct):
    """선형보간 퍼센타일(numpy 없이)."""
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * pct / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def build_row(code, data, sector_map, naver_sector_map, cutoff_date):
    pairs = [(d, m) for d, m in zip(data["dates"], data["mult"]) if m is not None and m > 0]
    if not pairs:
        return None
    latest_date, latest_mult = data["dates"][-1], data["mult"][-1]
    if latest_mult is None:
        return None

    recent = [m for d, m in pairs if d >= cutoff_date]
    used_window = f"최근{BOTTOM_WINDOW_YEARS}년"
    if len(recent) < 60:   # 최근 구간 표본이 너무 적으면(신규상장 등) 전체 기간으로
        recent = [m for _d, m in pairs]
        used_window = "전체기간"
    if not recent:
        return None
    srt = sorted(recent)

    row = {
        "code": code, "name": data["name"],
        "sector": naver_sector_map.get(code.lstrip("A")) or sector_map.get(data["name"]),
        "latest_date": latest_date,
        "latest_mult": round(latest_mult, 2),
        "window": used_window,
        "n_obs": len(recent),
        "min_mult": round(srt[0], 2),
        "latest_mktcap": round(data["mktcap"][-1], 0) if data.get("mktcap") else None,
    }
    for p in PERCENTILES:
        b = percentile(srt, p)
        row[f"bottom_p{p}"] = round(b, 2)
        # 바텀 대비 현재 배수가 몇 % 위인가(마이너스면 바텀 아래로 내려간 것)
        row[f"gap_p{p}"] = round((latest_mult - b) / b * 100, 1) if b and b > 0 else None
    return row


def main():
    wb_path = find_workbook()
    if not wb_path:
        print("data/manual/ 에 '기업 밴드' 워크북이 없습니다.")
        return
    print(f"워크북 로드 중: {wb_path}")
    wb = openpyxl.load_workbook(wb_path, read_only=True, data_only=True)

    all_results = {}
    for sn in wb.sheetnames:
        print(f"처리 중: {sn} ...")
        res = process_sheet_v2(wb[sn])
        for code, data in res.items():
            all_results.setdefault(code, data)
        print(f"  {len(res)}개 종목(12개월 선행 보간)")

    naver_sector_map = load_naver_sector_map()
    sector_map = load_sector_map()

    latest_overall = max(d["dates"][-1] for d in all_results.values())
    cutoff = (pd.Timestamp(latest_overall) - pd.DateOffset(years=BOTTOM_WINDOW_YEARS)).strftime("%Y-%m-%d")
    print(f"바텀 계산 구간: {cutoff} ~ {latest_overall}")

    os.makedirs(DETAIL_OUT_DIR, exist_ok=True)
    rows = []
    for code, data in all_results.items():
        r = build_row(code, data, sector_map, naver_sector_map, cutoff)
        if not r:
            continue
        rows.append(r)
        # 종목 클릭 시 띄우는 밴드 차트용 상세 데이터.
        # 용량 절약: (1) mult는 mktcap/op로 브라우저에서 계산 가능하므로 저장하지 않고,
        # (2) 금액은 원 대신 억원 단위로 반올림해서 자릿수를 줄인다(2,300여 종목 x 매일
        # 재생성이라 원 단위 16자리를 그대로 쓰면 저장소가 빠르게 불어남).
        detail = {
            "code": code, "name": data["name"],
            "dates": data["dates"],
            "mktcapEok": [round(v / 1e8, 2) for v in data["mktcap"]],
            "opEok": [round(v / 1e8, 2) for v in data["op"]],
            "bandMultiples": pick_band_multiples(data["mult"]),
            "bottoms": {f"p{p}": r[f"bottom_p{p}"] for p in PERCENTILES},
        }
        with open(os.path.join(DETAIL_OUT_DIR, f"{code}.json"), "w", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False)
    # 기존 OP밴드와 동일하게, 데이터가 몇 년씩 밀린 종목은 제외(비교 자체가 무의미)
    rows = [r for r in rows if r["latest_date"] == latest_overall]
    print(f"결과 {len(rows)}종목 (최신 기준일 {latest_overall})")

    os.makedirs(SCREEN_DIR, exist_ok=True)
    pd.DataFrame(rows).to_csv(SUMMARY_PATH, index=False, encoding="utf-8-sig")

    html = TEMPLATE.format(
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        n_stocks=len(rows),
        latest_date=latest_overall,
        window_years=BOTTOM_WINDOW_YEARS,
        cutoff=cutoff,
        rows_json=json.dumps(rows, ensure_ascii=False),
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
<title>OP밴드 v2 (시험) - 12개월 선행 보간 + 바텀 퍼센타일</title>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  a.back {{ color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }}
  h1 {{ font-size:20px; margin:8px 0 4px 0; }}
  .updated {{ color:#9aa0a6; font-size:13px; margin-bottom:16px; }}
  .exp {{ background:#1a1d24; border-radius:10px; padding:14px 18px; font-size:12px; color:#9aa0a6; line-height:1.8; max-width:1000px; margin-bottom:18px; }}
  .exp b {{ color:#ffa94d; }}
  .exp code {{ background:#23262e; padding:1px 6px; border-radius:4px; color:#63e6be; }}
  .filters {{ display:flex; gap:12px; flex-wrap:wrap; align-items:center; margin-bottom:14px; font-size:13px; color:#9aa0a6; }}
  .filters input, .filters select {{ background:#1a1d24; border:1px solid #2a2e37; color:#e6e6e6; border-radius:6px; padding:6px 10px; font-size:13px; }}
  .filters input[type="number"] {{ width:80px; }}
  .table-wrap {{ overflow-x:auto; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  th, td {{ padding:8px 12px; text-align:right; border-bottom:1px solid #23262e; white-space:nowrap; }}
  th:first-child, td:first-child, th:nth-child(3), td:nth-child(3) {{ text-align:left; }}
  th {{ color:#9aa0a6; font-weight:normal; font-size:12px; position:sticky; top:0; background:#0f1115; }}
  .gap-hot {{ color:#ff2ec4; font-weight:bold; }}
  .gap-warm {{ color:#ff8787; }}
  .sub {{ color:#6b7280; font-size:11px; }}
  .count {{ color:#63e6be; font-size:12px; margin-bottom:8px; }}
  tbody tr {{ cursor:pointer; }}
  tbody tr:hover {{ background:#1a1d24; }}
  .overlay {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,0.75); z-index:200; overflow:auto; padding:40px 20px; }}
  .overlay.open {{ display:block; }}
  .modal {{ background:#12151b; border:1px solid #23262e; border-radius:14px; max-width:1100px; margin:0 auto; padding:22px 26px; }}
  .modal h2 {{ font-size:17px; margin:0 0 6px 0; }}
  .close-btn {{ float:right; background:none; border:none; color:#9aa0a6; font-size:22px; cursor:pointer; line-height:1; }}
  .range-bar {{ display:flex; gap:6px; margin:12px 0; flex-wrap:wrap; }}
  .range-btn {{ background:#1a1d24; border:1px solid #2a2e37; color:#9aa0a6; padding:5px 12px; border-radius:999px; cursor:pointer; font-size:12px; font-family:inherit; }}
  .range-btn.active {{ background:#4dabf7; color:#0f1115; border-color:#4dabf7; font-weight:bold; }}
  .band-controls {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; font-size:12px; color:#9aa0a6; margin-bottom:8px; }}
  .band-controls input {{ background:#1a1d24; border:1px solid #2a2e37; color:#e6e6e6; border-radius:6px; padding:5px 8px; font-size:12px; width:70px; }}
  .band-controls button {{ background:#1a1d24; border:1px solid #2a2e37; color:#9aa0a6; padding:5px 12px; border-radius:6px; cursor:pointer; font-size:12px; font-family:inherit; }}
  .chart-wrap {{ height:420px; position:relative; margin-top:8px; }}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <a class="back" href="op_band.html">기존 OP밴드 &rarr;</a>
  <h1>OP밴드 v2 <span style="font-size:13px;color:#ffa94d">(시험 버전)</span></h1>
  <div class="updated">최종 갱신: {updated_at} &middot; {n_stocks}종목 &middot; 기준일 {latest_date} &middot; 바텀 산출구간 {cutoff} ~ {latest_date}({window_years}년)</div>

  <div class="exp">
    <b>기존과 뭐가 다른가</b><br>
    ① <b>분모 = 12개월 선행 보간</b>: <code>선행OP = w×당해연도 + (1−w)×차년도, w=(13−월)/12</code>.
    기존 6월 스위칭은 7/1에 분모가 한꺼번에 바뀌면서 배수가 계단으로 급락했고(실측: 6/30→7/1 중앙값 −7.3%),
    그래서 역대 최저가 죄다 7월에 찍혔습니다. 보간하면 분모가 매일 조금씩 굴러가서 절벽이 사라지고,
    "OP가 늘어서 배수가 낮아지는" 흐름이 특정 하루에 몰리지 않습니다.<br>
    ② <b>바텀 = 단일 최저값이 아니라 최근 {window_years}년 하위 퍼센타일</b>(10/15/20 선택).
    하루짜리 이상치에 최저값이 좌우되지 않고, 실제로 여러 번 눌렸던 구간의 평균적 하단이 잡힙니다.<br>
    ③ <b>정렬 기준 = 바텀 대비 %</b>. 0%에 가까울수록(또는 마이너스면 바텀 아래로) 바텀권입니다.
  </div>

  <div class="filters">
    <label>검색 <input type="text" id="fSearch" placeholder="종목명/코드"></label>
    <label>섹터 <select id="fSector"><option value="">전체</option></select></label>
    <label>바텀 기준 <select id="fPct">
      <option value="10">하위 10%</option>
      <option value="15" selected>하위 15%</option>
      <option value="20">하위 20%</option>
    </select></label>
    <label>바텀대비 이내 <input type="number" id="fGap" value="10" step="1">%</label>
    <label>현재배수 최소 <input type="number" id="fMultMin" step="0.5"></label>
    <label>현재배수 최대 <input type="number" id="fMultMax" step="0.5"></label>
    <label><input type="checkbox" id="fIncludeNeg"> 적자(마이너스 배수) 포함</label>
    <label>시총 최소(억) <input type="number" id="fMktcapMin" step="100"></label>
    <label>정렬 <select id="fSort">
      <option value="gap_asc">바텀 대비 근접순</option>
      <option value="mult_asc">현재배수 낮은순</option>
      <option value="mult_desc">현재배수 높은순</option>
      <option value="mktcap_desc">시가총액 큰순</option>
      <option value="mktcap_asc">시가총액 작은순</option>
    </select></label>
  </div>
  <div class="count" id="count"></div>

  <div class="table-wrap">
  <table>
    <thead><tr>
      <th>종목명</th><th>코드</th><th>섹터</th><th>시가총액</th><th>현재배수</th><th>바텀배수</th><th>바텀 대비</th><th>구간최저</th><th>표본</th><th>기준일</th>
    </tr></thead>
    <tbody id="tbody"></tbody>
  </table>
  </div>

  <div class="overlay" id="overlay">
    <div class="modal">
      <button class="close-btn" id="closeBtn">&times;</button>
      <h2 id="detailName"></h2>
      <div class="sub" id="detailSub"></div>
      <div class="range-bar" id="rangeBar"></div>
      <div class="band-controls">
        <label>밴드 개수 <input type="number" id="bandCount" min="1" max="20" step="1" value="6"></label>
        <label>직접 배수(콤마, 예 5,10,20) <input type="text" id="bandCustom" style="width:150px" placeholder="비우면 자동"></label>
        <label>세로축 최대(억원) <input type="number" id="yAxisMax" placeholder="자동"></label>
        <button id="bandApplyBtn">적용</button>
      </div>
      <div class="chart-wrap"><canvas id="bandChart"></canvas></div>
    </div>
  </div>

<script>
const ROWS = {rows_json};

const sectors = [...new Set(ROWS.map(r => r.sector).filter(Boolean))].sort();
const selSector = document.getElementById('fSector');
sectors.forEach(s => {{ const o = document.createElement('option'); o.value = s; o.textContent = s; selSector.appendChild(o); }});

function gapClass(g) {{ if (g == null) return ''; if (g <= 0) return 'gap-hot'; if (g <= 10) return 'gap-warm'; return ''; }}
function fmtMktcap(v) {{
  if (v == null) return '-';
  const eok = v / 1e8;
  return eok >= 10000 ? (eok / 10000).toFixed(2) + '조' : Math.round(eok).toLocaleString() + '억';
}}

function applyFilters() {{
  const q = document.getElementById('fSearch').value.trim().toLowerCase();
  const sec = selSector.value;
  const pct = document.getElementById('fPct').value;
  const gapMax = parseFloat(document.getElementById('fGap').value);
  const multMin = parseFloat(document.getElementById('fMultMin').value);
  const multMax = parseFloat(document.getElementById('fMultMax').value);
  const mktcapMin = parseFloat(document.getElementById('fMktcapMin').value);
  const sort = document.getElementById('fSort').value;
  const includeNeg = document.getElementById('fIncludeNeg').checked;
  const bKey = 'bottom_p' + pct, gKey = 'gap_p' + pct;

  let rows = ROWS.filter(r => {{
    // 적자(현재 배수 마이너스)는 배수 자체가 의미 없어서 기본 제외 - 바텀 대비 %가
    // -5만% 같은 무의미한 값으로 정렬 상단을 채워버림
    if (!includeNeg && r.latest_mult <= 0) return false;
    if (q && !(r.name.toLowerCase().includes(q) || r.code.toLowerCase().includes(q))) return false;
    if (sec && r.sector !== sec) return false;
    if (!isNaN(gapMax) && (r[gKey] == null || r[gKey] > gapMax)) return false;
    if (!isNaN(multMin) && r.latest_mult < multMin) return false;
    if (!isNaN(multMax) && r.latest_mult > multMax) return false;
    if (!isNaN(mktcapMin) && (r.latest_mktcap == null || r.latest_mktcap / 1e8 < mktcapMin)) return false;
    return true;
  }});

  if (sort === 'gap_asc') rows.sort((a, b) => (a[gKey] ?? 9e9) - (b[gKey] ?? 9e9));
  else if (sort === 'mult_asc') rows.sort((a, b) => a.latest_mult - b.latest_mult);
  else if (sort === 'mult_desc') rows.sort((a, b) => b.latest_mult - a.latest_mult);
  else if (sort === 'mktcap_desc') rows.sort((a, b) => (b.latest_mktcap ?? 0) - (a.latest_mktcap ?? 0));
  else rows.sort((a, b) => (a.latest_mktcap ?? 9e18) - (b.latest_mktcap ?? 9e18));

  document.getElementById('count').textContent = rows.length + '종목 (행 클릭하면 밴드 차트)';
  document.getElementById('tbody').innerHTML = rows.slice(0, 400).map(r => `
    <tr data-code="${{r.code}}">
      <td>${{r.name}}</td><td class="sub">${{r.code}}</td><td class="sub">${{r.sector || '-'}}</td>
      <td>${{fmtMktcap(r.latest_mktcap)}}</td>
      <td>${{r.latest_mult.toFixed(2)}}x</td>
      <td>${{r[bKey].toFixed(2)}}x</td>
      <td class="${{gapClass(r[gKey])}}">${{r[gKey] == null ? '-' : r[gKey].toFixed(1) + '%'}}</td>
      <td class="sub">${{r.min_mult.toFixed(2)}}x</td>
      <td class="sub">${{r.n_obs}}일<br>${{r.window}}</td>
      <td class="sub">${{r.latest_date}}</td>
    </tr>`).join('');
  document.querySelectorAll('#tbody tr').forEach(tr =>
    tr.addEventListener('click', () => openDetail(tr.dataset.code)));
}}

['fSearch','fSector','fPct','fGap','fMultMin','fMultMax','fMktcapMin','fSort','fIncludeNeg'].forEach(id => {{
  document.getElementById(id).addEventListener('input', applyFilters);
  document.getElementById(id).addEventListener('change', applyFilters);
}});

// ---------- 밴드 차트(모달) ----------
const NICE_STEPS = [1, 2, 5, 10, 15, 20, 25, 30, 50, 100, 200, 250, 500, 1000];
const RANGE_OPTIONS = [
  {{ label: '1년', days: 365 }}, {{ label: '2년', days: 730 }},
  {{ label: '3년', days: 1095 }}, {{ label: '전체', days: null }},
];
let chart = null, currentDetail = null, currentRange = {{ days: null }};

function pickBandMultiples(mults, targetLines) {{
  const positive = mults.filter(m => m != null && m > 0);
  if (!positive.length) return [];
  const end = Math.ceil(Math.max(...positive)) + 1;
  const step = NICE_STEPS.find(s => s >= end / targetLines) ?? NICE_STEPS[NICE_STEPS.length - 1];
  const out = [];
  for (let v = step; v <= end; v += step) out.push(v);
  return out.length ? out : [step];
}}

function sliceRange(dates) {{
  if (currentRange.days == null) return 0;
  const cutoff = new Date(dates[dates.length - 1]);
  cutoff.setDate(cutoff.getDate() - currentRange.days);
  const i = dates.findIndex(d => new Date(d) >= cutoff);
  return i < 0 ? 0 : i;
}}

function renderChart(bandMultiples) {{
  const d = currentDetail;
  const s = sliceRange(d.dates);
  // 저장 용량 때문에 JSON엔 억원 단위 시총/OP만 있고 배수는 없다 - 여기서 나눠서 쓴다.
  const dates = d.dates.slice(s), op = d.opEok.slice(s), mktcap = d.mktcapEok.slice(s);

  document.getElementById('detailSub').textContent =
    `밴드선: ${{bandMultiples.map(m => m + 'x').join(', ')}}  ·  바텀(하위15%) ${{d.bottoms.p15}}x`;

  const datasets = bandMultiples.map((m, i) => ({{
    label: `${{m}}x`, data: op.map(v => v * m),
    borderColor: `hsl(${{200 + i * 30}}, 60%, 55%)`, backgroundColor: 'transparent',
    borderWidth: 1, borderDash: [4, 3], pointRadius: 0, tension: 0,
  }}));
  // 바텀(하위15%) 라인 - 이 페이지의 핵심 기준선이라 점선 말고 굵게 표시
  datasets.push({{
    label: `바텀 ${{d.bottoms.p15}}x`, data: op.map(v => v * d.bottoms.p15),
    borderColor: '#ff2ec4', backgroundColor: 'transparent',
    borderWidth: 2, pointRadius: 0, tension: 0,
  }});
  datasets.push({{
    label: '시가총액(실제, 억원)', data: mktcap,
    borderColor: '#e6e6e6', backgroundColor: 'transparent',
    borderWidth: 2.5, pointRadius: 0, tension: 0, order: 0,
  }});

  // 이 페이지의 핵심은 "시가총액 vs 바텀선" 비교라, y축 기본 최대값을 그 둘이 잘 보이는
  // 수준(둘 중 큰 값의 1.25배)으로 잡는다. 그보다 위쪽 밴드선은 잘려 보이는 게 정상 -
  // 그만큼 비싼 배수라는 뜻. (기존 v1은 시총의 4.5배로 잡아서, OP가 출렁이는 종목은
  // 시총선이 바닥에 눌려 안 보였다.) 필요하면 "세로축 최대" 입력으로 직접 덮어쓴다.
  const vals = mktcap.filter(v => v != null);
  const bottomVals = op.map(v => v * d.bottoms.p15).filter(v => v != null && v > 0);
  const yOverrideTxt = document.getElementById('yAxisMax').value.trim();
  const yOverride = yOverrideTxt ? parseFloat(yOverrideTxt) : null;
  const autoMax = Math.max(vals.length ? Math.max(...vals) : 0,
                           bottomVals.length ? Math.max(...bottomVals) : 0) * 1.25;
  const yMax = (yOverride && !isNaN(yOverride)) ? yOverride : (autoMax > 0 ? autoMax : undefined);
  const minV = vals.length ? Math.min(...vals) : 0;

  if (chart) chart.destroy();
  chart = new Chart(document.getElementById('bandChart').getContext('2d'), {{
    type: 'line',
    data: {{ labels: dates, datasets }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ labels: {{ color: '#e6e6e6', boxWidth: 14, font: {{ size: 11 }} }} }} }},
      scales: {{
        x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 10 }}, grid: {{ color: '#23262e' }} }},
        y: {{ min: minV < 0 ? minV * 1.5 : 0, max: yMax, ticks: {{ color: '#9aa0a6' }}, grid: {{ color: '#23262e' }} }},
      }}
    }}
  }});
}}

function currentBands() {{
  const txt = document.getElementById('bandCustom').value.trim();
  if (txt) return txt.split(',').map(s => parseFloat(s.trim())).filter(v => !isNaN(v) && v > 0).sort((a, b) => a - b);
  const n = parseInt(document.getElementById('bandCount').value, 10) || 6;
  const mults = currentDetail.mktcapEok.map((v, i) => {{
    const o = currentDetail.opEok[i];
    return o ? v / o : null;
  }});
  return pickBandMultiples(mults, n);
}}

function openDetail(code) {{
  fetch(`op_band_v2_data/${{code}}.json`).then(r => r.json()).then(data => {{
    currentDetail = data;
    const n = data.mktcapEok.length - 1;
    const latest = data.opEok[n] ? data.mktcapEok[n] / data.opEok[n] : null;
    document.getElementById('detailName').textContent =
      `${{data.name}} (${{data.code}})` + (latest != null ? ` - 현재 ${{latest.toFixed(2)}}x` : '');
    document.getElementById('bandCount').value = data.bandMultiples.length || 6;
    document.getElementById('bandCustom').value = '';
    document.getElementById('yAxisMax').value = '';
    currentRange = {{ days: null }};
    document.querySelectorAll('.range-btn').forEach(b => b.classList.toggle('active', b.dataset.days === 'null'));
    renderChart(data.bandMultiples);
    document.getElementById('overlay').classList.add('open');
  }});
}}

const rangeBar = document.getElementById('rangeBar');
RANGE_OPTIONS.forEach(o => {{
  const b = document.createElement('button');
  b.className = 'range-btn'; b.textContent = o.label; b.dataset.days = String(o.days);
  b.onclick = () => {{
    currentRange = {{ days: o.days }};
    document.querySelectorAll('.range-btn').forEach(x => x.classList.toggle('active', x === b));
    renderChart(currentBands());
  }};
  rangeBar.appendChild(b);
}});
document.getElementById('bandApplyBtn').addEventListener('click', () => renderChart(currentBands()));
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
