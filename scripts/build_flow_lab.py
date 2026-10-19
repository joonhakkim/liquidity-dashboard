"""
수급 국면·평균단가(실험) 페이지(docs/flow_lab.html) - 2026-10-19 사용자 요청으로 하루 동안 다듬음.
"누적 순매수가 이미 높은데 더 안 늘 때는 주가가 혼자 움직이는 것 같다 - 실험 버전으로 넣고 수급 평균단가까지" -> "따로 카드로" -> 단가 가설
-> "왜 60일" -> 40일 -> "외국인·기관 따로" -> "국면 통합(수급이랑 단가)".

정의(주체 = 외국인+기관 / 외국인 / 기관 각각)
- 수급 비율(bp) = 순매수 / 전일 시가총액 x 1e4. 60일 누적 = 최근 60일 비율 합 / 100(%), 8일 누적 = 최근 8일 합 / 100(%).
- 수급 평균단가 = 최근 COST_W(40)일 중 순매수한 날들의 매수량(비율) 가중 평균 시총(유효일 26일+), 평균단가 대비 = 현재 시총 / 평균단가 - 1.
  (20/40/60/90/120일 비교 flow12.py - 40일이 2025·2026 둘 다 가장 고름)
- 통합 국면(위에서부터 먼저 해당하는 것, 상위·하위 20% = 섹터는 42섹터끼리, 종목은 시총 1천억+ 분포의 경계):
  1 끌어올리기 = 평균단가 10%+ 위 & 8일 강한 매수(상위 20%)
  2 매집       = 60일 누적 상위 20% & 8일 순매수 & 평균단가 위
  3 이탈       = 8일 순매도 & (60일 누적 하위 20% 또는 (평균단가 아래 & 8일 강한 매도))      <- 옛 이탈 + 물린 채 매도
  4 쉬어가기   = 8일 순매도 & (60일 누적 상위 20% 또는 (평균단가 10%+ 위 & 8일 강한 매도))   <- 옛 정체 + 수익실현
  5 저점 매수  = 8일 순매수 & (60일 누적 하위 20% 또는 (평균단가 아래 & (강한 매수 또는 60일 상위)))  <- 옛 바닥 매수 + 단가 아래 매수(물타기)
  0 중간
검증(flow17.py, 2025-01~2026-10, 이후 20일 상대수익 2025/2026 %p, [겹치지 않는 20일 바스켓 이긴 횟수]):
  섹터 외국인: 끌어올리기 +7.1/+3.1 [9/13, 평균 +10.3], 매집 +1.4/-0.7, 쉬어가기 +1.5/+4.9, 저점 매수 +0.6/-0.4, 이탈 -1.3/-0.9, 중간 -0.7/-0.3.
  섹터 합산: 끌어올리기 +3.5/+3.9, 쉬어가기 +0.1/+3.7, 이탈 -1.2/-1.7(40일 -1.9/-1.7). 섹터 기관은 대체로 약함.
  종목: 끌어올리기 외국인 +3.4/+3.5 [12/18], 쉬어가기 +1.3/+1.4, 이탈 효과 없음. 중앙값은 전체와 비슷(효과 작음, 소수 큰 상승이 평균을 끎).
  예전 단계: 끌어올리기 효과 크기(flow14.py) 종목 20일 평균 +2.4 / 중앙값 -2.9(전체 -2.9) / 이긴 44% / 표준편차 28%p, 외국인 x 기관 조합(flow16.py)
  외국인 끌어올리기 + 기관 강한 매도 종목 +3.4/+3.2. 평균단가 대비 vs 60일 주가 수익률 순위상관 섹터 0.64, 종목 0.57(일부는 모멘텀).
데이터: 수급.xlsx(build_flow_analysis.load_flows), '기업 밴드 찾기 2' 시총, 종목 차트는 종목 수급 페이지가 만든 docs/flow_stock_data/{코드}.json 재사용.
파이프라인: run_pipeline.py에서 build_flow_stocks.py 다음.
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
OUT_PATH = os.path.join(BASE_DIR, "docs", "flow_lab.html")
NAVER_SECTOR_PATH = os.path.join(BASE_DIR, "data", "sector_map.csv")
WHO = ["합산", "외국인", "기관"]
COST_W, COST_MIN = 40, 26   # 수급 평균단가 창(거래일)과 최소 유효일 - 20~120일 비교해 40일(2026-10-19)


def unified_phase(L, M, G, hi, lo, mhi, mlo):
    """통합 국면(위에서부터 먼저 해당): 1 끌어올리기, 2 매집, 3 이탈, 4 쉬어가기, 5 저점 매수, 0 중간. 페이지 JS assignUnified와 같은 규칙."""
    if L is None or M is None or G is None:
        return None
    buy = M > 0
    sb, ss = buy and M >= mhi, (not buy) and M <= mlo
    top, bot = L >= hi, L <= lo
    if G >= 10 and sb:
        return 1
    if top and buy and G >= 0:
        return 2
    if not buy and (bot or (G < 0 and ss)):
        return 3
    if not buy and (top or (G >= 10 and ss)):
        return 4
    if buy and (bot or (G < 0 and (sb or top))):
        return 5
    return 0


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
    # ---------- 섹터(섹터 정리) - 일별 배열을 넘기고 상태·평균단가는 페이지 JS가 계산
    meta_u, members, ucodes, _c, _s, _n, _ = bsm.load_universe()
    secs = {s: [c for c in cs if c in codes] for s, cs in members.items()}
    secs = {s: cs for s, cs in secs.items() if len(cs) >= 3}
    eok = lambda s: [None if not np.isfinite(v) else round(float(v) / 1e8, 1) for v in s.values]
    sectors = []
    for sname, cs in sorted(secs.items()):
        sectors.append({"name": sname, "n": len(cs),
                        "cap": [None if not np.isfinite(v) else int(round(v / 1e8)) for v in cap[cs].sum(axis=1, min_count=1).values],
                        "capL": [None if not np.isfinite(v) else int(round(v / 1e8)) for v in capL[cs].sum(axis=1, min_count=1).values],
                        "F": eok(net["외국인"][cs].sum(axis=1, min_count=1)), "I": eok(net["기관"][cs].sum(axis=1, min_count=1))})
    # ---------- 종목: 상태·평균단가를 여기서 계산(표용)
    sm = pd.read_csv(NAVER_SECTOR_PATH, dtype=str)
    meta = pd.DataFrame({"name": sm["name"].values}, index=sm["code"].str.zfill(6).values)
    meta = meta[~meta.index.duplicated()]
    naver = dict(zip(sm["code"].str.zfill(6), sm["sector"]))
    usec, _ = assign_sectors(meta)
    sec_of = {c: (s if src == "U" else None) for c, (s, src) in usec.items()}
    r20 = (cap.iloc[-1] / cap.iloc[-21] - 1) * 100
    f = lambda v, d=2: None if v is None or not np.isfinite(v) else round(float(v), d)
    rows = []
    capv = cap.values
    for j, c in enumerate(codes):
        rec = {"c": c, "n": fnames.get(c) or meta["name"].get(c, c), "s": sec_of.get(c) or naver.get(c) or "-",
               "cap": int(round(capv[-1, j] / 1e8)) if np.isfinite(capv[-1, j]) else None, "r20": f(r20.get(c), 1)}
        cp = capv[-COST_W:, j]
        for w in WHO:
            r = (net[w][c] / capL[c] * 1e4).values
            r60 = r[-60:]
            L60 = np.nansum(r60) / 100 if np.isfinite(r60).sum() >= 48 else np.nan
            M8 = np.nansum(r[-8:]) / 100 if np.isfinite(r[-8:]).sum() >= 7 else np.nan
            rc = r[-COST_W:]   # 평균단가: 최근 COST_W일 중 순매수한 날의 매수량 가중 평균 시총
            wgt = np.where(np.isfinite(rc) & (rc > 0), rc, 0.0)
            ok = np.isfinite(cp) & (wgt > 0)
            cost = (wgt[ok] * cp[ok]).sum() / wgt[ok].sum() if ok.any() and np.isfinite(rc).sum() >= COST_MIN else np.nan
            gap = (capv[-1, j] / cost - 1) * 100 if np.isfinite(cost) and cost > 0 else np.nan
            rec[w] = [None, f(L60), f(M8), f(gap, 1), None]   # [통합 국면, 60일 누적 %, 8일 누적 %, 평균단가 대비 %, 8일 강도(1 강한 매수/-1 강한 매도/0)]
        rows.append(rec)
    for w in WHO:   # 상·하위 20% 경계 = 시총 1천억+ 종목 분포(검증과 같은 기준)
        bigL = np.array([r[w][1] for r in rows if r["cap"] and r["cap"] >= 1000 and r[w][1] is not None], float)
        bigM = np.array([r[w][2] for r in rows if r["cap"] and r["cap"] >= 1000 and r[w][2] is not None], float)
        if len(bigL) < 50 or len(bigM) < 50:
            continue
        hi, lo = np.quantile(bigL, 0.8), np.quantile(bigL, 0.2)
        mhi, mlo = np.quantile(bigM, 0.8), np.quantile(bigM, 0.2)
        for r in rows:
            L, M, G = r[w][1], r[w][2], r[w][3]
            if M is not None:
                r[w][4] = 1 if (M >= mhi and M > 0) else -1 if (M <= mlo and M <= 0) else 0
            r[w][0] = unified_phase(L, M, G, hi, lo, mhi, mlo)
    out = {"asof": idx[-1].strftime("%Y-%m-%d"), "dates": [x.strftime("%Y-%m-%d") for x in idx], "sectors": sectors, "stocks": rows}
    html = (TEMPLATE.replace("__UPDATED__", datetime.now().strftime("%Y-%m-%d %H:%M")).replace("__ASOF__", out["asof"])
            .replace("__FROM__", out["dates"][0]).replace("__CW__", str(COST_W)).replace("__CMIN__", str(COST_MIN)).replace("__DATA__", json.dumps(out, ensure_ascii=False, separators=(",", ":"))))
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"섹터 {len(sectors)}개, 종목 {len(rows)}개 | 저장 완료: {OUT_PATH} ({len(html) // 1024}KB)")


TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>수급 국면·평균단가 (실험)</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body { font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  a.back { color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }
  h1 { font-size:20px; margin:8px 0 4px 0; } h1 .sub { font-size:13px; color:#9aa0a6; font-weight:normal; margin-left:6px; }
  .exp { display:inline-block; background:#ffd43b; color:#0f1115; font-size:11px; font-weight:bold; border-radius:4px; padding:1px 6px; margin-left:6px; vertical-align:middle; }
  .updated { color:#9aa0a6; font-size:13px; margin-bottom:14px; }
  .ctl { display:flex; gap:10px; flex-wrap:wrap; align-items:center; font-size:12px; color:#c9ccd1; margin-bottom:12px; }
  .ctl select, .ctl input { background:#0f1115; color:#e6e6e6; border:1px solid #2a2e37; border-radius:6px; padding:4px 6px; font-family:inherit; font-size:12px; }
  .seg button { background:#1a1d24; border:1px solid #2a2e37; color:#c9ccd1; padding:5px 11px; border-radius:999px; cursor:pointer; font-size:12px; font-family:inherit; margin-right:4px; }
  .seg button.active { background:#4dabf7; color:#0f1115; border-color:#4dabf7; font-weight:bold; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:10px; margin-bottom:12px; }
  .card { background:#1a1d24; border:1px solid #23262e; border-radius:12px; padding:10px 12px; font-size:12px; color:#9aa0a6; line-height:1.5; }
  .card .val { font-size:19px; font-weight:700; margin:2px 0; }
  .box { background:#12151b; border:1px solid #23262e; border-radius:12px; padding:14px 16px; margin-bottom:16px; }
  .box h2 { font-size:15px; margin:0 0 6px 0; } .desc { font-size:12px; color:#9aa0a6; margin-bottom:8px; line-height:1.65; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  th, td { padding:5px 7px; border-bottom:1px solid #23262e; text-align:right; white-space:nowrap; }
  th:nth-child(-n+2), td:nth-child(-n+2) { text-align:left; }
  th { color:#9aa0a6; font-weight:normal; cursor:pointer; position:sticky; top:0; background:#12151b; } th.sorted { color:#4dabf7; }
  tr.rw { cursor:pointer; } tr.rw:hover { background:#1a1f28; } tr.sel { background:#1c2633; }
  .pos { color:#ff8787; } .neg { color:#74a7ff; } .sub { color:#7c828b; }
  .tblwrap { overflow:auto; max-height:560px; }
  .cw { position:relative; height:300px; }
  .note { font-size:12px; color:#9aa0a6; line-height:1.8; background:#1a1d24; border-radius:10px; padding:12px 16px; margin-bottom:16px; } .note b { color:#ffa94d; }
  #overlay { display:none; position:fixed; inset:0; background:#000a; z-index:10; overflow:auto; } #overlay.open { display:block; }
  #panel { background:#12151b; border:1px solid #2a2e37; border-radius:12px; max-width:1100px; margin:30px auto; padding:16px 18px; }
  .close { float:right; background:#2a2e37; color:#e6e6e6; border:0; border-radius:6px; padding:5px 12px; cursor:pointer; }
  @media (max-width: 640px) { body { padding:16px; } .cw { height:240px; } #panel { margin:0; border-radius:0; } }
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <a class="back" href="flow_analysis.html">섹터 수급 분석 &rarr;</a>
  <a class="back" href="flow_stocks.html">종목 수급 &rarr;</a>
  <h1>수급 국면·평균단가<span class="exp">실험</span><span class="sub">많이 샀나 · 지금도 사나 · 산 가격보다 위인가</span></h1>
  <div class="updated">최종 갱신: __UPDATED__ &middot; 데이터 __FROM__ ~ __ASOF__ &middot; 수급.xlsx(외국인·기관 순매수) + 기업 밴드 찾기 2 시가총액</div>
  <div class="ctl"><span>주체</span><span class="seg" id="segWho"><button data-v="합산" class="active">외국인+기관</button><button data-v="외국인">외국인</button><button data-v="기관">기관</button></span>
    <span>차트 기간</span><span class="seg" id="segP"><button data-p="126">6달</button><button data-p="250" class="active">1년</button><button data-p="0">전체</button></span></div>

  <div class="note" style="font-size:13px;line-height:1.9">
    <b>국면</b> = 외국인·기관이 ① 최근 3달 많이 샀나 ② 지금도 사나 ③ 산 가격(최근 40일 평균단가)보다 위인가를 합쳐 하나로 정한 것<br>
    <span style="color:#ff6b6b">● 끌어올리기</span> 산 가격보다 10%+ 위인데 또 강하게 삼 → <b>가장 강했음</b>(섹터·외국인 기준 이후 20일 +7.0 / +3.1%p, 2025 / 2026)<br>
    <span style="color:#ffa94d">● 매집</span> 많이 샀고 수익 중이고 지금도 삼 · <span style="color:#ffd43b">● 쉬어가기</span> 많이 사거나 오른 뒤 매수 멈춤·차익실현 → 둘 다 약한 플러스, <b>쉬어가기는 매도 신호 아님</b><br>
    <span style="color:#a9e34b">● 저점 매수</span> 많이 판 곳·물린 곳에서 사기 시작 → 아직 효과 없음 · <span style="color:#74a7ff">● 이탈</span> 많이 판 곳이 계속 팔거나 물린 채 팜 → <b>가장 약했음</b>(섹터 -1.2 / -1.7%p)<br>
    <span class="sub">효과가 작고 종목 하나하나엔 잘 안 맞음 - 1~2달 섹터 비중 참고용. 외국인 기준이 가장 잘 맞음.</span>
  </div>
  <details style="margin-bottom:16px"><summary style="cursor:pointer;color:#4dabf7;font-size:13px;margin-bottom:10px">자세한 설명·검증 결과 보기</summary>
  <div class="note">
    <b style="font-size:13px">세 가지 질문</b><br>
    <b>① 많이 샀나?</b> 최근 60일(약 3달) 순매수가 시가총액의 몇 %인지(60일 누적) - 다른 섹터·종목보다 상위 20%면 "많이 산 곳", 하위 20%면 "많이 판 곳".<br>
    <b>② 지금도 사나?</b> 최근 8일 순매수(8일 누적) - 상위 20%면 "강한 매수", 하위 20%면 "강한 매도".<br>
    <b>③ 산 가격보다 위인가?</b> 수급 평균단가 = 최근 40일 동안 순매수한 날들의 평균 매수 가격(많이 산 날에 무게). 예) 10,000원에 10억, 12,000원에 30억 샀으면 11,500원 - 지금 13,000원이면 +13%(산 쪽이 수익 중).
    <span class="sub">계산은 주가 대신 시가총액으로(같은 뜻). 40일은 20~120일을 비교해 두 해 모두 가장 고르게 맞은 기간.</span>
  </div>
  <div class="note">
    <b style="font-size:13px">국면 정하는 순서</b> (위에서부터 먼저 해당하는 것)<br>
    1. <span style="color:#ff6b6b">끌어올리기</span> - 평균단가보다 10%+ 위 & 지금 강한 매수<br>
    2. <span style="color:#ffa94d">매집</span> - 3달간 많이 산 곳 & 지금도 순매수 & 평균단가 위<br>
    3. <span style="color:#74a7ff">이탈</span> - 지금 순매도 & (3달간 많이 판 곳 또는 평균단가 아래에서 강한 매도)<br>
    4. <span style="color:#ffd43b">쉬어가기</span> - 지금 순매도 & (3달간 많이 산 곳 또는 평균단가 10%+ 위에서 강한 매도)<br>
    5. <span style="color:#a9e34b">저점 매수</span> - 지금 순매수 & (3달간 많이 판 곳 또는 평균단가 아래에서 강하게·많이 삼)<br>
    나머지는 중간(절반 정도).
  </div>
  <div class="note">
    <b style="font-size:13px">검증</b> - 국면별 이후 상대수익(다른 섹터·종목 평균 대비 %p, 2025 / 2026), [ ] = 20일씩 끊어 그 국면을 전부 샀을 때 이긴 횟수
    <div class="seg" id="segVal" style="margin-top:8px"><button data-h="20" class="active">이후 20일</button><button data-h="40">이후 40일</button></div>
    <div id="cmpT" style="display:flex;gap:14px;flex-wrap:wrap;margin-top:8px"></div>
    <div class="sub" style="margin-top:6px">외국인 쪽이 가장 깔끔하고 기관은 약함(기관은 주가를 따라 사는 편). 외국인이 사고 기관이 팔면 외국인 쪽을 따라감.
    종목은 효과가 작음 - 끌어올리기 종목도 보통 종목(중앙값)은 전체와 비슷하고, 평균은 크게 오른 몇 종목이 끌어올린 값. 평균단가 위 = 최근 주가가 오른 것과도 겹침(순위상관 0.6). 표본 2025-01부터 약 2년.</div>
  </div>
  </details>

  <div class="box">
    <h2>섹터 <span id="sCnt" style="font-size:12px;font-weight:normal;color:#9aa0a6"></span></h2>
    <div class="cards" id="sCards"></div>
    <div class="desc">섹터 정리 42섹터(섹터 순매수 합 / 섹터 시총 합). 행을 누르면 아래 차트가 그 섹터로 바뀝니다.</div>
    <div class="tblwrap" style="max-height:420px"><table id="sTbl"></table></div>
    <div class="desc" style="margin-top:12px" id="sChartTitle"></div>
    <div class="cw"><canvas id="cS"></canvas></div>
  </div>

  <div class="box">
    <h2>종목 <span id="kCnt" style="font-size:12px;font-weight:normal;color:#9aa0a6"></span></h2>
    <div class="cards" id="kCards"></div>
    <div class="ctl">
      <span>시총</span><select id="selCap"><option value="0">전체</option><option value="500">500억+</option><option value="1000" selected>1,000억+</option><option value="3000">3,000억+</option><option value="10000">1조+</option></select>
      <span>보기</span><select id="selSt"><option value="">전체</option><option value="u1" selected>끌어올리기</option><option value="u2">매집</option><option value="u4">쉬어가기</option><option value="u5">저점 매수</option><option value="u3">이탈</option>
        <optgroup label="외국인 x 기관 (주체 버튼과 무관)"><option value="xf">외국인 끌어올리기</option><option value="xb">외국인·기관 둘 다 끌어올리기</option><option value="xfi">외국인 끌어올리기 + 기관 강한 매도</option><option value="xif">기관 끌어올리기 + 외국인 강한 매도</option><option value="xf3">외국인 이탈</option></optgroup></select>
      <span>섹터</span><select id="selSec"></select>
      <span>검색</span><input id="q" placeholder="종목명·코드" style="width:110px">
    </div>
    <div class="desc">상·하위 20% 경계는 시총 1천억+ 종목 분포 기준(작은 종목은 참고만). 행을 누르면 차트.</div>
    <div class="tblwrap"><table id="kTbl"></table></div>
  </div>

  <div id="overlay"><div id="panel">
    <button class="close" id="closeBtn">닫기</button>
    <h2 id="dTitle" style="font-size:16px;margin:0 0 4px 0"></h2>
    <div class="desc" id="dSub"></div>
    <div class="cw"><canvas id="cK"></canvas></div>
  </div></div>

<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
const nn = v => v !== null && v !== undefined && !isNaN(v);
const N = D.dates.length;
const sg = (v, d = 1) => nn(v) ? (v >= 0 ? '+' : '') + v.toFixed(d) : '-';
const cls = v => nn(v) ? (v >= 0 ? 'pos' : 'neg') : '';
const UN = {1: '끌어올리기', 2: '매집', 4: '쉬어가기', 5: '저점 매수', 3: '이탈', 0: '중간'}, UC = {1: '#ff6b6b', 2: '#ffa94d', 4: '#ffd43b', 5: '#a9e34b', 3: '#74a7ff', 0: '#7c828b'};
const UORD = [1, 2, 4, 5, 3], URANK = {1: 5, 2: 4, 4: 3, 0: 2, 5: 1, 3: 0};
// 검증(flow17.py 통합 국면, flow16.py 조합): SUMM[대상][주체][국면] = 이후 20·40일 [2025, 2026] %p, bk = 겹치지 않는 20일 바스켓 이긴 횟수, md = 중앙값
const SUMM = {"섹터":{"합산":{"끌어올리기":{"20":[3.5,3.93],"40":[5.63,7.07],"bk":"7/13","bkm":-0.4,"md":0.0,"share":5},"매집":{"20":[-0.12,0.75],"40":[0.61,1.78],"bk":"7/15","bkm":-0.7,"md":-1.0,"share":9},"쉬어가기":{"20":[0.09,3.74],"40":[1.2,0.59],"bk":"12/16","bkm":4.7,"md":-0.1,"share":5},"중간":{"20":[0.21,-0.37],"40":[0.07,-0.79],"bk":"7/18","bkm":0.0,"md":-1.6,"share":48},"저점 매수":{"20":[-0.29,0.2],"40":[-0.46,0.88],"bk":"6/16","bkm":2.1,"md":-1.6,"share":12},"이탈":{"20":[-1.2,-1.72],"40":[-1.94,-1.74],"bk":"8/18","bkm":-1.2,"md":-2.5,"share":20}},"외국인":{"끌어올리기":{"20":[7.05,3.11],"40":[10.84,6.13],"bk":"9/13","bkm":10.3,"md":1.4,"share":4},"매집":{"20":[1.42,-0.67],"40":[3.91,-0.87],"bk":"7/17","bkm":0.6,"md":-0.3,"share":8},"쉬어가기":{"20":[1.45,4.92],"40":[1.67,5.07],"bk":"8/17","bkm":2.4,"md":-0.5,"share":7},"중간":{"20":[-0.66,-0.31],"40":[-1.17,-0.9],"bk":"5/18","bkm":-1.0,"md":-1.7,"share":48},"저점 매수":{"20":[0.58,-0.44],"40":[-0.3,0.43],"bk":"9/18","bkm":0.2,"md":-1.7,"share":14},"이탈":{"20":[-1.27,-0.91],"40":[-1.53,-1.01],"bk":"10/18","bkm":-0.4,"md":-2.5,"share":19}},"기관":{"끌어올리기":{"20":[0.25,4.7],"40":[0.97,5.41],"bk":"5/11","bkm":1.1,"md":-1.3,"share":6},"매집":{"20":[-1.04,-0.35],"40":[-1.24,0.81],"bk":"8/17","bkm":-1.1,"md":-2.5,"share":8},"쉬어가기":{"20":[0.67,0.77],"40":[1.25,-0.28],"bk":"6/17","bkm":-3.6,"md":-0.8,"share":6},"중간":{"20":[0.29,0.28],"40":[0.22,0.43],"bk":"9/18","bkm":0.3,"md":-1.5,"share":48},"저점 매수":{"20":[-1.27,-0.57],"40":[-0.8,0.18],"bk":"6/18","bkm":-2.3,"md":-2.1,"share":12},"이탈":{"20":[0.19,-1.78],"40":[-0.14,-3.17],"bk":"10/18","bkm":0.7,"md":-1.8,"share":21}}},"종목(1천억+)":{"합산":{"끌어올리기":{"20":[2.43,2.78],"40":[3.45,3.67],"bk":"10/18","bkm":1.1,"md":-2.9,"share":5},"매집":{"20":[0.26,0.43],"40":[1.48,2.2],"bk":"7/18","bkm":-0.6,"md":-3.2,"share":6},"쉬어가기":{"20":[1.32,2.11],"40":[2.35,1.66],"bk":"11/18","bkm":1.5,"md":-3.2,"share":5},"중간":{"20":[-0.21,0.13],"40":[-0.54,0.64],"bk":"8/18","bkm":-0.0,"md":-2.9,"share":49},"저점 매수":{"20":[0.19,-0.98],"40":[0.49,-1.02],"bk":"9/18","bkm":-0.2,"md":-3.5,"share":14},"이탈":{"20":[-0.11,0.4],"40":[0.2,0.3],"bk":"8/18","bkm":0.1,"md":-3.0,"share":21}},"외국인":{"끌어올리기":{"20":[3.4,3.45],"40":[4.69,3.9],"bk":"12/18","bkm":3.3,"md":-2.5,"share":4},"매집":{"20":[0.9,0.69],"40":[2.14,2.89],"bk":"10/18","bkm":0.2,"md":-2.9,"share":6},"쉬어가기":{"20":[1.26,1.44],"40":[2.66,1.24],"bk":"13/18","bkm":1.3,"md":-3.3,"share":6},"중간":{"20":[-0.14,0.0],"40":[-0.53,0.22],"bk":"7/18","bkm":-0.2,"md":-2.9,"share":48},"저점 매수":{"20":[0.25,-0.36],"40":[0.72,-0.09],"bk":"8/18","bkm":-0.1,"md":-3.3,"share":16},"이탈":{"20":[-0.68,0.37],"40":[-0.47,0.65],"bk":"7/18","bkm":0.1,"md":-3.3,"share":20}},"기관":{"끌어올리기":{"20":[1.43,2.08],"40":[2.77,2.64],"bk":"11/18","bkm":0.3,"md":-3.2,"share":5},"매집":{"20":[-0.19,0.74],"40":[0.68,1.33],"bk":"6/18","bkm":-0.4,"md":-3.3,"share":5},"쉬어가기":{"20":[1.03,0.49],"40":[1.25,0.02],"bk":"12/18","bkm":1.3,"md":-3.0,"share":5},"중간":{"20":[-0.38,0.24],"40":[-0.96,0.45],"bk":"7/18","bkm":-0.1,"md":-3.0,"share":50},"저점 매수":{"20":[-0.37,-0.67],"40":[-0.11,-0.74],"bk":"8/18","bkm":0.2,"md":-3.3,"share":14},"이탈":{"20":[0.53,-0.02],"40":[0.98,-0.22],"bk":"9/18","bkm":0.0,"md":-2.8,"share":21}}}};
const COMBO = {"섹터":{"둘 다 끌어올리기":{"20":[5.39,1.95],"40":[10.25,7.53],"n":242},"외국인 끌어올리기 + 기관 강한 매도":{"20":[15.03,0.8],"40":[22.05,-5.41],"n":100},"기관 끌어올리기 + 외국인 강한 매도":{"20":[-2.17,9.5],"40":[-4.23,4.56],"n":272}},"종목(1천억+)":{"둘 다 끌어올리기":{"20":[2.92,2.5],"40":[5.13,1.92],"n":6007},"외국인 끌어올리기 + 기관 강한 매도":{"20":[3.43,3.2],"40":[3.9,5.77],"n":4936},"기관 끌어올리기 + 외국인 강한 매도":{"20":[0.31,2.63],"40":[1.24,3.17],"n":7331}}};
const capTxt = v => !nn(v) ? '-' : (v >= 10000 ? (v / 10000).toFixed(1) + '조' : v.toLocaleString() + '억');
let WHO = '합산', VH = 20, P = 250, TGT = null, sSort = 'u', sDesc = true, kSort = 'cg', kDesc = true, CUR = null, cS = null, cK = null;
Chart.defaults.color = '#9aa0a6'; Chart.defaults.borderColor = '#23262e';
// ---------- 공통 계산(일별 순매수 억 + 전일 시총 억 -> 비율 bp, 누적, 평균단가)
function netArr(F, I, w = WHO) { return F.map((v, i) => (nn(v) || nn(I[i])) ? (w === '외국인' ? v : w === '기관' ? I[i] : (v || 0) + (I[i] || 0)) : null); }
const CW = __CW__, CMIN = __CMIN__;   // 수급 평균단가 창(거래일), 최소 유효일
function costLine(r, cap) { return cap.map((_, t) => { if (t < CW - 1) return null; let sw = 0, sp = 0, c = 0;
  for (let i = t - CW + 1; i <= t; i++) { if (nn(r[i])) c++; if (nn(r[i]) && r[i] > 0 && nn(cap[i])) { sw += r[i]; sp += r[i] * cap[i]; } }
  return c >= CMIN && sw > 0 ? sp / sw : null; }); }
function sumLast(a, n) { let s = 0, c = 0; for (let i = N - n; i < N; i++) if (nn(a[i])) { s += a[i]; c++; } return c >= n * 0.8 ? s : null; }
function sectorStats(g, w = WHO) {
  const nt = netArr(g.F, g.I, w), r = nt.map((v, i) => nn(v) && nn(g.capL[i]) && g.capL[i] > 0 ? v / g.capL[i] * 1e4 : null), cost = costLine(r, g.cap);
  const s60 = sumLast(r, 60), s8 = sumLast(r, 8), k = N - 1;
  return {g, r, nt, cost, L60: nn(s60) ? s60 / 100 : null, M8: nn(s8) ? s8 / 100 : null, cg: nn(cost[k]) && nn(g.cap[k]) ? (g.cap[k] / cost[k] - 1) * 100 : null,
    c20: nn(g.cap[k]) && nn(g.cap[k - 20]) ? (g.cap[k] / g.cap[k - 20] - 1) * 100 : null};
}
function qOf(v, x) { let c = 0; while (c < v.length && v[c] <= x) c++; return c / v.length; }
function assignUnified(list) {   // 통합 국면 - 상·하위 20%는 섹터끼리 비교(Python unified_phase와 같은 규칙)
  const vL = list.filter(x => nn(x.L60)).map(x => x.L60).sort((a, b) => a - b), vM = list.filter(x => nn(x.M8)).map(x => x.M8).sort((a, b) => a - b);
  list.forEach(x => { x.u = null; x.sb = 0; if (!nn(x.M8) || !vM.length) return;
    const qM = qOf(vM, x.M8), buy = x.M8 > 0, sb = buy && qM >= 0.8, ss = !buy && qM <= 0.2; x.sb = sb ? 1 : ss ? -1 : 0;
    if (!nn(x.L60) || !nn(x.cg)) return;
    const qL = qOf(vL, x.L60), top = qL >= 0.8, bot = qL <= 0.2, G = x.cg;
    x.u = G >= 10 && sb ? 1 : top && buy && G >= 0 ? 2 : !buy && (bot || (G < 0 && ss)) ? 3 : !buy && (top || (G >= 10 && ss)) ? 4 : buy && (bot || (G < 0 && (sb || top))) ? 5 : 0; });
}
function chartOf(canvas, old, L, s0, cap, nt, cost, title) {
  if (old) old.destroy();
  const big = Math.max(...cap.slice(s0).filter(nn)) >= 10000, sc = v => nn(v) ? (big ? v / 10000 : v) : null;
  let t = 0; const cum = nt.slice(s0).map(v => { t += nn(v) ? v : 0; return t; });
  return new Chart(canvas, {type: 'line', data: {labels: L, datasets: [
    {label: big ? '시가총액(조)' : '시가총액(억)', data: cap.slice(s0).map(sc), borderColor: '#e6e6e6', borderWidth: 1.8, pointRadius: 0, yAxisID: 'y'},
    {label: '수급 평균단가(' + CW + '일)', data: cost.slice(s0).map(sc), borderColor: '#ffd43b', borderWidth: 1.6, borderDash: [6, 3], pointRadius: 0, yAxisID: 'y'},
    {label: '누적 순매수(억, 오른쪽)', data: cum, borderColor: '#ff6b6b99', borderWidth: 1.3, pointRadius: 0, yAxisID: 'y2'}
  ]}, options: {responsive: true, maintainAspectRatio: false, animation: false, interaction: {mode: 'index', intersect: false},
    plugins: {legend: {labels: {color: '#e6e6e6', boxWidth: 12, font: {size: 11}}}, tooltip: {callbacks: {label: c => c.dataset.label + ': ' + (c.dataset.yAxisID === 'y2' ? Math.round(c.parsed.y).toLocaleString() + '억' : c.parsed.y.toFixed(big ? 2 : 0) + (big ? '조' : '억')),
      afterBody: it => { const i = s0 + it[0].dataIndex; return nn(cost[i]) && nn(cap[i]) ? '평균단가 대비 ' + sg((cap[i] / cost[i] - 1) * 100) + '%' : ''; }}}},
    scales: {x: {ticks: {color: '#9aa0a6', maxTicksLimit: 8, maxRotation: 0}, grid: {display: false}}, y: {position: 'left', ticks: {color: '#c9ccd1'}, grid: {color: '#23262e'}}, y2: {position: 'right', grid: {display: false}, ticks: {color: '#ff8787'}}}}});
}
const cardsOf = (list, getU, extra) => UORD.map(q => { const xs = list.filter(x => getU(x) === q); return `<div class="card"><span style="color:${UC[q]}">●</span> ${UN[q]}<div class="val" style="color:${UC[q]}">${xs.length}</div>${extra(xs)}</div>`; }).join('');
const uTd = u => `<td style="color:${nn(u) ? UC[u] : '#7c828b'}">${nn(u) ? UN[u] : '-'}</td>`;
const WN = w => w === '합산' ? '외국인+기관' : w;
const pair = v => nn(v[0]) || nn(v[1]) ? v.map(x => nn(x) ? `<span class="${cls(x)}">${sg(Math.abs(x) < 0.05 ? 0 : x)}</span>` : '-').join(' / ') : '-';
function renderCmp() {
  const th = t => `<th style="cursor:default;text-align:right;background:none">${t}</th>`;
  $('cmpT').innerHTML = Object.entries(SUMM).map(([k, S]) => `<table style="width:auto;font-size:11px"><tr><th style="cursor:default;text-align:left;background:none">${k} · 이후 ${VH}일 (2025 / 2026) [바스켓]</th>` +
    ['외국인+기관', '외국인', '기관'].map(th).join('') + '</tr>' +
    [...UORD.slice(0, 3), 0, ...UORD.slice(3)].map(u => `<tr><td style="color:${UC[u]}">${UN[u]}</td>` + ['합산', '외국인', '기관'].map(w => { const x = S[w][UN[u]];
      return `<td style="text-align:right${w === WHO ? ';background:#1c2633' : ''}">${pair(x[VH])} <span class="sub">[${x.bk}]</span></td>`; }).join('') + '</tr>').join('') + '</table>').join('') +
    `<table style="width:auto;font-size:11px"><tr><th style="cursor:default;text-align:left;background:none">외국인 x 기관 · 이후 ${VH}일 (2025 / 2026)</th>${th('섹터')}${th('종목(1천억+)')}</tr>` +
    Object.keys(COMBO['섹터']).map(nm => `<tr><td>${nm}</td>` + ['섹터', '종목(1천억+)'].map(k => `<td style="text-align:right">${pair(COMBO[k][nm][VH])} <span class="sub">n=${COMBO[k][nm].n.toLocaleString()}</span></td>`).join('') + '</tr>').join('') + '</table>';
}
renderCmp();
// ---------- 섹터
let SST = [];
function renderSectors() {
  SST = D.sectors.map(g => sectorStats(g)); assignUnified(SST);
  const byW = {};   // 외국인·기관 각각의 국면(표에 나란히)
  ['외국인', '기관'].forEach(w => { const L = D.sectors.map(g => sectorStats(g, w)); assignUnified(L); L.forEach(x => (byW[x.g.name] = byW[x.g.name] || {})[w] = x.u); });
  SST.forEach(x => { x.uF = byW[x.g.name]['외국인']; x.uI = byW[x.g.name]['기관']; });
  $('sCards').innerHTML = cardsOf(SST, x => x.u, xs => xs.map(x => x.g.name).join(', ') || '-');
  const key = {name: x => x.g.name, u: x => URANK[x.u], uF: x => URANK[x.uF], uI: x => URANK[x.uI], L60: x => x.L60, M8: x => x.M8, cg: x => x.cg, c20: x => x.c20, cap: x => x.g.cap[N - 1]}[sSort];
  const rows = SST.slice().sort((a, b) => { const va = key(a), vb = key(b); if (typeof va === 'string') return sDesc ? vb.localeCompare(va) : va.localeCompare(vb); const x = nn(va) ? va : (sDesc ? -1e18 : 1e18), y = nn(vb) ? vb : (sDesc ? -1e18 : 1e18); return sDesc ? y - x : x - y; });
  const H = (k, t) => `<th data-s="${k}"${sSort === k ? ' class="sorted"' : ''}>${t}${sSort === k ? (sDesc ? ' ▼' : ' ▲') : ''}</th>`;
  let h = '<tr>' + H('name', '섹터') + H('u', '국면') + H('uF', '외국인') + H('uI', '기관') + H('L60', '60일 누적') + H('M8', '8일 누적') + H('cg', '평균단가 대비') + H('c20', '20일 시총') + H('cap', '시총') + '</tr>';
  rows.forEach(x => h += `<tr class="rw${TGT === x.g.name ? ' sel' : ''}" data-n="${x.g.name}"><td>${x.g.name} <span class="sub">${x.g.n}</span></td>${uTd(x.u)}${uTd(x.uF)}${uTd(x.uI)}` +
    `<td class="${cls(x.L60)}">${sg(x.L60, 2)}%</td><td class="${cls(x.M8)}">${sg(x.M8, 2)}%</td><td class="${cls(x.cg)}"><b>${sg(x.cg)}%</b></td><td class="${cls(x.c20)}">${sg(x.c20)}%</td><td>${capTxt(x.g.cap[N - 1])}</td></tr>`);
  $('sTbl').innerHTML = h;
  $('sCnt').textContent = `${WHO === '합산' ? '외국인+기관' : WHO} 기준 · ${D.asof}`;
  $('sTbl').querySelectorAll('th[data-s]').forEach(th => th.onclick = () => { const k = th.dataset.s; if (sSort === k) sDesc = !sDesc; else { sSort = k; sDesc = k !== 'name'; } renderSectors(); });
  $('sTbl').querySelectorAll('tr.rw').forEach(tr => tr.onclick = () => { TGT = tr.dataset.n; renderSectors(); });
  if (!TGT) TGT = rows[0] ? rows[0].g.name : null;
  const x = SST.find(y => y.g.name === TGT); if (!x) return;
  const s0 = P ? Math.max(0, N - P) : 0;
  cS = chartOf($('cS'), cS, D.dates.slice(s0), s0, x.g.cap, x.nt, x.cost);
  $('sChartTitle').innerHTML = `<b style="color:#e6e6e6">${x.g.name}</b> · 국면 <b style="color:${nn(x.u) ? UC[x.u] : '#9aa0a6'}">${nn(x.u) ? UN[x.u] : '-'}</b>(외국인 ${nn(x.uF) ? UN[x.uF] : '-'}, 기관 ${nn(x.uI) ? UN[x.uI] : '-'}) · 60일 누적 ${sg(x.L60, 2)}% · 8일 누적 ${sg(x.M8, 2)}% · 평균단가 대비 <b class="${cls(x.cg)}">${sg(x.cg)}%</b> - 흰 선 = 시가총액, 노란 점선 = 수급 평균단가(최근 ${CW}일, 시총 환산 - 흰 선이 위면 산 쪽이 수익 중), 빨간 선 = 차트 시작일부터 누적 순매수`;
}
// ---------- 종목
const secs = [...new Set(D.stocks.map(r => r.s))].sort();
$('selSec').innerHTML = '<option value="">전체</option>' + secs.map(s => `<option>${s}</option>`).join('');
function renderStocks() {
  const capMin = +$('selCap').value, st = $('selSt').value, sec = $('selSec').value, q = $('q').value.trim().toLowerCase(), A = r => r[WHO];
  const base = D.stocks.filter(r => (!capMin || r.cap >= capMin) && (!sec || r.s === sec));
  $('kCards').innerHTML = cardsOf(base, r => A(r)[0], xs => xs.slice().sort((a, b) => (b.cap || 0) - (a.cap || 0)).slice(0, 4).map(r => r.n).join(', ') + (xs.length > 4 ? ' 등' : ''));
  const rows = base.filter(r => {
    if (q && !(r.n.toLowerCase().includes(q) || r.c.includes(q))) return false;
    const a = A(r);
    const f = r['외국인'], i = r['기관'];
    if (st === 'xf') return f[0] === 1;
    if (st === 'xb') return f[0] === 1 && i[0] === 1;
    if (st === 'xfi') return f[0] === 1 && i[4] === -1;
    if (st === 'xif') return i[0] === 1 && f[4] === -1;
    if (st === 'xf3') return f[0] === 3;
    if (st[0] === 'u') return a[0] === +st[1];
    return true; });
  const key = {n: r => r.n, s: r => r.s, cap: r => r.cap, r20: r => r.r20, u: r => URANK[A(r)[0]], uF: r => URANK[r['외국인'][0]], uI: r => URANK[r['기관'][0]], L60: r => A(r)[1], M8: r => A(r)[2], cg: r => A(r)[3]}[kSort];
  rows.sort((a, b) => { const va = key(a), vb = key(b); if (typeof va === 'string') return kDesc ? vb.localeCompare(va) : va.localeCompare(vb); const x = nn(va) ? va : (kDesc ? -1e18 : 1e18), y = nn(vb) ? vb : (kDesc ? -1e18 : 1e18); return kDesc ? y - x : x - y; });
  const H = (k, t) => `<th data-s="${k}"${kSort === k ? ' class="sorted"' : ''}>${t}${kSort === k ? (kDesc ? ' ▼' : ' ▲') : ''}</th>`;
  let h = '<tr>' + H('n', '종목') + H('s', '섹터') + H('cap', '시총') + H('u', '국면') + H('uF', '외국인') + H('uI', '기관') + H('L60', '60일 누적') + H('M8', '8일 누적') + H('cg', '평균단가 대비') + H('r20', '20일 주가') + '</tr>';
  rows.slice(0, 300).forEach(r => { const a = A(r); h += `<tr class="rw" data-c="${r.c}"><td>${r.n} <span class="sub">${r.c}</span></td><td class="sub">${r.s}</td><td>${capTxt(r.cap)}</td>` +
    `${uTd(a[0])}${uTd(r['외국인'][0])}${uTd(r['기관'][0])}<td class="${cls(a[1])}">${sg(a[1], 2)}%</td><td class="${cls(a[2])}">${sg(a[2], 2)}%</td><td class="${cls(a[3])}"><b>${sg(a[3])}%</b></td><td class="${cls(r.r20)}">${sg(r.r20)}%</td></tr>`; });
  $('kTbl').innerHTML = h;
  $('kCnt').textContent = `${rows.length.toLocaleString()}종목${rows.length > 300 ? ' (위 300개만 표시)' : ''} · ${WHO === '합산' ? '외국인+기관' : WHO} 기준`;
  $('kTbl').querySelectorAll('th[data-s]').forEach(th => th.onclick = () => { const k = th.dataset.s; if (kSort === k) kDesc = !kDesc; else { kSort = k; kDesc = !(k === 'n' || k === 's'); } renderStocks(); });
  $('kTbl').querySelectorAll('tr.rw').forEach(tr => tr.onclick = () => openStock(tr.dataset.c));
}
function openStock(c) { fetch(`flow_stock_data/${c}.json?t=${D.asof}`).then(r => r.json()).then(d => { CUR = {d, r: D.stocks.find(x => x.c === c)}; drawStock(); $('overlay').classList.add('open'); }); }
function drawStock() {
  const {d, r} = CUR, s0 = P ? Math.max(0, N - P) : 0, a = r[WHO];
  const nt = netArr(d.F, d.I).map(v => nn(v) ? v / 100 : null);                       // 백만원 -> 억
  const ratio = nt.map((v, i) => i > 0 && nn(v) && nn(d.cap[i - 1]) && d.cap[i - 1] > 0 ? v / d.cap[i - 1] * 1e4 : null);
  const cost = costLine(ratio, d.cap);
  cK = chartOf($('cK'), cK, D.dates.slice(s0), s0, d.cap, nt, cost);
  $('dTitle').textContent = `${r.n} (${r.c}) · ${r.s}`;
  $('dSub').innerHTML = `${WHO === '합산' ? '외국인+기관' : WHO} · 시총 ${capTxt(r.cap)} · 국면 <b style="color:${nn(a[0]) ? UC[a[0]] : '#9aa0a6'}">${nn(a[0]) ? UN[a[0]] : '-'}</b>(외국인 ${nn(r['외국인'][0]) ? UN[r['외국인'][0]] : '-'}, 기관 ${nn(r['기관'][0]) ? UN[r['기관'][0]] : '-'}) · 60일 누적 ${sg(a[1], 2)}% · 8일 누적 ${sg(a[2], 2)}% · 평균단가 대비 <b class="${cls(a[3])}">${sg(a[3])}%</b> · 20일 주가 <span class="${cls(r.r20)}">${sg(r.r20)}%</span><br>흰 선 = 시가총액, 노란 점선 = 수급 평균단가(최근 ${CW}일 순매수한 날들의 평균 매수 가격, 시총 환산) - 흰 선이 노란 선 위면 산 쪽이 수익 중, 빨간 선 = 차트 시작일부터 누적 순매수`;
}
$('closeBtn').onclick = () => $('overlay').classList.remove('open');
$('overlay').onclick = e => { if (e.target.id === 'overlay') $('overlay').classList.remove('open'); };
const seg = (id, fn) => $(id).onclick = e => { const b = e.target.closest('button'); if (!b) return; document.querySelectorAll('#' + id + ' button').forEach(x => x.classList.toggle('active', x === b)); fn(b); };
seg('segWho', b => { WHO = b.dataset.v; renderCmp(); renderSectors(); renderStocks(); if (CUR && $('overlay').classList.contains('open')) drawStock(); });
seg('segP', b => { P = +b.dataset.p; renderSectors(); if (CUR && $('overlay').classList.contains('open')) drawStock(); });
seg('segVal', b => { VH = +b.dataset.h; renderCmp(); });
['selCap', 'selSt', 'selSec'].forEach(id => $(id).onchange = renderStocks);
$('q').oninput = renderStocks;
renderSectors(); renderStocks();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
