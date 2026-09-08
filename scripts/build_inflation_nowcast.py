"""
data/inflation_nowcast_raw.csv(클리블랜드 연은 나우캐스트 일별 스냅샷) +
data/fred_raw.csv(FRED 실적치)로 docs/inflation_nowcast.html
(CPI/Core CPI/PCE/Core PCE 나우캐스트 vs 실적치 트래커)를 만든다.

나우캐스트(clevelandfed.org)는 그 사이트에 과거 나우캐스트 아카이브가 없어서
(직접 확인함, 2026-09-08 - 사이트가 제공하는 CSV도 지금 이 순간의 당월/전월
스냅샷뿐, 과거 히스토리는 없음) 이 트래커가 매일 쌓아가는 것부터가 유일한
히스토리 소스다. 대신 "실적치"(BLS/BEA가 실제 발표한 CPI/Core CPI/PCE/Core PCE)는
FRED(CPIAUCSL/CPILFESL/PCEPI/PCEPILFE)에 2000년부터 있으니 그건 바로 긴 히스토리를
같이 보여준다 - "나우캐스트(최근 며칠~몇 주치)"와 "실적치(2000년~)"를 같은 차트에
겹쳐서, 나우캐스트가 발표 전 실적치를 얼마나 잘 예측하는지/최근 흐름이 장기
추세 대비 어떤지 같이 볼 수 있게 한다.
"""
import json
import os
from datetime import datetime

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
DOWNLOADS_DIR = os.path.join(DOCS_DIR, "downloads")
NOWCAST_PATH = os.path.join(DATA_DIR, "inflation_nowcast_raw.csv")
FRED_PATH = os.path.join(DATA_DIR, "fred_raw.csv")
HTML_PATH = os.path.join(DOCS_DIR, "inflation_nowcast.html")
XLSX_PATH = os.path.join(DOWNLOADS_DIR, "inflation_nowcast.xlsx")

METRICS = [
    ("cpi", "CPI", "us_cpi_index", "#4dabf7"),
    ("core_cpi", "Core CPI", "us_core_cpi_index", "#ffa94d"),
    ("pce", "PCE", "us_pce_index", "#ff8787"),
    ("core_pce", "Core PCE", "us_core_pce_index", "#63e6be"),
]


def build_excel(nowcast_df, actual_df):
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    nc = nowcast_df.rename(columns={
        "fetch_date": "조회일", "target_month": "대상월", "source_updated": "원본갱신일",
        "cpi_mom": "CPI MoM나우캐스트(%)", "cpi_yoy": "CPI YoY나우캐스트(%)",
        "core_cpi_mom": "CoreCPI MoM나우캐스트(%)", "core_cpi_yoy": "CoreCPI YoY나우캐스트(%)",
        "pce_mom": "PCE MoM나우캐스트(%)", "pce_yoy": "PCE YoY나우캐스트(%)",
        "core_pce_mom": "CorePCE MoM나우캐스트(%)", "core_pce_yoy": "CorePCE YoY나우캐스트(%)",
    })
    ac = actual_df.rename(columns={
        "date": "월", "cpi_yoy": "CPI YoY실적(%)", "cpi_mom": "CPI MoM실적(%)",
        "core_cpi_yoy": "CoreCPI YoY실적(%)", "core_cpi_mom": "CoreCPI MoM실적(%)",
        "pce_yoy": "PCE YoY실적(%)", "pce_mom": "PCE MoM실적(%)",
        "core_pce_yoy": "CorePCE YoY실적(%)", "core_pce_mom": "CorePCE MoM실적(%)",
    })
    with pd.ExcelWriter(XLSX_PATH, engine="openpyxl") as writer:
        nc.to_excel(writer, sheet_name="나우캐스트(일별)", index=False)
        ac.to_excel(writer, sheet_name="실적치(FRED,2000~)", index=False)
        for sheet, out in [("나우캐스트(일별)", nc), ("실적치(FRED,2000~)", ac)]:
            ws = writer.sheets[sheet]
            for i, col in enumerate(out.columns, start=1):
                width = max(12, min(20, out[col].astype(str).str.len().max() + 2))
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width


def main():
    if not os.path.exists(NOWCAST_PATH):
        print("inflation_nowcast_raw.csv가 없습니다. fetch_inflation_nowcast.py를 먼저 실행하세요.")
        return
    if not os.path.exists(FRED_PATH):
        print("fred_raw.csv가 없습니다. fetch_fred.py를 먼저 실행하세요.")
        return

    nowcast = pd.read_csv(NOWCAST_PATH, parse_dates=["fetch_date"]).sort_values(["fetch_date", "target_month"])
    fred = pd.read_csv(FRED_PATH, parse_dates=["date"])

    # --- 실적치(FRED 지수레벨 -> YoY/MoM) ---
    actual = None
    for key, _label, col, _color in METRICS:
        s = fred[["date", col]].dropna().sort_values("date").reset_index(drop=True)
        s[f"{key}_yoy"] = s[col].pct_change(12) * 100
        s[f"{key}_mom"] = s[col].pct_change(1) * 100
        s = s.drop(columns=[col])
        actual = s if actual is None else actual.merge(s, on="date", how="outer")
    actual = actual.sort_values("date").reset_index(drop=True)
    actual_hist = actual.dropna(subset=[f"{k}_yoy" for k, *_ in METRICS], how="all")

    build_excel(nowcast.assign(fetch_date=nowcast["fetch_date"].dt.strftime("%Y-%m-%d")),
                actual_hist.assign(date=actual_hist["date"].dt.strftime("%Y-%m-%d")))

    latest_fetch = nowcast["fetch_date"].max()
    latest_rows = nowcast[nowcast["fetch_date"] == latest_fetch].copy()
    latest_rows["_month_dt"] = pd.to_datetime(latest_rows["target_month"], format="%B %Y")
    latest_rows = latest_rows.sort_values("_month_dt")
    newest_month_row = latest_rows.iloc[-1]
    target_month = newest_month_row["target_month"]

    latest_actual_date = actual_hist["date"].max()
    latest_actual_row = actual_hist[actual_hist["date"] == latest_actual_date].iloc[0]

    badges = []
    for key, label, _col, _color in METRICS:
        yoy_nc = newest_month_row.get(f"{key}_yoy")
        mom_nc = newest_month_row.get(f"{key}_mom")
        yoy_ac = latest_actual_row.get(f"{key}_yoy")
        mom_ac = latest_actual_row.get(f"{key}_mom")
        badges.append({
            "label": label,
            "yoy_nc": f"{yoy_nc:.2f}%" if pd.notna(yoy_nc) else "N/A",
            "mom_nc": f"{mom_nc:.2f}%" if pd.notna(mom_nc) else "N/A",
            "yoy_ac": f"{yoy_ac:.2f}%" if pd.notna(yoy_ac) else "N/A",
            "mom_ac": f"{mom_ac:.2f}%" if pd.notna(mom_ac) else "N/A",
        })

    # 실적치 차트용(2015년~, 너무 길면 무거워져서 최근 10년으로 제한 - 필요하면 엑셀에서 2000년~ 전체 확인)
    actual_chart = actual_hist[actual_hist["date"] >= "2015-01-01"]
    actual_dates = actual_chart["date"].dt.strftime("%Y-%m-%d").tolist()
    actual_series = {
        key: [None if pd.isna(v) else round(v, 3) for v in actual_chart[f"{key}_yoy"]]
        for key, *_ in METRICS
    }

    # 나우캐스트 히스토리(우리가 쌓아온 것, target_month별로 최신 대상월 것만 이어붙임 -
    # 대상월이 바뀌면 그 월의 첫 나우캐스트로 자연히 이어짐)
    nc_hist = nowcast.sort_values(["fetch_date"])
    nc_dates = nc_hist["fetch_date"].dt.strftime("%Y-%m-%d").tolist()
    nc_series = {
        key: [None if pd.isna(v) else round(v, 3) for v in nc_hist[f"{key}_yoy"]]
        for key, *_ in METRICS
    }

    html = TEMPLATE.format(
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        source_updated=newest_month_row["source_updated"],
        target_month=target_month,
        latest_actual_date=latest_actual_date.strftime("%Y-%m"),
        n_nowcast_rows=len(nowcast),
        badges_html="".join(
            f"""<div class="badge"><div class="label">{b['label']}</div>
              <div class="row"><span class="tag nc">나우캐스트</span><span class="value">{b['yoy_nc']}</span><span class="sub">YoY (MoM {b['mom_nc']})</span></div>
              <div class="row"><span class="tag ac">실적치</span><span class="value ac">{b['yoy_ac']}</span><span class="sub">YoY (MoM {b['mom_ac']})</span></div>
              </div>"""
            for b in badges
        ),
        actual_dates_json=json.dumps(actual_dates),
        actual_series_json=json.dumps(actual_series),
        nc_dates_json=json.dumps(nc_dates),
        nc_series_json=json.dumps(nc_series),
    )
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"저장 완료: {HTML_PATH}, {XLSX_PATH}")


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>인플레이션 나우캐스트 트래커</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  a.back {{ color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }}
  h1 {{ font-size:20px; margin:8px 0 4px 0; }}
  .updated {{ color:#9aa0a6; font-size:13px; margin-bottom:20px; }}
  .badges {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap:12px; margin-bottom:20px; max-width:1000px; }}
  .badge {{ background:#1a1d24; border-radius:10px; padding:14px 16px; }}
  .badge .label {{ color:#e6e6e6; font-size:13px; font-weight:bold; margin-bottom:8px; }}
  .badge .row {{ display:flex; align-items:baseline; gap:6px; margin-top:4px; }}
  .badge .tag {{ font-size:10px; padding:2px 6px; border-radius:4px; }}
  .badge .tag.nc {{ background:#2a3a4a; color:#63e6be; }}
  .badge .tag.ac {{ background:#3a2a1a; color:#ffa94d; }}
  .badge .value {{ font-size:16px; font-weight:bold; color:#63e6be; }}
  .badge .value.ac {{ color:#ffa94d; }}
  .badge .sub {{ font-size:11px; color:#9aa0a6; }}
  .section-title {{ font-size:14px; color:#4dabf7; margin:28px 0 8px 0; }}
  .dl {{ display:inline-block; background:#1a1d24; border:1px solid #23262e; border-radius:8px; padding:10px 16px;
    color:#4dabf7; text-decoration:none; font-size:13px; margin-bottom:24px; }}
  .dl:hover {{ border-color:#4dabf7; }}
  .chart-wrap {{ height:420px; position:relative; max-width:1050px; margin-bottom:16px; }}
  .note {{ color:#9aa0a6; font-size:12px; line-height:1.7; max-width:900px; background:#1a1d24; border-radius:10px; padding:16px 18px; margin-top:8px; }}
  .note b {{ color:#ffa94d; }}
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>인플레이션 나우캐스트 트래커</h1>
  <div class="updated">최종 갱신: {updated_at} &middot; 나우캐스트 원본기준 {source_updated}(대상월 {target_month}) &middot; 실적치 최신 {latest_actual_date} &middot; 나우캐스트 누적 {n_nowcast_rows}행</div>

  <div class="badges">{badges_html}</div>

  <a class="dl" href="downloads/inflation_nowcast.xlsx">&#128190; 엑셀 다운로드 (나우캐스트 일별 + 실적치 2000년~)</a>

  <div class="section-title">실적치 YoY 추이 (FRED, 2015년~ - 엑셀에서 2000년~ 전체 확인 가능)</div>
  <div class="chart-wrap"><canvas id="actualChart"></canvas></div>

  <div class="section-title">나우캐스트 YoY 추이 (매일 누적 - {target_month} 등 대상월이 바뀌면 이어서 표시)</div>
  <div class="chart-wrap"><canvas id="nowcastChart"></canvas></div>

  <div class="note">
    <b>나우캐스트</b> — 클리블랜드 연은 Inflation Nowcasting 모델이 매 영업일(미국 동부시간 오전 10시경) 갱신하는 예측치.
    BLS(CPI)·BEA(PCE) 공식 실적치가 발표되기 전까지 유가 등 고빈도 데이터로 미리 추정합니다.
    <a href="https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting" style="color:#4dabf7;" target="_blank">공식 페이지</a>에
    과거 나우캐스트 아카이브가 없어서(확인함, 2026-09-08) 이 트래커가 매일 스크래핑해서 쌓는 히스토리가 유일한 소스입니다 - 시작일 이전 과거값은 존재하지 않습니다.<br><br>
    <b>실적치</b> — BLS/BEA가 실제 발표한 확정치. FRED 공식 API로 CPI(CPIAUCSL)/근원CPI(CPILFESL)/PCE(PCEPI)/근원PCE(PCEPILFE) 지수레벨을 받아
    전년동월비(YoY)·전월비(MoM)를 직접 계산 - 2000년부터 있습니다(엑셀 다운로드에서 전체 확인).<br><br>
    <b>한계</b> — 나우캐스트는 모델 추정치로 확정 실적과 다를 수 있고, 실적치는 발표 후 개정(revision)될 수 있어 지금 보이는 값이 그 당시 첫 발표치와 다를 수 있습니다.
  </div>

<script>
const ad = {actual_dates_json};
const as = {actual_series_json};
new Chart(document.getElementById('actualChart').getContext('2d'), {{
  type: 'line',
  data: {{
    labels: ad,
    datasets: [
      {{ label: 'CPI YoY 실적(%)', data: as.cpi, borderColor: '#4dabf7', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 1.6 }},
      {{ label: 'Core CPI YoY 실적(%)', data: as.core_cpi, borderColor: '#ffa94d', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 1.6 }},
      {{ label: 'PCE YoY 실적(%)', data: as.pce, borderColor: '#ff8787', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 1.6 }},
      {{ label: 'Core PCE YoY 실적(%)', data: as.core_pce, borderColor: '#63e6be', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 2.0 }},
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ color: '#e6e6e6' }} }} }},
    scales: {{
      x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 14 }}, grid: {{ color: '#23262e' }} }},
      y: {{ title: {{ display: true, text: '%', color: '#9aa0a6' }}, ticks: {{ color: '#9aa0a6' }}, grid: {{ color: '#23262e' }} }},
    }}
  }}
}});

const nd = {nc_dates_json};
const ns = {nc_series_json};
new Chart(document.getElementById('nowcastChart').getContext('2d'), {{
  type: 'line',
  data: {{
    labels: nd,
    datasets: [
      {{ label: 'CPI YoY 나우캐스트(%)', data: ns.cpi, borderColor: '#4dabf7', backgroundColor: 'transparent', tension: 0.1, pointRadius: 2, borderWidth: 1.6, borderDash: [4,3] }},
      {{ label: 'Core CPI YoY 나우캐스트(%)', data: ns.core_cpi, borderColor: '#ffa94d', backgroundColor: 'transparent', tension: 0.1, pointRadius: 2, borderWidth: 1.6, borderDash: [4,3] }},
      {{ label: 'PCE YoY 나우캐스트(%)', data: ns.pce, borderColor: '#ff8787', backgroundColor: 'transparent', tension: 0.1, pointRadius: 2, borderWidth: 1.6, borderDash: [4,3] }},
      {{ label: 'Core PCE YoY 나우캐스트(%)', data: ns.core_pce, borderColor: '#63e6be', backgroundColor: 'transparent', tension: 0.1, pointRadius: 2, borderWidth: 2.0, borderDash: [4,3] }},
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ color: '#e6e6e6' }} }} }},
    scales: {{
      x: {{ ticks: {{ color: '#9aa0a6' }}, grid: {{ color: '#23262e' }} }},
      y: {{ title: {{ display: true, text: '%', color: '#9aa0a6' }}, ticks: {{ color: '#9aa0a6' }}, grid: {{ color: '#23262e' }} }},
    }}
  }}
}});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
