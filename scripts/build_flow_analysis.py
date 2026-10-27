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
- 화면 단위(2026-10-19 사용자 "8일 누적 0.9%처럼, 15일도 누적 %, 30일 누적 추가"): 표·카드 = 기간 누적 %(= 일평균 bp x 일수 / 100,
  "그 기간 동안 시총의 몇 %를 순매수했나"), 차트 = 막대 그날 %, 선 하루 평균 %(교차·볼린저는 평균끼리 비교라 선은 평균 유지, 툴팁에 누적 %).
- 수급 볼린저(2026-10-19 사용자 "수급으로 과매수 과매도를 볼린저 밴드처럼, 일별 말고 8일 15일선 기준으로"): 기준선(기본 15일선)의
  W일(기본 60) 평균 ± k(기본 2) x W일 표준편차. 기준선이 상단 위 = 과매수(수급이 평소보다 이례적으로 많이 들어옴), 하단 아래 = 과매도.
  정의 8가지 x 주체·집계 96조합 비교(scratchpad flow/flow4.py)에서 2025·2026 두 해 모두 같은 방향이었던 게 '15일선 + 60일 밴드'뿐이라 기본값.
검증(2025-01~2026-10, scratchpad flow/flow1~flow5.py) 요약은 페이지 아래 '수급과 주가' 칸 참고.
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
FLOW_UPD_XLSX = os.path.join(BASE_DIR, "data", "manual", "수급 - 갱신용.xlsx")
CACHE_PATH = os.path.join(BASE_DIR, "data", "screening", "cache", "flow_daily.pkl")
OUT_PATH = os.path.join(BASE_DIR, "docs", "flow_analysis.html")
SUMMARY_PATH = os.path.join(BASE_DIR, "data", "screening", "flow_sector_summary.csv")
EQ_CLIP = 200.0      # 종목평균 집계 때 종목 하루 비율을 ±200bp(시총의 2%)로 자름(블록딜 등 극단값)
MIN_MEMBERS = 3
WHO = ["합산", "외국인", "기관"]


def _parse_flow_xlsx(path):
    """DataGuide 수급 엑셀 -> (외국인, 기관, 이름) - 값이 아직 없는 날(당일)은 뺌."""
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr = next(i for i, r in enumerate(rows) if r[0] == "D A T E")
    codes, names, items = rows[7], rows[8], rows[hdr]
    data = [r for r in rows[hdr + 1:] if r[0] is not None]
    dates = pd.to_datetime([r[0] for r in data])
    arr = np.array([[np.nan if v is None else v for v in r[1:]] for r in data], dtype=float)
    cols = pd.MultiIndex.from_tuples([(str(codes[j + 1])[-6:], "외국인" if "외국인" in str(items[j + 1]) else "기관") for j in range(arr.shape[1])])
    df = pd.DataFrame(arr, index=dates, columns=cols)
    df = df[df.notna().any(axis=1)]
    F, I = df.xs("외국인", level=1, axis=1), df.xs("기관", level=1, axis=1)
    F = F.loc[:, ~F.columns.duplicated()]; I = I.loc[:, ~I.columns.duplicated()]
    nm = {str(codes[j + 1])[-6:]: names[j + 1] for j in range(arr.shape[1]) if codes[j + 1]}
    return F, I, nm


def _overlay(base, upd):
    """갱신용 값으로 덮어쓰기·추가(갱신용 빈칸은 기존 값 유지)."""
    out = upd.combine_first(base)
    return out.loc[:, ~out.columns.duplicated()].sort_index()


def load_flows():
    """수급.xlsx(쌓인 본 파일) + 수급 - 갱신용.xlsx(최근 며칠) -> (외국인, 기관) 일별 DataFrame(백만원, 열 = 6자리 종목코드).
    두 파일 수정시각이 같으면 캐시(2026-10-27 갱신용 파일 추가 - 밴드 파일과 같은 방식)."""
    key = (os.path.getmtime(FLOW_XLSX), os.path.getmtime(FLOW_UPD_XLSX) if os.path.exists(FLOW_UPD_XLSX) else None)
    if os.path.exists(CACHE_PATH):
        c = pd.read_pickle(CACHE_PATH)
        if c.get("mtime") == key:
            return c["F"], c["I"], c["names"]
    F, I, nm = _parse_flow_xlsx(FLOW_XLSX)
    if key[1] is not None:
        uF, uI, unm = _parse_flow_xlsx(FLOW_UPD_XLSX)
        F, I, nm = _overlay(F, uF), _overlay(I, uI), {**nm, **unm}
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    pd.to_pickle({"mtime": key, "F": F, "I": I, "names": nm}, CACHE_PATH)
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
  <a class="back" href="flow_stocks.html">종목 수급 &rarr;</a>
  <a class="back" href="flow_lab.html">수급 국면·평균단가(실험) &rarr;</a>
  <h1>수급 분석<span class="sub">섹터 정리 섹터·종목의 외국인·기관 순매수, 시가총액 대비 비율</span></h1>
  <div class="updated">최종 갱신: __UPDATED__ &middot; 데이터 __FROM__ ~ __ASOF__ &middot; 수급.xlsx(외국인·기관 순매수대금) + 기업 밴드 찾기 2 시가총액</div>

  <div class="ctl">
    <span>주체</span><span class="seg" id="segWho"><button data-v="합산" class="active">외국인+기관</button><button data-v="외국인">외국인</button><button data-v="기관">기관</button></span>
    <span>섹터 집계</span><span class="seg" id="segHow"><button data-v="cap" class="active">섹터 시총 합</button><button data-v="eq">종목 평균</button></span>
    <span>빠른</span><select id="selF"></select><span>느린</span><select id="selS"></select>
    <span>차트 기간</span><span class="seg" id="segP"><button data-p="63">3달</button><button data-p="126">6달</button><button data-p="250" class="active">1년</button><button data-p="0">전체</button></span>
  </div>

  <div class="cards" id="cards"></div>

  <div class="box">
    <h2>수급 방향 <span id="t1" style="font-size:12px;font-weight:normal;color:#9aa0a6"></span></h2>
    <div class="desc">막대 = 그날 순매수 / 전일 시가총액(%), 선 = 이동평균(하루 평균 %, 누적 % = 하루 평균 x 일수 - 마우스를 올리면 같이 보임). 빠른 선이 느린 선을 위로 뚫으면 골드 ▲, 아래로 뚫으면 데드 ▼. 아래 표에서 섹터를 누르면 그 섹터로 바뀝니다.</div>
    <div class="ctl" style="margin-bottom:6px"><span>대상</span><select id="selT"></select><span id="xNow"></span></div>
    <div class="chart-wrap"><canvas id="c1"></canvas></div>
  </div>

  <div class="box">
    <h2>수급 볼린저(과매수·과매도) <span id="t3" style="font-size:12px;font-weight:normal;color:#9aa0a6"></span></h2>
    <div class="desc">기준선(수급 비율의 이동평균)에 볼린저밴드를 씌운 것: 가운데 점선 = 기준선의 최근 W일 평균, 위·아래 선 = 평균 ± 배수 x W일 표준편차. 기준선이 위 밴드를 뚫으면 <b style="color:#ff8787">과매수 ▲</b>(평소보다 이례적으로 많이 사들임), 아래 밴드를 뚫으면 <b style="color:#74a7ff">과매도 ▼</b>(이례적으로 많이 팖). 대상 섹터는 위 '수급 방향' 칸과 같습니다.</div>
    <div class="ctl" style="margin-bottom:6px">
      <span>기준선</span><select id="selBL"></select>
      <span>밴드 기간</span><span class="seg" id="segBW"><button data-v="20">20일</button><button data-v="60" class="active">60일</button></span>
      <span>배수</span><span class="seg" id="segBK"><button data-v="1.5">1.5</button><button data-v="2" class="active">2</button></span>
      <span id="bNow"></span>
    </div>
    <div class="chart-wrap"><canvas id="c3"></canvas></div>
  </div>

  <div class="box">
    <h2>시가총액과 누적 순매수 <span id="t2" style="font-size:12px;font-weight:normal;color:#9aa0a6"></span></h2>
    <div class="desc">흰 선 = 섹터 시가총액(조원, 왼쪽), 빨간 선 = 차트 시작일부터 누적 외국인+기관 순매수(억원, 오른쪽). 외국인·기관 따로 보려면 범례의 '누적 외국인'·'누적 기관'을 누르세요. 누적 순매수가 오르는데 시총이 같이 오르면 수급이 주가를 받치는 것, 엇갈리면 다른 주체(개인 등)가 반대로 움직이는 것.</div>
    <div class="chart-wrap"><canvas id="c2"></canvas></div>
  </div>

  <div class="box">
    <h2>섹터 수급 순위 <span style="font-size:12px;font-weight:normal;color:#9aa0a6">머리글 = 정렬, 행 = 위 차트 대상 + 소속 종목 수급 펼치기(고른 섹터는 맨 위 ▶, '대상'에서 섹터 정리 전체를 고르면 원래 순서)</span></h2>
    <div class="tblwrap"><table id="tbl"></table></div>
  </div>

  <div class="note" id="study"></div>

<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
const nn = v => v !== null && v !== undefined && !isNaN(v);
const N = D.dates.length;
const WINS = [3, 5, 8, 10, 14, 15, 20, 40, 60];
let WHO = '합산', HOW = 'cap', XF = 8, XS = 15, PERIOD = 250,   /* 차트 기본 1년(2026-10-19 사용자) */ TGT = '', SORT = 'slow', DESC = true, OPEN = null;
let BL = 15, BW = 60, BK = 2;   // 수급 볼린저 기본: 15일선 + 60일 밴드 + 2배(두 해 모두 같은 방향이었던 유일한 정의)
// 기준선 길이(2026-10-19 사용자 "기준선을 11로 하면" -> 5~20일 비교: 12~18일이 두 해 모두 안정, 11일 이하는 과매수 쪽이 해마다 흔들림 - 15 유지, 5~20 선택 가능)
const f1 = (v, d = 1) => nn(v) ? v.toFixed(d) : '-';
const sg = (v, d = 1) => nn(v) ? (v >= 0 ? '+' : '') + v.toFixed(d) : '-';
const cls = v => nn(v) ? (v >= 0 ? 'pos' : 'neg') : '';
const pc = (bpv, d = 2) => nn(bpv) ? sg(bpv / 100, d) + '%' : '-';         // bp -> %
const cum = (avg, n, d = 2) => nn(avg) ? sg(avg * n / 100, d) + '%' : '-';   // 일평균 bp -> n일 누적 %
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
function rollMS(a, w) {   // 이동평균·표본표준편차(값이 80% 이상 있을 때만)
  const m = new Array(a.length).fill(null), s = new Array(a.length).fill(null), need = Math.ceil(w * 0.8);
  for (let i = w - 1; i < a.length; i++) { const v = []; for (let j = i - w + 1; j <= i; j++) if (nn(a[j])) v.push(a[j]);
    if (v.length >= need) { const mu = v.reduce((x, y) => x + y, 0) / v.length; m[i] = mu; s[i] = Math.sqrt(v.reduce((x, y) => x + (y - mu) ** 2, 0) / (v.length - 1)); } }
  return {m, s};
}
function bandOf(g, r) {
  r = r || ratioOf(g);
  const line = ma(r, BL), other = ma(r, BL === XS ? XF : XS), {m, s} = rollMS(line, BW);
  const up = m.map((v, i) => nn(v) && nn(s[i]) ? v + BK * s[i] : null), dn = m.map((v, i) => nn(v) && nn(s[i]) ? v - BK * s[i] : null);
  const z = line.map((v, i) => nn(v) && nn(m[i]) && s[i] > 0 ? (v - m[i]) / s[i] : null);
  const st = z.map(v => !nn(v) ? null : v > BK ? 1 : v < -BK ? -1 : 0);
  const k = N - 1; let d = 0; if (nn(st[k])) { let j = k; while (j >= 0 && st[j] === st[k]) j--; d = k - j; }
  return {line, other, m, up, dn, z, st, now: st[k], days: d, zNow: z[k]};
}
function sumLast(a, n, end = N - 1) { let s = 0, c = 0; for (let i = Math.max(0, end - n + 1); i <= end; i++) if (nn(a[i])) { s += a[i]; c++; } return c ? s : null; }
function groupStats(g) {
  const r = ratioOf(g), f = ma(r, XF), s = ma(r, XS), nt = netOf(g, WHO), k = N - 1;
  const cap0 = g.cap[k], cap20 = g.cap[k - 20];
  const m30 = ma(r, 30)[k];
  return {g, r, f, s, fast: f[k], slow: s[k], slow5: s[k - 5], fast5: f[k - 5], m30, x: crossState(f, s),
    n5: sumLast(nt, 5), n20: sumLast(nt, 20), c20: nn(cap0) && nn(cap20) && cap20 ? (cap0 / cap20 - 1) * 100 : null, cap: cap0};
}
let c3 = null, c4 = null;
const BNAME = {1: '과매수', '-1': '과매도', 0: '보통'};
function renderBand() {
  const g = getGroup(TGT), B = bandOf(g), s0 = PERIOD ? Math.max(0, N - PERIOD) : 0, L = D.dates.slice(s0);
  const ent = (sign) => B.line.map((v, i) => i > 0 && B.st[i] === sign && B.st[i - 1] !== sign && nn(B.st[i - 1]) ? v : null);
  const lb = BL + '일선', ob = (BL === XS ? XF : XS) + '일선';
  if (c3) c3.destroy();
  c3 = new Chart($('c3'), {type: 'line', data: {labels: L, datasets: [
    {label: '상단(평균+' + BK + 'σ)', data: B.up.slice(s0).map(v => nn(v) ? v / 100 : null), borderColor: '#ff878799', borderWidth: 1, borderDash: [4, 3], pointRadius: 0, fill: '+1', backgroundColor: '#4dabf712'},
    {label: '하단(평균-' + BK + 'σ)', data: B.dn.slice(s0).map(v => nn(v) ? v / 100 : null), borderColor: '#74a7ff99', borderWidth: 1, borderDash: [4, 3], pointRadius: 0},
    {label: BW + '일 평균', data: B.m.slice(s0).map(v => nn(v) ? v / 100 : null), borderColor: '#6b7280', borderWidth: 1, borderDash: [2, 3], pointRadius: 0},
    {label: lb + '(기준)', data: B.line.slice(s0).map(v => nn(v) ? v / 100 : null), borderColor: '#4dabf7', borderWidth: 2, pointRadius: 0},
    {label: ob, data: B.other.slice(s0).map(v => nn(v) ? v / 100 : null), borderColor: '#ffa94d66', borderWidth: 1, pointRadius: 0},
    {label: '과매수 ▲', data: ent(1).slice(s0).map(v => nn(v) ? v / 100 : null), showLine: false, pointStyle: 'triangle', pointRadius: 7, backgroundColor: '#ff6b6b'},
    {label: '과매도 ▼', data: ent(-1).slice(s0).map(v => nn(v) ? v / 100 : null), showLine: false, pointStyle: 'triangle', rotation: 180, pointRadius: 7, backgroundColor: '#4dabf7'}
  ]}, options: {responsive: true, maintainAspectRatio: false, animation: false, interaction: {mode: 'index', intersect: false},
    plugins: {legend: {labels: {color: '#e6e6e6', boxWidth: 12, font: {size: 11}, filter: it => it.text}}, tooltip: {filter: c => c.parsed.y !== null, callbacks: {label: c => c.dataset.label + ': ' + sg(c.parsed.y, 3) + '%' + (c.datasetIndex === 3 ? ' (' + BL + '일 누적 ' + sg(c.parsed.y * BL, 2) + '%)' : ''), afterBody: it => { const i = s0 + it[0].dataIndex; return nn(B.z[i]) ? '위치 z = ' + sg(B.z[i], 2) + ' (' + BNAME[B.st[i]] + ')' : ''; }}}},
    scales: {x: {ticks: xTicks(), grid: {display: false}}, y: {ticks: {color: '#9aa0a6', callback: v => v.toFixed(2) + '%'}, grid: {color: '#23262e'}}}}});
  $('t3').textContent = `${g.name} · ${lb} 기준 · ${BW}일 밴드 · ${BK}σ`;
  $('bNow').innerHTML = nn(B.now) ? `<b style="color:${B.now > 0 ? '#ff8787' : B.now < 0 ? '#74a7ff' : '#c9ccd1'}">지금 ${BNAME[B.now]}${B.now ? ' ' + B.days + '일째' : ''}</b> · 밴드 안 위치 z = ${sg(B.zNow, 2)}(±${BK} 밖이면 과매수/과매도)` : '';
}
function getGroup(name) { return name ? D.sectors.find(x => x.name === name) : D.all; }
// ---------- 카드
function renderCards() {
  const a = groupStats(D.all);
  const fw = D.all.net['외국인'], iw = D.all.net['기관'];
  $('cards').innerHTML =
    `<div class="card">섹터 정리 전체 · ${XF}일 / ${XS}일 누적(시총 대비)<div class="val"><span class="${cls(a.fast)}">${cum(a.fast, XF)}</span> / <span class="${cls(a.slow)}">${cum(a.slow, XS)}</span></div>${WHO === '합산' ? '외국인+기관' : WHO} · ${HOW === 'cap' ? '시총 합' : '종목 평균'} 기준 · 30일 누적 ${cum(a.m30, 30)} · 1주 전 ${XS}일 누적 ${cum(a.slow5, XS)}</div>` +
    `<div class="card">교차 상태(${XF}/${XS})<div class="val" style="color:${a.x && a.x.up ? '#ff8787' : '#74a7ff'}">${a.x ? (a.x.up ? '▲ 골드' : '▼ 데드') + ' ' + a.x.days + '일째' : '-'}</div>${a.x && a.x.since ? a.x.since + ' 교차' : ''}</div>` +
    `<div class="card">순매수(${D.asof} 까지)<div class="val">${eok(a.n5)} <span style="font-size:12px;color:#9aa0a6">5일</span></div>20일 ${eok(a.n20)} · 외국인 20일 ${eok(sumLast(fw, 20))} · 기관 20일 ${eok(sumLast(iw, 20))}</div>` +
    (() => { const B = D.sectors.map(x => ({n: x.name, b: bandOf(x)})); const up = B.filter(x => x.b.now === 1), dn = B.filter(x => x.b.now === -1);
      return `<div class="card">수급 볼린저(${BL}일선·${BW}일·${BK}σ)<div class="val"><span class="pos">과매수 ${up.length}</span> · <span class="neg">과매도 ${dn.length}</span></div>${up.map(x => x.n).slice(0, 4).join(', ') || '-'}${dn.length ? ' / ' + dn.map(x => x.n).slice(0, 4).join(', ') : ''}</div>`; })() +
    `<div class="card">섹터 정리 시가총액<div class="val">${nn(a.cap) ? (a.cap / 10000).toFixed(0) + '조' : '-'}</div>20일 변화 <span class="${cls(a.c20)}">${sg(a.c20)}%</span></div>`;
}
// ---------- 차트
let c1 = null, c2 = null;
const C2SHOW = {2: false, 3: false};
Chart.defaults.color = '#9aa0a6'; Chart.defaults.borderColor = '#23262e';
function xTicks() { return {color: '#9aa0a6', maxTicksLimit: 8, maxRotation: 0}; }
function renderCharts() {
  const g = getGroup(TGT), st = groupStats(g), s0 = PERIOD ? Math.max(0, N - PERIOD) : 0, L = D.dates.slice(s0);
  const gold = st.f.map((v, i) => i > 0 && nn(v) && nn(st.s[i]) && nn(st.f[i - 1]) && nn(st.s[i - 1]) && st.f[i - 1] <= st.s[i - 1] && v > st.s[i] ? v : null);
  const dead = st.f.map((v, i) => i > 0 && nn(v) && nn(st.s[i]) && nn(st.f[i - 1]) && nn(st.s[i - 1]) && st.f[i - 1] >= st.s[i - 1] && v < st.s[i] ? v : null);
  const lim = Math.max(1, ...st.f.slice(s0).concat(st.s.slice(s0)).filter(nn).map(Math.abs)) * 2.2 / 100;   // % 단위
  if (c1) c1.destroy();
  c1 = new Chart($('c1'), {data: {labels: L, datasets: [
    {type: 'bar', label: '그날(%)', data: st.r.slice(s0).map(v => nn(v) ? Math.max(-lim, Math.min(lim, v / 100)) : null), backgroundColor: st.r.slice(s0).map(v => v >= 0 ? '#ff878744' : '#74a7ff44'), order: 3},
    {type: 'line', label: XF + '일 평균(하루 %)', data: st.f.slice(s0).map(v => nn(v) ? v / 100 : null), borderColor: '#ffa94d', borderWidth: 1.8, pointRadius: 0, order: 1},
    {type: 'line', label: XS + '일 평균(하루 %)', data: st.s.slice(s0).map(v => nn(v) ? v / 100 : null), borderColor: '#4dabf7', borderWidth: 1.8, pointRadius: 0, order: 1},
    {type: 'line', label: '골드 ▲', data: gold.slice(s0).map(v => nn(v) ? v / 100 : null), showLine: false, pointStyle: 'triangle', pointRadius: 7, backgroundColor: '#ff6b6b', order: 0},
    {type: 'line', label: '데드 ▼', data: dead.slice(s0).map(v => nn(v) ? v / 100 : null), showLine: false, pointStyle: 'triangle', rotation: 180, pointRadius: 7, backgroundColor: '#4dabf7', order: 0}
  ]}, options: {responsive: true, maintainAspectRatio: false, animation: false, interaction: {mode: 'index', intersect: false},
    plugins: {legend: {labels: {color: '#e6e6e6', boxWidth: 12, font: {size: 11}}}, tooltip: {filter: c => c.parsed.y !== null, callbacks: {label: c => { const i = s0 + c.dataIndex; if (c.dataset.type === 'bar') return '그날: ' + pc(st.r[i], 3);
      if (c.datasetIndex === 1) return XF + '일: 하루 평균 ' + pc(st.f[i], 3) + ' · ' + XF + '일 누적 ' + cum(st.f[i], XF);
      if (c.datasetIndex === 2) return XS + '일: 하루 평균 ' + pc(st.s[i], 3) + ' · ' + XS + '일 누적 ' + cum(st.s[i], XS);
      return c.dataset.label; }}}},
    scales: {x: {ticks: xTicks(), grid: {display: false}}, y: {min: -lim, max: lim, ticks: {color: '#9aa0a6', callback: v => v.toFixed(2) + '%'}, grid: {color: '#23262e'}}}}});
  $('t1').textContent = `${g.name} · ${WHO === '합산' ? '외국인+기관' : WHO} · ${HOW === 'cap' ? '섹터 시총 합' : '종목 평균'} 기준`;
  $('xNow').innerHTML = st.x ? `<b style="color:${st.x.up ? '#ff8787' : '#74a7ff'}">지금 ${st.x.up ? '골드' : '데드'} ${st.x.days}거래일째</b>${st.x.since ? ' (' + st.x.since + ' 교차)' : ''} · ${XF}일 누적 ${cum(st.fast, XF)} vs ${XS}일 누적 ${cum(st.slow, XS)} (하루 평균 ${pc(st.fast, 3)} vs ${pc(st.slow, 3)})` : '';
  // 시총 + 누적 순매수
  const cumNet = a => { let s = 0; return a.slice(s0).map(v => { s += nn(v) ? v : 0; return s; }); };
  // 외국인·기관 따로 누적선은 기본 꺼짐(2026-10-19 사용자 "기본적으로 꺼진 상태, 나중에 켜서 보게") - 범례로 켜면 다시 그려도 유지
  if (c2) { [2, 3].forEach(i => C2SHOW[i] = c2.isDatasetVisible(i)); c2.destroy(); }
  c2 = new Chart($('c2'), {type: 'line', data: {labels: L, datasets: [
    {label: '시가총액(조)', data: g.cap.slice(s0).map(v => nn(v) ? v / 10000 : null), borderColor: '#e6e6e6', borderWidth: 1.8, pointRadius: 0, yAxisID: 'y'},
    {label: '누적 외국인+기관(억)', data: cumNet(netOf(g, '합산')), borderColor: '#ff6b6b', borderWidth: 1.6, pointRadius: 0, yAxisID: 'y2'},
    {label: '누적 외국인(억)', data: cumNet(g.net['외국인']), borderColor: '#ffa94d', borderWidth: 1.2, borderDash: [4, 3], pointRadius: 0, yAxisID: 'y2', hidden: !C2SHOW[2]},
    {label: '누적 기관(억)', data: cumNet(g.net['기관']), borderColor: '#69db7c', borderWidth: 1.2, borderDash: [4, 3], pointRadius: 0, yAxisID: 'y2', hidden: !C2SHOW[3]}
  ]}, options: {responsive: true, maintainAspectRatio: false, animation: false, interaction: {mode: 'index', intersect: false},
    plugins: {legend: {labels: {color: '#e6e6e6', boxWidth: 12, font: {size: 11}}}, tooltip: {callbacks: {label: c => c.dataset.label + ': ' + (c.dataset.yAxisID === 'y' ? c.parsed.y.toFixed(1) + '조' : Math.round(c.parsed.y).toLocaleString() + '억')}}},
    scales: {x: {ticks: xTicks(), grid: {display: false}}, y: {position: 'left', ticks: {color: '#c9ccd1'}, grid: {color: '#23262e'}},
      y2: {position: 'right', grid: {display: false}, ticks: {color: '#ff8787', callback: v => Math.round(v).toLocaleString()}}}}});
  const capChg = nn(g.cap[s0]) && nn(g.cap[N - 1]) ? (g.cap[N - 1] / g.cap[s0] - 1) * 100 : null;
  $('t2').innerHTML = `${g.name} · ${D.dates[s0]} 이후 시총 <span class="${cls(capChg)}">${sg(capChg)}%</span>, 누적 외국인+기관 ${eok(cumNet(netOf(g, '합산')).slice(-1)[0])}`;
}
// ---------- 순위표
function renderTable() {
  const st = D.sectors.map(groupStats);
  const rankBy = (arr, key) => { const o = arr.filter(x => nn(x[key])).sort((a, b) => b[key] - a[key]); const m = {}; o.forEach((x, i) => m[x.g.name] = i + 1); return m; };
  const rk = rankBy(st, 'slow'), rk5 = rankBy(st, 'slow5');
  st.forEach(x => x.b = bandOf(x.g, x.r));
  const keyf = {c30: x => x.m30, bb: x => x.b.zNow, name: x => x.g.name, n: x => x.g.n, fast: x => x.fast, slow: x => x.slow, x: x => x.x ? (x.x.up ? 1 : -1) * (1000 - x.x.days) : null, n5: x => x.n5, n20: x => x.n20, c20: x => x.c20, cap: x => x.cap, rkchg: x => nn(rk5[x.g.name]) && nn(rk[x.g.name]) ? rk5[x.g.name] - rk[x.g.name] : null};
  const rows = st.slice().sort((a, b) => { const f = keyf[SORT], va = f(a), vb = f(b); if (SORT === 'name') return DESC ? vb.localeCompare(va) : va.localeCompare(vb); const x = nn(va) ? va : (DESC ? -1e18 : 1e18), y = nn(vb) ? vb : (DESC ? -1e18 : 1e18); return DESC ? y - x : x - y; });
  // 고른 섹터는 정렬과 관계없이 맨 위(2026-10-19 사용자 "그 섹터가 맨 위로 오게") - 펼친 종목 목록도 바로 아래
  if (TGT) { const k = rows.findIndex(x => x.g.name === TGT); if (k > 0) rows.unshift(rows.splice(k, 1)[0]); }
  const H = (k, t) => `<th data-s="${k}"${SORT === k ? ' class="sorted"' : ''}>${t}${SORT === k ? (DESC ? ' ▼' : ' ▲') : ''}</th>`;
  let h = '<tr>' + `<th class="nosort">${XS}일 순위</th>` + H('name', '섹터') + H('n', '종목') + H('fast', XF + '일 누적') + H('slow', XS + '일 누적') + H('c30', '30일 누적') + H('x', '교차(' + XF + '/' + XS + ')') + H('bb', '볼린저(z)') + H('n5', '5일 순매수') + H('n20', '20일 순매수') + H('c20', '20일 시총') + H('cap', '시총') + H('rkchg', '1주 전 순위') + '</tr>';
  rows.forEach(x => {
    const nm = x.g.name, chg = nn(rk5[nm]) && nn(rk[nm]) ? rk5[nm] - rk[nm] : null;
    h += `<tr class="sec${TGT === nm ? ' sel' : ''}" data-name="${nm}"><td>${rk[nm] || '-'}</td><td>${TGT === nm ? '<span style="color:#4dabf7">▶</span> ' : ''}${nm}</td><td>${x.g.n}</td>` +
      `<td class="${cls(x.fast)}">${cum(x.fast, XF)}</td><td class="${cls(x.slow)}"><b>${cum(x.slow, XS)}</b></td><td class="${cls(x.m30)}">${cum(x.m30, 30)}</td>` +
      `<td style="color:${x.x && x.x.up ? '#ff8787' : '#74a7ff'}">${x.x ? (x.x.up ? '▲ 골드 ' : '▼ 데드 ') + x.x.days + '일' + (x.x.days <= 3 ? ' <span style="color:#ffd43b;font-size:11px">새로</span>' : '') : '-'}</td>` +
      `<td style="color:${x.b.now > 0 ? '#ff8787' : x.b.now < 0 ? '#74a7ff' : '#9aa0a6'}">${nn(x.b.zNow) ? sg(x.b.zNow, 1) + (x.b.now ? ' ' + BNAME[x.b.now] + ' ' + x.b.days + '일' : '') : '-'}</td>` +
      `<td class="${cls(x.n5)}">${eok(x.n5)}</td><td class="${cls(x.n20)}">${eok(x.n20)}</td><td class="${cls(x.c20)}">${sg(x.c20)}%</td><td>${nn(x.cap) ? (x.cap / 10000).toFixed(1) + '조' : '-'}</td>` +
      `<td>${rk5[nm] || '-'}${nn(chg) && chg !== 0 ? ` <span class="${chg > 0 ? 'pos' : 'neg'}" style="font-size:11px">${chg > 0 ? '▲' : '▼'}${Math.abs(chg)}</span>` : ''}</td></tr>`;
    if (OPEN === nm) h += membersRow(x.g);
  });
  $('tbl').innerHTML = h;
  $('tbl').querySelectorAll('th[data-s]').forEach(th => th.onclick = () => { const s = th.dataset.s; if (SORT === s) DESC = !DESC; else { SORT = s; DESC = s !== 'name'; } renderTable(); });
  $('tbl').querySelectorAll('tr.sec').forEach(tr => tr.onclick = () => { const nm = tr.dataset.name; OPEN = OPEN === nm ? null : nm; TGT = nm; $('selT').value = nm; renderCharts(); renderBand(); renderTable(); });
}
function membersRow(g) {
  const k = WHO;
  const list = g.codes.map(c => ({c, ...D.stocks[c]})).filter(x => x.name).sort((a, b) => (nn(b[k][1]) ? b[k][1] : -1e9) - (nn(a[k][1]) ? a[k][1] : -1e9));
  const cell = x => `<span style="display:inline-block;margin:2px 14px 2px 0;white-space:nowrap">${x.name} <b class="${cls(x[k][1])}">${cum(x[k][1], 15)}</b> · 20일 <span class="${cls(x[k][3])}">${eok(x[k][3])}</span> · 주가 <span class="${cls(x.r20)}">${sg(x.r20)}%</span></span>`;
  return `<tr class="members"><td colspan="13">${g.name} 소속 종목 - ${k === '합산' ? '외국인+기관' : k} 15일 누적 수급(시총 대비 %) 높은 순 · 20일 순매수 · 20일 주가<br>${list.map(cell).join('')}</td></tr>`;
}
function renderAll() { renderCards(); renderCharts(); renderBand(); renderTable(); }
// ---------- 설정
$('selF').innerHTML = WINS.map(w => `<option value="${w}">${w}일</option>`).join(''); $('selF').value = XF;
$('selS').innerHTML = WINS.map(w => `<option value="${w}">${w}일</option>`).join(''); $('selS').value = XS;
$('selT').innerHTML = '<option value="">섹터 정리 전체</option>' + D.sectors.map(s => `<option value="${s.name}">${s.name}</option>`).join('');
$('selF').onchange = () => { XF = +$('selF').value; renderAll(); };
$('selS').onchange = () => { XS = +$('selS').value; renderAll(); };
$('selT').onchange = () => { TGT = $('selT').value; renderCharts(); renderBand(); renderTable(); };
const seg = (id, fn) => $(id).onclick = e => { const b = e.target.closest('button'); if (!b) return; document.querySelectorAll('#' + id + ' button').forEach(x => x.classList.toggle('active', x === b)); fn(b); };
seg('segWho', b => { WHO = b.dataset.v; renderAll(); });
seg('segHow', b => { HOW = b.dataset.v; renderAll(); });
seg('segP', b => { PERIOD = +b.dataset.p; renderCharts(); renderBand(); });
$('selBL').innerHTML = Array.from({length: 16}, (_, i) => i + 5).map(w => `<option value="${w}">${w}일선${w === 15 ? ' (기본)' : ''}</option>`).join(''); $('selBL').value = BL;
$('selBL').onchange = () => { BL = +$('selBL').value; renderAll(); };
seg('segBW', b => { BW = +b.dataset.v; renderAll(); });
seg('segBK', b => { BK = +b.dataset.v; renderAll(); });
$('study').innerHTML = `<b>수급과 주가 (${D.dates[1]} ~ ${D.asof} 검증, 섹터 정리 42섹터)</b><br>` +
  '· <b>같은 날</b>: 수급과 주가는 강하게 같이 움직입니다(섹터별 일간 상관 0.5~0.7, 시장 전체 0.71). 20일 누적으로 봐도 순매수가 많았던 섹터일수록 그 기간 시총도 많이 올랐습니다(섹터 간 순위상관 +0.53, 매번 양수). 이 관계는 기관(+0.56)이 외국인(+0.16)보다 훨씬 뚜렷합니다.<br>' +
  '· <b>수급이 주가를 앞서나</b>: 약합니다. 15일 평균 수급 상위 1/5 섹터가 하위 1/5보다 이후 20일 평균 +1.7~2.0%p 더 올랐지만, 겹치지 않는 20일 구간 20번 중 이긴 건 12번(60%) 정도라 우연과 잘 구분되지 않습니다. 종목 하나하나 단위로는 예측력이 없었습니다(순위상관 0 근처).<br>' +
  '· <b>주가가 수급을 끈다</b>: 최근 5·20일 많이 오른 섹터로 이후 수급이 더 들어옵니다(특히 기관 - 추종 매매, 순위상관 +0.15). 외국인은 약간 반대(오른 섹터를 덜 삼).<br>' +
  '· <b>교차·괴리</b>: 섹터 수급의 8일/15일 골드·데드크로스 뒤 이후 5·20일 상대수익은 골드와 데드가 비슷했습니다(이긴 비율 둘 다 45% 안팎) - 교차 자체로는 매매 신호가 안 됩니다. "주가는 약한데 수급은 강한" 섹터도 이후 계속 약했고(-0.7%p), 주가와 수급이 함께 강한 섹터가 가장 나았습니다(+2.2%p, 외국인 기준 +2.9%p).<br>' +
  '· <b>기준선 길이</b>(5~20일 비교): 12~18일은 두 해 모두 과매수 +, 과매도 - 로 안정적이었고, 11일 이하는 과매도 쪽은 맞지만 과매수 쪽이 해마다 흔들렸습니다(11일선: 과매수 뒤 +2.6 / +0.5%p, 과매도 뒤 -1.4 / -2.6%p). 밴드 기간 20일은 어떤 길이와도 일관성이 없었습니다.<br>' +
  '· <b>수급 볼린저</b>: 기준선·밴드 정의 8가지 x 주체·집계를 비교(96조합)했을 때 대부분은 2025년과 2026년 방향이 엇갈렸고, <b>15일선 + 60일 밴드(±2σ)</b>만 두 해 모두 같은 방향이었습니다 - 과매수 진입 섹터는 이후 20일 다른 섹터보다 강했고(+2.4%p / +2.6%p), 과매도 진입 섹터는 약했습니다(-1.2%p / -1.5%p). 즉 <b>되돌림이 아니라 추세 지속</b> 쪽입니다(외국인 기준 +2.5 / -2.1%p 비슷). 종목 단위도 같은 방향이었는데 기관 과매도 종목만 예외로 이후 반등했습니다. 시장 전체로는 <b>기관</b> 과매수 섹터 수 - 과매도 섹터 수만 이후 20일 시장(동일가중)과 두 해 모두 양의 관계였습니다(상관 +0.36 / +0.20). 이긴 비율은 50% 안팎이라 큰 수익 몇 번이 평균을 끌어올린 형태입니다.<br>' +
  '· <b>효과가 나타나는 기간</b>(외국인+기관·섹터 시총 합): 신호 뒤 1~5일은 거의 효과가 없고(2026년은 오히려 반대), 10~15일부터 나타나 20~40거래일(1~2달)에 걸쳐 쌓인 뒤 멈춥니다 - 볼린저 과매수 뒤 누적 +2.4/+2.6%p(20일) → +3.3/+7.8%p(40일), 15일선 순위 상위1/5-하위1/5도 +2.1/+2.1%p(20일) → +2.6/+6.6%p(40일). 단기 매매보다 1~2달 섹터 비중 조절용입니다. 외국인·기관 단독은 해마다 엇갈려 합산 기준이 맞습니다.<br>' +
  '· <b>쓰는 법</b>: 수급은 "지금 어느 섹터에 돈이 들어오고 있나"를 확인하는 지표로 보고, 단독 매매 신호보다는 가격 추세(섹터 이평선 위 비율)와 같은 방향인지 확인하는 용도가 맞습니다. 기간이 2025-01부터라 표본이 짧다는 점도 감안하세요.';
renderAll();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
