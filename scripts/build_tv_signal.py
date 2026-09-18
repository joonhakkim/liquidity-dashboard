"""
"거래대금 신호" 페이지(docs/tv_signal.html) 빌드.

2026-09-18 대화에서 나온 아이디어를 백테스트한 결과를 페이지로 만든 것.

방법론
------
1. 코스피 종가에서 "스윙고점"을 잡는다: 좌우 N(기본 10)거래일보다 높은 날.
2. 연속한 두 스윙고점(전 파동 -> 현재 파동)을 비교한다:
   - 가격이 전 파동 고점보다 높은 "신고점"이면서
   - 그 파동 구간의 거래대금 최고치가 전 파동 거래대금 최고치보다 낮으면
   -> "거래대금 신호"로 표시한다 (가격은 오르는데 거래대금(매수 에너지)은 못 따라오는 상태).
3. 신호 발생 후 5/10/20/40/60거래일 수익률을 baseline(전체 평균)과 비교해 검증했다.
   - 대화 중 백테스트(2000~2026, 2020년 이전은 야후파이낸스 거래량을 거래대금 프록시로 사용)에서
     신호 후 5~10일 음수승률 100%, 20일 81~86%, 40~60일로 갈수록 효과가 옅어짐을 확인.
   - 2013년을 기준으로 전/후반 절반씩 나눠 검증해도(아웃오브샘플) 같은 패턴이 재현됨.
   - 이 페이지의 신호/백테스트는 실제 원화 거래대금만 있는 2020년 이후 데이터(krx_raw.csv)만
     사용한다 (프록시 데이터 섞지 않음 - 실거래 페이지라 정확도 우선).
"""
import json
import os
from datetime import datetime

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
KRX_RAW_PATH = os.path.join(DATA_DIR, "krx_raw.csv")
OUT_PATH = os.path.join(DOCS_DIR, "tv_signal.html")

SWING_WINDOW = 10  # 좌우 10거래일보다 높아야 스윙고점
FORWARD_HORIZONS = [5, 10, 20, 40, 60]


def find_swing_highs(close, n=SWING_WINDOW):
    idxs = []
    length = len(close)
    for i in range(n, length - n):
        window = close[i - n:i + n + 1]
        if close[i] == window.max():
            if not idxs or i - idxs[-1] > n:
                idxs.append(i)
    return idxs


def find_signals(close, tv, swing_idxs):
    signals = []
    for k in range(1, len(swing_idxs)):
        prev_i, cur_i = swing_idxs[k - 1], swing_idxs[k]
        start_prev = swing_idxs[k - 2] if k >= 2 else max(0, prev_i - 60)
        tv_prev_peak = tv[start_prev:prev_i + 1].max()
        tv_cur_peak = tv[prev_i:cur_i + 1].max()
        if close[cur_i] > close[prev_i] and tv_cur_peak < tv_prev_peak:
            signals.append({
                "idx": cur_i,
                "prev_idx": prev_i,
                "tv_prev_peak": float(tv_prev_peak),
                "tv_cur_peak": float(tv_cur_peak),
            })
    return signals


def find_live_signal(df, close, tv, swing_idxs):
    """확정된 스윙고점(좌우 N일 다 지나야 확정)만으로는 '지금'은 절대 신호가 안 뜬다 -
    최근 N거래일은 미래 데이터가 없어서 스윙고점 확정이 불가능하기 때문. 그래서 마지막으로
    확정된 스윙고점 이후 '진행 중인 파동'을 별도로 추적해서, 아직 파동이 안 끝났어도
    지금 시점 기준으로 신호 조건(가격 신고점 + 거래대금 고점 하락)을 만족하는지 본다."""
    if len(swing_idxs) < 1:
        return None

    prev1 = swing_idxs[-1]
    if len(swing_idxs) >= 2:
        prev2 = swing_idxs[-2]
        tv_prev_peak = float(tv[prev2:prev1 + 1].max())
    else:
        start = max(0, prev1 - 60)
        tv_prev_peak = float(tv[start:prev1 + 1].max())

    wave = close[prev1:]
    wave_tv = tv[prev1:]
    cur_max_close_offset = int(wave.argmax())
    cur_max_close_idx = prev1 + cur_max_close_offset
    cur_max_close = float(wave[cur_max_close_offset])
    cur_wave_tv_peak_offset = int(wave_tv.argmax())
    cur_wave_tv_peak_idx = prev1 + cur_wave_tv_peak_offset
    cur_wave_tv_peak = float(wave_tv[cur_wave_tv_peak_offset])

    price_new_high = cur_max_close > float(close[prev1])
    tv_lower = cur_wave_tv_peak < tv_prev_peak
    active = bool(price_new_high and tv_lower)

    return {
        "active": active,
        "as_of_date": df.loc[len(df) - 1, "date"].strftime("%Y-%m-%d"),
        "prev_swing_idx": int(prev1),
        "prev_swing_date": df.loc[prev1, "date"].strftime("%Y-%m-%d"),
        "prev_swing_close": float(close[prev1]),
        "cur_wave_high_idx": int(cur_max_close_idx),
        "cur_wave_tv_peak_idx": int(cur_wave_tv_peak_idx),
        "cur_wave_high_date": df.loc[cur_max_close_idx, "date"].strftime("%Y-%m-%d"),
        "cur_wave_high_close": cur_max_close,
        "tv_prev_peak": round(tv_prev_peak / 1e6, 1),
        "cur_wave_tv_peak": round(cur_wave_tv_peak / 1e6, 1),
    }


def main():
    if not os.path.exists(KRX_RAW_PATH):
        print("krx_raw.csv가 없습니다. fetch_krx.py를 먼저 실행하세요.")
        return

    df = pd.read_csv(KRX_RAW_PATH)
    df["date"] = pd.to_datetime(df["date"])
    df = df[["date", "kospi_close", "kospi_trading_value"]].dropna()
    df = df.sort_values("date").reset_index(drop=True)

    close = df["kospi_close"].values
    tv = df["kospi_trading_value"].values

    swing_idxs = find_swing_highs(close)
    signals = find_signals(close, tv, swing_idxs)
    live_status = find_live_signal(df, close, tv, swing_idxs)

    for h in FORWARD_HORIZONS:
        df[f"fwd{h}"] = df["kospi_close"].shift(-h) / df["kospi_close"] - 1

    baseline = {h: float(df[f"fwd{h}"].mean()) for h in FORWARD_HORIZONS}

    backtest_rows = []
    for h in FORWARD_HORIZONS:
        vals = [df.loc[s["idx"], f"fwd{h}"] for s in signals if pd.notna(df.loc[s["idx"], f"fwd{h}"])]
        if vals:
            mean_ret = sum(vals) / len(vals)
            win_neg = sum(1 for v in vals if v < 0) / len(vals)
        else:
            mean_ret, win_neg = None, None
        backtest_rows.append({
            "horizon": h,
            "signal_mean": round(mean_ret * 100, 2) if mean_ret is not None else None,
            "baseline_mean": round(baseline[h] * 100, 2),
            "neg_winrate": round(win_neg * 100, 1) if win_neg is not None else None,
            "n": len(vals),
        })

    signal_rows = []
    for s in signals:
        row_date = df.loc[s["idx"], "date"].strftime("%Y-%m-%d")
        row_close = float(df.loc[s["idx"], "kospi_close"])
        fwd = {h: (round(float(df.loc[s["idx"], f"fwd{h}"]) * 100, 2)
                   if pd.notna(df.loc[s["idx"], f"fwd{h}"]) else None) for h in FORWARD_HORIZONS}
        signal_rows.append({"date": row_date, "close": row_close, "fwd": fwd})

    last_signal = signal_rows[-1] if signal_rows else None
    last_swing_date = df.loc[swing_idxs[-1], "date"].strftime("%Y-%m-%d") if swing_idxs else None

    n = len(df)
    # 가격선 위에 신호 마커를 찍을 때 scatter를 별도 데이터셋으로 분리하면 category 축과
    # 안 맞아서 위치가 틀어진다(Chart.js가 scatter는 기본 선형축으로 좌표를 해석함) - 그래서
    # 가격선(close) 자체의 포인트 스타일을 인덱스별로 다르게 줘서(대부분 반지름 0, 신호 인덱스만
    # 크게) 같은 카테고리 축을 그대로 쓰게 한다.
    point_radius = [0] * n
    point_style = ["circle"] * n
    point_bg = ["transparent"] * n
    for s in signals:
        point_radius[s["idx"]] = 7
        point_style[s["idx"]] = "triangle"
        point_bg[s["idx"]] = "#ff6b6b"
    if live_status and live_status["active"]:
        li = live_status["cur_wave_high_idx"]
        point_radius[li] = 9
        point_style[li] = "star"
        point_bg[li] = "#ffd43b"

    # 파동별 거래대금 고점을 이어서 그린 추세선(사용자가 보여준 차트처럼 손으로 그은 고점
    # 연결선과 같은 개념) - 스윙고점 구간마다 그 구간의 거래대금 최고치 지점만 값을 채우고
    # 나머지는 null로 둬서 line + spanGaps로 점들만 이어지게 한다.
    tv_wave_peaks = [None] * n
    tv_wave_troughs = [None] * n
    for k in range(len(swing_idxs)):
        seg_start = 0 if k == 0 else swing_idxs[k - 1]
        seg_end = swing_idxs[k]
        seg_tv = tv[seg_start:seg_end + 1]
        peak_idx = seg_start + int(seg_tv.argmax())
        trough_idx = seg_start + int(seg_tv.argmin())
        tv_wave_peaks[peak_idx] = round(float(tv[peak_idx]) / 1e6, 1)
        tv_wave_troughs[trough_idx] = round(float(tv[trough_idx]) / 1e6, 1)
    if live_status:
        tv_wave_peaks[live_status["cur_wave_tv_peak_idx"]] = live_status["cur_wave_tv_peak"]
        live_wave_tv = tv[live_status["prev_swing_idx"]:]
        live_trough_idx = live_status["prev_swing_idx"] + int(live_wave_tv.argmin())
        tv_wave_troughs[live_trough_idx] = round(float(tv[live_trough_idx]) / 1e6, 1)

    chart_data = {
        "dates": df["date"].dt.strftime("%Y-%m-%d").tolist(),
        "close": df["kospi_close"].round(2).tolist(),
        "tv": (df["kospi_trading_value"] / 1e6).round(1).tolist(),  # 백만원 -> 조원
        "point_radius": point_radius,
        "point_style": point_style,
        "point_bg": point_bg,
        "tv_wave_peaks": tv_wave_peaks,
        "tv_wave_troughs": tv_wave_troughs,
    }

    html = TEMPLATE.format(
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        swing_window=SWING_WINDOW,
        data_start=df["date"].min().strftime("%Y-%m-%d"),
        data_end=df["date"].max().strftime("%Y-%m-%d"),
        n_swings=len(swing_idxs),
        n_signals=len(signals),
        last_signal_json=json.dumps(last_signal, ensure_ascii=False),
        last_swing_date=last_swing_date or "N/A",
        live_status_json=json.dumps(live_status, ensure_ascii=False),
        backtest_json=json.dumps(backtest_rows, ensure_ascii=False),
        signal_rows_json=json.dumps(signal_rows, ensure_ascii=False),
        chart_json=json.dumps(chart_data, ensure_ascii=False),
    )
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"저장 완료: {OUT_PATH} (스윙고점 {len(swing_idxs)}개, 신호 {len(signals)}건)")


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>거래대금 신호</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  a.back {{ color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }}
  h1 {{ font-size:20px; margin:8px 0 4px 0; }}
  .updated {{ color:#9aa0a6; font-size:13px; margin-bottom:20px; }}
  .status {{ background:#1a1d24; border:1px solid #23262e; border-radius:12px; padding:16px 20px; margin-bottom:20px; max-width:900px; }}
  .status.on {{ border-color:#ff6b6b; background:#241a1a; }}
  .status .big {{ font-size:16px; font-weight:bold; margin-bottom:4px; }}
  .status.on .big {{ color:#ff6b6b; }}
  .status .sub {{ color:#9aa0a6; font-size:13px; }}
  .chart-wrap {{ background:#1a1d24; border-radius:12px; padding:16px 18px; max-width:1200px; margin-bottom:20px; }}
  .chart-range-buttons {{ display:flex; gap:6px; margin-bottom:10px; }}
  .chart-range-buttons button {{ background:#23262e; color:#c7cbd1; border:none; border-radius:6px; padding:5px 12px; font-size:12px; cursor:pointer; }}
  .chart-range-buttons button.active {{ background:#4dabf7; color:#0f1115; font-weight:bold; }}
  #mainChart {{ height:420px; }}
  .note {{ color:#9aa0a6; font-size:12px; line-height:1.7; max-width:900px; background:#1a1d24; border-radius:10px; padding:16px 18px; margin-top:20px; }}
  .note b {{ color:#e6e6e6; }}
  table {{ border-collapse:collapse; max-width:900px; width:100%; margin-top:14px; font-size:13px; }}
  th, td {{ padding:8px 10px; text-align:right; border-bottom:1px solid #23262e; }}
  th:first-child, td:first-child {{ text-align:left; }}
  th {{ color:#9aa0a6; font-weight:normal; }}
  td.neg {{ color:#ff6b6b; }}
  td.pos {{ color:#63e6be; }}
  h3 {{ font-size:14px; color:#9aa0a6; margin:28px 0 8px 0; }}
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>거래대금 신호</h1>
  <div class="updated">최종 갱신: {updated_at} (데이터 {data_start} ~ {data_end}, 스윙고점 {n_swings}개 중 신호 {n_signals}건)</div>

  <div id="statusBox" class="status"></div>

  <div class="chart-wrap">
    <div class="chart-range-buttons" id="rangeButtons"></div>
    <div style="font-size:12px; color:#9aa0a6; margin-bottom:8px;">
      <span style="color:#ff6b6b;">&#9650;</span> 확정 신호&nbsp;&nbsp;
      <span style="color:#ffd43b;">&#9733;</span> 지금 진행중&nbsp;&nbsp;
      <span style="color:#ffd43b;">- - -</span> 파동별 거래대금 고점 연결선&nbsp;&nbsp;
      <span style="color:#63e6be;">- - -</span> 파동별 거래대금 저점 연결선(박스)
    </div>
    <div id="mainChart"><canvas id="tvChart"></canvas></div>
  </div>

  <div class="note">
    <b>계산 방법</b><br>
    1. 코스피 종가에서 좌우 {swing_window}거래일보다 높은 날을 "스윙고점"으로 잡는다.<br>
    2. 연속한 두 스윙고점을 비교해, 가격은 직전 스윙고점보다 <b>높은 신고점</b>인데 그 구간(파동)의
    거래대금 최고치는 직전 파동의 거래대금 최고치보다 <b>낮은 경우</b>를 "거래대금 신호"로 표시한다
    (가격 상승을 뒷받침하는 매수 에너지가 파동을 거듭할수록 줄어드는 상태 - 약세 다이버전스).<br>
    3. 신호 발생 후 5/10/20/40/60거래일 수익률을 전체 평균(baseline)과 비교해 검증한다.
  </div>

  <h3>백테스트 결과 (신호 발생 후 N거래일 수익률, 2020년 이후 실거래대금 기준)</h3>
  <table id="backtestTable"></table>

  <h3>과거 신호 목록</h3>
  <table id="signalTable"></table>

  <div class="note">
    <b>참고</b> - 대화 중 검증에서는 2000년까지 시계열을 늘려(2020년 이전은 야후파이낸스 거래량을
    거래대금 프록시로 사용) 49건의 신호로 백테스트했고, 신호 후 5~10일 음수승률 100%, 20일
    81~86%, 2013년 기준 전/후반 절반씩 나눈 아웃오브샘플 검증에서도 같은 패턴이 재현됐다.
    이 페이지는 실거래 대시보드라 정확한 원화 거래대금만 있는 2020년 이후 데이터만 사용하므로
    신호 건수가 위 표보다 적다. 40~60일로 갈수록 효과가 옅어지는 걸 보면 장기 추세 전환보다는
    <b>1~4주 단기 조정</b> 신호에 가깝다.
  </div>

<script>
const LAST_SIGNAL = {last_signal_json};
const LAST_SWING_DATE = "{last_swing_date}";
const LIVE = {live_status_json};
const BACKTEST = {backtest_json};
const SIGNAL_ROWS = {signal_rows_json};
const CHART = {chart_json};

// 상태 박스: 확정된 스윙고점만 보면 최근 N거래일은 절대 신호가 안 뜬다(미래 데이터가 있어야
// 스윙고점이 확정되므로). 그래서 마지막 확정 스윙고점 이후 '진행 중인 파동'을 지금 시점
// 기준으로 평가한 LIVE 신호를 우선 보여준다.
const statusBox = document.getElementById('statusBox');
if (LIVE) {{
  statusBox.className = 'status' + (LIVE.active ? ' on' : '');
  if (LIVE.active) {{
    statusBox.innerHTML = `
      <div class="big">지금 신호 진행 중</div>
      <div class="sub">${{LIVE.prev_swing_date}} 스윙고점(코스피 ${{LIVE.prev_swing_close.toFixed(2)}}) 이후,
      ${{LIVE.cur_wave_high_date}}에 코스피 ${{LIVE.cur_wave_high_close.toFixed(2)}}로 신고점을 냈지만
      이번 파동 거래대금 최고치(${{LIVE.cur_wave_tv_peak}}조원)가 직전 파동 최고치(${{LIVE.tv_prev_peak}}조원)보다 낮음
      (기준일 ${{LIVE.as_of_date}}, 아직 파동이 끝나지 않아 미확정)</div>`;
  }} else {{
    statusBox.innerHTML = `
      <div class="big">지금 신호 없음</div>
      <div class="sub">가장 최근 확정 스윙고점: ${{LIVE.prev_swing_date}} (코스피 ${{LIVE.prev_swing_close.toFixed(2)}})
      &middot; 이후 최고가 ${{LIVE.cur_wave_high_date}} ${{LIVE.cur_wave_high_close.toFixed(2)}}
      (거래대금 ${{LIVE.cur_wave_tv_peak}}조원 vs 직전파동 ${{LIVE.tv_prev_peak}}조원)</div>`;
  }}
}} else if (LAST_SIGNAL) {{
  statusBox.innerHTML = `<div class="big">확정 신호 이력만 있음</div>
    <div class="sub">가장 최근 확정 신호: ${{LAST_SIGNAL.date}} (코스피 ${{LAST_SIGNAL.close.toFixed(2)}}) &middot; 가장 최근 스윙고점: ${{LAST_SWING_DATE}}</div>`;
}} else {{
  statusBox.innerHTML = '<div class="big">신호 없음</div>';
}}

// 백테스트 표
const btTable = document.getElementById('backtestTable');
btTable.innerHTML = `<tr><th>N거래일 후</th><th>신호평균</th><th>baseline</th><th>음수승률</th><th>표본수</th></tr>` +
  BACKTEST.map(r => `<tr>
    <td>${{r.horizon}}일</td>
    <td class="${{r.signal_mean < 0 ? 'neg' : 'pos'}}">${{r.signal_mean !== null ? r.signal_mean.toFixed(2) + '%' : 'N/A'}}</td>
    <td>${{r.baseline_mean.toFixed(2)}}%</td>
    <td>${{r.neg_winrate !== null ? r.neg_winrate.toFixed(1) + '%' : 'N/A'}}</td>
    <td>${{r.n}}</td>
  </tr>`).join('');

// 신호 목록 표
const sigTable = document.getElementById('signalTable');
sigTable.innerHTML = `<tr><th>신호일</th><th>코스피</th><th>5일후</th><th>10일후</th><th>20일후</th><th>40일후</th><th>60일후</th></tr>` +
  (SIGNAL_ROWS.length ? SIGNAL_ROWS.slice().reverse().map(r => `<tr>
    <td>${{r.date}}</td>
    <td>${{r.close.toFixed(2)}}</td>
    ${{[5,10,20,40,60].map(h => {{
      const v = r.fwd[h];
      if (v === null || v === undefined) return '<td>-</td>';
      return `<td class="${{v < 0 ? 'neg' : 'pos'}}">${{v.toFixed(2)}}%</td>`;
    }}).join('')}}
  </tr>`).join('') : '<tr><td colspan="7">신호 없음</td></tr>');

// 차트
const RANGE_LABELS = ['6개월', '1년', '3년', '전체'];
const RANGE_DAYS = [126, 252, 756, null];
let currentRangeIdx = 3;
let chartObj = null;

function sliceStart(days) {{
  if (days === null) return 0;
  return Math.max(0, CHART.dates.length - days);
}}

function buildChart(rangeIdx) {{
  currentRangeIdx = rangeIdx;
  const startIdx = sliceStart(RANGE_DAYS[rangeIdx]);
  const labels = CHART.dates.slice(startIdx);
  const close = CHART.close.slice(startIdx);
  const tv = CHART.tv.slice(startIdx);
  const pointRadius = CHART.point_radius.slice(startIdx);
  const pointStyle = CHART.point_style.slice(startIdx);
  const pointBg = CHART.point_bg.slice(startIdx);
  const tvPeaks = CHART.tv_wave_peaks.slice(startIdx);
  const tvTroughs = CHART.tv_wave_troughs.slice(startIdx);

  if (chartObj) chartObj.destroy();
  chartObj = new Chart(document.getElementById('tvChart').getContext('2d'), {{
    data: {{
      labels,
      datasets: [
        {{ type: 'bar', label: '거래대금(조원)', data: tv, backgroundColor: '#4dabf799', borderWidth: 0, yAxisID: 'yTv', order: 3 }},
        {{ type: 'line', label: '거래대금 파동고점선', data: tvPeaks, borderColor: '#ffd43b', backgroundColor: '#ffd43b', borderWidth: 1.5, borderDash: [5, 4], spanGaps: true, pointRadius: 4, pointStyle: 'circle', pointBackgroundColor: '#ffd43b', tension: 0, yAxisID: 'yTv', order: 2 }},
        {{ type: 'line', label: '거래대금 파동저점선', data: tvTroughs, borderColor: '#63e6be', backgroundColor: '#63e6be', borderWidth: 1.5, borderDash: [5, 4], spanGaps: true, pointRadius: 4, pointStyle: 'circle', pointBackgroundColor: '#63e6be', tension: 0, yAxisID: 'yTv', order: 2 }},
        {{ type: 'line', label: '코스피', data: close, borderColor: '#e6e6e6', backgroundColor: 'transparent', borderWidth: 1.5, pointRadius, pointStyle, pointBackgroundColor: pointBg, pointBorderColor: pointBg, tension: 0.1, yAxisID: 'yClose', order: 1 }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ labels: {{ color: '#e6e6e6' }} }} }},
      scales: {{
        x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 12 }}, grid: {{ color: '#23262e' }} }},
        yTv: {{ position: 'left', title: {{ display: true, text: '거래대금(조원)', color: '#9aa0a6' }}, ticks: {{ color: '#9aa0a6' }}, grid: {{ color: '#23262e' }} }},
        yClose: {{ position: 'right', title: {{ display: true, text: '코스피', color: '#e6e6e6' }}, ticks: {{ color: '#e6e6e6' }}, grid: {{ drawOnChartArea: false }} }},
      }}
    }}
  }});
}}

const rangeBtns = document.getElementById('rangeButtons');
rangeBtns.innerHTML = RANGE_LABELS.map((l, i) => `<button data-i="${{i}}" class="${{i === currentRangeIdx ? 'active' : ''}}">${{l}}</button>`).join('');
rangeBtns.querySelectorAll('button').forEach(btn => {{
  btn.addEventListener('click', () => {{
    buildChart(parseInt(btn.dataset.i, 10));
    rangeBtns.querySelectorAll('button').forEach(b => b.classList.toggle('active', b === btn));
  }});
}});

buildChart(currentRangeIdx);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
