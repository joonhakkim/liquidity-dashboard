"""
시장 국면 점수 페이지(docs/market_phase.html)를 만든다 (2026-10-07 사용자 요청 - "공격 방어 상대강도,
하단 종목 비율, 120일선 위 종목에 점수 비중을 줘서 6국면으로 나누고 홈페이지에 하나 만들자, 그 밑에
각각 따로 차트로").

국면 점수(0~100) = (밸류 하단 3점 + 공격-방어 2점 + 120일선 위 2점) / 7 - 2026-10-07 백테스트로 정한 배점
(2018~2026 주간, 0~3점 조합 2만여 개 탐색 + 2018~21로 고르고 2022~26에 적용하는 표본 외 검증;
코스피를 이긴 섹터 수/종목 수, 중소형-대형 상대강도는 정답률에 기여가 없어 0점).
 - C 밸류 하단 비율: OP밴드 배수(시총/12개월 선행 영업이익)가 자기 최근 3년 하위 10% 아래인 종목 비율
   (docs/op_band_data 종목별 시계열로 그 시점까지 데이터만 써서 계산, 2년 이상 이력 종목만). 낮을수록 +.
 - D 공격-방어 상대강도: 공격 섹터 동일가중 1달(20거래일) 수익률 - 방어 섹터 1달 수익률. 높을수록 +.
 - X 120일선 위 종목 비율: 주가가 120일 이동평균 위인 보통주 비율(상장 250일 이상). 높을수록 +.
각 지표는 그 시점까지의 과거 값 대비 백분위(0~100점, 미래 정보 없음)로 바꿔 배점대로 평균한다.
국면 = 13주 평균 점수의 수준(그 시점까지 점수 분포의 1/3, 2/3 기준: 낮음/중간/높음) x 방향(8주 전보다 상승/하락)
  상승: 낮음 ① 반등시작, 중간 ② 중소형 확산, 높음 ③ 전면 확산 / 하락: 높음 ④ 고점, 중간 ⑤ 중소형 하락, 낮음 ⑥ 바닥
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from build_relative_strength import assign_sectors, exclusion_mask, load_kospi_series  # noqa: E402

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(BASE_DIR, "data")
DOCS_DIR = os.path.join(BASE_DIR, "docs")
PRICES_PATH = os.path.join(DATA_DIR, "bollinger_prices.csv")
OP_BAND_DIR = os.path.join(DOCS_DIR, "op_band_data")
SUMMARY_PATH = os.path.join(DATA_DIR, "screening", "market_phase_weekly.csv")
PAGE_OUT_PATH = os.path.join(DOCS_DIR, "market_phase.html")

WEIGHTS = {"C": 3, "D": 2, "X": 2}
SMOOTH_WEEKS = 13
DIR_WEEKS = 8
MAX_DAILY_MOVE = 0.31
# 공격 = 섹터 정리 섹터 중 코스피 대비 주간 베타(2016~2026-10) 상위 1/3, 방어 = 통상 방어업종.
# 2026-10-07 매년 직전 3년 베타로 다시 고르는 방식과 비교해 주간 국면 판정 96% 일치(별 차이 없어 고정 목록 사용).
AGGRESSIVE = ["MLCC/기판", "전력기기", "로직", "반도체 장비", "후공정/테스트", "소재", "로봇", "이차전지",
              "태양광, 풍력", "휴대폰 부품", "원전", "디스플레이", "IDM"]
DEFENSIVE = ["유틸리티", "통신", "음식료", "보험", "은행", "지주", "유통"]
PHASES = {1: "① 반등시작", 2: "② 중소형 확산", 3: "③ 전면 확산", 4: "④ 고점", 5: "⑤ 중소형 하락", 6: "⑥ 대형주까지 바닥"}


def read_all_prices():
    df = pd.read_csv(PRICES_PATH, usecols=["date", "market", "code", "name", "close"],
                     dtype={"date": "int32", "market": "category", "code": "str", "name": "category", "close": "float32"})
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    wide = df.pivot_table(index="date", columns="code", values="close", aggfunc="last", observed=True).sort_index()
    meta = df.sort_values("date").groupby("code", observed=True).agg(name=("name", "last"), market=("market", "last"))
    meta["name"] = meta["name"].astype(str)
    meta["market"] = meta["market"].astype(str)
    return wide, meta


def exp_pct(s, min_n=52):
    """그 시점까지(포함)의 과거 값 대비 백분위(0~100) - 미래 정보 없음."""
    import bisect
    out = np.full(len(s), np.nan)
    hist = []
    for i, v in enumerate(s.values):
        if np.isnan(v):
            continue
        bisect.insort(hist, v)
        if len(hist) >= min_n:
            out[i] = bisect.bisect_right(hist, v) / len(hist) * 100
    return pd.Series(out, index=s.index)


def valuation_bottom_share(grid):
    """종목별 OP밴드 배수가 자기 최근 3년 하위10% 아래인지(그 시점까지 데이터만) -> 주간 그리드의 비율."""
    flags = {}
    for f in glob.glob(os.path.join(OP_BAND_DIR, "A*.json")):
        with open(f, encoding="utf-8") as fh:
            j = json.load(fh)
        dts = pd.to_datetime(j["dates"])
        mc = np.array(j["mktcapEok"], dtype=float)
        op = np.array(j["opEok"], dtype=float)
        m = pd.Series(np.where(op > 0, mc / np.where(op > 0, op, 1), np.nan), index=dts).sort_index()
        m = m[~m.index.duplicated()]
        if m.notna().sum() < 20:
            continue
        p10 = m.rolling("1095D", min_periods=20).quantile(0.10)
        elig = (m.index - m.index.min()).days >= 730
        fl = (m < p10).astype(float).where(m.notna() & p10.notna() & elig)
        # 주간 그리드 날짜 기준으로 그 이전 마지막 관측값(10일 이내만) - tolerance 인자는 NumPy 경고가 나서 직접 계산
        on_grid = fl.reindex(grid, method="ffill")
        src = pd.Series(fl.index, index=fl.index).reindex(grid, method="ffill")
        age = (grid - pd.DatetimeIndex(src.values)).days
        flags[j["code"]] = on_grid.where(age <= 10)
    F = pd.DataFrame(flags)
    n = F.notna().sum(axis=1)
    return F.sum(axis=1) / n.replace(0, np.nan), n


def compute():
    wide, meta = read_all_prices()
    meta = meta.loc[wide.columns]
    keep = ~exclusion_mask(meta)
    wide = wide.loc[:, keep.values]
    meta = meta[keep.values]
    asof = wide.index[-1]
    R = wide.pct_change(fill_method=None)
    R[R.abs() > MAX_DAILY_MOVE] = np.nan
    L = np.log1p(R.fillna(0)).cumsum()
    ok = wide.notna()
    active = ok & (ok.cumsum() >= 250)
    # X: 120일선 위 종목 비율
    X = ((L > L.rolling(120).mean()) & active).sum(axis=1) / active.sum(axis=1).replace(0, np.nan)
    # D: 공격-방어 상대강도(섹터 정리 섹터, 활성 종목 동일가중)
    sectors, _ = assign_sectors(meta)
    members = {}
    for code, (sec, src) in sectors.items():
        if src == "U":
            members.setdefault(sec, []).append(code)
    def sector_ret(sec):
        cs = [c for c in members.get(sec, []) if c in R.columns]
        if not cs:
            return None
        return R[cs].where(active[cs]).mean(axis=1).where(active[cs].sum(axis=1) >= 3)
    agg_list = [s for s in AGGRESSIVE if s in members]
    def_list = [s for s in DEFENSIVE if s in members]
    ag = (1 + pd.concat([sector_ret(s) for s in agg_list], axis=1).mean(axis=1).fillna(0)).cumprod()
    df_ = (1 + pd.concat([sector_ret(s) for s in def_list], axis=1).mean(axis=1).fillna(0)).cumprod()
    D = (ag / ag.shift(20) - 1) - (df_ / df_.shift(20) - 1)
    # 주간 그리드(목요일) + 데이터 기준일
    grid = pd.date_range("2015-01-01", asof, freq="W-THU")
    if grid[-1] != asof:
        grid = grid.append(pd.DatetimeIndex([asof]))
    Xw = X.reindex(wide.index).reindex(grid, method="ffill")
    Dw = D.reindex(wide.index).reindex(grid, method="ffill")
    Cw, Cn = valuation_bottom_share(grid)
    Cw = Cw.where(Cn >= 300)
    kospi = load_kospi_series()
    ks = pd.Series({pd.Timestamp(k): v for k, v in kospi.items()}).sort_index()
    Kw = ks.reindex(ks.index.union(grid)).ffill().reindex(grid)
    # 점수
    sc = pd.DataFrame({"C": 100 - exp_pct(Cw), "D": exp_pct(Dw), "X": exp_pct(Xw)})
    valid = sc.notna().all(axis=1)
    start = valid.idxmax()
    sc2 = sc.loc[start:]
    comp = sum(sc2[k] * w for k, w in WEIGHTS.items()) / sum(WEIGHTS.values())
    comp = comp.where(sc2.notna().all(axis=1))
    Ls = comp.rolling(SMOOTH_WEEKS, min_periods=1).mean()
    lo = Ls.expanding(26).quantile(1 / 3)
    hi = Ls.expanding(26).quantile(2 / 3)
    level = np.where(Ls < lo, 0, np.where(Ls > hi, 2, 1))
    rising = (Ls - Ls.shift(DIR_WEEKS)) > 0
    ph = pd.Series(np.where(rising, level + 1, 6 - level), index=Ls.index).where(lo.notna() & Ls.shift(DIR_WEEKS).notna())
    out = pd.DataFrame({"kospi": Kw, "C": Cw, "D": Dw, "X": Xw, "sC": sc["C"], "sD": sc["D"], "sX": sc["X"]})
    out["score"] = Ls.reindex(grid)
    out["lo"] = lo.reindex(grid)
    out["hi"] = hi.reindex(grid)
    out["phase"] = ph.reindex(grid)
    return asof, out, agg_list, def_list, int(Cn.iloc[-1])


def main():
    global PAGE_OUT_PATH, SUMMARY_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="테스트용: HTML 출력 경로")
    ap.add_argument("--summary", help="테스트용: 주간 CSV 출력 경로")
    args = ap.parse_args()
    if args.out:
        PAGE_OUT_PATH = args.out
    if args.summary:
        SUMMARY_PATH = args.summary
    asof, out, agg_list, def_list, c_n = compute()
    last = out.dropna(subset=["phase"]).iloc[-1]
    prev = out["score"].dropna()
    print(f"데이터 기준일 {asof.date()} | 국면 {PHASES[int(last['phase'])]} | 점수 {last['score']:.1f} (낮음<{last['lo']:.1f}, 높음>{last['hi']:.1f}) | 8주 전 {prev.iloc[-1 - DIR_WEEKS]:.1f}")
    print(f"  밸류 하단 비율 {out['C'].iloc[-1] * 100:.1f}% ({c_n}종목) | 공격-방어 {out['D'].iloc[-1] * 100:+.1f}%p | 120일선 위 {out['X'].iloc[-1] * 100:.1f}%")
    os.makedirs(os.path.dirname(SUMMARY_PATH), exist_ok=True)
    out.rename_axis("date").to_csv(SUMMARY_PATH, encoding="utf-8-sig", float_format="%.5g")

    r = lambda v, d=2: None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), d)
    payload = {
        "asof": asof.strftime("%Y-%m-%d"),
        "dates": [d.strftime("%Y-%m-%d") for d in out.index],
        "kospi": [r(v, 1) for v in out["kospi"]],
        "score": [r(v, 1) for v in out["score"]],
        "lo": [r(v, 1) for v in out["lo"]],
        "hi": [r(v, 1) for v in out["hi"]],
        "phase": [None if np.isnan(v) else int(v) for v in out["phase"]],
        "C": [r(v * 100 if v == v else v, 2) for v in out["C"]],
        "D": [r(v * 100 if v == v else v, 2) for v in out["D"]],
        "X": [r(v * 100 if v == v else v, 2) for v in out["X"]],
        "sC": [r(v, 0) for v in out["sC"]],
        "sD": [r(v, 0) for v in out["sD"]],
        "sX": [r(v, 0) for v in out["sX"]],
        "phases": PHASES,
        "weights": WEIGHTS,
        "aggr": agg_list,
        "dfn": def_list,
        "cN": c_n,
        "smooth": SMOOTH_WEEKS,
        "dirWeeks": DIR_WEEKS,
    }
    html = (TEMPLATE.replace("__UPDATED__", datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__ASOF__", payload["asof"])
            .replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
    os.makedirs(os.path.dirname(PAGE_OUT_PATH), exist_ok=True)
    with open(PAGE_OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"저장 완료: {SUMMARY_PATH}")
    print(f"저장 완료: {PAGE_OUT_PATH}")


TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>시장 국면 점수</title>
<style>
  body { font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  a.back { color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }
  h1 { font-size:20px; margin:8px 0 4px 0; }
  h1 .sub { font-size:13px; color:#9aa0a6; font-weight:normal; margin-left:6px; }
  .updated { color:#9aa0a6; font-size:13px; margin-bottom:16px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(210px, 1fr)); gap:12px; margin-bottom:16px; }
  .card { background:#1a1d24; border:1px solid #23262e; border-radius:12px; padding:14px 16px; }
  .card .lbl { font-size:12px; color:#9aa0a6; margin-bottom:6px; }
  .card .val { font-size:22px; font-weight:700; }
  .card .sub { font-size:12px; color:#9aa0a6; margin-top:6px; line-height:1.6; }
  .card.main { grid-column:span 2; }
  .badge { display:inline-block; padding:6px 14px; border-radius:999px; font-weight:700; font-size:20px; color:#0f1115; }
  .strip { display:flex; gap:3px; margin-top:10px; flex-wrap:wrap; }
  .strip span { width:14px; height:14px; border-radius:3px; display:inline-block; }
  .exp { background:#1a1d24; border-radius:10px; padding:14px 18px; font-size:12px; color:#9aa0a6; line-height:1.8; max-width:1150px; margin-bottom:16px; }
  .exp b { color:#ffa94d; }
  .range-bar { display:flex; gap:6px; margin:0 0 12px 0; flex-wrap:wrap; align-items:center; font-size:12px; color:#9aa0a6; }
  .range-btn { background:#1a1d24; border:1px solid #2a2e37; color:#9aa0a6; padding:5px 12px; border-radius:999px; cursor:pointer; font-size:12px; font-family:inherit; }
  .range-btn.active { background:#4dabf7; color:#0f1115; border-color:#4dabf7; font-weight:bold; }
  .chart-box { background:#12151b; border:1px solid #23262e; border-radius:12px; padding:14px 16px; margin-bottom:16px; }
  .chart-box h2 { font-size:15px; margin:0 0 4px 0; }
  .chart-box h2 .now { font-size:13px; font-weight:normal; color:#c9ccd1; margin-left:8px; }
  .chart-box .desc { font-size:12px; color:#9aa0a6; margin-bottom:8px; line-height:1.6; }
  .chart-wrap { position:relative; height:320px; }
  .legend-ph { display:flex; gap:12px; flex-wrap:wrap; font-size:12px; color:#c9ccd1; margin:4px 0 8px 0; }
  .legend-ph i { width:14px; height:10px; border-radius:2px; display:inline-block; margin-right:4px; vertical-align:middle; }
  .pos { color:#ff8787; } .neg { color:#74a7ff; }
  @media (max-width: 640px) { body { padding:16px; } .card.main { grid-column:span 1; } .chart-wrap { height:260px; } }
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <a class="back" href="relative_strength.html">종목·섹터 상대강도 순위 &rarr;</a>
  <h1>시장 국면 점수<span class="sub">밸류 하단 · 공격-방어 · 120일선 위 종목으로 본 6국면 (주간)</span></h1>
  <div class="updated">최종 갱신: __UPDATED__ &middot; 데이터 기준일: __ASOF__ &middot; 매주 목요일 기준(마지막 점은 기준일)</div>

  <div class="cards" id="cards"></div>

  <div class="exp">
    <b>점수 만드는 법</b>: 세 지표를 각각 <b>그 시점까지의 과거 값 대비 백분위(0~100점)</b>로 바꾼 뒤(미래 정보 없음)
    <b>밸류 하단 3점 · 공격-방어 2점 · 120일선 위 2점</b> 비중으로 평균하고, 13주 평균을 국면 점수로 씁니다. 높을수록 시장 내부가 튼튼하고 많이 올라와 있다는 뜻입니다.<br>
    <b>국면 판정</b>: 점수가 지금까지 점수 분포의 하위 1/3이면 낮음, 상위 1/3이면 높음, 그 사이는 중간이고, 8주 전보다 오르는 중이면
    낮음 ① 반등시작 · 중간 ② 중소형 확산 · 높음 ③ 전면 확산, 내리는 중이면 높음 ④ 고점 · 중간 ⑤ 중소형 하락 · 낮음 ⑥ 대형주까지 바닥입니다.<br>
    <b>배점 근거(2026-10-07 백테스트)</b>: 2018~2026 주간, 0~3점 조합 2만여 개를 코스피·시장(동일가중) 지수의 실제 상승·하락 구간과 대조했습니다.
    인접 국면까지 맞춘 비율 약 65%(무작위 50%), 상승·하락 구분 약 60%. 2018~21로 배점을 고르고 2022~26에 그대로 적용해도 59~61%였습니다.
    코스피를 이긴 섹터 수·종목 수, 중소형-대형 상대강도는 정답률을 올리지 못해 0점입니다.<br>
    <b>주의</b>: 정확히 같은 국면을 맞히는 비율은 18% 정도라 <b>대략적인 위치</b>로만 보세요. 특히 ④ 고점 판정 뒤에도 코스피가 더 오른 경우(2020·2025·2026 상승장)가
    많아 매도 신호로 쓰면 안 됩니다. 섹터·밸류 데이터는 현재 상장 종목 기준입니다.
  </div>

  <div class="range-bar" id="rangeBar"><span>기간</span></div>

  <div class="chart-box">
    <h2>코스피와 국면 <span class="now" id="nowPhase"></span></h2>
    <div class="legend-ph" id="phLegend"></div>
    <div class="chart-wrap"><canvas id="cKospi"></canvas></div>
  </div>

  <div class="chart-box">
    <h2>국면 점수 (0~100, 13주 평균) <span class="now" id="nowScore"></span></h2>
    <div class="desc">점선은 그 시점까지 점수 분포의 하위 1/3(낮음 기준)·상위 1/3(높음 기준)입니다. 점수가 어느 구간에 있는지 + 8주 전보다 오르는지로 국면이 정해집니다.</div>
    <div class="chart-wrap" style="height:260px"><canvas id="cScore"></canvas></div>
  </div>

  <div class="chart-box">
    <h2>공격-방어 상대강도 (2점) <span class="now" id="nowD"></span></h2>
    <div class="desc" id="descD"></div>
    <div class="chart-wrap"><canvas id="cD"></canvas></div>
  </div>

  <div class="chart-box">
    <h2>밸류 하단 종목 비율 (3점) <span class="now" id="nowC"></span></h2>
    <div class="desc" id="descC"></div>
    <div class="chart-wrap"><canvas id="cC"></canvas></div>
  </div>

  <div class="chart-box">
    <h2>120일선 위 종목 비율 (2점) <span class="now" id="nowX"></span></h2>
    <div class="desc">주가가 자기 120일 이동평균 위에 있는 코스피·코스닥 보통주(상장 250거래일 이상) 비율입니다. 높을수록 상승 국면 쪽입니다(점수에 그대로 반영).</div>
    <div class="chart-wrap"><canvas id="cX"></canvas></div>
  </div>

<script>
const DATA = __DATA__;
const N = DATA.dates.length;
const PH = DATA.phases;
const PHC = {1: '#9be3b0', 2: '#4cc47a', 3: '#1f8a4c', 4: '#f2a25c', 5: '#e8684a', 6: '#b5252b'};
const PHDESC = {
  1: '점수가 낮은 곳에서 오르기 시작 - 시총 큰 종목·성장 높은 종목부터 반등하는 구간',
  2: '점수 중간·상승 - 상승이 중소형으로 퍼지기 시작, 바닥권 종목은 아직 많음',
  3: '점수 높음·상승 - 소형주까지 상승이 퍼짐, 밸류 바닥 종목이 적음',
  4: '점수 높음·하락 전환 - 공격 업종이 꺾이고 방어주가 상대적으로 강해지기 시작',
  5: '점수 중간·하락 - 중소형부터 하락이 퍼지고 방어주 상대강도가 강해짐',
  6: '점수 낮음·하락 - 대형주까지 밸류 바닥권, 대부분 지표 부진'
};
const $ = id => document.getElementById(id);
const fmt = (v, d) => v === null || v === undefined ? '-' : v.toFixed(d === undefined ? 1 : d);
const sgn = (v, d) => v === null || v === undefined ? '-' : (v >= 0 ? '+' : '') + v.toFixed(d === undefined ? 1 : d);
function lastIdx(arr) { for (let i = arr.length - 1; i >= 0; i--) if (arr[i] !== null) return i; return -1; }
function firstIdx(arr) { for (let i = 0; i < arr.length; i++) if (arr[i] !== null) return i; return -1; }

// ---------- 현재 상태 카드
(function renderCards() {
  const iP = lastIdx(DATA.phase), p = DATA.phase[iP];
  const iS = lastIdx(DATA.score), s = DATA.score[iS], s8 = DATA.score[iS - DATA.dirWeeks];
  const strip = DATA.phase.slice(Math.max(0, iP - 25), iP + 1).map((q, k, a) => '<span title="' + DATA.dates[iP - (a.length - 1 - k)] + ' ' + (q ? PH[q] : '-') + '" style="background:' + (q ? PHC[q] : '#2a2e37') + '"></span>').join('');
  const cd = (lbl, val, sub) => '<div class="card"><div class="lbl">' + lbl + '</div><div class="val">' + val + '</div><div class="sub">' + sub + '</div></div>';
  const iC = lastIdx(DATA.C), iD = lastIdx(DATA.D), iX = lastIdx(DATA.X);
  $('cards').innerHTML =
    '<div class="card main"><div class="lbl">현재 국면 (' + DATA.dates[iP] + ')</div><span class="badge" style="background:' + PHC[p] + '">' + PH[p] + '</span>' +
    '<div class="sub">' + PHDESC[p] + '</div><div class="sub">최근 26주 국면 흐름(왼쪽이 과거)</div><div class="strip">' + strip + '</div></div>' +
    cd('국면 점수', fmt(s) + '<span style="font-size:13px;color:#9aa0a6"> / 100</span>', '낮음 &lt; ' + fmt(DATA.lo[iS]) + ' · 높음 &gt; ' + fmt(DATA.hi[iS]) + '<br>8주 전 ' + fmt(s8) + ' → <span class="' + (s - s8 >= 0 ? 'pos' : 'neg') + '">' + sgn(s - s8) + '</span>') +
    cd('공격-방어 상대강도(1달)', '<span class="' + (DATA.D[iD] >= 0 ? 'pos' : 'neg') + '">' + sgn(DATA.D[iD]) + '%p</span>', '점수 ' + fmt(DATA.sD[iD], 0) + '점 (높을수록 공격 섹터 우위)') +
    cd('밸류 하단 종목 비율', fmt(DATA.C[iC]) + '%', '점수 ' + fmt(DATA.sC[iC], 0) + '점 (비율이 낮을수록 점수 높음) · ' + DATA.cN.toLocaleString() + '종목 기준') +
    cd('120일선 위 종목 비율', fmt(DATA.X[iX]) + '%', '점수 ' + fmt(DATA.sX[iX], 0) + '점 (높을수록 상승 쪽)');
  $('nowPhase').textContent = '현재 ' + PH[p];
  $('nowScore').textContent = '현재 ' + fmt(s) + ' (8주 전 ' + fmt(s8) + ')';
  $('nowD').textContent = '현재 ' + sgn(DATA.D[iD]) + '%p · ' + fmt(DATA.sD[iD], 0) + '점';
  $('nowC').textContent = '현재 ' + fmt(DATA.C[iC]) + '% · ' + fmt(DATA.sC[iC], 0) + '점';
  $('nowX').textContent = '현재 ' + fmt(DATA.X[iX]) + '% · ' + fmt(DATA.sX[iX], 0) + '점';
  $('descD').innerHTML = '공격 섹터(코스피 대비 민감도 상위 ' + DATA.aggr.length + '개: ' + DATA.aggr.join(', ') + ') 동일가중 1달(20거래일) 수익률에서 ' +
    '방어 섹터(' + DATA.dfn.join(', ') + ') 1달 수익률을 뺀 값입니다. 높을수록 위험 선호(상승 국면 쪽)입니다. 섹터는 내 섹터 정리 기준입니다.';
  $('descC').innerHTML = 'OP밴드 배수(시가총액 ÷ 12개월 선행 영업이익 추정치)가 <b>자기 최근 3년 하위 10% 아래</b>인 종목 비율입니다(그 시점까지 데이터만 사용, 2년 이상 이력이 있는 종목). ' +
    '많을수록 시장이 밸류 바닥권이라는 뜻이라 점수에는 <b>거꾸로</b>(비율이 낮을수록 높은 점수) 반영됩니다.';
  $('phLegend').innerHTML = Object.keys(PH).map(k => '<span><i style="background:' + PHC[k] + '"></i>' + PH[k] + '</span>').join('');
})();

// ---------- 차트 공통
function bgPlugin(ph, alpha) {
  return {
  id: 'phaseBg',
  beforeDatasetsDraw(chart) {
    const {ctx, chartArea: a, scales: {x}} = chart;
    const n = chart.data.labels.length;
    ctx.save();
    for (let i = 0; i < n; i++) {
      const q = ph[i];
      if (!q) continue;
      const xc = x.getPixelForValue(i);
      const x0 = i === 0 ? a.left : (x.getPixelForValue(i - 1) + xc) / 2;
      const x1 = i === n - 1 ? a.right : (xc + x.getPixelForValue(i + 1)) / 2;
      ctx.fillStyle = PHC[q] + alpha;
      ctx.fillRect(x0, a.top, x1 - x0, a.bottom - a.top);
    }
    ctx.restore();
  }
  };
}
Chart.defaults.color = '#9aa0a6';
Chart.defaults.borderColor = '#23262e';
Chart.defaults.font.family = '-apple-system, "Malgun Gothic", sans-serif';
const RANGES = [['1년', 52], ['3년', 156], ['5년', 260], ['전체', 0]];
let RANGE = 0;
const charts = {};
function windowStart(seriesList) {
  let f = N;
  seriesList.forEach(s => { const k = firstIdx(s); if (k >= 0) f = Math.min(f, k); });
  return RANGE ? Math.max(f, N - RANGE) : f;
}
function xAxis() {
  return {grid: {display: false}, ticks: {maxTicksLimit: window.innerWidth < 640 ? 4 : 10, maxRotation: 0, autoSkip: true,
    callback: function (v) { const l = this.getLabelForValue(v); return l ? l.slice(2, 4) + '.' + l.slice(5, 7) : l; }}};
}
function rolling(arr, n) {
  const out = new Array(arr.length).fill(null);
  for (let i = 0; i < arr.length; i++) {
    let s = 0, c = 0;
    for (let j = Math.max(0, i - n + 1); j <= i; j++) if (arr[j] !== null) { s += arr[j]; c++; }
    out[i] = (arr[i] !== null && c) ? s / c : null;
  }
  return out;
}
const D13 = rolling(DATA.D, 13);
function baseOpts(extra) {
  return Object.assign({
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: {mode: 'index', intersect: false},
    plugins: {legend: {labels: {boxWidth: 12, font: {size: 11}}}},
    scales: {x: xAxis()}
  }, extra);
}
function kospiDataset(sl) {
  return {label: '코스피(오른쪽)', data: sl(DATA.kospi), borderColor: '#6b7280', borderWidth: 1, pointRadius: 0, yAxisID: 'y2', order: 9};
}
function build() {
  Object.values(charts).forEach(c => c.destroy());
  // 1) 코스피 + 국면 배경
  let s0 = windowStart([DATA.phase]);
  let sl = a => a.slice(s0);
  const phAt = sl(DATA.phase);
  charts.k = new Chart($('cKospi'), {type: 'line', plugins: [bgPlugin(phAt, '88')], data: {labels: sl(DATA.dates), datasets: [
    {label: '코스피', data: sl(DATA.kospi), borderColor: '#e6e6e6', borderWidth: 1.6, pointRadius: 0}
  ]}, options: baseOpts({
    plugins: {legend: {display: false},
      tooltip: {callbacks: {afterBody: items => { const q = phAt[items[0].dataIndex]; return q ? '국면: ' + PH[q] : ''; }}}},
    scales: {x: xAxis(), y: {type: 'logarithmic', ticks: {callback: v => Number(v).toLocaleString()}}}
  })});
  // 2) 점수
  charts.s = new Chart($('cScore'), {type: 'line', plugins: [bgPlugin(phAt, '33')], data: {labels: sl(DATA.dates), datasets: [
    {label: '국면 점수', data: sl(DATA.score), borderColor: '#ffd43b', borderWidth: 2, pointRadius: 0},
    {label: '높음 기준', data: sl(DATA.hi), borderColor: '#ff8787', borderWidth: 1, borderDash: [5, 4], pointRadius: 0},
    {label: '낮음 기준', data: sl(DATA.lo), borderColor: '#74a7ff', borderWidth: 1, borderDash: [5, 4], pointRadius: 0}
  ]}, options: baseOpts({scales: {x: xAxis(), y: {min: 0, max: 100}}})});
  // 3~5) 구성요소 (코스피를 오른쪽 축에)
  const comp = (id, key, label, color, unit, ref, smooth) => {
    const st = windowStart([DATA[key]]);
    const s2 = a => a.slice(st);
    const ds = [];
    if (smooth) ds.push({label: '13주 평균', data: s2(smooth), borderColor: color, borderWidth: 2.4, pointRadius: 0, yAxisID: 'y', order: 0});
    ds.push({label: label, data: s2(DATA[key]), borderColor: smooth ? color + '66' : color, borderWidth: smooth ? 1 : 1.8, pointRadius: 0, yAxisID: 'y', order: 1});
    if (ref !== undefined) ds.push({label: '기준 ' + ref + unit, data: s2(DATA[key]).map(() => ref), borderColor: '#9aa0a6', borderWidth: 1, borderDash: [4, 4], pointRadius: 0, yAxisID: 'y', order: 2});
    ds.push(Object.assign(kospiDataset(s2), {}));
    charts[id] = new Chart($(id), {type: 'line', data: {labels: s2(DATA.dates), datasets: ds}, options: baseOpts({
      plugins: {tooltip: {callbacks: {label: c => c.dataset.label + ': ' + (c.parsed.y === null ? '-' : (c.dataset.yAxisID === 'y2' ? Math.round(c.parsed.y).toLocaleString() : c.parsed.y.toFixed(1) + unit))}}},
      scales: {x: xAxis(),
        y: {position: 'left', ticks: {callback: v => v + unit}},
        y2: {position: 'right', grid: {display: false}, ticks: {callback: v => Number(v).toLocaleString()}}}
    })});
  };
  comp('cD', 'D', '주간 값', '#ffa94d', '%p', 0, D13);
  comp('cC', 'C', '밸류 하단 종목 비율', '#b197fc', '%');
  comp('cX', 'X', '120일선 위 종목 비율', '#63e6be', '%', 50);
}
$('rangeBar').innerHTML += RANGES.map(([l, w]) => '<button class="range-btn' + (w === RANGE ? ' active' : '') + '" data-w="' + w + '">' + l + '</button>').join('');
$('rangeBar').addEventListener('click', e => {
  const b = e.target.closest('button[data-w]');
  if (!b) return;
  RANGE = parseInt(b.dataset.w, 10);
  document.querySelectorAll('.range-btn').forEach(x => x.classList.toggle('active', x === b));
  build();
});
build();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
