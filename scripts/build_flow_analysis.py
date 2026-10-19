"""
수급 분석 페이지(docs/flow_analysis.html) - 2026-10-19 사용자 요청.
"우리가 만든 유니버스(섹터 정리 섹터·종목)로 업종별 수급 분석: (1) 기관+외인 합산 순매수의 시가총액 대비 비율 8일·15일 이동평균,
(2) 섹터 수급 방향과 시가총액 변화 차트, (3) 8일·15일 데드/골드 크로스, (4) 이동평균 기준 섹터별 수급 순위, (5) 주가와의 상관관계 확인".

데이터
- data/manual/수급.xlsx: 사용자가 DataGuide로 받는 종목별 일별 외국인·기관 순매수대금(단위 백만원, 열 = 종목 x [외국인, 기관]).
  8행 종목코드, 9행 종목명, 14행 항목명, 15행부터 날짜별 값. 아직 값이 없는 마지막 날(당일)은 건너뜀.
- 시가총액: '기업 밴드 찾기 2' 일별 시총(build_sector_ma_breadth.load_universe와 같은 캐시), 섹터·종목은 섹터 정리 기준.
계산
- 수급 비율(bp, 시총 대비 0.01%) = 순매수대금 / 전일 시가총액 x 10,000 (전일 시총을 써서 당일 주가 변동이 분모에 섞이지 않게).
- 섹터 집계 두 가지: 시총합 = 섹터 순매수 합 / 섹터 전일 시총 합(큰 종목 영향 큼), 종목평균 = 종목별 비율의 평균(종목당 ±200bp로 자름).
- 이동평균·교차·순위는 페이지 JS가 고른 기간으로 계산(기본 8일/15일). 종목 펼침 표는 8일·15일 고정.
검증(2025-01~2026-10, scratchpad flow/flow1·flow2.py) 요약은 페이지 아래 '수급과 주가' 칸 참고.
파이프라인: run_pipeline.py에서 build_sector_ma_breadth.py 다음(시총 캐시가 새로 저장된 뒤).
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import openpyxl
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import build_sector_ma_breadth as bsm  # noqa: E402

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
FLOW_XLSX = os.path.join(BASE_DIR, "data", "manual", "수급.xlsx")
CACHE_PATH = os.path.join(BASE_DIR, "data", "screening", "cache", "flow_daily.pkl")
OUT_PATH = os.path.join(BASE_DIR, "docs", "flow_analysis.html")
SUMMARY_PATH = os.path.join(BASE_DIR, "data", "screening", "flow_sector_summary.csv")
EQ_CLIP = 200.0      # 종목평균 집계 때 종목 하루 비율을 ±200bp(시총의 2%)로 자름(블록딜 등 극단값)
MIN_MEMBERS = 3
WHO = ["합산", "외국인", "기관"]


def load_flows():
    """수급.xlsx -> (외국인, 기관) 일별 DataFrame(백만원, 열 = 6자리 종목코드). 파일 수정시각이 같으면 캐시."""
    mtime = os.path.getmtime(FLOW_XLSX)
    if os.path.exists(CACHE_PATH):
        c = pd.read_pickle(CACHE_PATH)
        if c.get("mtime") == mtime:
            return c["F"], c["I"], c["names"]
    ws = openpyxl.load_workbook(FLOW_XLSX, read_only=True, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr = next(i for i, r in enumerate(rows) if r[0] == "D A T E")
    codes, names, items = rows[7], rows[8], rows[hdr]
    data = [r for r in rows[hdr + 1:] if r[0] is not None]
    dates = pd.to_datetime([r[0] for r in data])
    arr = np.array([[np.nan if v is None else v for v in r[1:]] for r in data], dtype=float)
    cols = pd.MultiIndex.from_tuples([(str(codes[j + 1])[-6:], "외국인" if "외국인" in str(items[j + 1]) else "기관") for j in range(arr.shape[1])])
    df = pd.DataFrame(arr, index=dates, columns=cols)
    df = df[df.notna().any(axis=1)]                 # 값이 아직 없는 날(당일) 제외
    F, I = df.xs("외국인", level=1, axis=1), df.xs("기관", level=1, axis=1)
    F = F.loc[:, ~F.columns.duplicated()]; I = I.loc[:, ~I.columns.duplicated()]
    nm = {str(codes[j + 1])[-6:]: names[j + 1] for j in range(arr.shape[1])}
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    pd.to_pickle({"mtime": mtime, "F": F, "I": I, "names": nm}, CACHE_PATH)
    return F, I, nm


def r1(v, d=1):
    return None if v is None or not np.isfinite(v) else round(float(v), d)


def main():
    F, I, fnames = load_flows()
    meta, members, codes, caps, series, no_data, _ = bsm.load_universe()
    idx = F.index
    cap = caps.reindex(idx.union(caps.index)).ffill().reindex(idx)
    codes = [c for c in codes if c in F.columns and c in cap.columns]
    capL = cap[codes].shift(1)                                         # 전일 시총(원)
    has = capL.notna()
    net = {"외국인": F[codes].fillna(0).where(has) * 1e6, "기관": I[codes].fillna(0).where(has) * 1e6}
    net["합산"] = net["외국인"] + net["기관"]
    ratio = {w: (net[w] / capL * 1e4) for w in WHO}                     # 종목별 bp
    secs = {s: [c for c in cs if c in codes] for s, cs in members.items()}
    secs = {s: cs for s, cs in secs.items() if len(cs) >= MIN_MEMBERS}
    allc = sorted({c for cs in secs.values() for c in cs})
    asof = idx[-1]
    print(f"수급 {idx[0].date()} ~ {asof.date()} ({len(idx)}일), 섹터 {len(secs)}개, 종목 {len(allc)}개(수급·시총 둘 다 있는 섹터 정리 종목)")

    def pack_group(cs, name, n):
        eok = lambda s: [r1(v / 1e8, 1) for v in s.values]
        g = {"name": name, "n": n,
             "cap": [None if not np.isfinite(v) else int(round(v / 1e8)) for v in cap[cs].sum(axis=1, min_count=1).values],  # 당일 시총(억)
             "capL": [None if not np.isfinite(v) else int(round(v / 1e8)) for v in capL[cs].sum(axis=1, min_count=1).values],
             "net": {w: eok(net[w][cs].sum(axis=1, min_count=1)) for w in ("외국인", "기관")},
             "eq": {w: [r1(v, 2) for v in ratio[w][cs].clip(-EQ_CLIP, EQ_CLIP).mean(axis=1).values] for w in WHO}}
        return g

    out = {"asof": asof.strftime("%Y-%m-%d"), "dates": [d.strftime("%Y-%m-%d") for d in idx], "who": WHO,
           "all": pack_group(allc, "섹터 정리 전체", len(allc)),
           "sectors": [pack_group(cs, s, len(cs)) | {"codes": cs} for s, cs in sorted(secs.items())], "stocks": {}}
    # 종목 펼침 표(8일·15일 고정)
    c2s = {c: s for s, cs in secs.items() for c in cs}
    r20 = (cap[allc].iloc[-1] / cap[allc].iloc[-21] - 1) * 100 if len(idx) > 21 else pd.Series(np.nan, index=allc)
    for c in allc:
        st = {"name": meta["name"].get(c) or fnames.get(c, c), "sec": c2s[c], "cap": r1(cap[c].iloc[-1] / 1e8, 0), "r20": r1(r20.get(c), 1)}
        for w in WHO:
            rt = ratio[w][c]
            st[w] = [r1(rt.iloc[-8:].mean(), 2), r1(rt.iloc[-15:].mean(), 2), r1(net[w][c].iloc[-5:].sum() / 1e8, 1), r1(net[w][c].iloc[-20:].sum() / 1e8, 1)]
        out["stocks"][c] = st
    # 요약 csv(섹터, 합산·시총합 기준 8/15)
    rows = []
    for g in [out["all"]] + out["sectors"]:
        nt = np.array([(a or 0) + (b or 0) for a, b in zip(g["net"]["외국인"], g["net"]["기관"])], float)
        cl = np.array([np.nan if v is None else v for v in g["capL"]], float)
        rr = pd.Series(nt / cl * 1e4)
        rows.append({"sector": g["name"], "n": g["n"], "ma8_bp": rr.iloc[-8:].mean(), "ma15_bp": rr.iloc[-15:].mean(),
                     "net5_eok": nt[-5:].sum(), "net20_eok": nt[-20:].sum(), "cap_jo": (g["cap"][-1] or 0) / 1e4})
    pd.DataFrame(rows).sort_values("ma15_bp", ascending=False).to_csv(SUMMARY_PATH, index=False, encoding="utf-8-sig", float_format="%.3f")
    html = (TEMPLATE.replace("__UPDATED__", datetime.now().strftime("%Y-%m-%d %H:%M")).replace("__ASOF__", out["asof"])
            .replace("__FROM__", out["dates"][0]).replace("__DATA__", json.dumps(out, ensure_ascii=False, separators=(",", ":"))))
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"저장 완료: {OUT_PATH} ({len(html) // 1024}KB)")
    print(f"저장 완료: {SUMMARY_PATH}")


TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>수급 분석</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body { font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  a.back { color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }
  h1 { font-size:20px; margin:8px 0 4px 0; } h1 .sub { font-size:13px; color:#9aa0a6; font-weight:normal; margin-left:6px; }
  .updated { color:#9aa0a6; font-size:13px; margin-bottom:14px; }
  .ctl { display:flex; gap:12px; flex-wrap:wrap; align-items:center; font-size:12px; color:#c9ccd1; margin-bottom:12px; }
  .ctl select { background:#0f1115; color:#e6e6e6; border:1px solid #2a2e37; border-radius:6px; padding:4px 6px; font-family:inherit; font-size:12px; }
  .seg button { background:#1a1d24; border:1px solid #2a2e37; color:#c9ccd1; padding:5px 11px; border-radius:999px; cursor:pointer; font-size:12px; font-family:inherit; margin-right:4px; }
  .seg button.active { background:#4dabf7; color:#0f1115; border-color:#4dabf7; font-weight:bold; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; margin-bottom:16px; }
  .card { background:#1a1d24; border:1px solid #23262e; border-radius:12px; padding:12px 14px; font-size:12px; color:#9aa0a6; }
  .card .val { font-size:21px; font-weight:700; color:#e6e6e6; margin:4px 0; }
  .box { background:#12151b; border:1px solid #23262e; border-radius:12px; padding:14px 16px; margin-bottom:16px; }
  .box h2 { font-size:15px; margin:0 0 6px 0; } .box .desc { font-size:12px; color:#9aa0a6; margin-bottom:8px; line-height:1.6; }
  .chart-wrap { position:relative; height:320px; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  th, td { padding:5px 7px; border-bottom:1px solid #23262e; text-align:right; white-space:nowrap; }
  th:nth-child(-n+2), td:nth-child(-n+2) { text-align:left; }
  th { color:#9aa0a6; font-weight:normal; cursor:pointer; } th.nosort { cursor:default; } th.sorted { color:#4dabf7; }
  tr.sec { cursor:pointer; } tr.sec:hover { background:#1a1f28; } tr.sel { background:#1c2633; }
  .members td { font-size:11px; color:#c9ccd1; background:#0f1218; text-align:left; white-space:normal; }
  .pos { color:#ff8787; } .neg { color:#74a7ff; }
  .tblwrap { overflow-x:auto; }
  .note { font-size:12px; color:#9aa0a6; line-height:1.75; background:#1a1d24; border-radius:10px; padding:12px 16px; }
  .note b { color:#ffa94d; }
  @media (max-width: 640px) { body { padding:16px; } .chart-wrap { height:250px; } }
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <a class="back" href="sector_ma_breadth.html">섹터 이평선 위 비율 &rarr;</a>
  <h1>수급 분석<span class="sub">섹터 정리 섹터·종목의 외국인·기관 순매수, 시가총액 대비 비율</span></h1>
  <div class="updated">최종 갱신: __UPDATED__ &middot; 데이터 __FROM__ ~ __ASOF__ &middot; 수급.xlsx(외국인·기관 순매수대금) + 기업 밴드 찾기 2 시가총액</div>

  <div class="ctl">
    <span>주체</span><span class="seg" id="segWho"><button data-v="합산" class="active">외국인+기관</button><button data-v="외국인">외국인</button><button data-v="기관">기관</button></span>
    <span>섹터 집계</span><span class="seg" id="segHow"><button data-v="cap" class="active">섹터 시총 합</button><button data-v="eq">종목 평균</button></span>
    <span>빠른</span><select id="selF"></select><span>느린</span><select id="selS"></select>
    <span>차트 기간</span><span class="seg" id="segP"><button data-p="63">3달</button><button data-p="126" class="active">6달</button><button data-p="250">1년</button><button data-p="0">전체</button></span>
  </div>

  <div class="cards" id="cards"></div>

  <div class="box">
    <h2>수급 방향 <span id="t1" style="font-size:12px;font-weight:normal;color:#9aa0a6"></span></h2>
    <div class="desc">막대 = 그날 순매수 / 전일 시가총액(bp, 1bp = 시총의 0.01%), 선 = 이동평균. 빠른 선이 느린 선을 위로 뚫으면 골드 ▲, 아래로 뚫으면 데드 ▼. 아래 표에서 섹터를 누르면 그 섹터로 바뀝니다.</div>
    <div class="ctl" style="margin-bottom:6px"><span>대상</span><select id="selT"></select><span id="xNow"></span></div>
    <div class="chart-wrap"><canvas id="c1"></canvas></div>
  </div>

  <div class="box">
    <h2>시가총액과 누적 순매수 <span id="t2" style="font-size:12px;font-weight:normal;color:#9aa0a6"></span></h2>
    <div class="desc">흰 선 = 섹터 시가총액(조원, 왼쪽), 색 선 = 차트 시작일부터 누적 순매수(억원, 오른쪽 - 빨강 외국인+기관 · 주황 외국인 · 초록 기관). 누적 순매수가 오르는데 시총이 같이 오르면 수급이 주가를 받치는 것, 엇갈리면 다른 주체(개인 등)가 반대로 움직이는 것.</div>
    <div class="chart-wrap"><canvas id="c2"></canvas></div>
  </div>

  <div class="box">
    <h2>섹터 수급 순위 <span style="font-size:12px;font-weight:normal;color:#9aa0a6">머리글 = 정렬, 행 = 위 차트 대상 + 소속 종목 수급 펼치기</span></h2>
    <div class="tblwrap"><table id="tbl"></table></div>
  </div>

  <div class="note" id="study"></div>

<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
const nn = v => v !== null && v !== undefined && !isNaN(v);
const N = D.dates.length;
const WINS = [3, 5, 8, 10, 14, 15, 20, 40, 60];
let WHO = '합산', HOW = 'cap', XF = 8, XS = 15, PERIOD = 126, TGT = '', SORT = 'slow', DESC = true, OPEN = null;
const f1 = (v, d = 1) => nn(v) ? v.toFixed(d) : '-';
const sg = (v, d = 1) => nn(v) ? (v >= 0 ? '+' : '') + v.toFixed(d) : '-';
const cls = v => nn(v) ? (v >= 0 ? 'pos' : 'neg') : '';
const eok = v => !nn(v) ? '-' : (Math.abs(v) >= 10000 ? (v / 10000).toFixed(2) + '조' : Math.round(v).toLocaleString() + '억');
function netOf(g, who) { const F = g.net['외국인'], I = g.net['기관']; return who === '외국인' ? F : who === '기관' ? I : F.map((v, i) => (nn(v) || nn(I[i])) ? (v || 0) + (I[i] || 0) : null); }
function ratioOf(g) {   // 일별 수급 비율(bp)
  if (HOW === 'eq') return g.eq[WHO];
  const nt = netOf(g, WHO);
  return nt.map((v, i) => nn(v) && nn(g.capL[i]) && g.capL[i] > 0 ? v / g.capL[i] * 1e4 : null);
}
function ma(a, w) { return a.map((_, i) => { if (i < w - 1) return null; let s = 0, c = 0; for (let j = i - w + 1; j <= i; j++) if (nn(a[j])) { s += a[j]; c++; } return c >= Math.ceil(w * 0.8) ? s / c : null; }); }
function crossState(f, s) {
  const k = N - 1; if (!nn(f[k]) || !nn(s[k])) return null;
  const up = f[k] > s[k]; let j = k;
  while (j - 1 >= 0 && nn(f[j - 1]) && nn(s[j - 1]) && (f[j - 1] > s[j - 1]) === up) j--;
  return {up, days: k - j + 1, since: j > 0 && nn(f[j - 1]) ? D.dates[j] : null};
}
function sumLast(a, n, end = N - 1) { let s = 0, c = 0; for (let i = Math.max(0, end - n + 1); i <= end; i++) if (nn(a[i])) { s += a[i]; c++; } return c ? s : null; }
function groupStats(g) {
  const r = ratioOf(g), f = ma(r, XF), s = ma(r, XS), nt = netOf(g, WHO), k = N - 1;
  const cap0 = g.cap[k], cap20 = g.cap[k - 20];
  return {g, r, f, s, fast: f[k], slow: s[k], slow5: s[k - 5], fast5: f[k - 5], x: crossState(f, s),
    n5: sumLast(nt, 5), n20: sumLast(nt, 20), c20: nn(cap0) && nn(cap20) && cap20 ? (cap0 / cap20 - 1) * 100 : null, cap: cap0};
}
function getGroup(name) { return name ? D.sectors.find(x => x.name === name) : D.all; }
// ---------- 카드
function renderCards() {
  const a = groupStats(D.all);
  const fw = D.all.net['외국인'], iw = D.all.net['기관'];
  $('cards').innerHTML =
    `<div class="card">섹터 정리 전체 · ${XF}일 / ${XS}일 평균<div class="val"><span class="${cls(a.fast)}">${sg(a.fast, 2)}</span> / <span class="${cls(a.slow)}">${sg(a.slow, 2)}</span>bp</div>${WHO === '합산' ? '외국인+기관' : WHO} · ${HOW === 'cap' ? '시총 합' : '종목 평균'} 기준 · 1주 전 ${XS}일 ${sg(a.slow5, 2)}bp</div>` +
    `<div class="card">교차 상태(${XF}/${XS})<div class="val" style="color:${a.x && a.x.up ? '#ff8787' : '#74a7ff'}">${a.x ? (a.x.up ? '▲ 골드' : '▼ 데드') + ' ' + a.x.days + '일째' : '-'}</div>${a.x && a.x.since ? a.x.since + ' 교차' : ''}</div>` +
    `<div class="card">순매수(${D.asof} 까지)<div class="val">${eok(a.n5)} <span style="font-size:12px;color:#9aa0a6">5일</span></div>20일 ${eok(a.n20)} · 외국인 20일 ${eok(sumLast(fw, 20))} · 기관 20일 ${eok(sumLast(iw, 20))}</div>` +
    `<div class="card">섹터 정리 시가총액<div class="val">${nn(a.cap) ? (a.cap / 10000).toFixed(0) + '조' : '-'}</div>20일 변화 <span class="${cls(a.c20)}">${sg(a.c20)}%</span></div>`;
}
// ---------- 차트
let c1 = null, c2 = null;
Chart.defaults.color = '#9aa0a6'; Chart.defaults.borderColor = '#23262e';
function xTicks() { return {color: '#9aa0a6', maxTicksLimit: 8, maxRotation: 0}; }
function renderCharts() {
  const g = getGroup(TGT), st = groupStats(g), s0 = PERIOD ? Math.max(0, N - PERIOD) : 0, L = D.dates.slice(s0);
  const gold = st.f.map((v, i) => i > 0 && nn(v) && nn(st.s[i]) && nn(st.f[i - 1]) && nn(st.s[i - 1]) && st.f[i - 1] <= st.s[i - 1] && v > st.s[i] ? v : null);
  const dead = st.f.map((v, i) => i > 0 && nn(v) && nn(st.s[i]) && nn(st.f[i - 1]) && nn(st.s[i - 1]) && st.f[i - 1] >= st.s[i - 1] && v < st.s[i] ? v : null);
  const lim = Math.max(1, ...st.f.slice(s0).concat(st.s.slice(s0)).filter(nn).map(Math.abs)) * 2.2;
  if (c1) c1.destroy();
  c1 = new Chart($('c1'), {data: {labels: L, datasets: [
    {type: 'bar', label: '일별(bp)', data: st.r.slice(s0).map(v => nn(v) ? Math.max(-lim, Math.min(lim, v)) : null), backgroundColor: st.r.slice(s0).map(v => v >= 0 ? '#ff878744' : '#74a7ff44'), order: 3},
    {type: 'line', label: XF + '일 평균', data: st.f.slice(s0), borderColor: '#ffa94d', borderWidth: 1.8, pointRadius: 0, order: 1},
    {type: 'line', label: XS + '일 평균', data: st.s.slice(s0), borderColor: '#4dabf7', borderWidth: 1.8, pointRadius: 0, order: 1},
    {type: 'line', label: '골드 ▲', data: gold.slice(s0), showLine: false, pointStyle: 'triangle', pointRadius: 7, backgroundColor: '#ff6b6b', order: 0},
    {type: 'line', label: '데드 ▼', data: dead.slice(s0), showLine: false, pointStyle: 'triangle', rotation: 180, pointRadius: 7, backgroundColor: '#4dabf7', order: 0}
  ]}, options: {responsive: true, maintainAspectRatio: false, animation: false, interaction: {mode: 'index', intersect: false},
    plugins: {legend: {labels: {color: '#e6e6e6', boxWidth: 12, font: {size: 11}}}, tooltip: {filter: c => c.parsed.y !== null, callbacks: {label: c => c.dataset.label + ': ' + (c.dataset.type === 'bar' ? sg(st.r[s0 + c.dataIndex], 2) : sg(c.parsed.y, 2)) + 'bp'}}},
    scales: {x: {ticks: xTicks(), grid: {display: false}}, y: {min: -lim, max: lim, ticks: {color: '#9aa0a6', callback: v => v.toFixed(1)}, grid: {color: '#23262e'}}}}});
  $('t1').textContent = `${g.name} · ${WHO === '합산' ? '외국인+기관' : WHO} · ${HOW === 'cap' ? '섹터 시총 합' : '종목 평균'} 기준`;
  $('xNow').innerHTML = st.x ? `<b style="color:${st.x.up ? '#ff8787' : '#74a7ff'}">지금 ${st.x.up ? '골드' : '데드'} ${st.x.days}거래일째</b>${st.x.since ? ' (' + st.x.since + ' 교차)' : ''} · ${XF}일 ${sg(st.fast, 2)}bp vs ${XS}일 ${sg(st.slow, 2)}bp` : '';
  // 시총 + 누적 순매수
  const cum = a => { let s = 0; return a.slice(s0).map(v => { s += nn(v) ? v : 0; return s; }); };
  if (c2) c2.destroy();
  c2 = new Chart($('c2'), {type: 'line', data: {labels: L, datasets: [
    {label: '시가총액(조)', data: g.cap.slice(s0).map(v => nn(v) ? v / 10000 : null), borderColor: '#e6e6e6', borderWidth: 1.8, pointRadius: 0, yAxisID: 'y'},
    {label: '누적 외국인+기관(억)', data: cum(netOf(g, '합산')), borderColor: '#ff6b6b', borderWidth: 1.6, pointRadius: 0, yAxisID: 'y2'},
    {label: '누적 외국인(억)', data: cum(g.net['외국인']), borderColor: '#ffa94d', borderWidth: 1.2, borderDash: [4, 3], pointRadius: 0, yAxisID: 'y2'},
    {label: '누적 기관(억)', data: cum(g.net['기관']), borderColor: '#69db7c', borderWidth: 1.2, borderDash: [4, 3], pointRadius: 0, yAxisID: 'y2'}
  ]}, options: {responsive: true, maintainAspectRatio: false, animation: false, interaction: {mode: 'index', intersect: false},
    plugins: {legend: {labels: {color: '#e6e6e6', boxWidth: 12, font: {size: 11}}}, tooltip: {callbacks: {label: c => c.dataset.label + ': ' + (c.dataset.yAxisID === 'y' ? c.parsed.y.toFixed(1) + '조' : Math.round(c.parsed.y).toLocaleString() + '억')}}},
    scales: {x: {ticks: xTicks(), grid: {display: false}}, y: {position: 'left', ticks: {color: '#c9ccd1'}, grid: {color: '#23262e'}},
      y2: {position: 'right', grid: {display: false}, ticks: {color: '#ff8787', callback: v => Math.round(v).toLocaleString()}}}}});
  const capChg = nn(g.cap[s0]) && nn(g.cap[N - 1]) ? (g.cap[N - 1] / g.cap[s0] - 1) * 100 : null;
  $('t2').innerHTML = `${g.name} · ${D.dates[s0]} 이후 시총 <span class="${cls(capChg)}">${sg(capChg)}%</span>, 누적 외국인+기관 ${eok(cum(netOf(g, '합산')).slice(-1)[0])}`;
}
// ---------- 순위표
function renderTable() {
  const st = D.sectors.map(groupStats);
  const rankBy = (arr, key) => { const o = arr.filter(x => nn(x[key])).sort((a, b) => b[key] - a[key]); const m = {}; o.forEach((x, i) => m[x.g.name] = i + 1); return m; };
  const rk = rankBy(st, 'slow'), rk5 = rankBy(st, 'slow5');
  const keyf = {name: x => x.g.name, n: x => x.g.n, fast: x => x.fast, slow: x => x.slow, x: x => x.x ? (x.x.up ? 1 : -1) * (1000 - x.x.days) : null, n5: x => x.n5, n20: x => x.n20, c20: x => x.c20, cap: x => x.cap, rkchg: x => nn(rk5[x.g.name]) && nn(rk[x.g.name]) ? rk5[x.g.name] - rk[x.g.name] : null};
  const rows = st.slice().sort((a, b) => { const f = keyf[SORT], va = f(a), vb = f(b); if (SORT === 'name') return DESC ? vb.localeCompare(va) : va.localeCompare(vb); const x = nn(va) ? va : (DESC ? -1e18 : 1e18), y = nn(vb) ? vb : (DESC ? -1e18 : 1e18); return DESC ? y - x : x - y; });
  const H = (k, t) => `<th data-s="${k}"${SORT === k ? ' class="sorted"' : ''}>${t}${SORT === k ? (DESC ? ' ▼' : ' ▲') : ''}</th>`;
  let h = '<tr>' + `<th class="nosort">${XS}일 순위</th>` + H('name', '섹터') + H('n', '종목') + H('fast', XF + '일 평균') + H('slow', XS + '일 평균') + H('x', '교차(' + XF + '/' + XS + ')') + H('n5', '5일 순매수') + H('n20', '20일 순매수') + H('c20', '20일 시총') + H('cap', '시총') + H('rkchg', '1주 전 순위') + '</tr>';
  rows.forEach(x => {
    const nm = x.g.name, chg = nn(rk5[nm]) && nn(rk[nm]) ? rk5[nm] - rk[nm] : null;
    h += `<tr class="sec${TGT === nm ? ' sel' : ''}" data-name="${nm}"><td>${rk[nm] || '-'}</td><td>${nm}</td><td>${x.g.n}</td>` +
      `<td class="${cls(x.fast)}">${sg(x.fast, 2)}</td><td class="${cls(x.slow)}"><b>${sg(x.slow, 2)}</b></td>` +
      `<td style="color:${x.x && x.x.up ? '#ff8787' : '#74a7ff'}">${x.x ? (x.x.up ? '▲ 골드 ' : '▼ 데드 ') + x.x.days + '일' + (x.x.days <= 3 ? ' <span style="color:#ffd43b;font-size:11px">새로</span>' : '') : '-'}</td>` +
      `<td class="${cls(x.n5)}">${eok(x.n5)}</td><td class="${cls(x.n20)}">${eok(x.n20)}</td><td class="${cls(x.c20)}">${sg(x.c20)}%</td><td>${nn(x.cap) ? (x.cap / 10000).toFixed(1) + '조' : '-'}</td>` +
      `<td>${rk5[nm] || '-'}${nn(chg) && chg !== 0 ? ` <span class="${chg > 0 ? 'pos' : 'neg'}" style="font-size:11px">${chg > 0 ? '▲' : '▼'}${Math.abs(chg)}</span>` : ''}</td></tr>`;
    if (OPEN === nm) h += membersRow(x.g);
  });
  $('tbl').innerHTML = h;
  $('tbl').querySelectorAll('th[data-s]').forEach(th => th.onclick = () => { const s = th.dataset.s; if (SORT === s) DESC = !DESC; else { SORT = s; DESC = s !== 'name'; } renderTable(); });
  $('tbl').querySelectorAll('tr.sec').forEach(tr => tr.onclick = () => { const nm = tr.dataset.name; OPEN = OPEN === nm ? null : nm; TGT = nm; $('selT').value = nm; renderCharts(); renderTable(); });
}
function membersRow(g) {
  const k = WHO;
  const list = g.codes.map(c => ({c, ...D.stocks[c]})).filter(x => x.name).sort((a, b) => (nn(b[k][1]) ? b[k][1] : -1e9) - (nn(a[k][1]) ? a[k][1] : -1e9));
  const cell = x => `<span style="display:inline-block;margin:2px 14px 2px 0;white-space:nowrap">${x.name} <b class="${cls(x[k][1])}">${sg(x[k][1], 2)}</b><span style="color:#7c828b">bp</span> · 20일 <span class="${cls(x[k][3])}">${eok(x[k][3])}</span> · 주가 <span class="${cls(x.r20)}">${sg(x.r20)}%</span></span>`;
  return `<tr class="members"><td colspan="11">${g.name} 소속 종목 - ${k === '합산' ? '외국인+기관' : k} 15일 평균 수급 비율 높은 순(종목 표는 8·15일 고정) · 20일 순매수 · 20일 주가<br>${list.map(cell).join('')}</td></tr>`;
}
function renderAll() { renderCards(); renderCharts(); renderTable(); }
// ---------- 설정
$('selF').innerHTML = WINS.map(w => `<option value="${w}">${w}일</option>`).join(''); $('selF').value = XF;
$('selS').innerHTML = WINS.map(w => `<option value="${w}">${w}일</option>`).join(''); $('selS').value = XS;
$('selT').innerHTML = '<option value="">섹터 정리 전체</option>' + D.sectors.map(s => `<option value="${s.name}">${s.name}</option>`).join('');
$('selF').onchange = () => { XF = +$('selF').value; renderAll(); };
$('selS').onchange = () => { XS = +$('selS').value; renderAll(); };
$('selT').onchange = () => { TGT = $('selT').value; renderCharts(); renderTable(); };
const seg = (id, fn) => $(id).onclick = e => { const b = e.target.closest('button'); if (!b) return; document.querySelectorAll('#' + id + ' button').forEach(x => x.classList.toggle('active', x === b)); fn(b); };
seg('segWho', b => { WHO = b.dataset.v; renderAll(); });
seg('segHow', b => { HOW = b.dataset.v; renderAll(); });
seg('segP', b => { PERIOD = +b.dataset.p; renderCharts(); });
$('study').innerHTML = `<b>수급과 주가 (${D.dates[1]} ~ ${D.asof} 검증, 섹터 정리 42섹터)</b><br>` +
  '· <b>같은 날</b>: 수급과 주가는 강하게 같이 움직입니다(섹터별 일간 상관 0.5~0.7, 시장 전체 0.71). 20일 누적으로 봐도 순매수가 많았던 섹터일수록 그 기간 시총도 많이 올랐습니다(섹터 간 순위상관 +0.53, 매번 양수). 이 관계는 기관(+0.56)이 외국인(+0.16)보다 훨씬 뚜렷합니다.<br>' +
  '· <b>수급이 주가를 앞서나</b>: 약합니다. 15일 평균 수급 상위 1/5 섹터가 하위 1/5보다 이후 20일 평균 +1.7~2.0%p 더 올랐지만, 겹치지 않는 20일 구간 20번 중 이긴 건 12번(60%) 정도라 우연과 잘 구분되지 않습니다. 종목 하나하나 단위로는 예측력이 없었습니다(순위상관 0 근처).<br>' +
  '· <b>주가가 수급을 끈다</b>: 최근 5·20일 많이 오른 섹터로 이후 수급이 더 들어옵니다(특히 기관 - 추종 매매, 순위상관 +0.15). 외국인은 약간 반대(오른 섹터를 덜 삼).<br>' +
  '· <b>교차·괴리</b>: 섹터 수급의 8일/15일 골드·데드크로스 뒤 이후 5·20일 상대수익은 골드와 데드가 비슷했습니다(이긴 비율 둘 다 45% 안팎) - 교차 자체로는 매매 신호가 안 됩니다. "주가는 약한데 수급은 강한" 섹터도 이후 계속 약했고(-0.7%p), 주가와 수급이 함께 강한 섹터가 가장 나았습니다(+2.2%p, 외국인 기준 +2.9%p).<br>' +
  '· <b>쓰는 법</b>: 수급은 "지금 어느 섹터에 돈이 들어오고 있나"를 확인하는 지표로 보고, 단독 매매 신호보다는 가격 추세(섹터 이평선 위 비율)와 같은 방향인지 확인하는 용도가 맞습니다. 기간이 2025-01부터라 표본이 짧다는 점도 감안하세요.';
renderAll();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
