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

CHART_WINDOW_DAYS = 365 * 3  # 차트는 최근 3년만 표시(전체는 엑셀에서 확인)


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

    cutoff = df["date"].max() - pd.Timedelta(days=CHART_WINDOW_DAYS)
    recent = df[df["date"] >= cutoff]

    dates = recent["date"].dt.strftime("%Y-%m-%d").tolist()
    wti = [round(v, 2) for v in recent["wti_usd_per_bbl"]]
    spread = [round(v, 2) for v in recent["crack_spread_321_usd_per_bbl"]]
    gasoline = [round(v, 2) for v in recent["gasoline_usd_per_bbl"]]
    diesel = [round(v, 2) for v in recent["diesel_usd_per_bbl"]]

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
const dates = {dates_json};
const wti = {wti_json};
const spread = {spread_json};
const gasoline = {gasoline_json};
const diesel = {diesel_json};

new Chart(document.getElementById('spreadChart').getContext('2d'), {{
  type: 'line',
  data: {{
    labels: dates,
    datasets: [
      {{ label: 'WTI 원유($/bbl, 좌축)', data: wti, borderColor: '#4dabf7', backgroundColor: 'transparent', yAxisID: 'yWti', tension: 0.1, pointRadius: 0, borderWidth: 1.5 }},
      {{ label: '3:2:1 크랙 스프레드($/bbl, 우축)', data: spread, borderColor: '#ff8787', backgroundColor: 'transparent', yAxisID: 'ySpread', tension: 0.1, pointRadius: 0, borderWidth: 2.5 }},
      {{ label: '휘발유($/bbl, 우축)', data: gasoline, borderColor: '#63e6be', backgroundColor: 'transparent', yAxisID: 'ySpread', tension: 0.1, pointRadius: 0, borderWidth: 1, borderDash: [3,3], hidden: true }},
      {{ label: '경유($/bbl, 우축)', data: diesel, borderColor: '#ffa94d', backgroundColor: 'transparent', yAxisID: 'ySpread', tension: 0.1, pointRadius: 0, borderWidth: 1, borderDash: [3,3], hidden: true }},
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
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
