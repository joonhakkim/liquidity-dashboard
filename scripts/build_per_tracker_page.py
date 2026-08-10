"""
data/kospi_per_tracker.csv(선행/후행 PER 누적치) + data/krx_raw.csv(코스피 지수)를 묶어
docs/per_tracker.html(코스피 선행 PER 트래커)을 만든다.
"""
import json
import os
from datetime import datetime

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
PER_PATH = os.path.join(DATA_DIR, "kospi_per_tracker.csv")
KRX_PATH = os.path.join(DATA_DIR, "krx_raw.csv")
OUT_PATH = os.path.join(DOCS_DIR, "per_tracker.html")


CHART_WINDOW_DAYS = 365  # 코스피 지수는 PER 수집 시작일과 무관하게 최근 1년치를 배경으로 깔아준다


def main():
    per = pd.read_csv(PER_PATH, parse_dates=["date"]).sort_values("date")
    krx = pd.read_csv(KRX_PATH, parse_dates=["date"])[["date", "kospi_close"]].sort_values("date")

    cutoff = krx["date"].max() - pd.Timedelta(days=CHART_WINDOW_DAYS)
    krx_recent = krx[krx["date"] >= cutoff]

    # 코스피 지수(1년치)를 기준 타임라인으로 깔고, PER은 수집된 날짜만 값이 채워지고 나머지는 공백(null)
    merged = krx_recent.merge(per, on="date", how="left").sort_values("date")

    dates = merged["date"].dt.strftime("%Y-%m-%d").tolist()
    per_trailing = [None if pd.isna(v) else round(v, 2) for v in merged["per_trailing"]]
    per_2026e = [None if pd.isna(v) else round(v, 2) for v in merged["per_2026e"]]
    per_2027e = [None if pd.isna(v) else round(v, 2) for v in merged["per_2027e"]]
    kospi = [None if pd.isna(v) else round(v, 2) for v in merged["kospi_close"]]

    latest = per.iloc[-1]  # PER 배지는 실제 PER이 마지막으로 수집된 날 기준
    latest_str = latest["date"].strftime("%Y-%m-%d")

    def fmt(v):
        return "N/A" if pd.isna(v) else f"{v:.2f}배"

    html = TEMPLATE.format(
        dates_json=json.dumps(dates),
        per_trailing_json=json.dumps(per_trailing),
        per_2026e_json=json.dumps(per_2026e),
        per_2027e_json=json.dumps(per_2027e),
        kospi_json=json.dumps(kospi),
        latest_date=latest_str,
        latest_trailing=fmt(latest["per_trailing"]),
        latest_2026e=fmt(latest["per_2026e"]),
        latest_2027e=fmt(latest["per_2027e"]),
        n_days=len(per),
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"저장 완료: {OUT_PATH} (코스피 지수 {len(merged)}일치, PER 수집 {len(per)}일치)")


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>코스피 선행 PER 트래커</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  a.back {{ color:#4dabf7; font-size:13px; text-decoration:none; }}
  h1 {{ font-size:20px; margin:8px 0 4px 0; }}
  .updated {{ color:#9aa0a6; font-size:13px; margin-bottom:20px; }}
  .badges {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap:12px; margin-bottom:20px; max-width:700px; }}
  .badge {{ background:#1a1d24; border-radius:10px; padding:14px 16px; }}
  .badge .label {{ color:#9aa0a6; font-size:12px; }}
  .badge .value {{ font-size:20px; font-weight:bold; margin-top:4px; }}
  .badge.trailing .value {{ color:#adb5bd; }}
  .badge.y2026 .value {{ color:#ff8787; }}
  .badge.y2027 .value {{ color:#4dabf7; }}
  .chart-wrap {{ height:440px; position:relative; max-width:1100px; margin-bottom:24px; }}
  .note {{ color:#9aa0a6; font-size:12px; line-height:1.7; max-width:900px; background:#1a1d24; border-radius:10px; padding:16px 18px; margin-bottom:16px; }}
  .note b {{ color:#ffa94d; }}
  .note h3 {{ font-size:13px; color:#c7cbd1; margin:0 0 8px 0; }}
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>코스피 선행 PER 트래커</h1>
  <div class="updated">최종 갱신: {updated_at} &middot; 최신 기준일 {latest_date} &middot; PER 수집 {n_days}일치(코스피 지수는 참고용으로 최근 1년 표시)</div>

  <div class="badges">
    <div class="badge trailing"><div class="label">후행(TTM) PER</div><div class="value">{latest_trailing}</div></div>
    <div class="badge y2026"><div class="label">당해선행(2026E) PER</div><div class="value">{latest_2026e}</div></div>
    <div class="badge y2027"><div class="label">차년선행(2027E) PER</div><div class="value">{latest_2027e}</div></div>
  </div>

  <div class="chart-wrap"><canvas id="perChart"></canvas></div>

  <div class="note">
    <h3>산출 방법론</h3>
    <b>수집 주기·소스</b> — 실행 시점 기준 코스피 시가총액 상위 50종목(우선주 제외)을 KRX Open API(stk_bydd_trd)로,
    각 종목의 당해년도(2026E)&middot;차년도(2027E) 컨센서스 EPS와 최근 확정 실적 EPS를 네이버 개별종목 페이지가
    쓰는 컨센서스 API(WiseReport)에서 가져옵니다.<br><br>
    <b>선행 PER 계산</b> — 종목별 선행 PER = 종가 &divide; 추정 EPS. 후행 PER은 최근 확정 연간 EPS 기준입니다.<br><br>
    <b>지수 집계</b> — 개별 종목 PER을 시가총액 가중 <b>조화평균</b>으로 묶어 지수 대표값을 만듭니다:
    지수 PER = &Sigma;(시총) &divide; &Sigma;(시총 &divide; 종목PER). 단순평균이 아니라 조화평균이라 대형주(삼성전자&middot;SK하이닉스 등)의
    이익 비중이 크게 반영되고, PER이 0 이하인(적자) 종목은 집계에서 제외합니다.<br><br>
    <b>후행선 vs 선행선</b> — 컨센서스 기반 선행 PER은 과거 시점의 컨센서스를 알 방법이 없어(백필 불가)
    <b>이 트래커를 실행하기 시작한 날부터 하루씩 축적</b>됩니다. 오늘 추정치로 과거를 채우면 왜곡되므로 그렇게 하지 않습니다.<br><br>
    <b>한계</b> — 시총 상위 50종목 자체 계산이라 KRX 공식 지수 산출식과는 정확히 일치하지 않는 근사치입니다.
    KRX 공식 PBR 히스토리(2004~)와 미국 기준금리&middot;OECD 경기선행지수(CLI) 오버레이는 아직 없고 추가 예정입니다.
  </div>

<script>
const dates = {dates_json};
const perTrailing = {per_trailing_json};
const per2026e = {per_2026e_json};
const per2027e = {per_2027e_json};
const kospi = {kospi_json};

new Chart(document.getElementById('perChart').getContext('2d'), {{
  type: 'line',
  data: {{
    labels: dates,
    datasets: [
      {{ label: '후행(TTM) PER', data: perTrailing, borderColor: '#adb5bd', backgroundColor: 'transparent', yAxisID: 'yPer', tension: 0.15 }},
      {{ label: '당해선행(2026E) PER', data: per2026e, borderColor: '#ff6b6b', backgroundColor: 'transparent', yAxisID: 'yPer', tension: 0.15 }},
      {{ label: '차년선행(2027E) PER', data: per2027e, borderColor: '#4dabf7', backgroundColor: 'transparent', yAxisID: 'yPer', tension: 0.15 }},
      {{ label: '코스피 지수(우축)', data: kospi, borderColor: '#63e6be', backgroundColor: 'transparent', yAxisID: 'yIdx', borderDash: [3,3], tension: 0.15 }},
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ color: '#e6e6e6' }} }} }},
    scales: {{
      x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 12 }}, grid: {{ color: '#23262e' }} }},
      yPer: {{ position: 'left', title: {{ display: true, text: 'PER(배)', color: '#9aa0a6' }}, ticks: {{ color: '#9aa0a6' }}, grid: {{ color: '#23262e' }} }},
      yIdx: {{ position: 'right', title: {{ display: true, text: '코스피 지수', color: '#63e6be' }}, ticks: {{ color: '#63e6be' }}, grid: {{ drawOnChartArea: false }} }},
    }}
  }}
}});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
