"""
PBR 밴드 + ROE 페이지(docs/pbr_band.html) - 2026-11-01 사용자 요청
("PBR밴드를 길게 볼 수 있는 종목, 섹터 / ROE도 알 수 있게", 블로그 '강세장의 중심에서 밸류를 외치다'의 PBR-ROE 방법론).

계산
- PBR = 일별 시가총액 ÷ 가장 최근 공시된 자본총계. 시총은 KRX 일별 매매정보(2015~, research/krx 캐시) + 그 뒤는 op_band_mktcap_daily.pkl.
- 자본총계·당기순이익은 fetch_equity_dart.py가 모은 DART 분기 값(연결 우선). 공시 반영 시점은 보수적으로
  1Q 5/16, 반기 8/15, 3Q 11/15, 사업보고서 다음해 3/31 (그 전까지는 직전 분기 자본을 씀).
- ROE(최근 4분기) = 최근 4분기 당기순이익 ÷ 평균 자본(지금과 1년 전). 자본총계·순이익 모두 비지배지분 포함.
- 블로그 공식: PBR = (ROE - g) / (r - g)  ->  요구 ROE = PBR x (r - g) + g,  적정 PBR = (ROE - g) / (r - g). 기본 r=10%, g=2.5%(페이지에서 조절).
- 섹터 PBR = 소속 종목 시총 합 ÷ 자본 합(그날 자본이 있는 종목만), 섹터 ROE = 순이익 합 ÷ 평균자본 합.
- 밴드 = 선택한 기간 안에서 그 종목(섹터) 자신의 10/50/90% 분위. 이력 연수가 짧은 종목은 밴드를 믿기 어렵다.
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from build_relative_strength import assign_sectors  # noqa: E402

BASE = os.path.join(os.path.dirname(__file__), "..")
CACHE = os.path.join(BASE, "data", "screening", "cache")
KRX_DIR = os.path.join(CACHE, "research", "krx")
EQ_PATH = os.path.join(CACHE, "equity_dart.pkl")
MC_PATH = os.path.join(CACHE, "pbr_mktcap.pkl")
BAND_MC = os.path.join(CACHE, "op_band_mktcap_daily.pkl")
OUT = os.path.join(BASE, "docs", "pbr_band.html")
DETAIL_DIR = os.path.join(BASE, "docs", "pbr_band_data")
START = pd.Timestamp("2015-01-01")
MIN_MEMBERS = 3


def load_mktcap():
    """날짜 x 종목코드 시가총액(원). 캐시에 없는 날짜만 KRX 캐시에서 이어붙인다."""
    old = pd.read_pickle(MC_PATH) if os.path.exists(MC_PATH) else pd.DataFrame()
    have = set(old.index)
    rows = {}
    for f in sorted(os.listdir(KRX_DIR)):
        if not f.endswith(".pkl"):
            continue
        d = pd.Timestamp(f[:8])
        if d < START or d in have:
            continue
        x = pd.read_pickle(os.path.join(KRX_DIR, f))
        if not len(x) or "MKTCAP" not in x.columns:
            continue
        s = pd.Series(pd.to_numeric(x["MKTCAP"], errors="coerce").values, index=x["ISU_CD"].astype(str).str.zfill(6))
        s = s[~s.index.duplicated()]
        rows[d] = pd.concat([rows[d], s]) if d in rows else s
    if rows:
        new = pd.DataFrame({d: s for d, s in rows.items()}).T
        old = pd.concat([old, new]).sort_index()
        old = old[~old.index.duplicated(keep="last")]
        old = old.groupby(level=0).first()
        pd.to_pickle(old, MC_PATH)
    mc = old.copy()
    # KRX 캐시 이후 날짜는 밴드 파일 시총으로 이어붙임
    b = pd.read_pickle(BAND_MC)
    b.columns = [str(c).lstrip("A") for c in b.columns]
    b = b[b.index > mc.index.max()]
    if len(b):
        mc = pd.concat([mc, b.reindex(columns=mc.columns.union(b.columns))]).sort_index()
    return mc


AVAIL = {"03": (0, "05-16"), "06": (0, "08-15"), "09": (0, "11-15"), "12": (1, "03-31")}


def avail_date(period):
    y, m = int(period[:4]), period[4:]
    add, md = AVAIL[m]
    return pd.Timestamp(f"{y + add}-{md}")


def build_fund(code, eq, ni):
    """종목 하나의 (공시시점, 자본, ROE TTM) 표. eq/ni: {(code, YYYYMM): 값}"""
    periods = sorted(p for (c, p) in eq if c == code)
    recs = []
    for p in periods:
        y, m = int(p[:4]), p[4:]
        e = eq[(code, p)]
        ni_cum = ni.get((code, p))
        ttm = None
        if m == "12":
            ttm = ni_cum
        else:
            fy_prev = ni.get((code, f"{y - 1}12"))
            cum_prev = ni.get((code, f"{y - 1}{m}"))
            if None not in (ni_cum, fy_prev, cum_prev):
                ttm = fy_prev + ni_cum - cum_prev
        # 1년 전 같은 분기 자본(평균자본용)
        e_prev = eq.get((code, f"{y - 1}{m}"))
        avg_e = (e + e_prev) / 2 if e_prev is not None else e
        recs.append((avail_date(p), e, ttm, avg_e))
    return recs


def month_end_idx(index):
    s = pd.Series(range(len(index)), index=index)
    return s.groupby([index.year, index.month]).last().values


def main():
    st = pd.read_pickle(EQ_PATH)
    eq, ni = st["eq"], st.get("ni", {})
    mc = load_mktcap()
    mc = mc[[c for c in mc.columns if c.endswith("0")]]
    dates = mc.index
    print(f"시총 {mc.shape[0]}일 x {mc.shape[1]}종목, 마지막 {dates.max():%Y-%m-%d}")

    sm = pd.read_csv(os.path.join(BASE, "data", "sector_map.csv"), dtype=str)
    meta = pd.DataFrame({"name": sm.iloc[:, 1].values}, index=sm.iloc[:, 0].str.zfill(6).values)
    meta = meta[~meta.index.duplicated()]
    sectors, _ = assign_sectors(meta)
    sec_of = {c: sec for c, (sec, src) in sectors.items() if src == "U"}

    codes_with_eq = sorted({c for (c, p) in eq})
    E, NI_T, AVG = {}, {}, {}      # 날짜 x 종목 일별 값
    for code in codes_with_eq:
        if code not in mc.columns:
            continue
        recs = build_fund(code, eq, ni)
        if not recs:
            continue
        recs.sort(key=lambda r: r[0])
        f = pd.DataFrame(recs, columns=["d", "eq", "ttm", "avg"]).drop_duplicates("d", keep="last").set_index("d")
        f = f.reindex(dates.union(f.index)).ffill().reindex(dates)
        E[code], NI_T[code], AVG[code] = f["eq"], f["ttm"], f["avg"]
    E, NI_T, AVG = pd.DataFrame(E), pd.DataFrame(NI_T), pd.DataFrame(AVG)
    mcs = mc[E.columns]
    valid = E > 0
    PBR = (mcs / E.where(valid)).where(mcs > 0)
    ROE = (NI_T / AVG.where(AVG > 0)) * 100
    print(f"PBR 계산 가능 종목 {PBR.notna().any().sum()}개")

    me = month_end_idx(dates)
    mdates = [dates[i].strftime("%Y-%m") for i in me]
    last = dates[-1]
    os.makedirs(DETAIL_DIR, exist_ok=True)

    WINDOWS = (3, 5, 7, 10, 0)    # 밴드 계산 기간(년), 0=전체

    def q(s):
        v = s.dropna()
        v = v[v > 0]
        if len(v) < 250:
            return None
        return [round(float(np.percentile(v, p)), 2) for p in (5, 10, 50, 90)] + [round(float(v.min()), 2), round(float(v.max()), 2)]

    def wins(s):
        """기간별 {분위 6개, 현재 위치%}. 이력이 기간보다 짧으면 있는 만큼으로 계산(None 아님)."""
        v = s.dropna()
        v = v[v > 0]
        out = {}
        for w in WINDOWS:
            vw = v if w == 0 else v[v.index >= v.index[-1] - pd.DateOffset(years=w)]
            qq = q(vw)
            if qq is None:
                out[str(w)] = None
                continue
            out[str(w)] = {"q": qq, "pct": round(float((vw <= vw.iloc[-1]).mean() * 100))}
        return out

    rows = []
    for code in PBR.columns:
        s = PBR[code]
        v = s.dropna()
        if v.empty or v.index[-1] != last:
            continue
        hist_years = (v.index[-1] - v.index[0]).days / 365.25
        cur = float(v.iloc[-1])
        roe = ROE[code].dropna()
        roe_now = float(roe.iloc[-1]) if len(roe) and roe.index[-1] == last else None
        rows.append({"c": code, "n": meta["name"].get(code, code), "s": sec_of.get(code, "-"),
                     "mc": round(float(mcs[code].iloc[-1]) / 1e8), "pbr": round(cur, 2),
                     "roe": round(roe_now, 1) if roe_now is not None and np.isfinite(roe_now) else None,
                     "yrs": round(hist_years, 1), "w": wins(s)})
        if hist_years >= 1:
            detail = {"d": mdates, "pbr": [None if np.isnan(x) else round(float(x), 2) for x in s.values[me]],
                      "roe": [None if (np.isnan(x) or not np.isfinite(x)) else round(float(x), 1) for x in ROE[code].values[me]]}
            with open(os.path.join(DETAIL_DIR, f"{code}.json"), "w", encoding="utf-8") as fh:
                json.dump(detail, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"종목 {len(rows)}개")

    # 섹터
    members = {}
    for r in rows:
        if r["s"] != "-":
            members.setdefault(r["s"], []).append(r["c"])
    sec_out = []
    for sec in sorted(members):
        cs = [c for c in members[sec] if c in PBR.columns]
        if len(cs) < MIN_MEMBERS:
            continue
        ok = PBR[cs].notna()
        cap = mcs[cs].where(ok).sum(axis=1, min_count=1)
        eqs = E[cs].where(ok).sum(axis=1, min_count=1)
        pbr_s = (cap / eqs).where(ok.sum(axis=1) >= MIN_MEMBERS)
        okr = ROE[cs].notna()
        ttm = NI_T[cs].where(okr).sum(axis=1, min_count=1)
        avg = AVG[cs].where(okr).sum(axis=1, min_count=1)
        roe_s = (ttm / avg * 100).where(okr.sum(axis=1) >= MIN_MEMBERS)
        v = pbr_s.dropna()
        if v.empty:
            continue
        sec_out.append({"name": sec, "n": len(cs), "yrs": round((v.index[-1] - v.index[0]).days / 365.25, 1),
                        "pbr": round(float(v.iloc[-1]), 2), "roe": None if pd.isna(roe_s.iloc[-1]) else round(float(roe_s.iloc[-1]), 1),
                        "w": wins(pbr_s),
                        "d": mdates,
                        "ps": [None if np.isnan(x) else round(float(x), 2) for x in pbr_s.values[me]],
                        "rs": [None if np.isnan(x) else round(float(x), 1) for x in roe_s.values[me]]})
    print(f"섹터 {len(sec_out)}개")

    data = {"asof": last.strftime("%Y-%m-%d"), "stocks": rows, "sectors": sec_out}
    html = HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":"))).replace(
        "__UPDATED__", datetime.now().strftime("%Y-%m-%d %H:%M")).replace("__ASOF__", data["asof"])
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"저장 완료: {OUT}")


HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PBR 밴드</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body { font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  a.back { color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }
  h1 { font-size:20px; margin:8px 0 4px 0; } h1 .sub { font-size:13px; color:#9aa0a6; font-weight:normal; margin-left:6px; }
  .updated { color:#9aa0a6; font-size:13px; margin-bottom:14px; }
  .ctl { display:flex; gap:14px; flex-wrap:wrap; align-items:center; font-size:12px; color:#c9ccd1; margin-bottom:12px; }
  .ctl select, .ctl input { background:#0f1115; color:#e6e6e6; border:1px solid #2a2e37; border-radius:6px; padding:4px 6px; font-family:inherit; font-size:12px; }
  .ctl input.num { width:54px; }
  .seg button { background:#1a1d24; border:1px solid #2a2e37; color:#c9ccd1; padding:5px 11px; border-radius:999px; cursor:pointer; font-size:12px; font-family:inherit; margin-right:4px; }
  .seg button.active { background:#4dabf7; color:#0f1115; border-color:#4dabf7; font-weight:bold; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:12px; margin-bottom:16px; }
  .card { background:#1a1d24; border:1px solid #23262e; border-radius:12px; padding:12px 14px; font-size:12px; color:#9aa0a6; }
  .card .val { font-size:22px; font-weight:700; color:#e6e6e6; margin:4px 0; }
  .box { background:#12151b; border:1px solid #23262e; border-radius:12px; padding:14px 16px; margin-bottom:16px; }
  .box h2 { font-size:15px; margin:0 0 6px 0; } .box .desc { font-size:12px; color:#9aa0a6; margin-bottom:8px; line-height:1.6; }
  .chart-wrap { position:relative; height:380px; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  th, td { padding:6px 8px; border-bottom:1px solid #23262e; text-align:right; white-space:nowrap; }
  th:first-child, td:first-child, th.l, td.l { text-align:left; }
  th { color:#9aa0a6; font-weight:normal; cursor:pointer; position:sticky; top:0; background:#12151b; }
  tr.row:hover { background:#1a1f28; cursor:pointer; } tr.sel { background:#1c2433; }
  .pos { color:#ff8787; } .neg { color:#74a7ff; }
  .tblwrap { overflow:auto; max-height:560px; }
  .note { font-size:12px; color:#9aa0a6; line-height:1.7; background:#1a1d24; border-radius:10px; padding:12px 16px; }
  .note b { color:#ffa94d; }
  @media (max-width: 640px) { body { padding:16px; } .chart-wrap { height:280px; } }
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>PBR 밴드<span class="sub">시총 &divide; 자본총계(DART 분기, 2015~) + ROE &middot; 블로그의 PBR-ROE 공식</span></h1>
  <div class="updated">최종 갱신: __UPDATED__ &middot; 시총 기준일: __ASOF__</div>

  <div class="ctl">
    <span class="seg" id="segM"><button data-m="stock" class="active">종목</button><button data-m="sector">섹터</button></span>
    <span>밴드 기간 <select id="win"><option value="3">3년</option><option value="5">5년</option><option value="7">7년</option><option value="10" selected>10년</option><option value="0">전체</option></select></span>
    <span>할인율 r <input class="num" id="rIn" type="number" value="10" step="0.5">%</span>
    <span>영구성장률 g <input class="num" id="gIn" type="number" value="2.5" step="0.5">%</span>
    <span id="fltStock">이력 <select id="yrs"><option value="0">전체</option><option value="5" selected>5년 이상</option><option value="8">8년 이상</option><option value="10">10년 이상</option></select>
      섹터 <select id="secSel"></select> 검색 <input id="q" placeholder="종목명"></span>
  </div>

  <div class="cards" id="cards"></div>

  <div class="box">
    <h2 id="cTitle">PBR 밴드</h2>
    <div class="desc">실선 = PBR(왼쪽 축), 점선 = ROE 최근 4분기(오른쪽 축). 가는 선은 선택한 기간(위 '밴드 기간') 안에서 이 종목 자신의 하위 10% / 중앙값 / 상위 10% 선입니다.</div>
    <div class="chart-wrap"><canvas id="ch"></canvas></div>
  </div>

  <div class="box">
    <h2 id="tTitle">목록</h2>
    <div class="desc">행을 누르면 위 차트가 바뀌고, 머리글을 누르면 정렬됩니다(종목은 기본 시총순). '현재 위치'는 자기 이력 안에서 지금 PBR이 아래에서 몇 %인지(낮을수록 싼 쪽)입니다.</div>
    <div class="tblwrap"><table id="tbl"></table></div>
  </div>

  <div class="note">
    <b>PBR</b> = 그날 시가총액 &divide; 가장 최근 공시된 자본총계(연결 우선, 비지배지분 포함). 공시 전에는 직전 분기 자본을 씁니다(1Q 5/16, 반기 8/15, 3Q 11/15, 사업 3/31 이후 반영).<br>
    <b>ROE</b> = 최근 4분기 당기순이익 &divide; 평균 자본(지금과 1년 전). <b>적정 PBR</b> = (ROE - g) &divide; (r - g), <b>요구 ROE</b> = PBR &times; (r - g) + g. 지금 PBR을 정당화하려면 앞으로 이만큼의 ROE가 유지돼야 한다는 뜻입니다.<br>
    <b>괴리</b> = 현재 PBR &divide; 적정 PBR - 1. 플러스면 지금 ROE 대비 비싸게 거래, 마이너스면 싸게 거래. 성장 초기라 자본이 빠르게 쌓이는 기업, 무형자산 비중이 큰 기업, 적자·ROE가 g 이하인 기업에는 맞지 않습니다(블로그도 맹신 금지).<br>
    <b>섹터</b> = 소속 종목 시총 합 &divide; 자본 합. 밴드는 선택한 기간(3·5·7·10년·전체) 안의 각자 분위이며, 이력이 짧으면 믿기 어렵습니다. 2015년 이전은 데이터가 없습니다.
  </div>

<script>
const D = __DATA__;
let mode = 'stock', win = '10', sel = null, chart = null, sortKey = 'mc', sortDir = -1;
const $ = id => document.getElementById(id);
const r = () => +$('rIn').value / 100, g = () => +$('gIn').value / 100;
const fair = roe => (roe == null) ? null : (roe / 100 - g()) / (r() - g());
const reqRoe = pbr => (pbr * (r() - g()) + g()) * 100;
const f1 = x => x == null ? '-' : x.toFixed(1), f2 = x => x == null ? '-' : x.toFixed(2);
const cls = x => x == null ? '' : (x > 0 ? 'pos' : 'neg');

const secs = [...new Set(D.stocks.map(s => s.s))].sort();
$('secSel').innerHTML = '<option value="">전체 섹터</option>' + secs.map(s => `<option>${s}</option>`).join('');

function withWin(o) { const w = o.w && o.w[win]; o.q = w ? w.q : null; o.pct = w ? w.pct : null; return o; }
function rows() {
  if (mode === 'sector') return D.sectors.map(s => withWin({key: s.name, n: s.name, s: s.n + '종목', mc: null, pbr: s.pbr, roe: s.roe, yrs: s.yrs, w: s.w}));
  const y = +$('yrs').value, sc = $('secSel').value, qq = $('q').value.trim();
  return D.stocks.filter(s => s.yrs >= y && s.w[win] && (!sc || s.s === sc) && (!qq || s.n.includes(qq)))
    .map(s => withWin({key: s.c, n: s.n, s: s.s, mc: s.mc, pbr: s.pbr, roe: s.roe, yrs: s.yrs, w: s.w}));
}
function enrich(x) {
  const fp = fair(x.roe);
  x.fair = fp != null && fp > 0 ? fp : null;
  x.gap = x.fair ? (x.pbr / x.fair - 1) * 100 : null;
  x.req = reqRoe(x.pbr);
  x.vsMed = x.q ? (x.pbr / x.q[2] - 1) * 100 : null;
  return x;
}
function table() {
  const rs = rows().map(enrich);
  rs.sort((a, b) => ((a[sortKey] ?? 1e9) - (b[sortKey] ?? 1e9)) * sortDir || 0);
  const cols = [['n','이름'],['s','섹터'],['mc','시총(억)'],['yrs','이력(년)'],['pbr','PBR'],['pct','현재 위치%'],['vsMed','중앙값 대비%'],['roe','ROE%'],['req','요구 ROE%'],['fair','적정 PBR'],['gap','괴리%']];
  const head = '<tr>' + cols.map(([k, t]) => `<th class="${k==='n'||k==='s'?'l':''}" data-k="${k}">${t}${sortKey===k?(sortDir>0?' ▲':' ▼'):''}</th>`).join('') + '</tr>';
  $('tbl').innerHTML = head + rs.slice(0, 400).map(x => `<tr class="row ${sel===x.key?'sel':''}" data-key="${x.key}"><td class="l">${x.n}</td><td class="l">${x.s}</td><td>${x.mc==null?'-':x.mc.toLocaleString()}</td><td>${f1(x.yrs)}</td><td>${f2(x.pbr)}</td><td>${x.pct==null?'-':x.pct}</td><td class="${cls(x.vsMed)}">${f1(x.vsMed)}</td><td>${f1(x.roe)}</td><td>${f1(x.req)}</td><td>${f2(x.fair)}</td><td class="${cls(x.gap)}">${f1(x.gap)}</td></tr>`).join('');
  $('tTitle').textContent = (mode === 'stock' ? '종목 ' : '섹터 ') + rs.length + '개' + (rs.length > 400 ? ' (상위 400개 표시)' : '');
  document.querySelectorAll('#tbl th').forEach(th => th.onclick = () => { const k = th.dataset.k; if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = 1; } table(); });
  document.querySelectorAll('#tbl tr.row').forEach(tr => tr.onclick = () => { sel = tr.dataset.key; show(); table(); window.scrollTo({top: 0, behavior: 'smooth'}); });
  if (!sel && rs.length) { sel = rs[0].key; show(); }
}
async function show() {
  let item, d, ps, rsr;
  if (mode === 'sector') { item = D.sectors.find(s => s.name === sel); if (!item) return; d = item.d; ps = item.ps; rsr = item.rs; }
  else {
    item = D.stocks.find(s => s.c === sel); if (!item) return;
    try { const j = await (await fetch('pbr_band_data/' + sel + '.json')).json(); d = j.d; ps = j.pbr; rsr = j.roe; } catch (e) { return; }
  }
  withWin(item);
  const e = enrich({pbr: item.pbr, roe: item.roe, q: item.q});
  const nW = win === '0' ? d.length : Math.min(d.length, +win * 12);
  d = d.slice(-nW); ps = ps.slice(-nW); rsr = rsr.slice(-nW);
  const nm = mode === 'sector' ? item.name : item.n;
  $('cTitle').textContent = nm + ' - PBR 밴드';
  $('cards').innerHTML = [
    ['현재 PBR', f2(item.pbr), '이력 ' + f1(item.yrs) + '년'],
    ['현재 위치', item.pct == null ? '-' : item.pct + '%', (win === '0' ? '전체' : win + '년') + ' 이력 아래에서부터'],
    ['10% / 중앙 / 90%', item.q ? item.q[1] + ' / ' + item.q[2] + ' / ' + item.q[3] : '-', '최저 ' + (item.q ? item.q[4] : '-') + ' · 최고 ' + (item.q ? item.q[5] : '-')],
    ['ROE (4분기)', f1(item.roe) + '%', '적정 PBR ' + f2(e.fair)],
    ['요구 ROE', f1(e.req) + '%', '지금 PBR을 정당화하는 ROE'],
    ['괴리', e.gap == null ? '-' : f1(e.gap) + '%', '+ 비쌈 / - 쌈'],
  ].map(([a, b, c]) => `<div class="card">${a}<div class="val">${b}</div>${c}</div>`).join('');
  const ds = [{label: 'PBR', data: ps, borderColor: '#4dabf7', backgroundColor: '#4dabf7', borderWidth: 2, pointRadius: 0, yAxisID: 'y', spanGaps: true}];
  if (item.q) [[1, '하위 10%', '#51cf66'], [2, '중앙값', '#adb5bd'], [3, '상위 10%(90%)', '#ff8787']].forEach(([i, l, c]) =>
    ds.push({label: l, data: d.map(() => item.q[i]), borderColor: c, borderWidth: 1, borderDash: [4, 4], pointRadius: 0, yAxisID: 'y'}));
  ds.push({label: 'ROE %', data: rsr, borderColor: '#ffa94d', borderWidth: 1.5, borderDash: [2, 3], pointRadius: 0, yAxisID: 'y2', spanGaps: true});
  if (chart) chart.destroy();
  chart = new Chart($('ch'), {type: 'line', data: {labels: d, datasets: ds}, options: {responsive: true, maintainAspectRatio: false, interaction: {mode: 'index', intersect: false},
    plugins: {legend: {labels: {color: '#c9ccd1', boxWidth: 14}}}, scales: {x: {ticks: {color: '#9aa0a6', maxTicksLimit: 12}, grid: {color: '#1d2027'}},
      y: {position: 'left', ticks: {color: '#9aa0a6'}, grid: {color: '#1d2027'}, title: {display: true, text: 'PBR', color: '#9aa0a6'}},
      y2: {position: 'right', ticks: {color: '#ffa94d'}, grid: {drawOnChartArea: false}, title: {display: true, text: 'ROE %', color: '#ffa94d'}}}}});
}
document.querySelectorAll('#segM button').forEach(b => b.onclick = () => {
  document.querySelectorAll('#segM button').forEach(x => x.classList.toggle('active', x === b));
  mode = b.dataset.m; sel = null; sortKey = mode === 'stock' ? 'mc' : 'pct'; sortDir = mode === 'stock' ? -1 : 1; $('fltStock').style.display = mode === 'stock' ? '' : 'none'; table();
});
['rIn', 'gIn'].forEach(id => $(id).oninput = () => { table(); show(); });
$('win').oninput = () => { win = $('win').value; table(); show(); };
['yrs', 'secSel', 'q'].forEach(id => $(id).oninput = () => { sel = null; table(); });
table();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
