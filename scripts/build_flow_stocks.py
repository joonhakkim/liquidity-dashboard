"""
종목 수급 페이지(docs/flow_stocks.html) - 2026-10-19 사용자 요청("개별 종목단으로 한번 만들어볼 수 있나, 따로 빼서, 섹터에 집어넣지 말고").
수급 분석(섹터) 페이지와 같은 지표를 종목마다:
- 수급 비율(bp) = 순매수(외국인·기관·합산) / 전일 시가총액 x 1e4, 8일·15일 이동평균, 8/15 골드·데드 상태와 유지일,
  수급 볼린저(15일선의 60일 평균 ± 2σ) 과매수·과매도와 유지일, 5·20일 순매수(억), 20일 시총 변화(=주가), 시총.
- 대상: 수급.xlsx와 '기업 밴드 찾기 2' 시총이 둘 다 있는 전 종목(섹터 정리 밖 종목 포함). 섹터는 표시용(섹터 정리 우선, 없으면 네이버 업종).
- 표는 Python이 기본 설정(8/15, 60일·2σ)으로 미리 계산한 최신값, 종목을 누르면 docs/flow_stock_data/{코드}.json(일별 외국인·기관 순매수 백만원, 시총 억)을
  불러와 차트 3개(수급 방향 / 볼린저 / 시총과 누적 순매수)를 그린다.
검증 메모(2025-01~2026-10, scratchpad flow/flow1·flow3·flow4.py): 종목 단위는 섹터보다 약함 - 15일 평균 순위의 이후 수익 예측력 ~0,
극단값만: 외국인 과매수 종목은 이후 20일 강함(+1.2/+1.1%p), 외국인 과매도는 약함, 기관 과매도 종목은 오히려 반등(+0.9/+0.6%p).
파이프라인: run_pipeline.py에서 build_flow_analysis.py 다음.
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import build_flow_analysis as bf  # noqa: E402
import build_sector_ma_breadth as bsm  # noqa: E402
from build_relative_strength import assign_sectors  # noqa: E402

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
OUT_PATH = os.path.join(BASE_DIR, "docs", "flow_stocks.html")
DETAIL_DIR = os.path.join(BASE_DIR, "docs", "flow_stock_data")
NAVER_SECTOR_PATH = os.path.join(BASE_DIR, "data", "sector_map.csv")
WHO = ["합산", "외국인", "기관"]
FAST, SLOW, BW, BK = 8, 15, 60, 2.0


def run_state(cond_up, cond_dn):
    """마지막 날 상태(1/-1/0)와 그 상태가 이어진 일수(배열 끝에서)."""
    st = np.where(cond_up, 1, np.where(cond_dn, -1, 0))
    k = len(st) - 1
    j = k
    while j - 1 >= 0 and st[j - 1] == st[k]:
        j -= 1
    return int(st[k]), int(k - j + 1)


def main():
    F, I, fnames = bf.load_flows()
    caps = pd.read_pickle(bsm.CAP_PATH)
    caps.columns = [c[1:] if c.startswith("A") else c for c in caps.columns]
    caps = caps.sort_index()
    caps = caps[caps.notna().sum(axis=1) >= 0.5 * caps.notna().sum(axis=1).max()]
    idx = F.index
    cap = caps.reindex(idx.union(caps.index)).ffill().reindex(idx)
    codes = [c for c in F.columns if c in cap.columns and cap[c].notna().sum() > 30]
    cap = cap[codes]
    capL = cap.shift(1)
    has = capL.notna()
    net = {"외국인": F[codes].fillna(0).where(has) * 1e6, "기관": I[codes].fillna(0).where(has) * 1e6}
    net["합산"] = net["외국인"] + net["기관"]
    # 섹터(표시용): 섹터 정리 우선, 없으면 네이버 업종
    sm = pd.read_csv(NAVER_SECTOR_PATH, dtype=str)
    meta = pd.DataFrame({"name": sm["name"].values}, index=sm["code"].str.zfill(6).values)
    meta = meta[~meta.index.duplicated()]
    naver = dict(zip(sm["code"].str.zfill(6), sm["sector"]))
    sectors, _ = assign_sectors(meta)
    sec_of = {c: (s if src == "U" else None) for c, (s, src) in sectors.items()}
    k = len(idx) - 1
    r20 = (cap.iloc[-1] / cap.iloc[-21] - 1) * 100 if len(idx) > 21 else pd.Series(np.nan, index=codes)
    rows = []
    for c in codes:
        rec = {"c": c, "n": fnames.get(c) or meta["name"].get(c, c), "s": sec_of.get(c) or naver.get(c) or "-", "u": 1 if sec_of.get(c) else 0,
               "cap": int(round(cap[c].iloc[-1] / 1e8)) if np.isfinite(cap[c].iloc[-1]) else None,
               "r20": None if not np.isfinite(r20.get(c, np.nan)) else round(float(r20[c]), 1)}
        for w in WHO:
            r = (net[w][c] / capL[c] * 1e4).values
            rs = pd.Series(r)
            m8, m15 = rs.rolling(FAST, min_periods=7).mean().values, rs.rolling(SLOW, min_periods=12).mean().values
            mm = pd.Series(m15).rolling(BW, min_periods=48)
            z = (m15 - mm.mean().values) / pd.Series(mm.std().values).replace(0, np.nan).values
            ok = np.isfinite(m8) & np.isfinite(m15)
            xs, xd = run_state(ok & (m8 > m15), ok & (m8 <= m15)) if ok[-1] else (0, 0)
            zf = np.isfinite(z)
            bs, bd = run_state(zf & (z > BK), zf & (z < -BK)) if zf[-1] else (0, 0)
            nt = net[w][c].values / 1e8
            f = lambda v, d=2: None if v is None or not np.isfinite(v) else round(float(v), d)
            rec[w] = [f(m8[-1]), f(m15[-1]), xs, xd, f(z[-1]), bs, bd if bs else 0, f(np.nansum(nt[-5:]), 1), f(np.nansum(nt[-20:]), 1)]
        rows.append(rec)
    # 종목별 상세(차트용) - 날짜는 페이지에 한 번, 종목 파일엔 외국인·기관(백만원 정수)·시총(억)만
    os.makedirs(DETAIL_DIR, exist_ok=True)
    for c in codes:
        fv = F[c].reindex(idx).values; iv = I[c].reindex(idx).values; cv = cap[c].values / 1e8
        d = {"F": [None if not np.isfinite(v) else int(round(v)) for v in fv], "I": [None if not np.isfinite(v) else int(round(v)) for v in iv],
             "cap": [None if not np.isfinite(v) else int(round(v)) for v in cv]}
        with open(os.path.join(DETAIL_DIR, f"{c}.json"), "w", encoding="utf-8") as fh:
            json.dump(d, fh, separators=(",", ":"))
    out = {"asof": idx[-1].strftime("%Y-%m-%d"), "dates": [x.strftime("%Y-%m-%d") for x in idx], "rows": rows,
           "fast": FAST, "slow": SLOW, "bw": BW, "bk": BK}
    html = (TEMPLATE.replace("__UPDATED__", datetime.now().strftime("%Y-%m-%d %H:%M")).replace("__ASOF__", out["asof"])
            .replace("__FROM__", out["dates"][0]).replace("__DATA__", json.dumps(out, ensure_ascii=False, separators=(",", ":"))))
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"종목 {len(rows)}개, {idx[0].date()} ~ {idx[-1].date()} | 저장 완료: {OUT_PATH} ({len(html) // 1024}KB), 상세 {DETAIL_DIR}")


TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>종목 수급</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body { font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  a.back { color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }
  h1 { font-size:20px; margin:8px 0 4px 0; } h1 .sub { font-size:13px; color:#9aa0a6; font-weight:normal; margin-left:6px; }
  .updated { color:#9aa0a6; font-size:13px; margin-bottom:14px; }
  .ctl { display:flex; gap:10px; flex-wrap:wrap; align-items:center; font-size:12px; color:#c9ccd1; margin-bottom:12px; }
  .ctl select, .ctl input { background:#0f1115; color:#e6e6e6; border:1px solid #2a2e37; border-radius:6px; padding:4px 6px; font-family:inherit; font-size:12px; }
  .seg button { background:#1a1d24; border:1px solid #2a2e37; color:#c9ccd1; padding:5px 11px; border-radius:999px; cursor:pointer; font-size:12px; font-family:inherit; margin-right:4px; }
  .seg button.active { background:#4dabf7; color:#0f1115; border-color:#4dabf7; font-weight:bold; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; margin-bottom:14px; }
  .card { background:#1a1d24; border:1px solid #23262e; border-radius:12px; padding:12px 14px; font-size:12px; color:#9aa0a6; }
  .card .val { font-size:19px; font-weight:700; color:#e6e6e6; margin:4px 0; }
  .box { background:#12151b; border:1px solid #23262e; border-radius:12px; padding:14px 16px; margin-bottom:16px; }
  .box h2 { font-size:15px; margin:0 0 6px 0; } .desc { font-size:12px; color:#9aa0a6; margin-bottom:8px; line-height:1.6; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  th, td { padding:5px 7px; border-bottom:1px solid #23262e; text-align:right; white-space:nowrap; }
  th:nth-child(-n+3), td:nth-child(-n+3) { text-align:left; }
  th { color:#9aa0a6; font-weight:normal; cursor:pointer; position:sticky; top:0; background:#12151b; } th.sorted { color:#4dabf7; }
  tr.st { cursor:pointer; } tr.st:hover { background:#1a1f28; }
  .pos { color:#ff8787; } .neg { color:#74a7ff; } .sub { color:#7c828b; }
  .tblwrap { overflow:auto; max-height:640px; }
  .note { font-size:12px; color:#9aa0a6; line-height:1.75; background:#1a1d24; border-radius:10px; padding:12px 16px; } .note b { color:#ffa94d; }
  #overlay { display:none; position:fixed; inset:0; background:#000a; z-index:10; overflow:auto; }
  #overlay.open { display:block; }
  #panel { background:#12151b; border:1px solid #2a2e37; border-radius:12px; max-width:1100px; margin:30px auto; padding:16px 18px; }
  .cw { position:relative; height:260px; margin-bottom:14px; }
  .close { float:right; background:#2a2e37; color:#e6e6e6; border:0; border-radius:6px; padding:5px 12px; cursor:pointer; }
  @media (max-width: 640px) { body { padding:16px; } .cw { height:220px; } #panel { margin:0; border-radius:0; } }
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <a class="back" href="flow_analysis.html">섹터 수급 분석 &rarr;</a>
  <h1>종목 수급<span class="sub">종목별 외국인·기관 순매수의 시가총액 대비 비율</span></h1>
  <div class="updated">최종 갱신: __UPDATED__ &middot; 데이터 __FROM__ ~ __ASOF__ &middot; 수급.xlsx + 기업 밴드 찾기 2 시가총액 &middot; 표 기준 8일/15일 평균, 볼린저 15일선·60일·2σ</div>

  <div class="ctl">
    <span>주체</span><span class="seg" id="segWho"><button data-v="합산" class="active">외국인+기관</button><button data-v="외국인">외국인</button><button data-v="기관">기관</button></span>
    <span>시총</span><select id="selCap"><option value="0">전체</option><option value="300">300억+</option><option value="500">500억+</option><option value="1000" selected>1,000억+</option><option value="3000">3,000억+</option><option value="10000">1조+</option></select>
    <span>섹터</span><select id="selSec"></select>
    <span>상태</span><select id="selSt"><option value="">전체</option><option value="bu">볼린저 과매수</option><option value="bd">볼린저 과매도</option><option value="gn">골드 새로(3일 이내)</option><option value="dn">데드 새로(3일 이내)</option><option value="g">골드 전체</option><option value="d">데드 전체</option></select>
    <span>검색</span><input id="q" placeholder="종목명·코드" style="width:110px">
    <label><input type="checkbox" id="onlyU"> 섹터 정리 종목만</label>
  </div>
  <div class="cards" id="cards"></div>

  <div class="box">
    <h2>종목 순위 <span id="cnt" style="font-size:12px;font-weight:normal;color:#9aa0a6"></span></h2>
    <div class="desc">bp = 시가총액의 0.01%. 머리글을 누르면 정렬, 행을 누르면 차트. 15일 평균이 높을수록 시총 대비 많이 사들이는 중. 볼린저 z가 +2 위면 과매수(평소보다 이례적 매수), -2 아래면 과매도.</div>
    <div class="tblwrap"><table id="tbl"></table></div>
  </div>
  <div class="note" id="study"></div>

  <div id="overlay"><div id="panel">
    <button class="close" id="closeBtn">닫기</button>
    <h2 id="dTitle" style="font-size:16px;margin:0 0 4px 0"></h2>
    <div class="desc" id="dSub"></div>
    <div class="ctl"><span>차트 기간</span><span class="seg" id="segP"><button data-p="63">3달</button><button data-p="126" class="active">6달</button><button data-p="250">1년</button><button data-p="0">전체</button></span></div>
    <div class="desc">① 수급 방향 - 막대 = 일별 수급 비율(bp), 주황 = 8일 평균, 파랑 = 15일 평균, ▲ 골드 / ▼ 데드</div>
    <div class="cw"><canvas id="d1"></canvas></div>
    <div class="desc">② 수급 볼린저 - 15일선과 60일 평균 ± 2σ 밴드, ▲ 과매수 진입 / ▼ 과매도 진입</div>
    <div class="cw"><canvas id="d2"></canvas></div>
    <div class="desc">③ 시가총액(조·억, 왼쪽)과 차트 시작일부터 누적 순매수(억, 오른쪽) - 외국인·기관 따로는 범례를 눌러 켜기</div>
    <div class="cw"><canvas id="d3"></canvas></div>
  </div></div>

<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
const nn = v => v !== null && v !== undefined && !isNaN(v);
const N = D.dates.length;
const sg = (v, d = 1) => nn(v) ? (v >= 0 ? '+' : '') + v.toFixed(d) : '-';
const cls = v => nn(v) ? (v >= 0 ? 'pos' : 'neg') : '';
const eok = v => !nn(v) ? '-' : (Math.abs(v) >= 10000 ? (v / 10000).toFixed(2) + '조' : (Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : v.toFixed(1)) + '억');
let WHO = '합산', SORT = 'm15', DESC = true, LIMIT = 300;
const I = {m8: 0, m15: 1, xs: 2, xd: 3, z: 4, bs: 5, bd: 6, n5: 7, n20: 8};
const secs = [...new Set(D.rows.map(r => r.s))].sort();
$('selSec').innerHTML = '<option value="">전체</option>' + secs.map(s => `<option>${s}</option>`).join('');
function filtered() {
  const cap = +$('selCap').value, sec = $('selSec').value, st = $('selSt').value, q = $('q').value.trim().toLowerCase(), onlyU = $('onlyU').checked;
  return D.rows.filter(r => {
    const a = r[WHO];
    if (cap && !(r.cap >= cap)) return false;
    if (sec && r.s !== sec) return false;
    if (onlyU && !r.u) return false;
    if (q && !(r.n.toLowerCase().includes(q) || r.c.includes(q))) return false;
    if (st === 'bu' && a[I.bs] !== 1) return false;
    if (st === 'bd' && a[I.bs] !== -1) return false;
    if (st === 'gn' && !(a[I.xs] === 1 && a[I.xd] <= 3)) return false;
    if (st === 'dn' && !(a[I.xs] === -1 && a[I.xd] <= 3)) return false;
    if (st === 'g' && a[I.xs] !== 1) return false;
    if (st === 'd' && a[I.xs] !== -1) return false;
    return true;
  });
}
function render() {
  const rows = filtered();
  const key = {n: r => r.n, s: r => r.s, cap: r => r.cap, r20: r => r.r20, m8: r => r[WHO][I.m8], m15: r => r[WHO][I.m15], x: r => r[WHO][I.xs] * (1000 - r[WHO][I.xd]), z: r => r[WHO][I.z], n5: r => r[WHO][I.n5], n20: r => r[WHO][I.n20],
    nf: r => r['외국인'][I.n20], ni: r => r['기관'][I.n20]}[SORT];
  rows.sort((a, b) => { const va = key(a), vb = key(b); if (typeof va === 'string') return DESC ? vb.localeCompare(va) : va.localeCompare(vb); const x = nn(va) ? va : (DESC ? -1e18 : 1e18), y = nn(vb) ? vb : (DESC ? -1e18 : 1e18); return DESC ? y - x : x - y; });
  const H = (k, t) => `<th data-s="${k}"${SORT === k ? ' class="sorted"' : ''}>${t}${SORT === k ? (DESC ? ' ▼' : ' ▲') : ''}</th>`;
  let h = '<tr><th class="nosort">#</th>' + H('n', '종목') + H('s', '섹터') + H('cap', '시총') + H('m8', D.fast + '일 평균') + H('m15', D.slow + '일 평균') + H('x', '교차(' + D.fast + '/' + D.slow + ')') + H('z', '볼린저 z') + H('n5', '5일 순매수') + H('n20', '20일 순매수') + H('nf', '외국인 20일') + H('ni', '기관 20일') + H('r20', '20일 주가') + '</tr>';
  rows.slice(0, LIMIT).forEach((r, i) => {
    const a = r[WHO], bs = a[I.bs];
    h += `<tr class="st" data-c="${r.c}"><td class="sub">${i + 1}</td><td>${r.n} <span class="sub">${r.c}</span></td><td class="sub">${r.s}</td><td>${nn(r.cap) ? (r.cap >= 10000 ? (r.cap / 10000).toFixed(1) + '조' : r.cap.toLocaleString() + '억') : '-'}</td>` +
      `<td class="${cls(a[I.m8])}">${sg(a[I.m8], 2)}</td><td class="${cls(a[I.m15])}"><b>${sg(a[I.m15], 2)}</b></td>` +
      `<td style="color:${a[I.xs] === 1 ? '#ff8787' : '#74a7ff'}">${a[I.xs] ? (a[I.xs] === 1 ? '▲ 골드 ' : '▼ 데드 ') + a[I.xd] + '일' + (a[I.xd] <= 3 ? ' <span style="color:#ffd43b;font-size:11px">새로</span>' : '') : '-'}</td>` +
      `<td style="color:${bs === 1 ? '#ff8787' : bs === -1 ? '#74a7ff' : '#9aa0a6'}">${sg(a[I.z], 1)}${bs ? (bs === 1 ? ' 과매수 ' : ' 과매도 ') + a[I.bd] + '일' : ''}</td>` +
      `<td class="${cls(a[I.n5])}">${eok(a[I.n5])}</td><td class="${cls(a[I.n20])}">${eok(a[I.n20])}</td>` +
      `<td class="${cls(r['외국인'][I.n20])}">${eok(r['외국인'][I.n20])}</td><td class="${cls(r['기관'][I.n20])}">${eok(r['기관'][I.n20])}</td><td class="${cls(r.r20)}">${sg(r.r20)}%</td></tr>`;
  });
  $('tbl').innerHTML = h;
  $('cnt').textContent = `${rows.length.toLocaleString()}종목${rows.length > LIMIT ? ' (위 ' + LIMIT + '개만 표시 - 필터·정렬로 좁히세요)' : ''} · ${WHO === '합산' ? '외국인+기관' : WHO} 기준`;
  $('tbl').querySelectorAll('th[data-s]').forEach(th => th.onclick = () => { const s = th.dataset.s; if (SORT === s) DESC = !DESC; else { SORT = s; DESC = !(s === 'n' || s === 's'); } render(); });
  $('tbl').querySelectorAll('tr.st').forEach(tr => tr.onclick = () => openDetail(tr.dataset.c));
  // 카드: 지금 필터(시총·섹터) 안에서 과매수/과매도·새 교차 수
  const base = D.rows.filter(r => (!+$('selCap').value || r.cap >= +$('selCap').value) && (!$('selSec').value || r.s === $('selSec').value) && (!$('onlyU').checked || r.u));
  const cnt = f => base.filter(f).length, A = r => r[WHO];
  const top = (f, k) => base.filter(f).sort((x, y) => k(y) - k(x)).slice(0, 5).map(r => r.n).join(', ');
  $('cards').innerHTML =
    `<div class="card">볼린저 과매수 / 과매도<div class="val"><span class="pos">${cnt(r => A(r)[I.bs] === 1)}</span> / <span class="neg">${cnt(r => A(r)[I.bs] === -1)}</span>종목</div>지금 필터 ${base.length.toLocaleString()}종목 중</div>` +
    `<div class="card">새 골드 / 새 데드(3일 이내)<div class="val"><span class="pos">${cnt(r => A(r)[I.xs] === 1 && A(r)[I.xd] <= 3)}</span> / <span class="neg">${cnt(r => A(r)[I.xs] === -1 && A(r)[I.xd] <= 3)}</span>종목</div>${D.fast}일 평균이 ${D.slow}일 평균을 뚫은 종목</div>` +
    `<div class="card">15일 평균 상위<div class="val" style="font-size:13px;line-height:1.5">${top(r => nn(A(r)[I.m15]), r => A(r)[I.m15])}</div></div>` +
    `<div class="card">20일 순매수 금액 상위<div class="val" style="font-size:13px;line-height:1.5">${top(r => nn(A(r)[I.n20]), r => A(r)[I.n20])}</div></div>`;
}
// ---------- 종목 차트
let P = 126, CUR = null, ch = {}, D3SHOW = {2: false, 3: false};
function ma(a, w, need) { return a.map((_, i) => { if (i < w - 1) return null; let s = 0, c = 0; for (let j = i - w + 1; j <= i; j++) if (nn(a[j])) { s += a[j]; c++; } return c >= need ? s / c : null; }); }
function rollMS(a, w, need) { const m = new Array(a.length).fill(null), s = new Array(a.length).fill(null);
  for (let i = w - 1; i < a.length; i++) { const v = []; for (let j = i - w + 1; j <= i; j++) if (nn(a[j])) v.push(a[j]); if (v.length >= need) { const mu = v.reduce((x, y) => x + y, 0) / v.length; m[i] = mu; s[i] = Math.sqrt(v.reduce((x, y) => x + (y - mu) ** 2, 0) / (v.length - 1)); } }
  return {m, s}; }
function openDetail(c) {
  fetch(`flow_stock_data/${c}.json?t=${D.asof}`).then(r => r.json()).then(d => { CUR = {c, d, r: D.rows.find(x => x.c === c)}; drawDetail(); $('overlay').classList.add('open'); });
}
function drawDetail() {
  const {d, r} = CUR, s0 = P ? Math.max(0, N - P) : 0, L = D.dates.slice(s0);
  const net = d.F.map((v, i) => (nn(v) || nn(d.I[i])) ? (WHO === '외국인' ? v : WHO === '기관' ? d.I[i] : (v || 0) + (d.I[i] || 0)) : null);   // 백만원
  const ratio = net.map((v, i) => i > 0 && nn(v) && nn(d.cap[i - 1]) && d.cap[i - 1] > 0 ? v / d.cap[i - 1] * 100 : null);   // 백만원 / 억 x 100 = bp
  const f = ma(ratio, D.fast, 7), s = ma(ratio, D.slow, 12), {m, s: sd} = rollMS(s, D.bw, 48);
  const up = m.map((v, i) => nn(v) && nn(sd[i]) ? v + D.bk * sd[i] : null), dn = m.map((v, i) => nn(v) && nn(sd[i]) ? v - D.bk * sd[i] : null);
  const z = s.map((v, i) => nn(v) && nn(m[i]) && sd[i] > 0 ? (v - m[i]) / sd[i] : null);
  const cross = (sign) => f.map((v, i) => i > 0 && nn(v) && nn(s[i]) && nn(f[i - 1]) && nn(s[i - 1]) && (sign > 0 ? f[i - 1] <= s[i - 1] && v > s[i] : f[i - 1] >= s[i - 1] && v < s[i]) ? v : null);
  const band = (sign) => s.map((v, i) => i > 0 && nn(z[i]) && nn(z[i - 1]) && (sign > 0 ? z[i] > D.bk && z[i - 1] <= D.bk : z[i] < -D.bk && z[i - 1] >= -D.bk) ? v : null);
  const a = r[WHO];
  $('dTitle').textContent = `${r.n} (${r.c}) · ${r.s}`;
  $('dSub').innerHTML = `${WHO === '합산' ? '외국인+기관' : WHO} · 시총 ${nn(r.cap) ? (r.cap >= 10000 ? (r.cap / 10000).toFixed(1) + '조' : r.cap.toLocaleString() + '억') : '-'} · ${D.fast}일 ${sg(a[I.m8], 2)}bp / ${D.slow}일 ${sg(a[I.m15], 2)}bp · ` +
    `${a[I.xs] === 1 ? '<span class="pos">골드</span>' : '<span class="neg">데드</span>'} ${a[I.xd]}일째 · 볼린저 z ${sg(a[I.z], 2)}${a[I.bs] ? (a[I.bs] === 1 ? ' <span class="pos">과매수</span>' : ' <span class="neg">과매도</span>') : ''} · 20일 순매수 ${eok(a[I.n20])} (외국인 ${eok(r['외국인'][I.n20])}, 기관 ${eok(r['기관'][I.n20])}) · 20일 주가 <span class="${cls(r.r20)}">${sg(r.r20)}%</span>`;
  Object.values(ch).forEach(c => c.destroy());
  const lim = Math.max(1, ...f.slice(s0).concat(s.slice(s0)).filter(nn).map(Math.abs)) * 2.5;
  const opt = extra => Object.assign({responsive: true, maintainAspectRatio: false, animation: false, interaction: {mode: 'index', intersect: false},
    plugins: {legend: {labels: {color: '#e6e6e6', boxWidth: 12, font: {size: 11}}}, tooltip: {filter: c => c.parsed.y !== null}},
    scales: {x: {ticks: {color: '#9aa0a6', maxTicksLimit: 8, maxRotation: 0}, grid: {display: false}}, y: {ticks: {color: '#9aa0a6'}, grid: {color: '#23262e'}}}}, extra || {});
  ch.a = new Chart($('d1'), {data: {labels: L, datasets: [
    {type: 'bar', label: '일별(bp)', data: ratio.slice(s0).map(v => nn(v) ? Math.max(-lim, Math.min(lim, v)) : null), backgroundColor: ratio.slice(s0).map(v => v >= 0 ? '#ff878744' : '#74a7ff44'), order: 3},
    {type: 'line', label: D.fast + '일 평균', data: f.slice(s0), borderColor: '#ffa94d', borderWidth: 1.8, pointRadius: 0, order: 1},
    {type: 'line', label: D.slow + '일 평균', data: s.slice(s0), borderColor: '#4dabf7', borderWidth: 1.8, pointRadius: 0, order: 1},
    {type: 'line', label: '골드 ▲', data: cross(1).slice(s0), showLine: false, pointStyle: 'triangle', pointRadius: 7, backgroundColor: '#ff6b6b', order: 0},
    {type: 'line', label: '데드 ▼', data: cross(-1).slice(s0), showLine: false, pointStyle: 'triangle', rotation: 180, pointRadius: 7, backgroundColor: '#4dabf7', order: 0}
  ]}, options: opt({scales: {x: {ticks: {color: '#9aa0a6', maxTicksLimit: 8, maxRotation: 0}, grid: {display: false}}, y: {min: -lim, max: lim, ticks: {color: '#9aa0a6'}, grid: {color: '#23262e'}}}})});
  ch.b = new Chart($('d2'), {type: 'line', data: {labels: L, datasets: [
    {label: '상단', data: up.slice(s0), borderColor: '#ff878799', borderWidth: 1, borderDash: [4, 3], pointRadius: 0, fill: '+1', backgroundColor: '#4dabf712'},
    {label: '하단', data: dn.slice(s0), borderColor: '#74a7ff99', borderWidth: 1, borderDash: [4, 3], pointRadius: 0},
    {label: D.bw + '일 평균', data: m.slice(s0), borderColor: '#6b7280', borderWidth: 1, borderDash: [2, 3], pointRadius: 0},
    {label: D.slow + '일선', data: s.slice(s0), borderColor: '#4dabf7', borderWidth: 2, pointRadius: 0},
    {label: '과매수 ▲', data: band(1).slice(s0), showLine: false, pointStyle: 'triangle', pointRadius: 7, backgroundColor: '#ff6b6b'},
    {label: '과매도 ▼', data: band(-1).slice(s0), showLine: false, pointStyle: 'triangle', rotation: 180, pointRadius: 7, backgroundColor: '#4dabf7'}
  ]}, options: opt()});
  const cum = arr => { let t = 0; return arr.slice(s0).map(v => { t += nn(v) ? v / 100 : 0; return t; }); };   // 백만원 -> 억
  const capS = d.cap.slice(s0), big = Math.max(...capS.filter(nn)) >= 10000;
  if (ch.c) { [2, 3].forEach(i => D3SHOW[i] = ch.c.isDatasetVisible(i)); }
  ch.c = new Chart($('d3'), {type: 'line', data: {labels: L, datasets: [
    {label: big ? '시가총액(조)' : '시가총액(억)', data: capS.map(v => nn(v) ? (big ? v / 10000 : v) : null), borderColor: '#e6e6e6', borderWidth: 1.8, pointRadius: 0, yAxisID: 'y'},
    {label: '누적 외국인+기관(억)', data: cum(d.F.map((v, i) => (v || 0) + (d.I[i] || 0))), borderColor: '#ff6b6b', borderWidth: 1.6, pointRadius: 0, yAxisID: 'y2'},
    {label: '누적 외국인(억)', data: cum(d.F), borderColor: '#ffa94d', borderWidth: 1.2, borderDash: [4, 3], pointRadius: 0, yAxisID: 'y2', hidden: !D3SHOW[2]},
    {label: '누적 기관(억)', data: cum(d.I), borderColor: '#69db7c', borderWidth: 1.2, borderDash: [4, 3], pointRadius: 0, yAxisID: 'y2', hidden: !D3SHOW[3]}
  ]}, options: opt({scales: {x: {ticks: {color: '#9aa0a6', maxTicksLimit: 8, maxRotation: 0}, grid: {display: false}}, y: {position: 'left', ticks: {color: '#c9ccd1'}, grid: {color: '#23262e'}}, y2: {position: 'right', grid: {display: false}, ticks: {color: '#ff8787'}}}})});
}
$('closeBtn').onclick = () => $('overlay').classList.remove('open');
$('overlay').onclick = e => { if (e.target.id === 'overlay') $('overlay').classList.remove('open'); };
const seg = (id, fn) => $(id).onclick = e => { const b = e.target.closest('button'); if (!b) return; document.querySelectorAll('#' + id + ' button').forEach(x => x.classList.toggle('active', x === b)); fn(b); };
seg('segWho', b => { WHO = b.dataset.v; render(); if (CUR && $('overlay').classList.contains('open')) drawDetail(); });
seg('segP', b => { P = +b.dataset.p; if (CUR) drawDetail(); });
['selCap', 'selSec', 'selSt', 'onlyU'].forEach(id => $(id).onchange = render);
$('q').oninput = render;
$('study').innerHTML = '<b>종목 수급 쓰는 법</b> (검증 2025-01 ~ ' + D.asof + ')<br>' +
  '· 종목 하나하나 단위로는 섹터보다 신호가 약합니다. 15일 평균 수급 순위로 이후 수익을 맞히는 힘은 거의 0이었습니다(날짜별 순위상관 ±0.02).<br>' +
  '· 극단값만 의미가 있었습니다: <b>외국인 과매수</b> 종목은 이후 20일 다른 종목보다 강했고(2025 +1.2%p / 2026 +1.1%p), <b>외국인 과매도</b>는 약했습니다(-0.6 / -1.4%p). 반대로 <b>기관 과매도</b> 종목은 이후 오히려 반등했습니다(+0.9 / +0.6%p) - 기관이 이례적으로 판 것만으로 팔 이유는 아닙니다.<br>' +
  '· 8/15 골드·데드크로스 자체는 신호가 아닙니다. 지금 누가 얼마나 사고파는지 확인하는 용도로 쓰고, 섹터 수급·가격 추세와 같은 방향인지 같이 보세요. 시총이 작은 종목은 블록딜 하루로 비율이 크게 튈 수 있습니다.';
render();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
