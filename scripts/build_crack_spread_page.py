"""
data/crack_spread_raw.csv(EIA 기반 3:2:1 크랙 스프레드)로
docs/crack_spread.html(정유화학 정제마진 트래커 페이지) + docs/downloads/crack_spread.xlsx(엑셀 다운로드)를 만든다.
"""
import json
import os
from datetime import datetime

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
DOWNLOADS_DIR = os.path.join(DOCS_DIR, "downloads")
IN_PATH = os.path.join(DATA_DIR, "crack_spread_raw.csv")
HTML_PATH = os.path.join(DOCS_DIR, "crack_spread.html")
XLSX_PATH = os.path.join(DOWNLOADS_DIR, "crack_spread.xlsx")

DEFAULT_RANGE_DAYS = 365 * 3  # 기본 표시 구간(최근 3년) - 기간 버튼/직접 날짜 지정으로 전체(2006~)까지 조절 가능


def build_excel(df):
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    out = df.copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out = out.rename(columns={
        "date": "날짜", "wti_usd_per_bbl": "WTI($/bbl)",
        "gasoline_usd_per_gal": "휘발유($/gal)", "diesel_usd_per_gal": "경유($/gal)",
        "gasoline_usd_per_bbl": "휘발유($/bbl)", "diesel_usd_per_bbl": "경유($/bbl)",
        "crack_spread_321_usd_per_bbl": "3:2:1크랙스프레드($/bbl)",
    })
    with pd.ExcelWriter(XLSX_PATH, engine="openpyxl") as writer:
        out.to_excel(writer, sheet_name="크랙스프레드", index=False)
        ws = writer.sheets["크랙스프레드"]
        for i, col in enumerate(out.columns, start=1):
            width = max(12, min(22, out[col].astype(str).str.len().max() + 2))
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width


def main():
    if not os.path.exists(IN_PATH):
        print("crack_spread_raw.csv가 없습니다. fetch_crack_spread.py를 먼저 실행하세요.")
        return
    df = pd.read_csv(IN_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)

    build_excel(df)

    # 차트는 전체 기간(2006년~)을 다 JS로 넘기고, 기본 표시 구간(최근 3년)과 기간 버튼/직접
    # 날짜 지정은 클라이언트에서 슬라이싱한다(유동성 대시보드의 날짜 구간 선택 기능과 동일한 패턴).
    dates = df["date"].dt.strftime("%Y-%m-%d").tolist()
    wti = [round(v, 2) for v in df["wti_usd_per_bbl"]]
    spread = [round(v, 2) for v in df["crack_spread_321_usd_per_bbl"]]
    gasoline = [round(v, 2) for v in df["gasoline_usd_per_bbl"]]
    diesel = [round(v, 2) for v in df["diesel_usd_per_bbl"]]

    latest = df.iloc[-1]
    latest_date = latest["date"].strftime("%Y-%m-%d")

    spread_series = df["crack_spread_321_usd_per_bbl"]
    spread_mean = spread_series.mean()
    spread_pctl = (spread_series < latest["crack_spread_321_usd_per_bbl"]).mean() * 100

    html = TEMPLATE.format(
        dates_json=json.dumps(dates),
        wti_json=json.dumps(wti),
        spread_json=json.dumps(spread),
        gasoline_json=json.dumps(gasoline),
        diesel_json=json.dumps(diesel),
        latest_date=latest_date,
        latest_wti=f"${latest['wti_usd_per_bbl']:.2f}",
        latest_spread=f"${latest['crack_spread_321_usd_per_bbl']:.2f}",
        spread_mean=f"${spread_mean:.2f}",
        spread_pctl=f"{spread_pctl:.0f}%ile",
        n_days=len(df),
        default_range_days=DEFAULT_RANGE_DAYS,
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"저장 완료: {HTML_PATH}, {XLSX_PATH}")


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>정유화학 정제마진(크랙 스프레드) 트래커</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  a.back {{ color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }}
  h1 {{ font-size:20px; margin:8px 0 4px 0; }}
  .updated {{ color:#9aa0a6; font-size:13px; margin-bottom:20px; }}
  .badges {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap:12px; margin-bottom:20px; max-width:820px; }}
  .badge {{ background:#1a1d24; border-radius:10px; padding:14px 16px; }}
  .badge .label {{ color:#9aa0a6; font-size:12px; }}
  .badge .value {{ font-size:20px; font-weight:bold; margin-top:4px; }}
  .badge.wti .value {{ color:#4dabf7; }}
  .badge.spread .value {{ color:#ff8787; }}
  .badge.mean .value {{ color:#adb5bd; }}
  .badge.pctl .value {{ color:#63e6be; }}
  .chart-wrap {{ height:440px; position:relative; max-width:1100px; margin-bottom:24px; }}
  .dl {{ display:inline-block; background:#1a1d24; border:1px solid #23262e; border-radius:8px; padding:10px 16px;
    color:#4dabf7; text-decoration:none; font-size:13px; margin-bottom:24px; }}
  .dl:hover {{ border-color:#4dabf7; }}
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
  .custom-range-bar button:hover {{ color:#c7cbd1; border-color:#4dabf7; }}
  .custom-range-hint {{ color:#63e6be; }}
  .note {{ color:#9aa0a6; font-size:12px; line-height:1.7; max-width:900px; background:#1a1d24; border-radius:10px; padding:16px 18px; margin-top:8px; }}
  .note b {{ color:#ffa94d; }}
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>정유화학 정제마진(3:2:1 크랙 스프레드) 트래커</h1>
  <div class="updated">최종 갱신: {updated_at} &middot; 최신 기준일 {latest_date} &middot; 데이터 {n_days}일치(2006년~, EIA)</div>

  <div class="badges">
    <div class="badge wti"><div class="label">WTI 원유</div><div class="value">{latest_wti}</div></div>
    <div class="badge spread"><div class="label">3:2:1 크랙 스프레드</div><div class="value">{latest_spread}</div></div>
    <div class="badge mean"><div class="label">역사적 평균(2006~)</div><div class="value">{spread_mean}</div></div>
    <div class="badge pctl"><div class="label">역사적 백분위</div><div class="value">{spread_pctl}</div></div>
  </div>

  <a class="dl" href="downloads/crack_spread.xlsx">&#128190; 엑셀 다운로드 (전체 기간)</a>

  <div class="range-bar" id="rangeBar"></div>
  <div class="custom-range-bar">
    <label>시작 <input type="date" id="rangeStart"></label>
    <label>종료 <input type="date" id="rangeEnd"></label>
    <button id="rangeApplyBtn">적용</button>
    <span class="custom-range-hint" id="rangeCustomHint"></span>
  </div>

  <div class="chart-wrap"><canvas id="spreadChart"></canvas></div>

  <div class="note">
    <b>정의</b> — 3:2:1 크랙 스프레드 = [(2&times;휘발유 $/bbl) + (1&times;경유 $/bbl) - (3&times;WTI $/bbl)] / 3.
    원유 3배럴을 정제해서 휘발유 2배럴+경유 1배럴을 만든다고 가정한 정유사 정제마진(Gross Refining Margin) 프록시입니다.
    스프레드가 넓을수록(확대) 정유사 마진 환경이 우호적이라는 신호로 흔히 쓰입니다.<br><br>
    <b>소스</b> — 미국 EIA(에너지정보청) 공식 API, WTI(Cushing)/뉴욕항 휘발유·경유 현물가(일별). 국내 정유사와는
    원료(두바이유 등)·판매지역이 달라 정확히 일치하진 않지만, 업계 표준으로 널리 쓰이는 방향성 지표입니다.<br><br>
    <b>한계</b> — 정제원가(에너지·수소 등), 운임, 재고효과, 국내 유가 프리미엄/할인은 반영하지 않은 단순 시장 프록시입니다.
    엑셀 다운로드에서 2006년 이후 전체 원시 데이터를 확인할 수 있습니다.
  </div>

<script>
const fullDates = {dates_json};
const fullData = {{
  wti: {wti_json},
  spread: {spread_json},
  gasoline: {gasoline_json},
  diesel: {diesel_json},
}};

const chart = new Chart(document.getElementById('spreadChart').getContext('2d'), {{
  type: 'line',
  data: {{
    labels: [],
    datasets: [
      {{ label: 'WTI 원유($/bbl, 좌축)', data: [], borderColor: '#4dabf7', backgroundColor: 'transparent', yAxisID: 'yWti', tension: 0.1, pointRadius: 0, borderWidth: 1.5, _key: 'wti' }},
      {{ label: '3:2:1 크랙 스프레드($/bbl, 우축)', data: [], borderColor: '#ff8787', backgroundColor: 'transparent', yAxisID: 'ySpread', tension: 0.1, pointRadius: 0, borderWidth: 2.5, _key: 'spread' }},
      {{ label: '휘발유($/bbl, 우축)', data: [], borderColor: '#63e6be', backgroundColor: 'transparent', yAxisID: 'ySpread', tension: 0.1, pointRadius: 0, borderWidth: 1, borderDash: [3,3], hidden: true, _key: 'gasoline' }},
      {{ label: '경유($/bbl, 우축)', data: [], borderColor: '#ffa94d', backgroundColor: 'transparent', yAxisID: 'ySpread', tension: 0.1, pointRadius: 0, borderWidth: 1, borderDash: [3,3], hidden: true, _key: 'diesel' }},
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ color: '#e6e6e6' }} }} }},
    scales: {{
      x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 12 }}, grid: {{ color: '#23262e' }} }},
      yWti: {{ position: 'left', title: {{ display: true, text: 'WTI($/bbl)', color: '#4dabf7' }}, ticks: {{ color: '#4dabf7' }}, grid: {{ color: '#23262e' }} }},
      ySpread: {{ position: 'right', title: {{ display: true, text: '스프레드/제품가($/bbl)', color: '#ff8787' }}, ticks: {{ color: '#ff8787' }}, grid: {{ drawOnChartArea: false }} }},
    }}
  }}
}});

const rangeBar = document.getElementById('rangeBar');
const rangeStartInput = document.getElementById('rangeStart');
const rangeEndInput = document.getElementById('rangeEnd');
const rangeCustomHint = document.getElementById('rangeCustomHint');
let currentRange = {{ mode: 'preset', days: {default_range_days} }};

const RANGE_OPTIONS = [
  {{ label: '1개월', days: 30 }},
  {{ label: '3개월', days: 90 }},
  {{ label: '6개월', days: 180 }},
  {{ label: '1년', days: 365 }},
  {{ label: '3년', days: 1095 }},
  {{ label: '5년', days: 1825 }},
  {{ label: '전체', days: null }},
];

function computeRangeIndices(range) {{
  if (!fullDates.length) return [0, -1];
  if (range.mode === 'custom') {{
    let startIdx = 0;
    if (range.start) {{
      const found = fullDates.findIndex(d => d >= range.start);
      startIdx = found < 0 ? fullDates.length : found;
    }}
    let endIdx = fullDates.length - 1;
    if (range.end) {{
      endIdx = -1;
      for (let i = fullDates.length - 1; i >= 0; i--) {{
        if (fullDates[i] <= range.end) {{ endIdx = i; break; }}
      }}
    }}
    return [startIdx, endIdx];
  }}
  let startIdx = 0;
  if (range.days !== null) {{
    const cutoff = new Date(fullDates[fullDates.length - 1]);
    cutoff.setDate(cutoff.getDate() - range.days);
    const found = fullDates.findIndex(d => new Date(d) >= cutoff);
    startIdx = found < 0 ? 0 : found;
  }}
  return [startIdx, fullDates.length - 1];
}}

function applyCurrentRange() {{
  const [startIdx, endIdx] = computeRangeIndices(currentRange);
  chart.data.labels = fullDates.slice(startIdx, endIdx + 1);
  chart.data.datasets.forEach(ds => {{ ds.data = fullData[ds._key].slice(startIdx, endIdx + 1); }});
  chart.update();
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

document.getElementById('rangeApplyBtn').onclick = applyCustomRange;
if (fullDates.length) {{
  const minD = fullDates[0], maxD = fullDates[fullDates.length - 1];
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
applyRange({default_range_days});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
