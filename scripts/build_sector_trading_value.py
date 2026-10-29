"""
섹터별 거래대금 페이지(docs/sector_trading_value.html) - 2026-10-29 사용자 요청
("섹터별로 거래대금 추이를 알고 싶거든 막대로 일별 거래대금을 보여주고, 외에는 섹터 20일 추이를 보여주도록").

계산
- 섹터: data/manual/섹터 정리.xlsx(build_relative_strength.assign_sectors)의 종목만. 섹터 거래대금 = 소속 종목 일별 거래대금 합(억원).
- 거래대금 원본: fetch_trading_value.py가 쌓는 data/screening/cache/trading_value.pkl(KRX 일별 전 종목 ACC_TRDVAL).
- 막대 = 일별 거래대금, 선 = 20일 이동평균(기본, 5/10/20/60일 선택). 배수 = 오늘 거래대금 ÷ 직전 20일 평균(오늘 제외).
- 종목 수는 날짜별로 다를 수 있음(상장/상폐) - 해당 날 값이 없는 종목은 0으로 둔다(상장 전).
파이프라인: run_pipeline.py에서 build_sector_ma_breadth 다음.
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from build_relative_strength import assign_sectors  # noqa: E402

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
STORE = os.path.join(BASE_DIR, "data", "screening", "cache", "trading_value.pkl")
SECTOR_MAP_PATH = os.path.join(BASE_DIR, "data", "sector_map.csv")
OUT_PATH = os.path.join(BASE_DIR, "docs", "sector_trading_value.html")
SUMMARY_PATH = os.path.join(BASE_DIR, "data", "screening", "sector_trading_value_summary.csv")
MIN_MEMBERS = 3
AVG_N = 20


def main():
    tv = pd.read_pickle(STORE)["df"].sort_index()
    sm = pd.read_csv(SECTOR_MAP_PATH, dtype=str)
    meta = pd.DataFrame({"name": sm.iloc[:, 1].values}, index=sm.iloc[:, 0].str.zfill(6).values)
    meta = meta[~meta.index.duplicated()]
    sectors, _ = assign_sectors(meta)
    members = {}
    for code, (sec, src) in sectors.items():
        if src == "U" and code in tv.columns:
            members.setdefault(sec, []).append(code)

    dates = [d.strftime("%Y-%m-%d") for d in tv.index]
    out = {"asof": dates[-1], "dates": dates, "avgN": AVG_N, "sectors": [], "stocks": {}}
    allcodes = sorted({c for v in members.values() for c in v})
    out["all"] = [round(float(x) / 1e8) for x in tv[allcodes].sum(axis=1, min_count=1).fillna(0).values]
    out["market"] = [round(float(x) / 1e8) for x in tv.sum(axis=1).values]
    rows = []
    for sec in sorted(members):
        cs = members[sec]
        if len(cs) < MIN_MEMBERS:
            continue
        s = tv[cs].sum(axis=1, min_count=1).fillna(0) / 1e8
        out["sectors"].append({"name": sec, "n": len(cs), "codes": cs, "v": [round(float(x)) for x in s.values]})
        base = s.iloc[-1 - AVG_N:-1].mean()
        rows.append({"sector": sec, "n": len(cs), "today_eok": round(float(s.iloc[-1])), "avg20_eok": round(float(base)),
                     "ratio": round(float(s.iloc[-1] / base), 2) if base > 0 else np.nan})
    last20 = tv[allcodes].iloc[-1 - AVG_N:-1].mean()
    for c in allcodes:
        out["stocks"][c] = {"name": meta["name"].get(c, c), "t": round(float(tv[c].iloc[-1]) / 1e8, 1) if pd.notna(tv[c].iloc[-1]) else None,
                            "a": round(float(last20[c]) / 1e8, 1) if pd.notna(last20[c]) else None}
    os.makedirs(os.path.dirname(SUMMARY_PATH), exist_ok=True)
    pd.DataFrame(rows).sort_values("ratio", ascending=False).to_csv(SUMMARY_PATH, index=False, encoding="utf-8-sig")
    html = (TEMPLATE.replace("__DATA__", json.dumps(out, ensure_ascii=False, separators=(",", ":")))
            .replace("__UPDATED__", datetime.now().strftime("%Y-%m-%d %H:%M")).replace("__ASOF__", out["asof"]))
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"섹터 {len(out['sectors'])}개, 종목 {len(allcodes)}개, 기준일 {out['asof']} | 저장 완료: {OUT_PATH} ({len(html) // 1024}KB)")


TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>섹터 거래대금</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body { font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  a.back { color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }
  h1 { font-size:20px; margin:8px 0 4px 0; } h1 .sub { font-size:13px; color:#9aa0a6; font-weight:normal; margin-left:6px; }
  .updated { color:#9aa0a6; font-size:13px; margin-bottom:14px; }
  .ctl { display:flex; gap:14px; flex-wrap:wrap; align-items:center; font-size:12px; color:#c9ccd1; margin-bottom:14px; }
  .ctl select { background:#0f1115; color:#e6e6e6; border:1px solid #2a2e37; border-radius:6px; padding:4px 6px; font-family:inherit; font-size:12px; }
  .seg button { background:#1a1d24; border:1px solid #2a2e37; color:#c9ccd1; padding:5px 11px; border-radius:999px; cursor:pointer; font-size:12px; font-family:inherit; margin-right:4px; }
  .seg button.active { background:#4dabf7; color:#0f1115; border-color:#4dabf7; font-weight:bold; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; margin-bottom:16px; }
  .card { background:#1a1d24; border:1px solid #23262e; border-radius:12px; padding:12px 14px; font-size:12px; color:#9aa0a6; }
  .card .val { font-size:22px; font-weight:700; color:#e6e6e6; margin:4px 0; }
  .box { background:#12151b; border:1px solid #23262e; border-radius:12px; padding:14px 16px; margin-bottom:16px; }
  .box h2 { font-size:15px; margin:0 0 6px 0; } .box .desc { font-size:12px; color:#9aa0a6; margin-bottom:8px; line-height:1.6; }
  .chart-wrap { position:relative; height:380px; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  th, td { padding:6px 8px; border-bottom:1px solid #23262e; text-align:right; white-space:nowrap; }
  th:first-child, td:first-child { text-align:left; }
  th { color:#9aa0a6; font-weight:normal; cursor:pointer; }
  tr.sec:hover { background:#1a1f28; cursor:pointer; } tr.sel { background:#1c2433; }
  .pos { color:#ff8787; } .neg { color:#74a7ff; }
  .tblwrap { overflow-x:auto; }
  .note { font-size:12px; color:#9aa0a6; line-height:1.7; background:#1a1d24; border-radius:10px; padding:12px 16px; }
  .note b { color:#ffa94d; }
  @media (max-width: 640px) { body { padding:16px; } .chart-wrap { height:280px; } }
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <a class="back" href="sector_ma_breadth.html">섹터 이평선 위 비율 &rarr;</a>
  <h1>섹터 거래대금<span class="sub">섹터 정리 기준, 소속 종목 거래대금 합계(코스피+코스닥)</span></h1>
  <div class="updated">최종 갱신: __UPDATED__ &middot; 데이터 기준일: __ASOF__ &middot; 매일 자동 갱신</div>

  <div class="ctl">
    <span>섹터</span><select id="sel"></select>
    <span>이동평균</span><span class="seg" id="segA"><button data-a="5">5일</button><button data-a="10">10일</button><button data-a="20" class="active">20일</button><button data-a="60">60일</button></span>
    <span>기간</span><span class="seg" id="segP"><button data-p="63">3달</button><button data-p="126" class="active">6달</button><button data-p="250">1년</button><button data-p="0">전체</button></span>
    <span>단위</span><span class="seg" id="segU"><button data-u="eok" class="active">억원</button><button data-u="pct">시장 대비 %</button></span>
  </div>

  <div class="cards" id="cards"></div>

  <div class="box">
    <h2 id="cTitle">일별 거래대금</h2>
    <div class="desc">막대 = 일별 거래대금, 선 = 이동평균. 막대가 선 위로 크게 올라오면 평소보다 거래가 몰린 날입니다.</div>
    <div class="chart-wrap"><canvas id="cBar"></canvas></div>
  </div>

  <div class="box">
    <h2>섹터 순위 <span style="font-size:12px;font-weight:normal;color:#9aa0a6">행을 누르면 위 차트가 바뀌고, 머리글을 누르면 정렬</span></h2>
    <div class="tblwrap"><table id="tbl"></table></div>
  </div>

  <div class="box">
    <h2 id="stkTitle">구성종목 거래대금</h2>
    <div class="tblwrap"><table id="stk"></table></div>
  </div>

  <div class="note">
    <b>계산</b>: 섹터는 '섹터 정리' 엑셀의 종목만 씁니다. 섹터 거래대금 = 소속 종목의 그날 거래대금(코스피·코스닥 정규장+시간외 포함 KRX 일별 매매정보) 합계입니다.<br>
    <b>배수</b> = 그날 거래대금 ÷ 직전 20거래일 평균(그날 제외). 1.5배 이상이면 평소보다 많이 몰린 날, 0.7배 이하면 한산한 날입니다.<br>
    <b>시장 대비 %</b> = 섹터 거래대금 ÷ 코스피·코스닥 전체 거래대금. 전체 거래대금이 늘어서 같이 늘어난 것인지, 이 섹터로만 몰린 것인지 가를 때 봅니다.
  </div>

<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
const nn = v => v !== null && v !== undefined && !isNaN(v);
let SEC = '전체', MA = 20, PERIOD = 126, UNIT = 'eok', SORT = 'ratio', DESC = true;
const series = name => name === '전체' ? D.all : D.sectors.find(s => s.name === name).v;
const per = a => UNIT === 'pct' ? a.map((x, i) => D.market[i] ? x / D.market[i] * 100 : null) : a;
function ma(a, n) { const o = []; let s = 0; for (let i = 0; i < a.length; i++) { s += a[i]; if (i >= n) s -= a[i - n]; o.push(i >= n - 1 ? s / n : null); } return o; }
const fmtE = v => nn(v) ? (v >= 10000 ? (v / 10000).toFixed(2) + '조' : Math.round(v).toLocaleString() + '억') : '-';
const fmtV = v => UNIT === 'pct' ? (nn(v) ? v.toFixed(1) + '%' : '-') : fmtE(v);
const base20 = a => { const x = a.slice(-1 - D.avgN, -1); return x.reduce((p, c) => p + c, 0) / x.length; };
function stats() {
  const rows = D.sectors.map(s => {
    const t = s.v[s.v.length - 1], b = base20(s.v), w = s.v.slice(-5).reduce((p, c) => p + c, 0) / 5;
    const mk = D.market[D.market.length - 1];
    return {name: s.name, n: s.n, today: t, avg: b, ratio: b > 0 ? t / b : null, wk: b > 0 ? w / b : null, share: mk ? t / mk * 100 : null};
  });
  return rows;
}
function renderCards() {
  const a = series(SEC), t = a[a.length - 1], b = base20(a), w = a.slice(-5).reduce((p, c) => p + c, 0) / 5, m = D.market[D.market.length - 1];
  const mx = Math.max(...a.slice(-250)), rank = a.slice(-250).filter(x => x > t).length + 1;
  $('cards').innerHTML =
    `<div class="card">${SEC} · 오늘 거래대금<div class="val">${fmtE(t)}</div>시장의 ${(t / m * 100).toFixed(1)}%</div>` +
    `<div class="card">직전 20일 평균<div class="val">${fmtE(b)}</div>오늘은 <b class="${t >= b ? 'pos' : 'neg'}">${(t / b).toFixed(2)}배</b></div>` +
    `<div class="card">최근 5일 평균<div class="val">${fmtE(w)}</div>20일 평균의 <b class="${w >= b ? 'pos' : 'neg'}">${(w / b).toFixed(2)}배</b></div>` +
    `<div class="card">최근 1년 중 순위<div class="val">${rank}위</div>1년 최대 ${fmtE(mx)}</div>`;
}
let chart = null;
function renderChart() {
  const a = per(series(SEC)), st = PERIOD ? Math.max(0, D.dates.length - PERIOD) : 0;
  const line = ma(a, MA);
  const labels = D.dates.slice(st);
  const vals = a.slice(st), lv = line.slice(st);
  const col = vals.map((v, i) => nn(lv[i]) && v >= lv[i] * 1.5 ? '#ff6b6b' : (nn(lv[i]) && v >= lv[i] ? '#ffa94d' : '#4a6fa5'));
  $('cTitle').textContent = `${SEC} 일별 거래대금${UNIT === 'pct' ? ' (시장 대비 %)' : ''} · ${MA}일 평균선`;
  if (chart) chart.destroy();
  chart = new Chart($('cBar'), {data: {labels, datasets: [
    {type: 'bar', label: '일별', data: vals, backgroundColor: col, order: 2},
    {type: 'line', label: `${MA}일 평균`, data: lv, borderColor: '#ffd43b', borderWidth: 2, pointRadius: 0, tension: 0.15, order: 1}]},
    options: {responsive: true, maintainAspectRatio: false, animation: false, interaction: {mode: 'index', intersect: false},
      plugins: {legend: {labels: {color: '#c9ccd1'}}, tooltip: {callbacks: {label: c => `${c.dataset.label}: ${fmtV(c.parsed.y)}`}}},
      scales: {x: {ticks: {color: '#9aa0a6', maxTicksLimit: 10}, grid: {display: false}}, y: {ticks: {color: '#9aa0a6', callback: v => UNIT === 'pct' ? v + '%' : (v >= 10000 ? (v / 10000) + '조' : v.toLocaleString())}, grid: {color: '#23262e'}}}}});
}
function renderTable() {
  const rows = stats().sort((x, y) => (SORT === 'name' ? x.name.localeCompare(y.name, 'ko') : (nn(y[SORT]) ? y[SORT] : -1) - (nn(x[SORT]) ? x[SORT] : -1)) * (DESC ? 1 : -1) * (SORT === 'name' ? (DESC ? -1 : 1) : 1));
  const H = [['name', '섹터'], ['n', '종목'], ['today', '오늘'], ['avg', '20일 평균'], ['ratio', '배수'], ['wk', '5일/20일'], ['share', '시장 비중']];
  let h = '<tr>' + H.map(([k, l]) => `<th data-k="${k}">${l}${SORT === k ? (DESC ? ' ▼' : ' ▲') : ''}</th>`).join('') + '</tr>';
  for (const r of rows) {
    const cl = v => nn(v) ? (v >= 1.5 ? 'pos' : (v <= 0.7 ? 'neg' : '')) : '';
    h += `<tr class="sec ${r.name === SEC ? 'sel' : ''}" data-n="${r.name}"><td>${r.name}</td><td>${r.n}</td><td>${fmtE(r.today)}</td><td>${fmtE(r.avg)}</td>` +
         `<td class="${cl(r.ratio)}"><b>${nn(r.ratio) ? r.ratio.toFixed(2) + '배' : '-'}</b></td><td class="${cl(r.wk)}">${nn(r.wk) ? r.wk.toFixed(2) + '배' : '-'}</td><td>${r.share.toFixed(1)}%</td></tr>`;
  }
  $('tbl').innerHTML = h;
  $('tbl').querySelectorAll('th').forEach(th => th.onclick = () => { const k = th.dataset.k; if (SORT === k) DESC = !DESC; else { SORT = k; DESC = true; } renderTable(); });
  $('tbl').querySelectorAll('tr.sec').forEach(tr => tr.onclick = () => { SEC = tr.dataset.n; $('sel').value = SEC; renderAll(); window.scrollTo({top: 0, behavior: 'smooth'}); });
}
function renderStocks() {
  const codes = SEC === '전체' ? Object.keys(D.stocks) : D.sectors.find(s => s.name === SEC).codes;
  const rows = codes.map(c => ({c, ...D.stocks[c]})).filter(x => nn(x.t)).sort((a, b) => b.t - a.t).slice(0, 30);
  $('stkTitle').textContent = `${SEC} 구성종목 거래대금 (오늘 기준 상위 ${rows.length}개)`;
  let h = '<tr><th>종목</th><th>오늘</th><th>20일 평균</th><th>배수</th></tr>';
  for (const r of rows) {
    const q = nn(r.a) && r.a > 0 ? r.t / r.a : null;
    h += `<tr><td>${r.name}</td><td>${fmtE(r.t)}</td><td>${fmtE(r.a)}</td><td class="${nn(q) && q >= 1.5 ? 'pos' : (nn(q) && q <= 0.7 ? 'neg' : '')}">${nn(q) ? q.toFixed(2) + '배' : '-'}</td></tr>`;
  }
  $('stk').innerHTML = h;
}
function renderAll() { renderCards(); renderChart(); renderTable(); renderStocks(); }
$('sel').innerHTML = ['전체', ...D.sectors.map(s => s.name)].map(n => `<option>${n}</option>`).join('');
$('sel').onchange = e => { SEC = e.target.value; renderAll(); };
function seg(id, key, fn) { $(id).onclick = e => { const b = e.target.closest('button'); if (!b) return; fn(b.dataset[key]); document.querySelectorAll('#' + id + ' button').forEach(x => x.classList.toggle('active', x === b)); renderAll(); }; }
seg('segA', 'a', v => MA = +v); seg('segP', 'p', v => PERIOD = +v); seg('segU', 'u', v => UNIT = v);
renderAll();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
