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
    find_workbook, detect_blocks, load_naver_sector_map, load_sector_map,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
SCREEN_DIR = os.path.join(DATA_DIR, "screening")
SUMMARY_PATH = os.path.join(SCREEN_DIR, "op_band_v2_summary.csv")
PAGE_OUT_PATH = os.path.join(DOCS_DIR, "op_band_v2.html")

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
        series_dates, series_mult, series_op = [], [], []
        prev_op = None
        for row_vals, d in zip(data_rows, dates):
            mktcap = row_vals[mktcap_idx]
            if mktcap is None:
                continue
            fy1 = row_vals[nfy1_idx] if nfy1_idx is not None else None
            fy2 = row_vals[nfy2_idx] if nfy2_idx is not None else None
            w = forward_weight(d)
            if fy1 is not None and fy2 is not None:
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
            series_mult.append(round(mktcap / op_won, 4))

        if series_dates:
            results[code] = {"name": name, "dates": series_dates, "mult": series_mult, "op": series_op}
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

    rows = []
    for code, data in all_results.items():
        r = build_row(code, data, sector_map, naver_sector_map, cutoff)
        if r:
            rows.append(r)
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
</style>
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
    <label>정렬 <select id="fSort">
      <option value="gap_asc">바텀 대비 근접순</option>
      <option value="mult_asc">현재배수 낮은순</option>
      <option value="mult_desc">현재배수 높은순</option>
    </select></label>
  </div>
  <div class="count" id="count"></div>

  <div class="table-wrap">
  <table>
    <thead><tr>
      <th>종목명</th><th>코드</th><th>섹터</th><th>현재배수</th><th>바텀배수</th><th>바텀 대비</th><th>구간최저</th><th>표본</th><th>기준일</th>
    </tr></thead>
    <tbody id="tbody"></tbody>
  </table>
  </div>

<script>
const ROWS = {rows_json};

const sectors = [...new Set(ROWS.map(r => r.sector).filter(Boolean))].sort();
const selSector = document.getElementById('fSector');
sectors.forEach(s => {{ const o = document.createElement('option'); o.value = s; o.textContent = s; selSector.appendChild(o); }});

function gapClass(g) {{ if (g == null) return ''; if (g <= 0) return 'gap-hot'; if (g <= 10) return 'gap-warm'; return ''; }}

function applyFilters() {{
  const q = document.getElementById('fSearch').value.trim().toLowerCase();
  const sec = selSector.value;
  const pct = document.getElementById('fPct').value;
  const gapMax = parseFloat(document.getElementById('fGap').value);
  const multMin = parseFloat(document.getElementById('fMultMin').value);
  const multMax = parseFloat(document.getElementById('fMultMax').value);
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
    return true;
  }});

  if (sort === 'gap_asc') rows.sort((a, b) => (a[gKey] ?? 9e9) - (b[gKey] ?? 9e9));
  else if (sort === 'mult_asc') rows.sort((a, b) => a.latest_mult - b.latest_mult);
  else rows.sort((a, b) => b.latest_mult - a.latest_mult);

  document.getElementById('count').textContent = rows.length + '종목';
  document.getElementById('tbody').innerHTML = rows.slice(0, 400).map(r => `
    <tr>
      <td>${{r.name}}</td><td class="sub">${{r.code}}</td><td class="sub">${{r.sector || '-'}}</td>
      <td>${{r.latest_mult.toFixed(2)}}x</td>
      <td>${{r[bKey].toFixed(2)}}x</td>
      <td class="${{gapClass(r[gKey])}}">${{r[gKey] == null ? '-' : r[gKey].toFixed(1) + '%'}}</td>
      <td class="sub">${{r.min_mult.toFixed(2)}}x</td>
      <td class="sub">${{r.n_obs}}일<br>${{r.window}}</td>
      <td class="sub">${{r.latest_date}}</td>
    </tr>`).join('');
}}

['fSearch','fSector','fPct','fGap','fMultMin','fMultMax','fSort','fIncludeNeg'].forEach(id =>
  document.getElementById(id).addEventListener('input', applyFilters));
applyFilters();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
