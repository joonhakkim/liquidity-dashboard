"""
시장 대비 강한 종목 - 최근 5·7·10거래일 중 코스피를 이긴 날 수 (2026-11-26 사용자 요청)

"코스피 하락일엔 덜 떨어지고, 상승일엔 더 오른 날" = 그날 종목 등락률 > 코스피 등락률인 날을 '이긴 날'로 센다.
- 하락일 이긴 날 / 상승일 이긴 날을 따로 보여 줌(하락일에 버틴 종목인지, 상승일에 더 오른 종목인지).
- 하루 ±31% 넘게 움직인 날(분할·병합 등)과 거래정지일은 세지 않음(분모에서 빠짐).
- 가격: data/bollinger_prices.csv 종가(상대강도 페이지와 같은 파일), 지수: 네이버 코스피(캐시 data/kospi_index_cache.csv).
- 대상: 코스피·코스닥 보통주(스팩·우선주·리츠 제외). 코스닥 종목도 코스피 대비(상대강도 페이지와 같은 규칙).
- 시총: op_band_summary.csv latest_mktcap(없는 종목은 시총 하한 0일 때만 표시).
출력: docs/beat_days.html, data/screening/beat_days_summary.csv
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from build_relative_strength import (MAX_DAILY_MOVE, OP_BAND_SUMMARY_PATH, PRICES_PATH,  # noqa: E402
                                     assign_sectors, exclusion_mask, load_kospi_series, read_recent_prices)

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
OUT_PATH = os.path.join(BASE_DIR, "docs", "beat_days.html")
SUMMARY_PATH = os.path.join(BASE_DIR, "data", "screening", "beat_days_summary.csv")
WINDOWS = [5, 7, 10]
NDAYS = max(WINDOWS)


def main():
    df = read_recent_prices(PRICES_PATH, keep_days=45)
    wide = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    cnt = wide.notna().sum(axis=1)
    while len(cnt) > 1 and cnt.iloc[-1] < 0.9 * cnt.iloc[-20:-1].median():   # 일부 종목만 들어온 마지막 날은 뺀다
        wide, cnt = wide.iloc[:-1], cnt.iloc[:-1]
    meta = df.sort_values("date").groupby("code").agg(name=("name", "last"), market=("market", "last")).loc[wide.columns]
    meta = meta[~exclusion_mask(meta)]
    wide = wide[meta.index]
    codes = list(wide.columns[wide.iloc[-1].notna()])
    meta = meta.loc[codes]

    R = wide[codes].pct_change(fill_method=None)
    R[R.abs() > MAX_DAILY_MOVE] = np.nan
    R = R.iloc[-NDAYS:]
    days = list(R.index)
    kospi = load_kospi_series()
    miss = [d for d in days + [wide.index[-NDAYS - 1]] if d not in kospi]
    if miss:
        raise SystemExit(f"코스피 지수에 없는 날짜: {miss}")
    kd = [wide.index[-NDAYS - 1]] + days
    kr = pd.Series([kospi[kd[i + 1]] / kospi[kd[i]] - 1 for i in range(NDAYS)], index=days)

    ex = R.sub(kr, axis=0)                      # 그날 초과수익률(종목 - 코스피)
    win = (ex > 0).astype(float).where(ex.notna())
    cap = {}
    if os.path.exists(OP_BAND_SUMMARY_PATH):
        op = pd.read_csv(OP_BAND_SUMMARY_PATH, dtype={"code": str}, usecols=["code", "latest_mktcap"])
        cap = dict(zip(op["code"].str.lstrip("A"), op["latest_mktcap"]))
    sectors, _ = assign_sectors(meta)

    rows = []
    for c in codes:
        w = win[c].values  # 오래된 날 -> 최근
        e = ex[c].values
        rec = {"c": c, "n": meta.at[c, "name"], "m": "P" if meta.at[c, "market"] == "KOSPI" else "Q",
               "s": sectors[c][0], "ss": sectors[c][1],
               "cap": None if pd.isna(cap.get(c, np.nan)) else int(round(cap[c] / 1e8)),
               "d": [None if np.isnan(x) else int(x) for x in w],                     # 10일 승패(1/0/None)
               "e": [None if np.isnan(x) else round(float(x) * 100, 2) for x in e]}    # 10일 초과수익률(%p)
        for n in WINDOWS:
            ww, rr, kk = w[-n:], R[c].values[-n:], kr.values[-n:]
            ok = ~np.isnan(ww)
            rec[f"w{n}"] = int(np.nansum(ww))
            rec[f"t{n}"] = int(ok.sum())
            # 기간 누적 초과수익률(종목 복리 - 코스피 복리, 빠진 날은 0)
            rec[f"x{n}"] = round((np.prod(1 + np.nan_to_num(rr)) - np.prod(1 + kk)) * 100, 2)
        rows.append(rec)

    kdays = [{"d": f"{d[4:6]}/{d[6:]}", "k": round(float(kr[d]) * 100, 2),
              "wins": int(win.loc[d].sum()), "tot": int(win.loc[d].notna().sum())} for d in days]
    asof = days[-1]
    payload = {"asof": f"{asof[:4]}-{asof[4:6]}-{asof[6:]}", "days": kdays, "windows": WINDOWS, "rows": rows}

    out = pd.DataFrame([{k: v for k, v in r.items() if k not in ("d", "e")} for r in rows])
    out.to_csv(SUMMARY_PATH, index=False, encoding="utf-8-sig")
    html = (TEMPLATE.replace("__UPDATED__", datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__ASOF__", payload["asof"]).replace("__N__", f"{len(rows):,}")
            .replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"기준일 {payload['asof']} | 종목 {len(rows)} | 코스피 최근 {NDAYS}일: " +
          " ".join(f"{x['d']} {x['k']:+.2f}%({x['wins']}/{x['tot']})" for x in kdays))
    for n in WINDOWS:
        full = sum(1 for r in rows if r[f"t{n}"] == n and r[f"w{n}"] == n)
        print(f"  {n}일 모두 이긴 종목 {full}개")
    print(f"저장: {OUT_PATH}")


TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>시장 대비 강한 종목</title>
<style>
  body { font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  a.back { color:#4dabf7; font-size:13px; text-decoration:none; }
  h1 { font-size:20px; margin:8px 0 4px 0; }
  .updated { color:#9aa0a6; font-size:13px; margin-bottom:14px; }
  .box { background:#12151b; border:1px solid #23262e; border-radius:12px; padding:14px 16px; margin-bottom:16px; }
  .note { font-size:12px; color:#9aa0a6; line-height:1.75; background:#1a1d24; border-radius:10px; padding:12px 16px; margin-bottom:14px; } .note b { color:#ffa94d; }
  .ctl { display:flex; gap:12px; flex-wrap:wrap; align-items:center; font-size:12px; color:#c9ccd1; margin-bottom:10px; }
  .ctl select, .ctl input { background:#0f1115; color:#e6e6e6; border:1px solid #2a2e37; border-radius:6px; padding:4px 6px; font-family:inherit; font-size:12px; }
  .ctl input[type=number] { width:64px; }
  .seg button { background:#0f1115; color:#c9ccd1; border:1px solid #2a2e37; padding:5px 12px; font-size:13px; cursor:pointer; font-family:inherit; }
  .seg button:first-child { border-radius:6px 0 0 6px; } .seg button:last-child { border-radius:0 6px 6px 0; }
  .seg button.on { background:#1c3a5e; color:#fff; border-color:#4dabf7; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  th, td { padding:5px 7px; border-bottom:1px solid #23262e; text-align:right; white-space:nowrap; }
  th { color:#9aa0a6; font-weight:normal; cursor:pointer; position:sticky; top:0; background:#12151b; } th.sorted { color:#4dabf7; }
  td.l, th.l { text-align:left; }
  .pos { color:#ff8787; } .neg { color:#74a7ff; } .sub { color:#7c828b; }
  .tblwrap { overflow:auto; max-height:780px; }
  .sq { display:inline-block; width:11px; height:11px; border-radius:2px; margin-right:2px; vertical-align:middle; }
  .sq.w { background:#e8590c; } .sq.l { background:#2b3a55; } .sq.n { background:#2a2e37; }
  .sq.dn { outline:1px solid #74a7ff; outline-offset:-1px; }
  .kd { display:flex; gap:6px; flex-wrap:wrap; }
  .kd div { background:#0f1115; border:1px solid #23262e; border-radius:8px; padding:6px 9px; font-size:12px; text-align:center; min-width:62px; }
  .kd .v { font-size:14px; font-weight:bold; }
  .dist { display:flex; gap:4px; align-items:flex-end; height:70px; margin-top:6px; }
  .dist div { flex:1; background:#4dabf7; border-radius:3px 3px 0 0; min-height:2px; position:relative; cursor:pointer; }
  .dist div.on { background:#ffa94d; }
  .dist span { position:absolute; top:-15px; left:0; right:0; text-align:center; font-size:10px; color:#9aa0a6; }
  .distx { display:flex; gap:4px; font-size:11px; color:#9aa0a6; } .distx div { flex:1; text-align:center; }
  .tag { font-size:10px; color:#9aa0a6; border:1px solid #2a2e37; border-radius:4px; padding:0 3px; margin-left:3px; }
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>시장 대비 강한 종목 <span style="font-size:13px;color:#9aa0a6;font-weight:normal">최근 5·7·10거래일 중 코스피를 이긴 날 수</span></h1>
  <div class="updated">최종 갱신: __UPDATED__ &middot; 기준일 __ASOF__ &middot; 코스피·코스닥 보통주 __N__종목</div>
  <div class="note">
    <b>이긴 날</b> = 그날 종목 등락률이 코스피 등락률보다 높은 날(코스피 하락일엔 덜 빠지고, 상승일엔 더 오른 날). 코스닥 종목도 코스피 대비.
    <b>하락일 승</b>·<b>상승일 승</b>은 10일 중 코스피가 내린 날·오른 날만 따로 센 것. 거래정지·±31% 넘는 날(분할 등)은 세지 않음(분모에서 빠짐).
    <b>초과</b> = 그 기간 누적 수익률 − 코스피 누적 수익률(%p). 같은 승수면 초과가 큰 순.
  </div>
  <div class="box">
    <div style="font-size:13px;margin-bottom:8px"><b>최근 10거래일 코스피</b> <span class="sub">&middot; 아래 숫자 = 그날 코스피를 이긴 종목 비율</span></div>
    <div class="kd" id="kd"></div>
  </div>
  <div class="ctl">
    <span class="seg" id="seg"></span>
    시장 <select id="mkt"><option value="">전체</option><option value="P">코스피</option><option value="Q">코스닥</option></select>
    시총 하한(억) <input type="number" id="minCap" value="1000" step="500">
    최소 이긴 날 <select id="minW"></select>
    <label><input type="checkbox" id="all3"> 5·7·10일 모두 승률 ≥ <input type="number" id="allPct" value="70" step="10" style="width:46px">%</label>
    섹터 <select id="sec"><option value="">전체</option></select>
    검색 <input type="text" id="q" placeholder="종목명">
    표시 <select id="topn"><option>50</option><option selected>100</option><option>300</option><option>9999</option></select>
    <span id="cnt" class="sub"></span>
  </div>
  <div class="box">
    <div style="font-size:13px"><b id="distT"></b> <span class="sub">&middot; 막대를 누르면 그 승수만 보기(다시 누르면 해제)</span></div>
    <div class="dist" id="dist"></div><div class="distx" id="distx"></div>
  </div>
  <div class="box"><div class="tblwrap"><table id="tbl"></table></div></div>
<script>
const D = __DATA__;
let N = 10, sortKey = null, sortDir = -1, exactW = null;
const f = (v,d=1)=> v==null? '-' : (v>0?'+':'')+v.toFixed(d);
const cl = v => v>0? 'pos' : v<0? 'neg' : '';
const NK = D.days.length;
// 하락일/상승일 승
D.rows.forEach(r=>{
  let dw=0, dt=0, uw=0, ut=0;
  r.d.forEach((x,i)=>{ if (x==null) return; if (D.days[i].k<0){ dt++; dw+=x; } else { ut++; uw+=x; } });
  Object.assign(r, {dw, dt, uw, ut});
});
document.getElementById('kd').innerHTML = D.days.map(x=>`<div><div class="sub">${x.d}</div><div class="v ${cl(x.k)}">${f(x.k,2)}%</div><div class="sub">${Math.round(x.wins/x.tot*100)}%</div></div>`).join('');
document.getElementById('seg').innerHTML = D.windows.map(n=>`<button data-n="${n}">${n}일</button>`).join('');
document.querySelectorAll('#seg button').forEach(b=>b.onclick=()=>{ N=+b.dataset.n; exactW=null; sortKey=null; setMinW(); render(); });
const secs = [...new Set(D.rows.map(r=>r.s))].sort();
document.getElementById('sec').innerHTML += secs.map(s=>`<option>${s}</option>`).join('');
function setMinW(){
  const el = document.getElementById('minW'), cur = Math.min(+el.value||0, N);
  el.innerHTML = Array.from({length:N+1},(_,i)=>`<option value="${i}">${i===0?'전체':i+'일 이상'}</option>`).join('');
  el.value = cur;
}
function base(){
  const mkt = document.getElementById('mkt').value, minCap = +document.getElementById('minCap').value||0;
  const sec = document.getElementById('sec').value, q = document.getElementById('q').value.trim();
  const all3 = document.getElementById('all3').checked, ap = (+document.getElementById('allPct').value||0)/100;
  return D.rows.filter(r=>(!mkt||r.m===mkt) && (minCap<=0 || (r.cap!=null && r.cap>=minCap)) && (!sec||r.s===sec) && (!q||r.n.includes(q))
    && r['t'+N] >= Math.ceil(N*0.6)
    && (!all3 || D.windows.every(n=> r['t'+n]>0 && r['w'+n]/r['t'+n] >= ap - 1e-9)));
}
function render(){
  document.querySelectorAll('#seg button').forEach(b=>b.classList.toggle('on', +b.dataset.n===N));
  const pool = base();
  // 분포
  const cnt = Array(N+1).fill(0); pool.forEach(r=>cnt[r['w'+N]]++);
  const mx = Math.max(...cnt, 1);
  document.getElementById('distT').textContent = `최근 ${N}일 중 이긴 날 수 분포 (${pool.length.toLocaleString()}종목)`;
  document.getElementById('dist').innerHTML = cnt.map((c,i)=>`<div data-w="${i}" class="${exactW===i?'on':''}" style="height:${c/mx*100}%"><span>${c}</span></div>`).join('');
  document.getElementById('distx').innerHTML = cnt.map((c,i)=>`<div>${i}일</div>`).join('');
  document.querySelectorAll('#dist div[data-w]').forEach(b=>b.onclick=()=>{ const w=+b.dataset.w; exactW = exactW===w? null : w; render(); });
  const minW = +document.getElementById('minW').value||0;
  let rows = pool.filter(r=> exactW!=null ? r['w'+N]===exactW : r['w'+N]>=minW);
  const key = sortKey || 'w'+N;
  const gv = r => key==='n'||key==='s'? r[key] : key.startsWith('w')? r[key] + r['x'+key.slice(1)]/1e4 : r[key];
  rows.sort((a,b)=>{ const x=gv(a), y=gv(b); if (x==null) return 1; if (y==null) return -1; return (x>y?1:x<y?-1:0)*(sortKey? sortDir : -1); });
  if (!sortKey) rows.sort((a,b)=> (b['w'+N]-a['w'+N]) || (b['x'+N]-a['x'+N]));
  const topn = +document.getElementById('topn').value;
  document.getElementById('cnt').textContent = `조건 맞는 종목 ${rows.length.toLocaleString()}개`;
  const H = [['#','#'],['n','종목','l'],['s','섹터','l'],['cap','시총(억)']]
    .concat(D.windows.map(n=>['w'+n, n+'일 승'])).concat([['dw','하락일 승'],['uw','상승일 승']])
    .concat(D.windows.map(n=>['x'+n, n+'일 초과%p'])).concat([['strip','최근 10일 (왼쪽=오래된 날)','l']]);
  const kHead = D.days.map(x=>`<span class="sq ${x.k<0?'dn':''}" style="background:${x.k<0?'#1b2740':'#3b1d1d'}" title="${x.d} 코스피 ${f(x.k,2)}%"></span>`).join('');
  let h = '<tr>' + H.map(([k,t,c])=>`<th data-k="${k}" class="${c||''} ${k===key?'sorted':''}">${k==='strip'? t+'<br>'+kHead : t}</th>`).join('') + '</tr>';
  h += rows.slice(0, topn).map((r,i)=>{
    const strip = r.d.map((x,j)=>`<span class="sq ${x==null?'n':x?'w':'l'} ${D.days[j].k<0?'dn':''}" title="${D.days[j].d} 코스피 ${f(D.days[j].k,2)}% / 초과 ${f(r.e[j],2)}%p"></span>`).join('');
    return `<tr><td>${i+1}</td><td class="l">${r.n}<span class="tag">${r.m==='P'?'P':'Q'}</span></td><td class="l sub">${r.s}${r.ss==='N'?'<span class="tag">N</span>':''}</td>
      <td>${r.cap==null?'-':r.cap.toLocaleString()}</td>` +
      D.windows.map(n=>`<td${n===N?' style="font-weight:bold;color:#fff"':''}>${r['w'+n]}/${r['t'+n]}</td>`).join('') +
      `<td>${r.dw}/${r.dt}</td><td>${r.uw}/${r.ut}</td>` +
      D.windows.map(n=>`<td class="${cl(r['x'+n])}">${f(r['x'+n])}</td>`).join('') +
      `<td class="l">${strip}</td></tr>`;
  }).join('');
  const t = document.getElementById('tbl'); t.innerHTML = h;
  t.querySelectorAll('th').forEach(th=>th.onclick=()=>{ const k=th.dataset.k; if (k==='#'||k==='strip') return;
    if (sortKey===k) sortDir*=-1; else { sortKey=k; sortDir=(k==='n'||k==='s')?1:-1; } render(); });
}
['mkt','minCap','minW','all3','allPct','sec','q','topn'].forEach(id=>document.getElementById(id).addEventListener('input',()=>{ if(id!=='topn') exactW=null; render(); }));
setMinW(); render();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
