"""
수급 상태·평균단가(실험) 페이지(docs/flow_lab.html) - 2026-10-19 사용자 요청.
"누적 순매수가 같이 따라가다가 이미 높은데 더 안 늘 때는 주가가 혼자 움직이는 것 같다 - 실험 버전으로 넣고 수급 평균단가까지"
-> 처음엔 수급 분석·종목 수급 페이지에 칸으로 넣었다가 사용자 "이렇게 하지 말고 따로 카드를 만들어줘"로 독립 페이지(홈 카드)로 분리.

정의
- 수급 비율(bp) = (외국인·기관·합산) 순매수 / 전일 시가총액 x 1e4. 60일 누적 = 최근 60일 비율 합 / 100(%), 8일 누적 = 최근 8일 합 / 100(%).
- 수급 상태: 60일 누적이 단면 상위 20% / 하위 20%(섹터는 42섹터끼리, 종목은 시총 1천억+ 종목 분포의 경계) x 8일 누적 방향
  -> 매집 진행(상위 & 8일 > 0) / 정체(상위 & 8일 <= 0) / 바닥 매수(하위 & 8일 > 0) / 이탈(하위 & 8일 <= 0) / 중간.
- 수급 평균단가: 최근 COST_W(40)일 동안 순매수한 날들의 매수량(시총 대비 비율) 가중 평균 시총 = 평균 매수가를 시총으로 환산(유효일 26일 이상).
  평균단가 대비 = 현재 시총 / 평균단가 - 1. 처음엔 60일이었다가 사용자 "왜 60일임?" -> flow12.py에서 20/40/60/90/120일 비교, 40일이 2025·2026
  둘 다 가장 고르게 맞아서 40일로(같은 날). 60일 누적(수급 상태)은 길수록 '이탈'이 뚜렷해서 60일 유지.
- 단가 국면(같은 날 사용자 가설 "평균단가보다 높은데 매집하면 주가를 계속 올리려는 것, 단가 위에서 매도하면 수익실현이라 하락할 것"):
  평균단가 10%+ 위 / 아래 x 최근 8일 누적 단면 상위 20%(강한 매수) / 하위 20%(강한 매도) -> 끌어올리기 / 수익실현 / 단가 아래 매수 / 물린 채 매도.
검증(40일 평균단가, 2025-01~2026-10, scratchpad flow/flow13.py, 이후 상대수익 2025/2026 %p):
  끌어올리기 맞음(20일 종목 +2.1/+2.8, 섹터 +3.3/+3.9, 40일 섹터 +5.4/+7.1). 수익실현 -> 하락 아님(20일 종목 +1.1/+2.5, 섹터 +0.6/+2.4, 40일 섹터 +0.2/-3.7).
  물린 채 매도 섹터 가장 약함(20일 -1.1/-1.9, 40일 -1.4/-1.8), 종목은 효과 없음. 매집 진행 & 단가 10%+ 위 종목 20일 +2.4/+2.7(40일 +3.9/+3.1), 단가 아래 -0.1/-1.3.
  정체는 매도 신호 아님(40일 섹터 +1.5/+2.5, 종목 +3.2/+1.4), 이탈 섹터 40일 -2.4/-3.2, 바닥 매수 섹터 -2.0/-1.4. 일별 수급-주가 상관은 상태 무관 ~0.6.
  주체별(flow16.py, 사용자 "기관 외국인 따로 볼 수 있게"): 외국인 끌어올리기가 가장 확실(섹터 20일 +6.7/+3.1, 중앙값 +1.4, 20일 바스켓 14회 10승 평균 +9.8;
  종목 +3.0/+3.5, 바스켓 19회 12승 +2.8). 기관 끌어올리기는 약함(섹터 0.0/+4.7, 바스켓 11회 5승). 외국인 끌어올리기 + 기관 강한 매도 종목 +3.4/+3.2.
  기관 수익실현 종목은 오히려 강함(+3.2/+2.5, 바스켓 19회 14승). 물린 채 매도 섹터는 둘 다 약함(기관 40일 -1.9/-3.6). 페이지 검증표는 주체 토글을 따라감.
  평균단가 대비 vs 60일 주가 수익률 순위상관 섹터 0.64, 종목 0.57(60일 평균단가 땐 0.84/0.77) - 일부는 모멘텀과 같은 얘기.
  효과 크기(flow14.py, 사용자 "그 정도 상승은 주가에 큰 영향 없지?"): 끌어올리기 종목 20일 평균 +2.4 / 중앙값 -2.9(전체 -2.9) / 이긴 비율 44%(전체 39%) /
  표준편차 28%p -> 평균은 소수 큰 상승이 끈 것. 날짜별 해당 종목 바스켓을 20일 간격(겹침 없이)으로 보면 종목 19회 중 10승 평균 +1.0%p(-23%p 1회), 섹터 14회 평균 -0.5%p.
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
            rec[w] = [None, f(L60), f(M8), f(gap, 1), None, None]   # [상태, 60일 누적 %, 8일 누적 %, 평균단가 대비 %, 단가 국면, 8일 강도]
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
            if L is not None and M is not None:
                r[w][0] = (4 if M > 0 else 3) if L >= hi else ((1 if M > 0 else 0) if L <= lo else 2)
            if M is not None:
                sb, ss = M >= mhi and M > 0, M <= mlo and M <= 0
                r[w][5] = 1 if sb else -1 if ss else 0   # 8일 강한 매수(1) / 강한 매도(-1) - 외국인 x 기관 조합 필터용
                if G is not None:   # 단가 국면: 평균단가 10%+ 위/아래 x 최근 8일 강한 매수(상위 20%)/강한 매도(하위 20%)
                    r[w][4] = (1 if sb else 2 if ss else 0) if G >= 10 else ((3 if sb else 4 if ss else 0) if G < 0 else 0)
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
<title>수급 상태·평균단가 (실험)</title>
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
  <h1>수급 상태·평균단가<span class="exp">실험</span><span class="sub">누적 순매수가 늘고 있나 멈췄나, 산 쪽이 수익 중인가</span></h1>
  <div class="updated">최종 갱신: __UPDATED__ &middot; 데이터 __FROM__ ~ __ASOF__ &middot; 수급.xlsx(외국인·기관 순매수) + 기업 밴드 찾기 2 시가총액</div>
  <div class="ctl"><span>주체</span><span class="seg" id="segWho"><button data-v="합산" class="active">외국인+기관</button><button data-v="외국인">외국인</button><button data-v="기관">기관</button></span>
    <span>차트 기간</span><span class="seg" id="segP"><button data-p="126">6달</button><button data-p="250" class="active">1년</button><button data-p="0">전체</button></span></div>

  <div class="note" style="font-size:13px;line-height:1.9">
    <b>평균단가</b> = 외국인·기관이 최근 40일 동안 산 평균 가격. 주가가 이보다 위면 산 쪽이 수익 중.<br>
    <span style="color:#ff8787">✔ 쓸 만한 것</span> 섹터 <b>외국인 끌어올리기</b>(단가보다 10%+ 위인데 외국인이 또 강하게 삼) → 이후 20일 +6.7 / +3.1%p(2025 / 2026)<br>
    <span style="color:#74a7ff">✔ 약한 것</span> 섹터 <b>물린 채 매도</b>(단가 아래에서 강하게 팜)·<b>이탈</b>(많이 팔았고 계속 팜)<br>
    <span style="color:#ffa94d">✘ 매도 신호 아님</span> 수익실현·정체(과거엔 오히려 계속 강했음)<br>
    <span class="sub">효과가 작고 종목 하나하나엔 잘 안 맞음 - 1~2달 섹터 비중 참고용.</span>
  </div>
  <details style="margin-bottom:16px"><summary style="cursor:pointer;color:#4dabf7;font-size:13px;margin-bottom:10px">자세한 설명·검증 결과 보기</summary>
  <div class="note">
    <b style="font-size:13px">이 페이지가 보는 것 - 외국인·기관에 대한 세 가지 질문</b><br>
    <b>① 많이 샀나?</b> 최근 60일(약 3달) 동안 순매수한 금액이 시가총액의 몇 %인지(<b>60일 누적</b>). 다른 섹터·종목과 비교해 상위 20%면 "많이 산 곳", 하위 20%면 "많이 판 곳".<br>
    <b>② 지금도 사고 있나?</b> 최근 8일 동안 순매수한 금액이 시가총액의 몇 %인지(<b>8일 누적</b>). 다른 곳과 비교해 상위 20%면 "강한 매수", 하위 20%면 "강한 매도".<br>
    <b>③ 산 사람들이 벌고 있나?</b> <b>수급 평균단가</b> = 최근 40일(약 2달) 동안 외국인·기관이 순매수한 날들의 평균 매수 가격(많이 산 날일수록 무게를 크게). 지금 주가가 이보다 위면 산 쪽이 수익 중, 아래면 물려 있음(<b>평균단가 대비</b> %).<br>
    <span style="color:#c9ccd1">예) 40일 안에 주가 10,000원일 때 10억, 12,000원일 때 30억어치를 순매수했다면 평균단가 = (10,000 x 10억 + 12,000 x 30억) / 40억 = 11,500원. 지금 주가가 13,000원이면 평균단가 대비 +13% = 산 쪽이 평균 13% 수익 중.
    실제 계산은 주가 대신 시가총액으로 합니다(같은 뜻이고, 증자 등으로 주식 수가 바뀌어도 덜 틀어짐). 순매도한 날은 평균단가 계산에서 뺍니다.</span><br>
    <b>왜 40일·60일?</b> 평균단가는 20·40·60·90·120일로 모두 계산해 보고 2025·2026 두 해 모두 가장 고르게 맞은 40일을 씁니다 - 너무 짧으면(20일) 평균단가가 주가를 바짝 따라가서 차이가 잘 안 나고, 너무 길면(90일+) 오래전에 사서 이미 팔았을 물량까지 섞여 2026년엔 잘 안 맞았습니다.
    ① "많이 샀나"는 60일 - 길게 볼수록 "오래 팔린 곳은 계속 약하다"가 뚜렷했고 60일이 그 중간입니다.
  </div>

  <div class="note">
    <b style="font-size:13px">단가 국면 = ③ 평균단가 x ② 지금 사고 있나</b> - "평균단가 위에서도 계속 사면 주가를 끌어올리려는 것, 평균단가 위에서 팔면 수익실현이라 빠질 것"이 맞는지 본 것<br>
    <span style="color:#ff8787">● 끌어올리기</span> 평균단가보다 10% 넘게 올랐는데도 또 강하게 사는 중 = 비싸져도 더 사겠다는 것 → <b>평균으로는 맞았습니다</b>(다만 효과가 작음 - 아래 "쓰는 법"의 주의). 이후 20일 동안 다른 종목보다 +2.1 / +2.8%p, 섹터는 +3.3 / +3.9%p 더 올랐고(2025 / 2026), 섹터는 40일 +5.4 / +7.1%p까지 벌어졌습니다.<br>
    <span style="color:#ffa94d">● 수익실현</span> 평균단가보다 10% 넘게 오른 상태에서 강하게 파는 중 → "빠질 것"은 <b>틀렸습니다</b>. 종목은 이후 20일 +1.1 / +2.5%p로 오히려 계속 강했고, 섹터도 20일 +0.6 / +2.4%p(40일은 +0.2 / -3.7%p로 엇갈림). 많이 오른 곳은 일부가 차익을 실현해도 추세가 이어진 경우가 많았다는 뜻 - <b>매도 이유로 쓰지 마세요</b>.<br>
    <span style="color:#a9e34b">● 단가 아래 매수</span> 평균단가 아래(물린 상태)인데 더 강하게 사는 중(물타기) → 뚜렷한 방향 없음.<br>
    <span style="color:#74a7ff">● 물린 채 매도</span> 평균단가 아래에서 강하게 파는 중(손절) → 섹터에서 <b>가장 약했습니다</b>(이후 20일 -1.1 / -1.9%p, 40일 -1.4 / -1.8%p). 종목 단위는 효과 없음.<br>
    <span style="color:#9aa0a6">아래 표 = 위 주체 버튼에서 고른 주체 기준, 평균단가 위치(행) x 최근 8일 수급(열) 모든 칸의 이후 상대수익(2025 / 2026, 다른 섹터·종목 평균 대비 %p). 테두리 친 네 칸이 위 네 국면.</span>
    <div class="seg" id="segVal" style="margin-top:8px"><button data-h="20" class="active">이후 20일</button><button data-h="40">이후 40일</button></div>
    <div id="valT" style="display:flex;gap:14px;flex-wrap:wrap;margin-top:8px"></div>
  </div>

  <div class="note">
    <b style="font-size:13px">외국인 vs 기관 - 나눠 보면 외국인이 핵심</b> <span class="sub">(위 주체 버튼으로 표·카드·검증표를 외국인/기관 따로 볼 수 있음, 섹터·종목 표에는 외국인·기관 국면을 나란히 표시)</span><br>
    <span style="color:#ff8787">●</span> <b>외국인 끌어올리기가 가장 확실했습니다.</b> 섹터는 이후 20일 +6.7 / +3.1%p, <b>보통 섹터(중앙값)도 +1.4%p</b>, 20일씩 끊어 산 바스켓 14번 중 10번 이김(평균 +9.8%p). 종목도 +3.0 / +3.5%p, 바스켓 19번 중 12번(평균 +2.8%p)으로 합산(19번 중 10번, +1.0%p)보다 나았습니다.<br>
    <span style="color:#9aa0a6">●</span> <b>기관 끌어올리기는 약했습니다.</b> 섹터 +0.0 / +4.7%p로 해마다 다르고 바스켓 11번 중 5번. 기관은 원래 주가를 따라 사는 편이라(수급 분석 검증) 단가 위에서 더 산다는 게 새 정보가 적습니다.<br>
    <span style="color:#ffd43b">●</span> <b>외국인이 사고 기관이 팔면 → 외국인 쪽을 따라감.</b> 종목 "외국인 끌어올리기 + 기관 강한 매도"는 이후 20일 +3.4 / +3.2%p, 반대("기관 끌어올리기 + 외국인 강한 매도")는 +0.3 / +2.6%p.<br>
    <span style="color:#ffa94d">●</span> <b>수익실현 → 하락은 주체별로 봐도 아니었습니다.</b> 오히려 <b>기관이 단가 위에서 강하게 파는 종목</b>은 이후 20일 +3.2 / +2.5%p, 바스켓 19번 중 14번 이김 - 기관 차익실현은 강한 종목에서 나옵니다. 외국인 수익실현은 +0.8 / +1.3%p로 중립.<br>
    <span style="color:#74a7ff">●</span> 섹터 <b>물린 채 매도</b>는 둘 다 약했고 기관 쪽이 40일 -1.9 / -3.6%p로 더 약했습니다. <b>이탈</b>은 외국인+기관 합산이 가장 뚜렷(40일 -2.4 / -3.2%p).<br>
    <span class="sub">종목 단위는 외국인 끌어올리기도 보통 종목(중앙값 -2.6%p)은 전체(-2.9%p)와 큰 차이가 없어서 여전히 참고용 - <b>섹터의 외국인 끌어올리기</b>가 가장 쓸 만합니다.</span>
    <div id="cmpT" style="display:flex;gap:14px;flex-wrap:wrap;margin-top:10px"></div>
  </div>

  <div class="note">
    <b style="font-size:13px">수급 상태 = ① 많이 샀나 x ② 지금 사고 있나</b><br>
    <span style="color:#ff8787">● 매집 진행</span> 많이 샀고 지금도 사는 중 · <span style="color:#ffd43b">● 정체</span> 많이 샀는데 최근엔 안 삼 · <span style="color:#a9e34b">● 바닥 매수</span> 많이 팔았는데 최근엔 삼 · <span style="color:#74a7ff">● 이탈</span> 많이 팔았고 지금도 파는 중 · 나머지 중간<br>
    <b>정체는 매도 신호가 아니었습니다</b> - 사는 게 멈춰도 주가는 대체로 같은 방향으로 갔습니다(이후 40일 섹터 +1.5 / +2.5%p, 종목 +3.2 / +1.4%p). 누적선이 평평해 보여도 하루하루 수급과 주가는 여전히 같이 움직입니다(상관 0.6).
    <b>이탈이 가장 약했고</b>(섹터 40일 -2.4 / -3.2%p) <b>바닥 매수도 약했습니다</b>(-2.0 / -1.4%p) - 많이 팔린 곳에서 며칠 사기 시작한 걸 바닥 신호로 보기는 어렵습니다.
    종목은 <b>매집 진행 + 평균단가 10% 위</b>가 이후 20일 +2.4 / +2.7%p(40일 +3.9 / +3.1%p)로 강했고, 매집 진행이어도 <b>평균단가 아래</b>면 -0.1 / -1.3%p로 약했습니다.
  </div>

  <div class="note">
    <b style="font-size:13px">쓰는 법</b> - 1~2달 단위 비중 조절용 참고 지표입니다(하루이틀 매매 신호 아님, 효과는 이후 20~40일에 걸쳐 나타남).<br>
    <span style="color:#69db7c">✔</span> 끌어올리기, 매집 진행 + 평균단가 위 → 들고 있는 종목·섹터를 계속 가져갈 근거 확인용<br>
    <span style="color:#69db7c">✔</span> 섹터가 물린 채 매도·이탈 → 비중 줄일 후보 점검용<br>
    <span style="color:#ff8787">✘</span> 수익실현·정체 → 이것만 보고 팔지 말 것(과거엔 오히려 계속 강했음)<br>
    <b style="color:#ffa94d">주의 - 효과가 작습니다.</b> 끌어올리기 종목의 이후 20일 평균 +2.4%p는 일부 크게 오른 종목이 끌어올린 값이고, <b>보통(중앙값) 종목은 다른 종목과 차이가 없었습니다</b>(-2.9 vs -2.9%p, 이긴 비율 44% vs 전체 39%).
    종목의 20일 상대수익은 원래 ±28%p씩 흔들려서 +2%p는 그 안에 묻히는 크기입니다. 날짜마다 해당 종목을 전부 샀다고 치고 20일씩 끊어 보면 19번 중 10번 이겼고 평균 +1.0%p(한 번은 -23%p), 섹터는 14번 중 7번, 평균 -0.5%p였습니다.
    → 단독으로 사고팔 근거가 아니라 다른 판단(실적·밸류·차트)을 거드는 참고 정도입니다.<br>
    <span style="color:#9aa0a6">또 평균단가보다 위 = 최근 주가가 올랐다는 뜻이기도 해서(60일 주가 상승률과 순위상관 섹터 0.64, 종목 0.57) 일부는 "오른 게 더 오른다"(모멘텀)와 같은 얘기입니다. 표본은 2025-01부터 약 2년. 그래서 "실험".</span>
  </div>


  </details>

  <div class="box">
    <h2>섹터 <span id="sCnt" style="font-size:12px;font-weight:normal;color:#9aa0a6"></span></h2>
    <div class="cards" id="sCards"></div>
    <div class="cards" id="sCards2"></div>
    <div class="desc">섹터 정리 42섹터(섹터 순매수 합 / 섹터 시총 합). 행을 누르면 아래 차트가 그 섹터로 바뀝니다.</div>
    <div class="tblwrap" style="max-height:420px"><table id="sTbl"></table></div>
    <div class="desc" style="margin-top:12px" id="sChartTitle"></div>
    <div class="cw"><canvas id="cS"></canvas></div>
  </div>

  <div class="box">
    <h2>종목 <span id="kCnt" style="font-size:12px;font-weight:normal;color:#9aa0a6"></span></h2>
    <div class="cards" id="kCards"></div>
    <div class="cards" id="kCards2"></div>
    <div class="ctl">
      <span>시총</span><select id="selCap"><option value="0">전체</option><option value="500">500억+</option><option value="1000" selected>1,000억+</option><option value="3000">3,000억+</option><option value="10000">1조+</option></select>
      <span>보기</span><select id="selSt"><option value="">전체</option><option value="p1" selected>끌어올리기(단가 10%+ 위 + 강한 매수)</option><option value="p2">수익실현(단가 10%+ 위 + 강한 매도)</option><option value="p3">단가 아래 매수</option><option value="p4">물린 채 매도</option><option value="4">매집 진행</option><option value="3">정체</option><option value="1">바닥 매수</option><option value="0">이탈</option><option value="c10">매집 진행 + 평균단가 10% 위</option><option value="cdn">매집 진행 + 평균단가 아래</option>
        <optgroup label="외국인 x 기관 (주체 버튼과 무관)"><option value="xf">외국인 끌어올리기</option><option value="xb">외국인·기관 둘 다 끌어올리기</option><option value="xfi">외국인 끌어올리기 + 기관 강한 매도</option><option value="xif">기관 끌어올리기 + 외국인 강한 매도</option><option value="xip">기관 수익실현</option></optgroup></select>
      <span>섹터</span><select id="selSec"></select>
      <span>검색</span><input id="q" placeholder="종목명·코드" style="width:110px">
    </div>
    <div class="desc">상태·단가 국면의 상·하위 20% 경계는 시총 1천억+ 종목 분포 기준(작은 종목은 참고만). 행을 누르면 차트.</div>
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
const STN = {4: '매집 진행', 3: '정체', 2: '중간', 1: '바닥 매수', 0: '이탈'}, STC = {4: '#ff8787', 3: '#ffd43b', 2: '#9aa0a6', 1: '#a9e34b', 0: '#74a7ff'};
const PHN = {1: '끌어올리기', 2: '수익실현', 3: '단가 아래 매수', 4: '물린 채 매도', 0: '-'}, PHC = {1: '#ff8787', 2: '#ffa94d', 3: '#a9e34b', 4: '#74a7ff', 0: '#7c828b'};
// 검증(flow16.py, 40일 평균단가): VAL[주체][20|40][대상] = 행(단가 아래 / 0~10% 위 / 10%+ 위) x 열(강한 매수 / 매수 / 매도 / 강한 매도) x [2025, 2026] 이후 상대수익 %p
// SUMM[대상][주체][국면·상태] = 20·40일 [2025, 2026], bk = 겹치지 않는 20일 바스켓 이긴 횟수·평균, md = 중앙값. COMBO = 외국인 x 기관 조합
const VAL = {"합산":{"20":{"섹터":[[[0.98,-0.06],[0.05,-0.31],[-0.19,0.03],[-1.09,-1.93]],[[0.29,-2.2],[-0.1,-0.49],[-0.07,-0.02],[-0.85,-2.6]],[[3.32,3.93],[0.32,4.54],[-1.74,4.66],[0.59,2.36]]],"종목(1천억+)":[[[0.44,-1.47],[-0.1,-0.19],[-0.31,0.26],[-0.07,0.39]],[[0.3,0.55],[-0.46,-0.43],[-0.27,-0.18],[0.65,1.1]],[[2.1,2.78],[0.92,1.42],[0.4,1.17],[1.12,2.52]]]},"40":{"섹터":[[[0.98,1.05],[-0.16,-1.07],[-0.93,-0.95],[-1.4,-1.75]],[[0.68,-0.32],[0.23,0.16],[0.03,-0.07],[-0.21,-6.06]],[[5.4,7.07],[-1.05,5.42],[-1.69,3.37],[0.24,-3.69]]],"종목(1천억+)":[[[1.34,-1.42],[0.32,-0.04],[-0.55,0.85],[0.66,0.75]],[[1.0,1.77],[-1.24,-0.15],[-0.94,0.99],[0.89,1.46]],[[3.01,3.67],[1.12,1.31],[-0.24,0.43],[1.71,0.12]]]}},"외국인":{"20":{"섹터":[[[1.24,-2.42],[-0.01,0.71],[-0.4,-0.13],[-1.2,-2.12]],[[0.43,-1.98],[0.2,-1.06],[-0.45,-1.19],[-1.0,-0.36]],[[6.73,3.11],[-0.17,2.86],[-1.99,4.18],[-1.65,4.49]]],"종목(1천억+)":[[[0.54,-0.83],[0.19,-0.01],[-0.53,0.35],[-0.39,0.06]],[[0.44,1.26],[-0.1,-0.42],[-0.5,-0.71],[-0.3,0.99]],[[2.99,3.45],[1.08,1.11],[-0.0,0.45],[0.83,1.32]]]},"40":{"섹터":[[[0.73,-1.61],[-0.5,0.0],[-1.41,-1.13],[-0.9,-1.4]],[[1.26,-2.69],[0.59,-1.32],[-0.48,-1.1],[-0.41,1.81]],[[10.63,6.13],[-2.04,4.38],[-4.3,6.11],[-2.14,0.26]]],"종목(1천억+)":[[[1.48,-0.7],[0.51,0.13],[-0.82,0.97],[0.53,0.89]],[[1.29,2.48],[-0.86,-0.24],[-1.18,0.55],[-0.14,0.4]],[[4.23,3.9],[0.87,0.72],[-0.2,0.92],[1.22,-0.5]]]}},"기관":{"20":{"섹터":[[[-1.28,-0.51],[-0.05,-0.15],[0.3,0.3],[-0.56,-2.2]],[[-0.32,-0.75],[-0.15,-1.25],[0.51,0.44],[-1.26,-3.3]],[[0.0,4.7],[0.35,3.24],[1.67,5.3],[5.79,0.97]]],"종목(1천억+)":[[[-0.59,-0.83],[-0.5,0.05],[-0.29,0.12],[0.08,-0.13]],[[0.24,-0.04],[-0.6,-0.63],[-0.38,0.15],[0.49,0.33]],[[1.15,2.08],[1.29,0.8],[0.14,2.12],[3.16,2.45]]]},"40":{"섹터":[[[-1.6,-1.02],[0.46,-1.31],[0.65,0.23],[-1.91,-3.62]],[[-1.1,3.04],[-0.45,-1.22],[1.36,2.32],[-1.41,-6.41]],[[0.42,5.41],[1.23,5.14],[3.57,3.26],[6.82,-2.0]]],"종목(1천억+)":[[[-0.43,-0.9],[-0.04,0.54],[-0.66,0.8],[0.36,-0.73]],[[0.44,0.35],[-1.44,-0.72],[-1.09,0.6],[0.47,-0.31]],[[2.43,2.64],[1.28,-0.74],[-0.29,-1.35],[4.15,3.72]]]}}};
const SUMM = {"섹터":{"합산":{"끌어올리기":{"20":[3.32,3.93],"40":[5.4,7.07],"bk":"7/14 평균 -0.5","md":-0.1},"수익실현":{"20":[0.59,2.36],"40":[0.24,-3.69],"bk":"5/7 평균 +6.7","md":-1.4},"단가아래매수":{"20":[0.98,-0.06],"40":[0.98,1.05],"bk":"6/12 평균 +2.3","md":-0.6},"물린채매도":{"20":[-1.09,-1.93],"40":[-1.4,-1.75],"bk":"8/18 평균 -1.5","md":-2.4},"매집진행":{"20":[0.46,0.69],"40":[1.65,1.35],"bk":"9/18 평균 -1.8","md":-1.0},"정체":{"20":[0.19,3.45],"40":[1.54,2.49],"bk":"11/17 평균 +5.5","md":0.3},"바닥매수":{"20":[-1.09,-0.16],"40":[-1.97,-1.43],"bk":"5/15 평균 +0.8","md":-2.9},"이탈":{"20":[-1.08,-2.65],"40":[-2.43,-3.24],"bk":"8/18 평균 -1.1","md":-2.9}},"외국인":{"끌어올리기":{"20":[6.73,3.11],"40":[10.63,6.13],"bk":"10/14 평균 +9.8","md":1.4},"수익실현":{"20":[-1.65,4.49],"40":[-2.14,0.26],"bk":"7/12 평균 -0.6","md":-1.7},"단가아래매수":{"20":[1.24,-2.42],"40":[0.73,-1.61],"bk":"7/14 평균 +0.9","md":-2.0},"물린채매도":{"20":[-1.2,-2.12],"40":[-0.9,-1.4],"bk":"8/17 평균 -1.1","md":-2.5},"매집진행":{"20":[2.18,-0.53],"40":[4.32,0.78],"bk":"9/18 평균 +1.0","md":-0.4},"정체":{"20":[2.03,1.28],"40":[1.32,5.91],"bk":"7/16 평균 +3.6","md":-0.4},"바닥매수":{"20":[0.5,0.92],"40":[-0.53,-0.56],"bk":"9/18 평균 -1.4","md":-2.1},"이탈":{"20":[-0.62,-0.57],"40":[-0.67,-0.84],"bk":"9/18 평균 -0.4","md":-2.4}},"기관":{"끌어올리기":{"20":[0.0,4.7],"40":[0.42,5.41],"bk":"5/11 평균 +1.1","md":-1.5},"수익실현":{"20":[5.79,0.97],"40":[6.82,-2.0],"bk":"5/8 평균 +11.1","md":0.7},"단가아래매수":{"20":[-1.28,-0.51],"40":[-1.6,-1.02],"bk":"3/15 평균 -1.0","md":-1.7},"물린채매도":{"20":[-0.56,-2.2],"40":[-1.91,-3.62],"bk":"9/19 평균 -0.7","md":-2.5},"매집진행":{"20":[-0.97,1.91],"40":[-1.02,1.36],"bk":"9/18 평균 -0.8","md":-1.8},"정체":{"20":[-0.0,-0.5],"40":[1.38,0.23],"bk":"10/18 평균 -1.1","md":-1.7},"바닥매수":{"20":[-1.75,-1.15],"40":[-1.81,2.59],"bk":"8/18 평균 -1.1","md":-2.8},"이탈":{"20":[-0.07,-2.4],"40":[-0.6,-3.71],"bk":"8/18 평균 -0.4","md":-1.9}}},"종목(1천억+)":{"합산":{"끌어올리기":{"20":[2.1,2.78],"40":[3.01,3.67],"bk":"10/19 평균 +1.0","md":-2.9},"수익실현":{"20":[1.12,2.52],"40":[1.71,0.12],"bk":"11/19 평균 +3.1","md":-4.6},"단가아래매수":{"20":[0.44,-1.47],"40":[1.34,-1.42],"bk":"12/19 평균 -0.1","md":-3.7},"물린채매도":{"20":[-0.07,0.39],"40":[0.66,0.75],"bk":"7/19 평균 -0.3","md":-2.8},"매집진행":{"20":[0.64,0.1],"40":[1.8,0.62],"bk":"9/18 평균 +0.1","md":-3.2},"정체":{"20":[1.22,1.14],"40":[3.18,1.36],"bk":"12/18 평균 +1.5","md":-3.0},"바닥매수":{"20":[0.36,-0.92],"40":[0.09,-1.95],"bk":"9/18 평균 +0.1","md":-3.7},"이탈":{"20":[-0.2,-0.1],"40":[-0.53,-0.97],"bk":"9/18 평균 +0.0","md":-3.3}},"외국인":{"끌어올리기":{"20":[2.99,3.45],"40":[4.23,3.9],"bk":"12/19 평균 +2.8","md":-2.6},"수익실현":{"20":[0.83,1.32],"40":[1.22,-0.5],"bk":"11/19 평균 +1.8","md":-4.4},"단가아래매수":{"20":[0.54,-0.83],"40":[1.48,-0.7],"bk":"9/19 평균 -0.2","md":-3.4},"물린채매도":{"20":[-0.39,0.06],"40":[0.53,0.89],"bk":"6/19 평균 -0.4","md":-3.0},"매집진행":{"20":[1.38,0.15],"40":[2.51,0.61],"bk":"11/18 평균 +0.4","md":-3.0},"정체":{"20":[1.29,1.26],"40":[3.66,1.9],"bk":"13/18 평균 +1.9","md":-2.9},"바닥매수":{"20":[-0.25,0.35],"40":[-0.3,0.2],"bk":"10/18 평균 +0.4","md":-3.6},"이탈":{"20":[-1.13,0.06],"40":[-1.68,-0.33],"bk":"7/18 평균 -0.0","md":-3.6}},"기관":{"끌어올리기":{"20":[1.15,2.08],"40":[2.43,2.64],"bk":"11/19 평균 +0.1","md":-3.2},"수익실현":{"20":[3.16,2.45],"40":[4.15,3.72],"bk":"14/19 평균 +3.9","md":-2.5},"단가아래매수":{"20":[-0.59,-0.83],"40":[-0.43,-0.9],"bk":"7/19 평균 -0.1","md":-3.4},"물린채매도":{"20":[0.08,-0.13],"40":[0.36,-0.73],"bk":"5/19 평균 -0.6","md":-2.8},"매집진행":{"20":[-0.06,0.23],"40":[0.84,0.45],"bk":"8/18 평균 +0.3","md":-3.3},"정체":{"20":[0.21,-0.09],"40":[0.79,-0.37],"bk":"10/18 평균 +0.4","md":-3.2},"바닥매수":{"20":[0.41,-0.9],"40":[0.56,-1.32],"bk":"13/18 평균 +0.5","md":-3.5},"이탈":{"20":[1.17,-0.37],"40":[1.97,-0.97],"bk":"12/18 평균 +0.3","md":-2.9}}}};
const COMBO = {"섹터":{"둘 다 끌어올리기":{"20":[5.39,1.95],"40":[10.25,7.53],"n":242},"외국인 끌어올리기 + 기관 강한 매도":{"20":[15.03,0.8],"40":[22.05,-5.41],"n":100},"기관 끌어올리기 + 외국인 강한 매도":{"20":[-2.17,9.5],"40":[-4.23,4.56],"n":272},"외국인만 끌어올리기(기관 강한매수 아님)":{"20":[8.51,4.44],"40":[13.18,6.0],"n":375},"기관만 끌어올리기(외국인 강한매수 아님)":{"20":[-2.65,6.02],"40":[-4.22,4.88],"n":593}},"종목(1천억+)":{"둘 다 끌어올리기":{"20":[2.92,2.5],"40":[5.13,1.92],"n":6007},"외국인 끌어올리기 + 기관 강한 매도":{"20":[3.43,3.2],"40":[3.9,5.77],"n":4936},"기관 끌어올리기 + 외국인 강한 매도":{"20":[0.31,2.63],"40":[1.24,3.17],"n":7331},"외국인만 끌어올리기(기관 강한매수 아님)":{"20":[3.05,4.25],"40":[3.72,5.28],"n":11049},"기관만 끌어올리기(외국인 강한매수 아님)":{"20":[0.28,1.93],"40":[0.89,2.96],"n":13528}}};
const capTxt = v => !nn(v) ? '-' : (v >= 10000 ? (v / 10000).toFixed(1) + '조' : v.toLocaleString() + '억');
let WHO = '합산', VH = 20, P = 250, TGT = null, sSort = 'L60', sDesc = true, kSort = 'cg', kDesc = true, CUR = null, cS = null, cK = null;
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
function assignStates(list) {
  const v = list.filter(x => nn(x.L60)).map(x => x.L60).sort((a, b) => a - b), n = v.length;
  list.forEach(x => { if (!nn(x.L60) || !nn(x.M8) || !n) { x.st = null; return; } let c = 0; while (c < n && v[c] <= x.L60) c++; const q = c / n;
    x.st = q >= 0.8 ? (x.M8 > 0 ? 4 : 3) : q <= 0.2 ? (x.M8 > 0 ? 1 : 0) : 2; });
}
function assignPhase(list) {   // 단가 국면: 평균단가 10%+ 위/아래 x 8일 누적의 섹터 간 상위 20%(강한 매수)/하위 20%(강한 매도)
  const v = list.filter(x => nn(x.M8)).map(x => x.M8).sort((a, b) => a - b), n = v.length;
  list.forEach(x => { x.ph = null; if (!nn(x.M8) || !nn(x.cg) || !n) return; let c = 0; while (c < n && v[c] <= x.M8) c++; const q = c / n;
    const sb = q >= 0.8 && x.M8 > 0, ss = q <= 0.2 && x.M8 <= 0;
    x.ph = x.cg >= 10 ? (sb ? 1 : ss ? 2 : 0) : x.cg < 0 ? (sb ? 3 : ss ? 4 : 0) : 0; });
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
const cardsOf = (list, getSt, extra, NM = STN, CL = STC, KS = [4, 3, 1, 0]) => KS.map(q => { const xs = list.filter(x => getSt(x) === q); return `<div class="card"><span style="color:${CL[q]}">●</span> ${NM[q]}<div class="val" style="color:${CL[q]}">${xs.length}</div>${extra(xs)}</div>`; }).join('');
const phTd = p => `<td style="color:${nn(p) ? PHC[p] : '#7c828b'}">${nn(p) ? PHN[p] : '-'}</td>`;
const CORNER = {'2,0': 1, '2,3': 2, '0,0': 3, '0,3': 4};
const WN = w => w === '합산' ? '외국인+기관' : w;
const renderVal = h => $('valT').innerHTML = Object.entries(VAL[WHO][h]).map(([k, M]) => `<table style="width:auto;font-size:11px"><tr><th style="cursor:default;text-align:left;background:none">${k} · <b style="color:#4dabf7">${WN(WHO)}</b> · 이후 ${h}일 상대수익(%p) 2025 / 2026</th>` +
  ['강한 매수', '매수', '매도', '강한 매도'].map(t => `<th style="cursor:default;text-align:right;background:none">${t}</th>`).join('') + '</tr>' +
  [[2, '평균단가 10%+ 위'], [1, '0~10% 위'], [0, '평균단가 아래']].map(([i, nm]) => `<tr><td>${nm}</td>` + M[i].map(([a, b], j) => { const p = CORNER[i + ',' + j]; a = Math.abs(a) < 0.05 ? 0 : a; b = Math.abs(b) < 0.05 ? 0 : b;
    return `<td style="text-align:right${p ? ';box-shadow:inset 0 0 0 1px ' + PHC[p] : ''}"${p ? ' title="' + PHN[p] + '"' : ''}><span class="${cls(a)}">${sg(a)}</span> / <span class="${cls(b)}">${sg(b)}</span></td>`; }).join('') + '</tr>').join('') + '</table>').join('') +
  '<div class="sub" style="font-size:11px;align-self:flex-end">테두리 = 단가 국면 4칸(색은 위 설명과 같음). 강한 매수·매도 = 그날 8일 누적이 상위·하위 20%</div>';
renderVal(20);
const pair = v => nn(v[0]) || nn(v[1]) ? v.map(x => nn(x) ? `<span class="${cls(x)}">${sg(Math.abs(x) < 0.05 ? 0 : x)}</span>` : '-').join(' / ') : '-';
const CMPR = [['끌어올리기', PHC[1]], ['수익실현', PHC[2]], ['단가아래매수', PHC[3]], ['물린채매도', PHC[4]], ['매집진행', STC[4]], ['정체', STC[3]], ['바닥매수', STC[1]], ['이탈', STC[0]]];
const CMPN = {단가아래매수: '단가 아래 매수', 물린채매도: '물린 채 매도', 매집진행: '매집 진행', 바닥매수: '바닥 매수'};
function renderCmp() {
  const th = t => `<th style="cursor:default;text-align:right;background:none">${t}</th>`;
  $('cmpT').innerHTML = Object.entries(SUMM).map(([k, S]) => `<table style="width:auto;font-size:11px"><tr><th style="cursor:default;text-align:left;background:none">${k} · 이후 ${VH}일 상대수익(%p) 2025 / 2026 <span class="sub">[20일 바스켓 이긴 횟수]</span></th>` +
    ['외국인+기관', '외국인', '기관'].map(th).join('') + '</tr>' +
    CMPR.map(([nm, c]) => `<tr><td style="color:${c}">${CMPN[nm] || nm}</td>` + ['합산', '외국인', '기관'].map(w => { const x = S[w][nm];
      return `<td style="text-align:right${w === WHO ? ';background:#1c2633' : ''}">${pair(x[VH])} <span class="sub">[${x.bk.split(' ')[0]}]</span></td>`; }).join('') + '</tr>').join('') + '</table>').join('') +
    `<table style="width:auto;font-size:11px"><tr><th style="cursor:default;text-align:left;background:none">외국인 x 기관 조합 · 이후 ${VH}일 (2025 / 2026)</th>${th('섹터')}${th('종목(1천억+)')}</tr>` +
    Object.keys(COMBO['섹터']).map(nm => `<tr><td>${nm}</td>` + ['섹터', '종목(1천억+)'].map(k => `<td style="text-align:right">${pair(COMBO[k][nm][VH])} <span class="sub">n=${COMBO[k][nm].n.toLocaleString()}</span></td>`).join('') + '</tr>').join('') + '</table>';
}
renderCmp();
// ---------- 섹터
let SST = [];
function renderSectors() {
  SST = D.sectors.map(g => sectorStats(g)); assignStates(SST); assignPhase(SST);
  const byW = {};   // 외국인·기관 각각의 단가 국면(표에 나란히)
  ['외국인', '기관'].forEach(w => { const L = D.sectors.map(g => sectorStats(g, w)); assignPhase(L); L.forEach(x => (byW[x.g.name] = byW[x.g.name] || {})[w] = x.ph); });
  SST.forEach(x => { x.phF = byW[x.g.name]['외국인']; x.phI = byW[x.g.name]['기관']; });
  $('sCards').innerHTML = cardsOf(SST, x => x.st, xs => xs.map(x => x.g.name).join(', ') || '-');
  $('sCards2').innerHTML = cardsOf(SST, x => x.ph, xs => xs.map(x => x.g.name).join(', ') || '-', PHN, PHC, [1, 2, 3, 4]);
  const key = {name: x => x.g.name, st: x => x.st, ph: x => x.ph, phF: x => x.phF, phI: x => x.phI, L60: x => x.L60, M8: x => x.M8, cg: x => x.cg, c20: x => x.c20, cap: x => x.g.cap[N - 1]}[sSort];
  const rows = SST.slice().sort((a, b) => { const va = key(a), vb = key(b); if (typeof va === 'string') return sDesc ? vb.localeCompare(va) : va.localeCompare(vb); const x = nn(va) ? va : (sDesc ? -1e18 : 1e18), y = nn(vb) ? vb : (sDesc ? -1e18 : 1e18); return sDesc ? y - x : x - y; });
  const H = (k, t) => `<th data-s="${k}"${sSort === k ? ' class="sorted"' : ''}>${t}${sSort === k ? (sDesc ? ' ▼' : ' ▲') : ''}</th>`;
  let h = '<tr>' + H('name', '섹터') + H('st', '수급 상태') + H('ph', '단가 국면') + H('phF', '외국인 국면') + H('phI', '기관 국면') + H('L60', '60일 누적') + H('M8', '8일 누적') + H('cg', '평균단가 대비') + H('c20', '20일 시총') + H('cap', '시총') + '</tr>';
  rows.forEach(x => h += `<tr class="rw${TGT === x.g.name ? ' sel' : ''}" data-n="${x.g.name}"><td>${x.g.name} <span class="sub">${x.g.n}</span></td><td style="color:${nn(x.st) ? STC[x.st] : '#9aa0a6'}">${nn(x.st) ? STN[x.st] : '-'}</td>${phTd(x.ph)}${phTd(x.phF)}${phTd(x.phI)}` +
    `<td class="${cls(x.L60)}">${sg(x.L60, 2)}%</td><td class="${cls(x.M8)}">${sg(x.M8, 2)}%</td><td class="${cls(x.cg)}"><b>${sg(x.cg)}%</b></td><td class="${cls(x.c20)}">${sg(x.c20)}%</td><td>${capTxt(x.g.cap[N - 1])}</td></tr>`);
  $('sTbl').innerHTML = h;
  $('sCnt').textContent = `${WHO === '합산' ? '외국인+기관' : WHO} 기준 · ${D.asof}`;
  $('sTbl').querySelectorAll('th[data-s]').forEach(th => th.onclick = () => { const k = th.dataset.s; if (sSort === k) sDesc = !sDesc; else { sSort = k; sDesc = k !== 'name'; } renderSectors(); });
  $('sTbl').querySelectorAll('tr.rw').forEach(tr => tr.onclick = () => { TGT = tr.dataset.n; renderSectors(); });
  if (!TGT) TGT = rows[0] ? rows[0].g.name : null;
  const x = SST.find(y => y.g.name === TGT); if (!x) return;
  const s0 = P ? Math.max(0, N - P) : 0;
  cS = chartOf($('cS'), cS, D.dates.slice(s0), s0, x.g.cap, x.nt, x.cost);
  $('sChartTitle').innerHTML = `<b style="color:#e6e6e6">${x.g.name}</b> · 상태 <b style="color:${nn(x.st) ? STC[x.st] : '#9aa0a6'}">${nn(x.st) ? STN[x.st] : '-'}</b>${x.ph ? ` · <b style="color:${PHC[x.ph]}">${PHN[x.ph]}</b>` : ''} · 60일 누적 ${sg(x.L60, 2)}% · 8일 누적 ${sg(x.M8, 2)}% · 평균단가 대비 <b class="${cls(x.cg)}">${sg(x.cg)}%</b> - 흰 선 = 시가총액, 노란 점선 = 수급 평균단가(최근 ${CW}일, 시총 환산 - 흰 선이 위면 산 쪽이 수익 중), 빨간 선 = 차트 시작일부터 누적 순매수`;
}
// ---------- 종목
const secs = [...new Set(D.stocks.map(r => r.s))].sort();
$('selSec').innerHTML = '<option value="">전체</option>' + secs.map(s => `<option>${s}</option>`).join('');
function renderStocks() {
  const capMin = +$('selCap').value, st = $('selSt').value, sec = $('selSec').value, q = $('q').value.trim().toLowerCase(), A = r => r[WHO];
  const base = D.stocks.filter(r => (!capMin || r.cap >= capMin) && (!sec || r.s === sec));
  $('kCards').innerHTML = cardsOf(base, r => A(r)[0], xs => `평균단가 10%↑ 위 ${xs.filter(r => A(r)[3] >= 10).length} · 아래 ${xs.filter(r => A(r)[3] < 0).length}`);
  $('kCards2').innerHTML = cardsOf(base, r => A(r)[4], xs => xs.slice().sort((a, b) => (b.cap || 0) - (a.cap || 0)).slice(0, 4).map(r => r.n).join(', ') + (xs.length > 4 ? ' 등' : ''), PHN, PHC, [1, 2, 3, 4]);
  const rows = base.filter(r => {
    if (q && !(r.n.toLowerCase().includes(q) || r.c.includes(q))) return false;
    const a = A(r);
    const f = r['외국인'], i = r['기관'];
    if (st === 'xf') return f[4] === 1;
    if (st === 'xb') return f[4] === 1 && i[4] === 1;
    if (st === 'xfi') return f[4] === 1 && i[5] === -1;
    if (st === 'xif') return i[4] === 1 && f[5] === -1;
    if (st === 'xip') return i[4] === 2;
    if (st[0] === 'p') return a[4] === +st[1];
    if (st === 'c10') return a[0] === 4 && a[3] >= 10;
    if (st === 'cdn') return a[0] === 4 && a[3] < 0;
    if (st !== '') return a[0] === +st;
    return true; });
  const key = {n: r => r.n, s: r => r.s, cap: r => r.cap, r20: r => r.r20, st: r => A(r)[0], ph: r => A(r)[4], phF: r => r['외국인'][4], phI: r => r['기관'][4], L60: r => A(r)[1], M8: r => A(r)[2], cg: r => A(r)[3]}[kSort];
  rows.sort((a, b) => { const va = key(a), vb = key(b); if (typeof va === 'string') return kDesc ? vb.localeCompare(va) : va.localeCompare(vb); const x = nn(va) ? va : (kDesc ? -1e18 : 1e18), y = nn(vb) ? vb : (kDesc ? -1e18 : 1e18); return kDesc ? y - x : x - y; });
  const H = (k, t) => `<th data-s="${k}"${kSort === k ? ' class="sorted"' : ''}>${t}${kSort === k ? (kDesc ? ' ▼' : ' ▲') : ''}</th>`;
  let h = '<tr>' + H('n', '종목') + H('s', '섹터') + H('cap', '시총') + H('st', '수급 상태') + H('ph', '단가 국면') + H('phF', '외국인 국면') + H('phI', '기관 국면') + H('L60', '60일 누적') + H('M8', '8일 누적') + H('cg', '평균단가 대비') + H('r20', '20일 주가') + '</tr>';
  rows.slice(0, 300).forEach(r => { const a = A(r); h += `<tr class="rw" data-c="${r.c}"><td>${r.n} <span class="sub">${r.c}</span></td><td class="sub">${r.s}</td><td>${capTxt(r.cap)}</td>` +
    `<td style="color:${nn(a[0]) ? STC[a[0]] : '#9aa0a6'}">${nn(a[0]) ? STN[a[0]] : '-'}</td>${phTd(a[4])}${phTd(r['외국인'][4])}${phTd(r['기관'][4])}<td class="${cls(a[1])}">${sg(a[1], 2)}%</td><td class="${cls(a[2])}">${sg(a[2], 2)}%</td><td class="${cls(a[3])}"><b>${sg(a[3])}%</b></td><td class="${cls(r.r20)}">${sg(r.r20)}%</td></tr>`; });
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
  $('dSub').innerHTML = `${WHO === '합산' ? '외국인+기관' : WHO} · 시총 ${capTxt(r.cap)} · 상태 <b style="color:${nn(a[0]) ? STC[a[0]] : '#9aa0a6'}">${nn(a[0]) ? STN[a[0]] : '-'}</b>${a[4] ? ` · <b style="color:${PHC[a[4]]}">${PHN[a[4]]}</b>` : ''} · 60일 누적 ${sg(a[1], 2)}% · 8일 누적 ${sg(a[2], 2)}% · 평균단가 대비 <b class="${cls(a[3])}">${sg(a[3])}%</b> · 20일 주가 <span class="${cls(r.r20)}">${sg(r.r20)}%</span><br>흰 선 = 시가총액, 노란 점선 = 수급 평균단가(최근 ${CW}일 순매수한 날들의 평균 매수 가격, 시총 환산) - 흰 선이 노란 선 위면 산 쪽이 수익 중, 빨간 선 = 차트 시작일부터 누적 순매수`;
}
$('closeBtn').onclick = () => $('overlay').classList.remove('open');
$('overlay').onclick = e => { if (e.target.id === 'overlay') $('overlay').classList.remove('open'); };
const seg = (id, fn) => $(id).onclick = e => { const b = e.target.closest('button'); if (!b) return; document.querySelectorAll('#' + id + ' button').forEach(x => x.classList.toggle('active', x === b)); fn(b); };
seg('segWho', b => { WHO = b.dataset.v; renderVal(VH); renderCmp(); renderSectors(); renderStocks(); if (CUR && $('overlay').classList.contains('open')) drawStock(); });
seg('segP', b => { P = +b.dataset.p; renderSectors(); if (CUR && $('overlay').classList.contains('open')) drawStock(); });
seg('segVal', b => { VH = +b.dataset.h; renderVal(VH); renderCmp(); });
['selCap', 'selSt', 'selSec'].forEach(id => $(id).onchange = renderStocks);
$('q').oninput = renderStocks;
renderSectors(); renderStocks();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
