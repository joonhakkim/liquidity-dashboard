"""
data/inflation_nowcast_raw.csv(클리블랜드 연은 인플레이션 나우캐스트 일별 스냅샷)로
docs/inflation_nowcast.html(CPI/Core CPI/PCE/Core PCE 나우캐스트 트래커)를 만든다.

나우캐스트는 "이번 달/저번 달" 예측치가 새 데이터가 들어올 때마다 매일 조금씩
바뀌는 구조라, 이 페이지는 (1) 가장 최근 예측치를 배지로 보여주고 (2) 특정
대상월(target_month)의 예측치가 그 달 안에서 날짜가 갈수록 어떻게 수렴/변해왔는지
꺾은선으로 보여준다(가장 최근 대상월 기준).
"""
import json
import os
from datetime import datetime

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
DOWNLOADS_DIR = os.path.join(DOCS_DIR, "downloads")
IN_PATH = os.path.join(DATA_DIR, "inflation_nowcast_raw.csv")
HTML_PATH = os.path.join(DOCS_DIR, "inflation_nowcast.html")
XLSX_PATH = os.path.join(DOWNLOADS_DIR, "inflation_nowcast.xlsx")

METRICS = [
    ("cpi", "CPI"), ("core_cpi", "Core CPI"), ("pce", "PCE"), ("core_pce", "Core PCE"),
]


def build_excel(df):
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    out = df.rename(columns={
        "fetch_date": "조회일", "target_month": "대상월", "source_updated": "원본갱신일",
        "cpi_mom": "CPI MoM(%)", "cpi_yoy": "CPI YoY(%)",
        "core_cpi_mom": "CoreCPI MoM(%)", "core_cpi_yoy": "CoreCPI YoY(%)",
        "pce_mom": "PCE MoM(%)", "pce_yoy": "PCE YoY(%)",
        "core_pce_mom": "CorePCE MoM(%)", "core_pce_yoy": "CorePCE YoY(%)",
    })
    with pd.ExcelWriter(XLSX_PATH, engine="openpyxl") as writer:
        out.to_excel(writer, sheet_name="인플레이션나우캐스트", index=False)
        ws = writer.sheets["인플레이션나우캐스트"]
        for i, col in enumerate(out.columns, start=1):
            width = max(12, min(20, out[col].astype(str).str.len().max() + 2))
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width


def main():
    if not os.path.exists(IN_PATH):
        print("inflation_nowcast_raw.csv가 없습니다. fetch_inflation_nowcast.py를 먼저 실행하세요.")
        return
    df = pd.read_csv(IN_PATH, parse_dates=["fetch_date"]).sort_values(["fetch_date", "target_month"])

    build_excel(df.assign(fetch_date=df["fetch_date"].dt.strftime("%Y-%m-%d")))

    latest_fetch = df["fetch_date"].max()
    latest_rows = df[df["fetch_date"] == latest_fetch].copy()
    # target_month 문자열("September 2026")을 정렬 가능한 날짜로 변환해 최신 대상월 판정
    latest_rows["_month_dt"] = pd.to_datetime(latest_rows["target_month"], format="%B %Y")
    latest_rows = latest_rows.sort_values("_month_dt")
    newest_month_row = latest_rows.iloc[-1]
    prior_month_row = latest_rows.iloc[-2] if len(latest_rows) >= 2 else None

    badges = []
    for key, label in METRICS:
        yoy = newest_month_row.get(f"{key}_yoy")
        mom = newest_month_row.get(f"{key}_mom")
        badges.append({
            "label": label,
            "yoy": f"{yoy:.2f}%" if pd.notna(yoy) else "N/A",
            "mom": f"{mom:.2f}%" if pd.notna(mom) else "N/A",
        })

    # 최신 대상월의 예측치가 날짜별로 어떻게 바뀌어왔는지(수렴 과정) 차트용 시계열
    target_month = newest_month_row["target_month"]
    hist = df[df["target_month"] == target_month].sort_values("fetch_date")
    hist_dates = hist["fetch_date"].dt.strftime("%Y-%m-%d").tolist()
    hist_series = {
        key: [None if pd.isna(v) else round(v, 3) for v in hist[f"{key}_yoy"]]
        for key, _ in METRICS
    }

    html = TEMPLATE.format(
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        source_updated=newest_month_row["source_updated"],
        target_month=target_month,
        prior_month=prior_month_row["target_month"] if prior_month_row is not None else "-",
        n_rows=len(df),
        badges_html="".join(
            f"""<div class="badge"><div class="label">{b['label']}</div>
              <div class="value">{b['yoy']}<span class="sub"> YoY</span></div>
              <div class="submom">MoM {b['mom']}</div></div>"""
            for b in badges
        ),
        hist_dates_json=json.dumps(hist_dates),
        hist_series_json=json.dumps(hist_series),
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
  .badges {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap:12px; margin-bottom:20px; max-width:900px; }}
  .badge {{ background:#1a1d24; border-radius:10px; padding:14px 16px; }}
  .badge .label {{ color:#9aa0a6; font-size:12px; }}
  .badge .value {{ font-size:22px; font-weight:bold; margin-top:4px; color:#63e6be; }}
  .badge .sub {{ font-size:12px; font-weight:normal; color:#9aa0a6; }}
  .badge .submom {{ color:#6b7280; font-size:11px; margin-top:2px; }}
  .dl {{ display:inline-block; background:#1a1d24; border:1px solid #23262e; border-radius:8px; padding:10px 16px;
    color:#4dabf7; text-decoration:none; font-size:13px; margin-bottom:24px; }}
  .dl:hover {{ border-color:#4dabf7; }}
  .chart-wrap {{ height:400px; position:relative; max-width:1000px; margin-bottom:24px; }}
  .note {{ color:#9aa0a6; font-size:12px; line-height:1.7; max-width:900px; background:#1a1d24; border-radius:10px; padding:16px 18px; margin-top:8px; }}
  .note b {{ color:#ffa94d; }}
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>인플레이션 나우캐스트 트래커</h1>
  <div class="updated">최종 갱신: {updated_at} &middot; 원본 기준(Updated) {source_updated} &middot; 대상월 {target_month} &middot; 누적 {n_rows}행</div>

  <div class="badges">{badges_html}</div>

  <a class="dl" href="downloads/inflation_nowcast.xlsx">&#128190; 엑셀 다운로드 (전체 히스토리)</a>

  <div class="chart-wrap"><canvas id="nowcastChart"></canvas></div>
  <div class="updated" style="margin-top:-12px;">위 차트: {target_month} 대상 YoY 나우캐스트가 매 영업일 갱신되며 어떻게 바뀌어왔는지(전월 {prior_month} 실적 확정 전까지 계속 수렴)</div>

  <div class="note">
    <b>정의</b> — 클리블랜드 연은 Inflation Nowcasting 모델이 매 영업일(미국 동부시간 오전 10시경) 갱신하는 CPI/근원CPI/PCE/근원PCE의
    당월·전월 전년동월비(YoY)·전월비(MoM) 예측치입니다. BLS(CPI)·BEA(PCE) 공식 실적치가 발표되기 전까지 유가 등 고빈도 데이터로
    미리 추정하는 "나우캐스트(nowcast)" 모델입니다.<br><br>
    <b>소스</b> — <a href="https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting" style="color:#4dabf7;" target="_blank">clevelandfed.org 공식 페이지</a>를 매일 스크래핑.
    공식 다운로드 API가 없어 페이지 표를 직접 파싱합니다 - 페이지 구조가 바뀌면 갱신이 멈출 수 있습니다.<br><br>
    <b>한계</b> — 어디까지나 모델 추정치이지 확정 실적이 아닙니다. 대상월의 실제 BLS/BEA 발표가 나오면 그 뒤로는 나우캐스트 대신 실적치를 봐야 합니다.
  </div>

<script>
const hd = {hist_dates_json};
const hs = {hist_series_json};
new Chart(document.getElementById('nowcastChart').getContext('2d'), {{
  type: 'line',
  data: {{
    labels: hd,
    datasets: [
      {{ label: 'CPI YoY(%)', data: hs.cpi, borderColor: '#4dabf7', backgroundColor: 'transparent', tension: 0.1, pointRadius: 2, borderWidth: 1.8 }},
      {{ label: 'Core CPI YoY(%)', data: hs.core_cpi, borderColor: '#ffa94d', backgroundColor: 'transparent', tension: 0.1, pointRadius: 2, borderWidth: 1.8 }},
      {{ label: 'PCE YoY(%)', data: hs.pce, borderColor: '#ff8787', backgroundColor: 'transparent', tension: 0.1, pointRadius: 2, borderWidth: 1.8 }},
      {{ label: 'Core PCE YoY(%)', data: hs.core_pce, borderColor: '#63e6be', backgroundColor: 'transparent', tension: 0.1, pointRadius: 2, borderWidth: 2.2 }},
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
