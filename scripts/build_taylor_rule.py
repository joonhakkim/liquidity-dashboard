"""
data/fred_raw.csv(FRED 데이터)로 "테일러 준칙(Taylor Rule) 적정금리" 트래커
(docs/taylor_rule.html + downloads/taylor_rule.xlsx)를 만든다.

테일러 준칙(1993년 원안, John Taylor)이 제시하는 "적정 정책금리" 공식:
    i = r* + π + 0.5*(π - π*) + 0.5*(산출갭)
  - r* = 실질중립금리(원안 상수 2%를 그대로 사용 - NY연은 Laubach-Williams 같은
    모델기반 추정치는 매 분기 크게 바뀌고 별도 API가 필요해서, 가장 표준적이고
    투명한 "교과서 버전"으로 구현. 나중에 필요하면 모델기반 r*로 교체 가능)
  - π = 근원 PCE 물가상승률(YoY, %) - 연준이 공식적으로 목표(π*=2%)를 거는 지표
  - 산출갭은 오쿤법칙(Okun's law)으로 근사: 산출갭 ≈ -2 × (실업률 - 자연실업률)
    (자연실업률은 CBO 장기추정치 NROU)
  - 위 대입해서 정리하면: i = 1 + 1.5π - (실업률 - 자연실업률)

비교 대상:
  - 미국채 10년물(DGS10, 일별) - 사용자가 원래 비교하고 싶어했던 장기금리
  - 연준 실효 기준금리(FEDFUNDS, 월별) - 테일러 준칙이 원래 겨냥하는 표준 비교 대상
  - 테일러 준칙 적정금리(월별, 위 공식으로 계산)

한계: 이건 실시간 당시 발표됐던 데이터(real-time vintage)가 아니라 지금 시점에
FRED가 제공하는 최신 확정치로 과거를 다시 계산한 것 - 실제 그 시점 연준이
봤던 숫자와는 (특히 GDP/실업률 수정치 때문에) 다를 수 있음. 어디까지나 사후적
"이 공식대로면 지금 금리가 얼마여야 하나"를 보는 참고용 지표.
"""
import json
import os
from datetime import datetime

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
DOWNLOADS_DIR = os.path.join(DOCS_DIR, "downloads")
IN_PATH = os.path.join(DATA_DIR, "fred_raw.csv")
HTML_PATH = os.path.join(DOCS_DIR, "taylor_rule.html")
XLSX_PATH = os.path.join(DOWNLOADS_DIR, "taylor_rule.xlsx")

DEFAULT_RANGE_DAYS = 365 * 5

R_STAR = 2.0       # 실질중립금리 가정치(%) - 테일러 1993 원안 상수
PI_TARGET = 2.0     # 인플레이션 목표(%)
OKUN_COEF = 2.0     # 오쿤법칙 계수(산출갭 ≈ -OKUN_COEF × 실업률갭)


def build_excel(df):
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    out = df.copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out = out.rename(columns={
        "date": "날짜", "dgs10": "미국채10년물(%)", "fed_funds": "연준기준금리(%)",
        "taylor_rate": "테일러준칙적정금리(%)", "core_pce_yoy": "근원PCE YoY(%)",
        "unemployment_gap": "실업률갭(실업률-자연실업률,%p)",
    })
    with pd.ExcelWriter(XLSX_PATH, engine="openpyxl") as writer:
        out.to_excel(writer, sheet_name="테일러준칙", index=False)
        ws = writer.sheets["테일러준칙"]
        for i, col in enumerate(out.columns, start=1):
            width = max(14, min(26, out[col].astype(str).str.len().max() + 2))
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width


def main():
    if not os.path.exists(IN_PATH):
        print("fred_raw.csv가 없습니다. fetch_fred.py를 먼저 실행하세요.")
        return
    raw = pd.read_csv(IN_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)

    # --- 1) 월별 근원 PCE YoY 계산 (지수레벨 -> 전년동월비) ---
    pce = raw[["date", "us_core_pce_index"]].dropna().sort_values("date").reset_index(drop=True)
    pce["core_pce_yoy"] = pce["us_core_pce_index"].pct_change(12) * 100
    pce = pce.dropna(subset=["core_pce_yoy"])[["date", "core_pce_yoy"]]

    # --- 2) 실업률갭 = 실업률(월별) - 자연실업률(분기별, 월별로 최근값 그대로 이어씀) ---
    unrate = raw[["date", "us_unemployment_rate"]].dropna().sort_values("date").reset_index(drop=True)
    nrou = raw[["date", "us_natural_unemployment_rate"]].dropna().sort_values("date").reset_index(drop=True)
    gap = pd.merge_asof(unrate, nrou, on="date", direction="backward")
    gap["unemployment_gap"] = gap["us_unemployment_rate"] - gap["us_natural_unemployment_rate"]
    gap = gap.dropna(subset=["unemployment_gap"])[["date", "unemployment_gap"]]

    # --- 3) 테일러 준칙 적정금리 계산 (월별, 근원PCE 발표월 기준으로 정렬) ---
    taylor = pd.merge_asof(pce, gap, on="date", direction="backward").dropna()
    r_star_minus_half_target = R_STAR - 0.5 * PI_TARGET
    taylor["taylor_rate"] = (
        r_star_minus_half_target + 1.5 * taylor["core_pce_yoy"] - taylor["unemployment_gap"]
    )

    # --- 4) 연준 기준금리(월별) + 미국채10년물(일별) + 테일러 적정금리(월별) 병합 ---
    fed_funds = raw[["date", "us_fed_funds_rate"]].dropna().rename(columns={"us_fed_funds_rate": "fed_funds"})
    dgs10 = raw[["date", "us_treasury_10y"]].dropna().rename(columns={"us_treasury_10y": "dgs10"})

    all_dates = pd.concat([fed_funds["date"], dgs10["date"], taylor["date"]], ignore_index=True).dropna()
    full_range = pd.date_range(all_dates.min(), all_dates.max(), freq="D")
    merged = pd.DataFrame({"date": full_range})

    raw_latest = {}
    for part, cols in [
        (fed_funds, ["fed_funds"]),
        (dgs10, ["dgs10"]),
        (taylor[["date", "taylor_rate", "core_pce_yoy", "unemployment_gap"]],
         ["taylor_rate", "core_pce_yoy", "unemployment_gap"]),
    ]:
        for col in cols:
            sub = part.dropna(subset=[col])
            if not sub.empty:
                raw_latest[col] = sub["date"].max()
        p = part.drop_duplicates(subset="date").sort_values("date")
        p = p.set_index("date").reindex(full_range).ffill().reset_index().rename(columns={"index": "date"})
        merged = merged.merge(p, on="date", how="left")

    merged["gap_10y_vs_taylor"] = merged["dgs10"] - merged["taylor_rate"]
    merged["gap_fedfunds_vs_taylor"] = merged["fed_funds"] - merged["taylor_rate"]

    build_excel(merged.dropna(subset=["dgs10", "fed_funds", "taylor_rate"], how="all"))

    dates = merged["date"].dt.strftime("%Y-%m-%d").tolist()
    dgs10_list = [None if pd.isna(v) else round(v, 3) for v in merged["dgs10"]]
    fedfunds_list = [None if pd.isna(v) else round(v, 3) for v in merged["fed_funds"]]
    taylor_list = [None if pd.isna(v) else round(v, 3) for v in merged["taylor_rate"]]

    latest_dgs10 = merged["dgs10"].dropna().iloc[-1]
    latest_fedfunds = merged["fed_funds"].dropna().iloc[-1]
    latest_taylor = merged["taylor_rate"].dropna().iloc[-1]
    latest_pce = merged["core_pce_yoy"].dropna().iloc[-1]
    latest_gap_unrate = merged["unemployment_gap"].dropna().iloc[-1]

    html = TEMPLATE.format(
        dates_json=json.dumps(dates),
        dgs10_json=json.dumps(dgs10_list),
        fedfunds_json=json.dumps(fedfunds_list),
        taylor_json=json.dumps(taylor_list),
        latest_dgs10=f"{latest_dgs10:.2f}%",
        latest_dgs10_date=raw_latest["dgs10"].strftime("%Y-%m-%d"),
        latest_fedfunds=f"{latest_fedfunds:.2f}%",
        latest_fedfunds_date=raw_latest["fed_funds"].strftime("%Y-%m-%d"),
        latest_taylor=f"{latest_taylor:.2f}%",
        latest_taylor_date=raw_latest["taylor_rate"].strftime("%Y-%m-%d"),
        gap_10y=f"{latest_dgs10 - latest_taylor:+.2f}%p",
        gap_fedfunds=f"{latest_fedfunds - latest_taylor:+.2f}%p",
        latest_pce=f"{latest_pce:.2f}%",
        latest_unrate_gap=f"{latest_gap_unrate:+.2f}%p",
        n_days=len(merged),
        default_range_days=DEFAULT_RANGE_DAYS,
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"저장 완료: {HTML_PATH}, {XLSX_PATH}")
    print(f"최신 테일러 적정금리: {latest_taylor:.2f}% ({raw_latest['taylor_rate'].date()} 기준 데이터) "
          f"vs 기준금리 {latest_fedfunds:.2f}% vs 10년물 {latest_dgs10:.2f}%")


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>테일러 준칙 적정금리 트래커</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  a.back {{ color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }}
  h1 {{ font-size:20px; margin:8px 0 4px 0; }}
  .updated {{ color:#9aa0a6; font-size:13px; margin-bottom:20px; }}
  .badges {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap:12px; margin-bottom:20px; max-width:1000px; }}
  .badge {{ background:#1a1d24; border-radius:10px; padding:14px 16px; }}
  .badge .label {{ color:#9aa0a6; font-size:12px; }}
  .badge .value {{ font-size:20px; font-weight:bold; margin-top:4px; }}
  .badge .subdate {{ color:#6b7280; font-size:11px; margin-top:2px; }}
  .badge.dgs10 .value {{ color:#4dabf7; }}
  .badge.fedfunds .value {{ color:#ffa94d; }}
  .badge.taylor .value {{ color:#63e6be; }}
  .badge.gap .value {{ color:#ff8787; }}
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
  .note code {{ color:#63e6be; }}
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>테일러 준칙(Taylor Rule) 적정금리 트래커</h1>
  <div class="updated">최종 갱신: {updated_at} &middot; 데이터 {n_days}일치</div>

  <div class="badges">
    <div class="badge dgs10"><div class="label">미국채 10년물</div><div class="value">{latest_dgs10}</div><div class="subdate">{latest_dgs10_date} 기준</div></div>
    <div class="badge fedfunds"><div class="label">연준 실효 기준금리</div><div class="value">{latest_fedfunds}</div><div class="subdate">{latest_fedfunds_date} 기준</div></div>
    <div class="badge taylor"><div class="label">테일러준칙 적정금리</div><div class="value">{latest_taylor}</div><div class="subdate">{latest_taylor_date} 기준</div></div>
    <div class="badge gap"><div class="label">10년물 - 적정금리</div><div class="value">{gap_10y}</div></div>
    <div class="badge gap"><div class="label">기준금리 - 적정금리</div><div class="value">{gap_fedfunds}</div></div>
  </div>

  <a class="dl" href="downloads/taylor_rule.xlsx">&#128190; 엑셀 다운로드 (전체 기간)</a>

  <div class="range-bar" id="rangeBar"></div>
  <div class="custom-range-bar">
    <label>시작 <input type="date" id="rangeStart"></label>
    <label>종료 <input type="date" id="rangeEnd"></label>
    <button id="rangeApplyBtn">적용</button>
    <span class="custom-range-hint" id="rangeCustomHint"></span>
  </div>

  <div class="chart-wrap"><canvas id="taylorChart"></canvas></div>

  <div class="note">
    <b>공식</b> — <code>적정금리 = r* + &pi; + 0.5&times;(&pi; - &pi;*) + 0.5&times;산출갭</code> (John Taylor 1993 원안).
    r*(실질중립금리)=2%, &pi;*(물가목표)=2%로 고정하고, &pi;(물가)는 연준이 실제 목표로 삼는 <b>근원 PCE YoY</b>({latest_pce}, 최신치)를,
    산출갭은 오쿤법칙(산출갭&asymp;-2&times;실업률갭)으로 근사해서 <b>실업률갭</b>({latest_unrate_gap}, 실업률-CBO 장기자연실업률)을 대입합니다.
    정리하면 <code>적정금리 = 1 + 1.5&times;근원PCE YoY - 실업률갭</code>.<br><br>
    <b>비교 대상</b> — 테일러 준칙은 원래 연준 <b>기준금리</b>와 비교하는 게 표준이라 같이 넣었고, 장기금리인 <b>미국채 10년물</b>도 함께 겹쳐서
    "지금 장기금리가 정책 펀더멘털이 시사하는 수준보다 높은지/낮은지"를 같이 볼 수 있게 했습니다.<br><br>
    <b>소스</b> — 전부 FRED(세인트루이스 연은) 공식 API: 근원PCE(PCEPILFE, 월별)/실업률(UNRATE, 월별)/자연실업률(NROU, CBO 장기추정, 분기별)/
    기준금리(FEDFUNDS, 월별)/10년물(DGS10, 일별). 월별·분기별 값은 다음 발표 전까지 직선으로 이어서(계단식) 표시합니다.<br><br>
    <b>한계</b> — 지금 시점 FRED 확정치로 과거를 소급 계산한 것이라 그 당시 연준이 실제로 봤던 실시간(real-time vintage) 수치와는
    (특히 실업률 개정치) 다를 수 있습니다. r*를 고정 2%로 쓰는 단순 버전이라, NY연은 모델기반 r*(Laubach-Williams, 최근 훨씬 낮음)를 쓰면
    결과가 달라집니다. 참고용 지표로만 활용하세요.
  </div>

<script>
const fullDates = {dates_json};
const fullData = {{
  dgs10: {dgs10_json},
  fedfunds: {fedfunds_json},
  taylor: {taylor_json},
}};

const chart = new Chart(document.getElementById('taylorChart').getContext('2d'), {{
  type: 'line',
  data: {{
    labels: [],
    datasets: [
      {{ label: '미국채 10년물(%)', data: [], borderColor: '#4dabf7', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 1.8, _key: 'dgs10' }},
      {{ label: '연준 기준금리(%)', data: [], borderColor: '#ffa94d', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 1.8, _key: 'fedfunds' }},
      {{ label: '테일러준칙 적정금리(%)', data: [], borderColor: '#63e6be', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 2.2, _key: 'taylor' }},
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ color: '#e6e6e6' }} }} }},
    scales: {{
      x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 12 }}, grid: {{ color: '#23262e' }} }},
      y: {{ title: {{ display: true, text: '%', color: '#9aa0a6' }}, ticks: {{ color: '#9aa0a6' }}, grid: {{ color: '#23262e' }} }},
    }}
  }}
}});

const rangeBar = document.getElementById('rangeBar');
const rangeStartInput = document.getElementById('rangeStart');
const rangeEndInput = document.getElementById('rangeEnd');
const rangeCustomHint = document.getElementById('rangeCustomHint');
let currentRange = {{ mode: 'preset', days: {default_range_days} }};

const RANGE_OPTIONS = [
  {{ label: '1년', days: 365 }},
  {{ label: '3년', days: 1095 }},
  {{ label: '5년', days: 1825 }},
  {{ label: '10년', days: 3650 }},
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
