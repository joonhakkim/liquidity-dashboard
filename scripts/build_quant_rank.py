"""
퀀트 종합 순위 페이지(docs/quant_rank.html) - 2026-11-19 사용자 요청 (사진 속 팩터 모델을 영업이익 중심으로 재현)

규칙(사용자 확정):
  - 유니버스: 컨센서스가 있고 당해(FY1)·차년(FY2) 영업이익 추정치가 모두 흑자인 종목 (순이익 추정치는 데이터가 없어 영업이익으로 대체)
  - 모멘텀(항목당 0.25): 1개월 하락(하락률 클수록 높은 점수), 6개월 상승률, 12개월 상승률, 6개월-1개월, 12개월-1개월(최근 1개월 제외 수익률)
  - 추정치 상/하향(항목당 1.0): FY1·FY2 영업이익 추정치의 1개월 변화율
  - 수급(항목당 0.25): 외국인·기관의 5일·20일 순매수대금 / 시가총액
  - 밸류(항목당 0.25): 시총 / FY1·FY2 영업이익 (낮을수록 높은 점수)
  - 점수 = 항목별 순위를 0~100 백분위로 바꿔 가중 평균(가중치: 모멘텀·수급·밸류 항목당 0.25, 추정치 항목당 1.0). 순위·가중치·시총 하한은 페이지에서 바로 바꿀 수 있다(계산은 페이지 JS).
데이터: 기업 밴드 찾기 2(+갱신용) 일별 시총·영업이익 추정치(build_op_band.load_band_workbook 캐시), 수급.xlsx(+갱신용).
주의: 수익률은 시가총액 변화로 계산하므로 유·무상증자 등 주식수 변화가 섞인다.
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import build_flow_analysis as bf  # noqa: E402
from build_op_band import load_band_workbook  # noqa: E402
from build_relative_strength import assign_sectors  # noqa: E402

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
OUT_PATH = os.path.join(BASE_DIR, "docs", "quant_rank.html")
SECTOR_MAP_PATH = os.path.join(BASE_DIR, "data", "sector_map.csv")
D1M, D6M, D12M = 21, 126, 252


def load_band_arrays():
    bk = load_band_workbook(verbose=False)
    sh = bk.store["sheets"]["기업밴드"] if hasattr(bk, "store") else None
    if sh is None:
        import pickle
        from build_op_band import BAND_STORE_PATH
        sh = pickle.load(open(BAND_STORE_PATH, "rb"))["sheets"]["기업밴드"]
    head, mat = sh["head"], sh["mat"]
    dates = pd.DatetimeIndex(sh["dates"])
    cols = {"S102100": {}, ("E121500.M", "NFY1"): {}, ("E121500.M", "NFY2"): {}}
    for k in range(len(head[7]) - 1):
        code, item, base = head[7][k + 1], head[9][k + 1], head[11][k + 1]
        if not code:
            continue
        c = str(code)[-6:]
        if item == "S102100":
            cols["S102100"][c] = k
        elif item == "E121500.M" and base in ("NFY1", "NFY2"):
            cols[(item, base)][c] = k
    def frame(m):
        return pd.DataFrame({c: mat[:, k] for c, k in m.items()}, index=dates)
    return frame(cols["S102100"]), frame(cols[("E121500.M", "NFY1")]) * 1000.0, frame(cols[("E121500.M", "NFY2")]) * 1000.0


def main():
    cap, op1, op2 = load_band_arrays()
    cap = cap.sort_index()
    ok_rows = cap.notna().sum(axis=1) >= 0.5 * cap.notna().sum(axis=1).max()
    cap, op1, op2 = cap[ok_rows], op1.reindex(cap.index[ok_rows]), op2.reindex(cap.index[ok_rows])
    # 추정치는 5일까지만 채운다(무기한 채우면 커버리지가 끊긴 종목의 몇 년 전 추정치가 남아 유니버스가 2.7배로 부풀었다 - 2026-11-19 발견)
    capf, op1f, op2f = cap.ffill(limit=3), op1.ffill(limit=5), op2.ffill(limit=5)
    t = len(capf) - 1
    asof = capf.index[t]
    c0 = capf.iloc[t]
    ret = lambda a, b: capf.iloc[a] / capf.iloc[b] - 1  # noqa: E731
    m1 = ret(t, t - D1M)
    m6, m12 = ret(t, t - D6M), ret(t, t - D12M)
    m6x, m12x = capf.iloc[t - D1M] / capf.iloc[t - D6M] - 1, capf.iloc[t - D1M] / capf.iloc[t - D12M] - 1
    # 추정치 기준연도는 매년 1월 첫 거래일에 한 해씩 넘어간다. 최근 1개월 안에 넘어간 종목은
    # FY1 변화 = 지금 FY1 / 1개월 전 FY2, FY2 변화는 비교 대상이 없어 비운다(페이지에서 FY1 점수로 대신).
    sw = ((op1f / op2f.shift(1) - 1).abs() < 0.02) & ((op1f / op1f.shift(1) - 1).abs() > 0.02)
    swin = sw.iloc[t - D1M + 1:t + 1].any()
    a1, a2, b1, b2 = op1f.iloc[t], op2f.iloc[t], op1f.iloc[t - D1M], op2f.iloc[t - D1M]
    r1 = pd.Series(np.where(swin, a1 / b2 - 1, a1 / b1 - 1), index=op1f.columns).where(np.where(swin, b2 > 0, b1 > 0))
    r2 = (a2 / b2 - 1).where(~swin & (b2 > 0))
    per1, per2 = c0 / op1f.iloc[t], c0 / op2f.iloc[t]
    # 수급
    F, I, fnames = bf.load_flows()
    fasof = F.index[-1]
    capflow = capf.reindex(F.index.union(capf.index)).ffill().reindex(F.index)
    def flow(df, n):
        s = df.fillna(0).iloc[-n:].sum() * 1e6
        cl = capflow.iloc[-1].reindex(s.index)
        return (s / cl * 1e4)  # bp
    fl = {"F5": flow(F, 5), "F20": flow(F, 20), "I5": flow(I, 5), "I20": flow(I, 20)}
    # 이름·섹터
    sm = pd.read_csv(SECTOR_MAP_PATH, dtype=str)
    meta = pd.DataFrame({"name": sm["name"].values}, index=sm["code"].str.zfill(6).values)
    meta = meta[~meta.index.duplicated()]
    naver = dict(zip(sm["code"].str.zfill(6), sm["sector"]))
    sectors, _ = assign_sectors(meta)
    sec_of = {c: (s if src == "U" else None) for c, (s, src) in sectors.items()}
    rows = []
    for c in capf.columns:
        name = meta["name"].get(c) or fnames.get(c)
        if not name or name.endswith("우") or name.endswith("우B") or name.endswith("우(전환)"):
            continue
        o1, o2 = op1f.iloc[t].get(c, np.nan), op2f.iloc[t].get(c, np.nan)
        if not (np.isfinite(o1) and np.isfinite(o2) and o1 > 0 and o2 > 0):
            continue
        vals = [m1.get(c), m6.get(c), m12.get(c), m6x.get(c), m12x.get(c), r1.get(c), r2.get(c),
                fl["F5"].get(c, np.nan), fl["F20"].get(c, np.nan), fl["I5"].get(c, np.nan), fl["I20"].get(c, np.nan),
                per1.get(c), per2.get(c)]
        if not all(v is not None and np.isfinite(v) for i, v in enumerate(vals) if i != 6):
            continue
        capok = c0.get(c)
        rows.append({"c": c, "n": name, "s": sec_of.get(c) or naver.get(c) or "-", "cap": int(round(capok / 1e8)),
                     "v": [None if (v is None or not np.isfinite(v)) else round(float(v) * (100 if i < 7 else 1), 2) for i, v in enumerate(vals)]})
    out = {"asof": asof.strftime("%Y-%m-%d"), "flow_asof": fasof.strftime("%Y-%m-%d"), "rows": rows}
    html = (TEMPLATE.replace("__UPDATED__", datetime.now().strftime("%Y-%m-%d %H:%M")).replace("__ASOF__", out["asof"])
            .replace("__FASOF__", out["flow_asof"]).replace("__DATA__", json.dumps(out, ensure_ascii=False, separators=(",", ":"))))
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"종목 {len(rows)}개, 시총·추정치 기준일 {out['asof']}, 수급 기준일 {out['flow_asof']} | 저장 완료: {OUT_PATH} ({len(html) // 1024}KB)")


TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>퀀트 종합 순위</title>
<style>
  body { font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  a.back { color:#4dabf7; font-size:13px; text-decoration:none; }
  h1 { font-size:20px; margin:8px 0 4px 0; }
  .updated { color:#9aa0a6; font-size:13px; margin-bottom:14px; }
  .ctl { display:flex; gap:12px; flex-wrap:wrap; align-items:center; font-size:12px; color:#c9ccd1; margin-bottom:10px; }
  .ctl select, .ctl input { background:#0f1115; color:#e6e6e6; border:1px solid #2a2e37; border-radius:6px; padding:4px 6px; font-family:inherit; font-size:12px; }
  .ctl input[type=number] { width:64px; }
  .box { background:#12151b; border:1px solid #23262e; border-radius:12px; padding:14px 16px; margin-bottom:16px; }
  .note { font-size:12px; color:#9aa0a6; line-height:1.75; background:#1a1d24; border-radius:10px; padding:12px 16px; margin-bottom:14px; } .note b { color:#ffa94d; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  th, td { padding:5px 7px; border-bottom:1px solid #23262e; text-align:right; white-space:nowrap; }
  th:nth-child(-n+3), td:nth-child(-n+3) { text-align:left; }
  th { color:#9aa0a6; font-weight:normal; cursor:pointer; position:sticky; top:0; background:#12151b; } th.sorted { color:#4dabf7; }
  .pos { color:#ff8787; } .neg { color:#74a7ff; } .sub { color:#7c828b; }
  .tblwrap { overflow:auto; max-height:720px; }
  .bar { display:inline-block; height:6px; background:#4dabf7; border-radius:3px; vertical-align:middle; margin-right:5px; }
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>퀀트 종합 순위 <span style="font-size:13px;color:#9aa0a6;font-weight:normal">모멘텀 · 추정치 상향 · 수급 · 밸류 합산</span></h1>
  <div class="updated">최종 갱신: __UPDATED__ &middot; 시총·추정치 기준일 __ASOF__ &middot; 수급 기준일 __FASOF__ &middot; 기업 밴드 찾기 2 + 수급.xlsx</div>
  <div class="note">
    <b>규칙</b> 유니버스 = FY1·FY2 영업이익 추정치가 모두 흑자인 종목(순이익 추정치 없음 &rarr; 영업이익 대체) &middot;
    모멘텀 5개(1개월은 <b>하락률 클수록</b> 고점수, 6M·12M 상승률, 6M-1M·12M-1M = 최근 1개월 제외 수익률) &middot;
    추정치 변화 2개(FY1·FY2 영업이익 1개월 변화율) &middot; 수급 4개(외국인·기관 5일·20일 순매수대금/시총) &middot; 밸류 2개(시총/영업이익 FY1·FY2, 낮을수록).
    항목별 순위를 0~100 백분위로 바꿔 가중 평균(가중치: 모멘텀·수급·밸류 항목당 0.25, 추정치 항목당 1.0). <b>수익률은 시총 변화</b>라 증자·분할 때 왜곡될 수 있음.
  </div>
  <div class="ctl">
    시총 하한(억) <input type="number" id="minCap" value="500" step="100">
    섹터 <select id="sec"><option value="">전체</option></select>
    검색 <input type="text" id="q" placeholder="종목명">
    표시 <select id="topn"><option selected>20</option><option>50</option><option>100</option><option>9999</option></select>
    <span id="cnt" class="sub"></span>
  </div>
  <div class="box"><div class="tblwrap"><table id="tbl"></table></div></div>
<script>
const D = __DATA__;
// 항목 인덱스: 0 1M수익률 1 6M 2 12M 3 6M-1M 4 12M-1M 5 OP1 변화 6 OP2 변화 7 외5 8 외20 9 기5 10 기20 11 PER1 12 PER2
// dir: 1=클수록 좋음, -1=작을수록 좋음 / grp: M,R,F,V
const ITEMS = [[0,-1,'M'],[1,1,'M'],[2,1,'M'],[3,1,'M'],[4,1,'M'],[5,1,'R'],[6,1,'R'],[7,1,'F'],[8,1,'F'],[9,1,'F'],[10,1,'F'],[11,-1,'V'],[12,-1,'V']];
const W = {M:0.25, R:1, F:0.25, V:0.25};   // 항목당 가중치(고정)
let sortKey = 'tot', sortDir = -1;
const secs = [...new Set(D.rows.map(r=>r.s))].sort();
document.getElementById('sec').innerHTML += secs.map(s=>`<option>${s}</option>`).join('');
const f = (v,d=1)=> v==null? '-' : v.toFixed(d);
const cl = v => v>0? 'pos' : v<0? 'neg' : '';
function pct(arr, dir){            // 백분위 점수 0~100 (좋을수록 높음), 값 없는 종목은 null
  const idx = arr.map((v,i)=>i).filter(i=>arr[i]!=null).sort((a,b)=>dir*(arr[a]-arr[b]));
  const n = idx.length, s = new Array(arr.length).fill(null); idx.forEach((i,k)=> s[i] = n>1? k/(n-1)*100 : 50); return s;
}
const FALLBACK = {6:5};   // FY2 변화가 없으면(1월 기준연도 전환 직후) FY1 변화 점수로 대신
function calc(){
  const minCap = +document.getElementById('minCap').value||0, sec = document.getElementById('sec').value;
  let rows = D.rows.filter(r => r.cap >= minCap);
  const sumW = ITEMS.reduce((a,it)=>a+W[it[2]],0) || 1;
  const sc = rows.map(()=>({M:0,R:0,F:0,V:0}));
  const P = {};
  for (const [k,dir,g] of ITEMS) P[k] = pct(rows.map(r=>r.v[k]), dir);
  for (const [k,dir,g] of ITEMS) P[k].forEach((x,i)=> sc[i][g] += (x==null? P[FALLBACK[k]][i] : x)*W[g]);
  const gw = {M:0,R:0,F:0,V:0}; for (const it of ITEMS) gw[it[2]] += W[it[2]];
  rows = rows.map((r,i)=>({...r, M:gw.M?sc[i].M/gw.M:0, R:gw.R?sc[i].R/gw.R:0, F:gw.F?sc[i].F/gw.F:0, V:gw.V?sc[i].V/gw.V:0,
    tot:(sc[i].M+sc[i].R+sc[i].F+sc[i].V)/sumW}));
  rows.sort((a,b)=>b.tot-a.tot); rows.forEach((r,i)=>r.rk=i+1);
  return {rows, sec};
}
function render(){
  const {rows:all, sec} = calc();
  const q = document.getElementById('q').value.trim(), topn = +document.getElementById('topn').value;
  let rows = all.filter(r=>(!sec||r.s===sec) && (!q||r.n.includes(q)));
  const key = sortKey; rows.sort((a,b)=>{ const x = key==='n'||key==='s'? a[key]: (key.startsWith('v')? a.v[+key.slice(1)]: a[key]); const y = key==='n'||key==='s'? b[key]: (key.startsWith('v')? b.v[+key.slice(1)]: b[key]);
    return (x>y?1:x<y?-1:0)*sortDir; });
  document.getElementById('cnt').textContent = `유니버스 ${all.length}종목 중 ${rows.length}개`;
  const H = [['rk','#'],['n','종목'],['s','섹터'],['cap','시총(억)'],['tot','총점'],['M','모멘텀'],['R','추정치'],['F','수급'],['V','밸류'],
    ['v0','1M%'],['v1','6M%'],['v2','12M%'],['v5','FY1 OP 1M%'],['v6','FY2 OP 1M%'],['v8','외20일bp'],['v10','기20일bp'],['v11','PER1'],['v12','PER2']];
  let h = '<tr>'+H.map(([k,t])=>`<th data-k="${k}" class="${k===sortKey?'sorted':''}">${t}</th>`).join('')+'</tr>';
  h += rows.slice(0, topn).map(r=>`<tr><td>${r.rk}</td><td>${r.n}</td><td class="sub">${r.s}</td><td>${r.cap.toLocaleString()}</td>
    <td><span class="bar" style="width:${r.tot*0.6}px"></span>${f(r.tot)}</td><td>${f(r.M,0)}</td><td>${f(r.R,0)}</td><td>${f(r.F,0)}</td><td>${f(r.V,0)}</td>
    <td class="${cl(r.v[0])}">${f(r.v[0])}</td><td class="${cl(r.v[1])}">${f(r.v[1])}</td><td class="${cl(r.v[2])}">${f(r.v[2])}</td>
    <td class="${cl(r.v[5])}">${f(r.v[5])}</td><td class="${cl(r.v[6])}">${f(r.v[6])}</td><td class="${cl(r.v[8])}">${f(r.v[8])}</td><td class="${cl(r.v[10])}">${f(r.v[10])}</td>
    <td>${f(r.v[11])}</td><td>${f(r.v[12])}</td></tr>`).join('');
  const t = document.getElementById('tbl'); t.innerHTML = h;
  t.querySelectorAll('th').forEach(th=>th.onclick=()=>{ const k=th.dataset.k; if(sortKey===k) sortDir*=-1; else {sortKey=k; sortDir=(k==='rk'||k==='n'||k==='s')?1:-1;} render(); });
}
['minCap','sec','q','topn'].forEach(id=>document.getElementById(id).addEventListener('input',render));
sortKey='rk'; sortDir=1; render();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
