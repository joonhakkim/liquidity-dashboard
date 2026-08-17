"""
data/bollinger_prices.csv(코스피+코스닥 전종목 일별 종가, fetch_bollinger_prices.py 결과)로
종목별 볼린저밴드(20일, ±2표준편차) 상단을 돌파한 종목수를 매일 세서
docs/bollinger_breakout.html(코스피/코스닥 패널 2개) 를 만든다.

방법론:
- 종목별로 20일 이동평균 ± 2*표준편차(20일)를 굴려서 상단밴드 계산(min_periods=20이라
  상장 20영업일 미만인 종목/데이터 시작 초반 20일은 자동으로 계산 제외됨).
- 그날 종가가 상단밴드보다 큰 종목 수를 그 시장(코스피/코스닥)의 "돌파종목수"로 카운트.
- 돌파종목수의 5일 이동평균을 같이 그린다(막대=당일 카운트, 선=5일선).
- 비교용으로 코스피/코스닥 지수(네이버 차트 API)를 우측축에 같이 그린다.
"""
import json
import os
from datetime import datetime

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
IN_PATH = os.path.join(DATA_DIR, "bollinger_prices.csv")
OUT_PATH = os.path.join(DOCS_DIR, "bollinger_breakout.html")

BB_WINDOW = 20
BB_STD_MULT = 2
MA_WINDOW = 5


def fetch_index_history(symbol, count=10000):
    r = requests.get(
        "https://fchart.stock.naver.com/sise.nhn",
        params={"symbol": symbol, "timeframe": "day", "count": count, "requestType": 0},
        timeout=20,
    )
    rows = []
    for line in r.text.split('<item data="')[1:]:
        raw = line.split('"')[0]
        parts = raw.split("|")
        if len(parts) < 5:
            continue
        try:
            rows.append((datetime.strptime(parts[0], "%Y%m%d").strftime("%Y%m%d"), float(parts[4])))
        except ValueError:
            continue
    return dict(rows)


def compute_breakout_series(df_market):
    """df_market: index=date(str YYYYMMDD), columns=code, values=close. 반환: (dates, count, ma5)."""
    wide = df_market.sort_index()
    mean = wide.rolling(BB_WINDOW, min_periods=BB_WINDOW).mean()
    std = wide.rolling(BB_WINDOW, min_periods=BB_WINDOW).std()
    upper = mean + BB_STD_MULT * std
    breakout = wide > upper
    count = breakout.sum(axis=1)
    # 계산 자체가 불가능한(전종목 데이터가 아직 20일이 안 찬) 초반 구간은 잘라낸다.
    count = count[mean.notna().any(axis=1)]
    ma5 = count.rolling(MA_WINDOW, min_periods=1).mean()
    return count, ma5


def build_panel_data(long_df, market, index_symbol):
    sub = long_df[long_df["market"] == market]
    wide = sub.pivot_table(index="date", columns="code", values="close", aggfunc="last")
    count, ma5 = compute_breakout_series(wide)

    index_hist = fetch_index_history(index_symbol)

    dates = count.index.tolist()
    formatted_dates = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates]
    index_vals = [index_hist.get(d) for d in dates]

    return {
        "dates": formatted_dates,
        "count": [int(v) for v in count.values],
        "ma5": [round(float(v), 2) for v in ma5.values],
        "index": [round(v, 2) if v is not None else None for v in index_vals],
        "latest_date": formatted_dates[-1] if formatted_dates else None,
        "latest_count": int(count.iloc[-1]) if len(count) else None,
        "latest_ma5": round(float(ma5.iloc[-1]), 1) if len(ma5) else None,
        "n_stocks": int(wide.shape[1]),
    }


def main():
    if not os.path.exists(IN_PATH):
        print("bollinger_prices.csv가 없습니다. fetch_bollinger_prices.py를 먼저 실행하세요.")
        return
    long_df = pd.read_csv(IN_PATH, dtype={"date": str, "code": str})

    print("코스피 계산 중...")
    kospi = build_panel_data(long_df, "KOSPI", "KOSPI")
    print(f"  {kospi['n_stocks']}종목, 최신({kospi['latest_date']}) 돌파 {kospi['latest_count']}개")

    print("코스닥 계산 중...")
    kosdaq = build_panel_data(long_df, "KOSDAQ", "KOSDAQ")
    print(f"  {kosdaq['n_stocks']}종목, 최신({kosdaq['latest_date']}) 돌파 {kosdaq['latest_count']}개")

    html = TEMPLATE.format(
        kospi_json=json.dumps(kospi, ensure_ascii=False),
        kosdaq_json=json.dumps(kosdaq, ensure_ascii=False),
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        kospi_latest_date=kospi["latest_date"] or "N/A",
        kospi_latest_count=kospi["latest_count"] if kospi["latest_count"] is not None else "N/A",
        kosdaq_latest_count=kosdaq["latest_count"] if kosdaq["latest_count"] is not None else "N/A",
    )
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n저장 완료: {OUT_PATH}")


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>볼린저밴드 상단 돌파 종목수</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  a.back {{ color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }}
  h1 {{ font-size:20px; margin:8px 0 4px 0; }}
  h2 {{ font-size:15px; margin:32px 0 4px 0; color:#c7cbd1; }}
  .updated {{ color:#9aa0a6; font-size:13px; margin-bottom:20px; }}
  .badges {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap:12px; margin-bottom:8px; max-width:820px; }}
  .badge {{ background:#1a1d24; border-radius:10px; padding:14px 16px; }}
  .badge .label {{ color:#9aa0a6; font-size:12px; }}
  .badge .value {{ font-size:20px; font-weight:bold; margin-top:4px; }}
  .badge.kospi .value {{ color:#4dabf7; }}
  .badge.kosdaq .value {{ color:#ff8787; }}
  .range-bar {{ display:flex; gap:6px; margin-bottom:12px; flex-wrap:wrap; }}
  .range-btn {{ background:#1a1d24; border:1px solid #2a2e37; color:#9aa0a6; padding:6px 14px;
    border-radius:999px; cursor:pointer; font-size:12px; font-family:inherit; }}
  .range-btn:hover {{ color:#c7cbd1; border-color:#4dabf7; }}
  .range-btn.active {{ background:#4dabf7; color:#0f1115; border-color:#4dabf7; font-weight:bold; }}
  .custom-range-bar {{ display:flex; align-items:center; gap:10px; margin-bottom:20px; flex-wrap:wrap; font-size:13px; color:#9aa0a6; }}
  .custom-range-bar label {{ display:flex; align-items:center; gap:6px; }}
  .custom-range-bar input[type="date"] {{
    background:#1a1d24; border:1px solid #2a2e37; color:#e6e6e6; border-radius:6px; padding:5px 8px; font-size:13px;
  }}
  .custom-range-bar button {{ background:#1a1d24; border:1px solid #2a2e37; color:#9aa0a6; padding:6px 14px;
    border-radius:6px; cursor:pointer; font-size:12px; font-family:inherit; }}
  .custom-range-hint {{ color:#63e6be; }}
  .chart-wrap {{ height:380px; position:relative; max-width:1100px; margin-bottom:24px; }}
  .note {{ color:#9aa0a6; font-size:12px; line-height:1.7; max-width:900px; background:#1a1d24; border-radius:10px; padding:16px 18px; margin-top:16px; }}
  .note b {{ color:#ffa94d; }}
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>볼린저밴드 상단 돌파 종목수</h1>
  <div class="updated">최종 갱신: {updated_at} &middot; 최신 기준일 {kospi_latest_date} &middot; 20일 볼린저밴드(&plusmn;2표준편차) 기준</div>

  <div class="badges">
    <div class="badge kospi"><div class="label">코스피 돌파종목수(오늘)</div><div class="value">{kospi_latest_count}</div></div>
    <div class="badge kosdaq"><div class="label">코스닥 돌파종목수(오늘)</div><div class="value">{kosdaq_latest_count}</div></div>
  </div>

  <div class="range-bar" id="rangeBar"></div>
  <div class="custom-range-bar">
    <label>시작 <input type="date" id="rangeStart"></label>
    <label>종료 <input type="date" id="rangeEnd"></label>
    <button id="rangeApplyBtn">적용</button>
    <span class="custom-range-hint" id="rangeCustomHint"></span>
  </div>

  <h2>코스피</h2>
  <div class="chart-wrap"><canvas id="kospiChart"></canvas></div>

  <h2>코스닥</h2>
  <div class="chart-wrap"><canvas id="kosdaqChart"></canvas></div>

  <div class="note">
    <b>정의</b> — 종목별로 20일 이동평균 &plusmn; 2표준편차로 볼린저밴드 상단을 계산하고,
    그날 종가가 상단밴드를 넘은 종목 수를 셉니다(막대). 노이즈를 줄이려고 5일 이동평균(선)도
    같이 표시합니다. 지수(우측축, 점선)는 비교용입니다.<br><br>
    <b>소스</b> — KRX 공식 Open API(코스피/코스닥 전종목 일별 종가), 지수는 네이버 차트 API.
    데이터 시작일 이후 20영업일 동안은 밴드 계산 자체가 안 돼서(이동평균 계산 최소기간 미충족)
    차트에 표시되지 않습니다.
  </div>

<script>
const PANELS = {{
  kospi: {{ data: {kospi_json}, canvasId: 'kospiChart', barColor: '#4dabf7', chart: null }},
  kosdaq: {{ data: {kosdaq_json}, canvasId: 'kosdaqChart', barColor: '#ff8787', chart: null }},
}};

const rangeBar = document.getElementById('rangeBar');
const rangeStartInput = document.getElementById('rangeStart');
const rangeEndInput = document.getElementById('rangeEnd');
const rangeCustomHint = document.getElementById('rangeCustomHint');
let currentRange = {{ mode: 'preset', days: 365 }};

const RANGE_OPTIONS = [
  {{ label: '1개월', days: 30 }},
  {{ label: '3개월', days: 90 }},
  {{ label: '6개월', days: 180 }},
  {{ label: '1년', days: 365 }},
  {{ label: '3년', days: 1095 }},
  {{ label: '5년', days: 1825 }},
  {{ label: '전체', days: null }},
];

function computeRangeIndices(dates, range) {{
  if (!dates.length) return [0, -1];
  if (range.mode === 'custom') {{
    let startIdx = 0;
    if (range.start) {{
      const found = dates.findIndex(d => d >= range.start);
      startIdx = found < 0 ? dates.length : found;
    }}
    let endIdx = dates.length - 1;
    if (range.end) {{
      endIdx = -1;
      for (let i = dates.length - 1; i >= 0; i--) {{
        if (dates[i] <= range.end) {{ endIdx = i; break; }}
      }}
    }}
    return [startIdx, endIdx];
  }}
  let startIdx = 0;
  if (range.days !== null) {{
    const cutoff = new Date(dates[dates.length - 1]);
    cutoff.setDate(cutoff.getDate() - range.days);
    const found = dates.findIndex(d => new Date(d) >= cutoff);
    startIdx = found < 0 ? 0 : found;
  }}
  return [startIdx, dates.length - 1];
}}

function buildChart(panel) {{
  const d = panel.data;
  const [startIdx, endIdx] = computeRangeIndices(d.dates, currentRange);
  const labels = d.dates.slice(startIdx, endIdx + 1);
  const count = d.count.slice(startIdx, endIdx + 1);
  const ma5 = d.ma5.slice(startIdx, endIdx + 1);
  const index = d.index.slice(startIdx, endIdx + 1);

  if (panel.chart) panel.chart.destroy();
  panel.chart = new Chart(document.getElementById(panel.canvasId).getContext('2d'), {{
    data: {{
      labels,
      datasets: [
        {{ type: 'bar', label: '돌파종목수', data: count, backgroundColor: panel.barColor + '99', borderWidth: 0, yAxisID: 'yCount', order: 2 }},
        {{ type: 'line', label: '5일선', data: ma5, borderColor: '#ffd43b', backgroundColor: 'transparent', borderWidth: 2, pointRadius: 0, tension: 0.2, yAxisID: 'yCount', order: 1 }},
        {{ type: 'line', label: '지수(우축)', data: index, borderColor: '#63e6be', backgroundColor: 'transparent', borderWidth: 1.5, borderDash: [4,3], pointRadius: 0, tension: 0.1, yAxisID: 'yIndex', order: 0 }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ labels: {{ color: '#e6e6e6' }} }} }},
      scales: {{
        x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 12 }}, grid: {{ color: '#23262e' }} }},
        yCount: {{ position: 'left', title: {{ display: true, text: '돌파종목수', color: '#9aa0a6' }}, ticks: {{ color: '#9aa0a6' }}, grid: {{ color: '#23262e' }} }},
        yIndex: {{ position: 'right', title: {{ display: true, text: '지수', color: '#63e6be' }}, ticks: {{ color: '#63e6be' }}, grid: {{ drawOnChartArea: false }} }},
      }}
    }}
  }});
}}

function applyCurrentRange() {{
  Object.values(PANELS).forEach(buildChart);
}}

function applyRange(days) {{
  currentRange = {{ mode: 'preset', days }};
  applyCurrentRange();
  document.querySelectorAll('.range-btn').forEach(btn => {{
    btn.classList.toggle('active', btn.dataset.days === String(days));
  }});
  rangeStartInput.value = '';
  rangeEndInput.value = '';
  rangeCustomHint.textContent = '';
}}

function applyCustomRange() {{
  const start = rangeStartInput.value || null;
  const end = rangeEndInput.value || null;
  if (!start && !end) return;
  if (start && end && start > end) {{
    rangeCustomHint.textContent = '시작일이 종료일보다 늦습니다.';
    return;
  }}
  currentRange = {{ mode: 'custom', start, end }};
  applyCurrentRange();
  document.querySelectorAll('.range-btn').forEach(btn => btn.classList.remove('active'));
  rangeCustomHint.textContent = `${{start || '처음'}} ~ ${{end || '최신'}} 구간 적용됨`;
}}

document.getElementById('rangeApplyBtn').addEventListener('click', applyCustomRange);
const allDates = PANELS.kospi.data.dates;
if (allDates.length) {{
  const minD = allDates[0], maxD = allDates[allDates.length - 1];
  [rangeStartInput, rangeEndInput].forEach(el => {{ el.min = minD; el.max = maxD; }});
}}

RANGE_OPTIONS.forEach(opt => {{
  const btn = document.createElement('button');
  btn.className = 'range-btn';
  btn.textContent = opt.label;
  btn.dataset.days = String(opt.days);
  btn.onclick = () => applyRange(opt.days);
  rangeBar.appendChild(btn);
}});
applyRange(365);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
