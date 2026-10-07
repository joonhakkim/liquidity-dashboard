"""
시장 국면 점수 페이지(docs/market_phase.html)를 만든다 (2026-10-07 사용자 요청 - "공격 방어 상대강도,
하단 종목 비율, 120일선 위 종목에 점수 비중을 줘서 6국면으로 나누고 홈페이지에 하나 만들자, 그 밑에
각각 따로 차트로", 이어서 "기간 조절할 수 있게, 반은 맞추고 반은 시험보게(과적합 점검),
⑤ 중소형 하락이 얼마나 잘 맞는지가 가장 중요").

국면 점수(0~100) 기본값 = (밸류 하단 3점 + 공격-방어 2점 + 120일선 위 2점) / 7, 13주 평균, 8주 방향
(2018~2026 주간 백테스트로 정한 배점; 페이지에서 배점·평균 기간·방향 기간을 바꾸면 브라우저에서 다시 계산).
 - C 밸류 하단 비율: OP밴드 배수(시총/12개월 선행 영업이익)가 자기 최근 3년 하위 10% 아래인 종목 비율
   (docs/op_band_data 종목별 시계열로 그 시점까지 데이터만 써서 계산, 2년 이상 이력 종목만). 낮을수록 +.
 - D 공격-방어 상대강도: 공격 섹터 동일가중 1달(20거래일) 수익률 - 방어 섹터 1달 수익률. 높을수록 +.
 - X 120일선 위 종목 비율: 주가가 120일 이동평균 위인 보통주 비율(상장 250일 이상). 높을수록 +.
각 지표는 그 시점까지의 과거 값 대비 백분위(0~100점, 미래 정보 없음)로 바꿔 배점대로 평균한다.
국면 = N주 평균 점수의 수준(그 시점까지 점수 분포의 1/3, 2/3 기준: 낮음/중간/높음) x 방향(K주 전보다 상승/하락)
  상승: 낮음 ① 반등시작, 중간 ② 중소형 확산, 높음 ③ 전면 확산 / 하락: 높음 ④ 고점, 중간 ⑤ 중소형 하락, 낮음 ⑥ 바닥
반반 검증용으로 실제(사후) 국면 = 코스피·시장(동일가중)·중소형(시총 101위~ 동일가중) 지수의 15% 되돌림 구간을
진행률 1/3씩 나눈 것, ⑤ 성적 = ⑤ 판정 주가 실제 중소형 하락 구간이었는지 + 이후 13주 중소형 수익률을 함께 싣는다.
고점 경고(실험용) = 코스피가 N주 최고치 근처인데 M일 ADR이 기준 미만(+120일선 위 비율 조건) - 반반 검증에서 과적합이
확인돼 실험용으로만 표시(2026-10-07).
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
EVAL_START = "2018-07-01"
HALF_SPLIT = "2022-07-01"
LARGE_N = 100          # 대형 = 매주 시가총액 상위 100(OP밴드 종목 시총 기준), 나머지 = 중소형
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


def on_grid(series, grid, max_age_days=10):
    """주간 그리드 날짜마다 그 이전 마지막 관측값(max_age_days 이내만)."""
    v = series.reindex(grid, method="ffill")
    src = pd.Series(series.index, index=series.index).reindex(grid, method="ffill")
    age = (grid - pd.DatetimeIndex(src.values)).days
    return v.where(age <= max_age_days)


def op_band_weekly(grid):
    """OP밴드 종목별 시계열 -> (밸류 하단 비율, 유효 종목수, 주간 시가총액 표)."""
    flags, caps = {}, {}
    for f in glob.glob(os.path.join(OP_BAND_DIR, "A*.json")):
        with open(f, encoding="utf-8") as fh:
            j = json.load(fh)
        dts = pd.to_datetime(j["dates"])
        mc = np.array(j["mktcapEok"], dtype=float)
        op = np.array(j["opEok"], dtype=float)
        code = j["code"][1:] if j["code"].startswith("A") else j["code"]
        cap = pd.Series(mc, index=dts).sort_index()
        cap = cap[~cap.index.duplicated()]
        caps[code] = on_grid(cap, grid)
        m = pd.Series(np.where(op > 0, mc / np.where(op > 0, op, 1), np.nan), index=dts).sort_index()
        m = m[~m.index.duplicated()]
        if m.notna().sum() < 20:
            continue
        p10 = m.rolling("1095D", min_periods=20).quantile(0.10)
        elig = (m.index - m.index.min()).days >= 730
        fl = (m < p10).astype(float).where(m.notna() & p10.notna() & elig)
        flags[code] = on_grid(fl, grid)
    F = pd.DataFrame(flags)
    n = F.notna().sum(axis=1)
    return F.sum(axis=1) / n.replace(0, np.nan), n, pd.DataFrame(caps)


def zigzag(s, thr):
    piv = []
    hi_d, hi = s.index[0], s.iloc[0]
    lo_d, lo = hi_d, hi
    trend = 0
    for dd, p in s.iloc[1:].items():
        if trend == 0:
            if p > hi:
                hi, hi_d = p, dd
            if p < lo:
                lo, lo_d = p, dd
            if p >= lo * (1 + thr):
                trend = 1; piv.append((lo_d, "L", lo)); hi, hi_d = p, dd
            elif p <= hi * (1 - thr):
                trend = -1; piv.append((hi_d, "H", hi)); lo, lo_d = p, dd
        elif trend == 1:
            if p > hi:
                hi, hi_d = p, dd
            elif p <= hi * (1 - thr):
                piv.append((hi_d, "H", hi)); trend = -1; lo, lo_d = p, dd
        else:
            if p < lo:
                lo, lo_d = p, dd
            elif p >= lo * (1 + thr):
                piv.append((lo_d, "L", lo)); trend = 1; hi, hi_d = p, dd
    return piv


def truth_phase(daily, thr, grid):
    """지그재그 상승 구간 진행률 1/3씩 = 1,2,3 / 하락 구간 낙폭 진행률 1/3씩 = 4,5,6 (주간 그리드, 마지막 미완성 구간은 없음)."""
    s = daily.dropna()
    piv = zigzag(s, thr)
    lab = pd.Series(np.nan, index=grid)
    for (d0, t0, v0), (d1, t1, v1) in zip(piv[:-1], piv[1:]):
        seg = s.loc[d0:d1]
        if t0 == "L":
            prog = (seg - v0) / (v1 - v0)
            ph = np.where(prog < 1 / 3, 1, np.where(prog < 2 / 3, 2, 3))
        else:
            prog = (v0 - seg) / (v0 - v1)
            ph = np.where(prog < 1 / 3, 4, np.where(prog < 2 / 3, 5, 6))
        g = grid[(grid >= d0) & (grid <= d1)]
        lab.loc[g] = pd.Series(ph, index=seg.index).reindex(g, method="ffill").values
    return lab, piv


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
    Xw = X.reindex(grid, method="ffill")
    Dw = D.reindex(grid, method="ffill")
    Cw, Cn, caps = op_band_weekly(grid)
    Cw = Cw.where(Cn >= 300)
    kospi = load_kospi_series()
    ks = pd.Series({pd.Timestamp(k): v for k, v in kospi.items()}).sort_index()
    Kd = ks.reindex(ks.index.union(wide.index)).ffill().reindex(wide.index)
    Kw = ks.reindex(ks.index.union(grid)).ffill().reindex(grid)
    # 시장(동일가중) / 중소형(매주 시총 상위 100 밖) 지수
    EW = (1 + R.where(active).mean(axis=1).fillna(0)).cumprod()
    rank = caps.rank(axis=1, ascending=False)
    large_w = (rank <= LARGE_N).reindex(columns=R.columns, fill_value=False).astype(float)
    large_d = large_w.shift(1).reindex(R.index, method="ffill").fillna(0).astype(bool)
    SM = (1 + R.where(~large_d).mean(axis=1).fillna(0)).cumprod()
    SMw = SM.reindex(grid, method="ffill")
    # 사후 실제 국면(15% 되돌림)
    t0 = pd.Timestamp("2016-01-01")
    tK, pivK = truth_phase(Kd.loc[t0:], 0.15, grid)
    tE, _ = truth_phase(EW.loc[t0:], 0.15, grid)
    tS, pivS = truth_phase(SM.loc[t0:], 0.15, grid)
    sm_base = SM.loc[t0:].iloc[0]
    pivS = [[d.strftime("%Y-%m-%d"), t, round(float(v / sm_base * 100), 2)] for d, t, v in pivS if d >= pd.Timestamp("2018-01-01")]
    peaksK = [d.strftime("%Y-%m-%d") for d, t, _ in pivK if t == "H" and d >= pd.Timestamp("2018-01-01")]
    # 이후 13주: 코스피 최대낙폭(일간 기준), 중소형 수익률
    kd_t = Kd.index.values.astype("datetime64[D]").astype(np.int64)
    kd_v = Kd.values.astype(float)
    g_t = grid.values.astype("datetime64[D]").astype(np.int64)
    mdd = []
    for gt in g_t:
        if gt + 91 > kd_t[-1]:
            mdd.append(np.nan)
            continue
        i0 = np.searchsorted(kd_t, gt, side="right")
        i1 = np.searchsorted(kd_t, gt + 91, side="right")
        base = kd_v[i0 - 1] if i0 > 0 else np.nan
        seg = kd_v[i0:i1]
        mdd.append(np.nanmin(seg) / base - 1 if len(seg) else np.nan)
    fK13mdd = pd.Series(mdd, index=grid)
    fSM13 = SMw.shift(-13) / SMw - 1
    # 고점 경고용: 코스피 상장 보통주 ADR(10/20/40/60일), 코스피 26/52주 최고치
    kcodes = [c for c in wide.columns if meta.at[c, "market"] == "KOSPI"]
    rk = wide[kcodes].pct_change(fill_method=None)
    adv, dec = (rk > 0).sum(axis=1), (rk < 0).sum(axis=1)
    adr = {n: (adv.rolling(n).sum() / dec.rolling(n).sum() * 100).reindex(grid, method="ffill") for n in (10, 20, 40, 60)}
    highs = {wk: Kd.rolling(wk * 5, min_periods=wk * 3).max().reindex(grid, method="ffill") for wk in (26, 52)}
    # 점수(소수 둘째 자리로 맞춰 브라우저 재계산과 똑같이)
    sc = pd.DataFrame({"C": 100 - exp_pct(Cw), "D": exp_pct(Dw), "X": exp_pct(Xw)}).round(2)
    valid = sc.notna().all(axis=1)
    start = valid.idxmax()
    sc2 = sc.loc[start:]
    comp = (sc2["C"] * WEIGHTS["C"] + sc2["D"] * WEIGHTS["D"] + sc2["X"] * WEIGHTS["X"]) / sum(WEIGHTS.values())
    comp = comp.where(sc2.notna().all(axis=1))
    Ls = comp.rolling(SMOOTH_WEEKS, min_periods=1).mean()
    lo = Ls.expanding(26).quantile(1 / 3)
    hi = Ls.expanding(26).quantile(2 / 3)
    level = np.where(Ls < lo, 0, np.where(Ls > hi, 2, 1))
    rising = (Ls - Ls.shift(DIR_WEEKS)) > 0
    ph = pd.Series(np.where(rising, level + 1, 6 - level), index=Ls.index).where(Ls.notna() & lo.notna() & Ls.shift(DIR_WEEKS).notna())
    out = pd.DataFrame({"kospi": Kw, "C": Cw, "D": Dw, "X": Xw, "sC": sc["C"], "sD": sc["D"], "sX": sc["X"]})
    out["score"] = Ls.reindex(grid)
    out["lo"] = lo.reindex(grid)
    out["hi"] = hi.reindex(grid)
    out["phase"] = ph.reindex(grid)
    out["tK"], out["tE"], out["tS"] = tK, tE, tS
    out["fK13mdd"], out["fSM13"] = fK13mdd, fSM13
    out["sm_idx"] = (SMw / sm_base * 100).where(grid >= t0)
    for n, s in adr.items():
        out[f"adr{n}"] = s
    for wk, s in highs.items():
        out[f"hi{wk}"] = s
    return asof, out, agg_list, def_list, int(Cn.iloc[-1]), peaksK, pivS


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
    asof, out, agg_list, def_list, c_n, peaksK, pivS = compute()
    last = out.dropna(subset=["phase"]).iloc[-1]
    prev = out["score"].dropna()
    print(f"데이터 기준일 {asof.date()} | 국면 {PHASES[int(last['phase'])]} | 점수 {last['score']:.1f} (낮음<{last['lo']:.1f}, 높음>{last['hi']:.1f}) | 8주 전 {prev.iloc[-1 - DIR_WEEKS]:.1f}")
    print(f"  밸류 하단 비율 {out['C'].iloc[-1] * 100:.1f}% ({c_n}종목) | 공격-방어 {out['D'].iloc[-1] * 100:+.1f}%p | 120일선 위 {out['X'].iloc[-1] * 100:.1f}%")
    os.makedirs(os.path.dirname(SUMMARY_PATH), exist_ok=True)
    out.rename_axis("date").to_csv(SUMMARY_PATH, encoding="utf-8-sig", float_format="%.5g")

    def r(v, d=2):
        return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), d)

    def col(name, mult=1.0, d=2):
        return [r(v * mult if v == v else v, d) for v in out[name]]

    def ints(name):
        return [None if v != v else int(v) for v in out[name]]
    payload = {
        "asof": asof.strftime("%Y-%m-%d"),
        "dates": [d.strftime("%Y-%m-%d") for d in out.index],
        "kospi": col("kospi", 1, 1),
        "score": col("score", 1, 2), "lo": col("lo", 1, 2), "hi": col("hi", 1, 2), "phase": ints("phase"),
        "C": col("C", 100), "D": col("D", 100), "X": col("X", 100),
        "sC": col("sC"), "sD": col("sD"), "sX": col("sX"),
        "tK": ints("tK"), "tE": ints("tE"), "tS": ints("tS"),
        "fK13mdd": col("fK13mdd", 100), "fSM13": col("fSM13", 100),
        "adr10": col("adr10", 1, 1), "adr20": col("adr20", 1, 1), "adr40": col("adr40", 1, 1), "adr60": col("adr60", 1, 1),
        "hi26": col("hi26", 1, 1), "hi52": col("hi52", 1, 1),
        "peaksK": peaksK, "pivS": pivS, "smIdx": col("sm_idx", 1, 2),
        "phases": PHASES, "weights": WEIGHTS, "aggr": agg_list, "dfn": def_list, "cN": c_n,
        "smooth": SMOOTH_WEEKS, "dirWeeks": DIR_WEEKS, "evalStart": EVAL_START, "halfSplit": HALF_SPLIT,
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
  .panel { background:#161a21; border:1px solid #2a2e37; border-radius:12px; padding:12px 16px; margin-bottom:16px; }
  .panel h3 { font-size:14px; margin:0 0 8px 0; }
  .ctl { display:flex; gap:14px; flex-wrap:wrap; align-items:center; font-size:12px; color:#c9ccd1; }
  .ctl label { display:inline-flex; gap:6px; align-items:center; }
  .ctl select, .ctl input[type=date] { background:#0f1115; color:#e6e6e6; border:1px solid #2a2e37; border-radius:6px; padding:4px 6px; font-family:inherit; font-size:12px; }
  .btn { background:#1a1d24; border:1px solid #2a2e37; color:#c9ccd1; padding:5px 12px; border-radius:999px; cursor:pointer; font-size:12px; font-family:inherit; }
  .btn.active { background:#4dabf7; color:#0f1115; border-color:#4dabf7; font-weight:bold; }
  .note { font-size:12px; color:#9aa0a6; margin-top:8px; line-height:1.7; }
  .note b { color:#ffa94d; }
  table.st { border-collapse:collapse; font-size:12px; margin-top:8px; width:100%; max-width:900px; }
  table.st th, table.st td { border-bottom:1px solid #23262e; padding:6px 8px; text-align:right; white-space:nowrap; }
  table.st th:first-child, table.st td:first-child { text-align:left; white-space:normal; }
  table.st th { color:#9aa0a6; font-weight:normal; }
  table.st td .b { color:#7c828b; font-size:11px; margin-left:4px; }
  .good { color:#69db7c; font-weight:bold; } .bad { color:#ff8787; } .mid { color:#e6e6e6; }
  .tblwrap { overflow-x:auto; }
  .chart-box { background:#12151b; border:1px solid #23262e; border-radius:12px; padding:14px 16px; margin-bottom:16px; }
  .chart-box h2 { font-size:15px; margin:0 0 4px 0; }
  .chart-box h2 .now { font-size:13px; font-weight:normal; color:#c9ccd1; margin-left:8px; }
  .chart-box .desc { font-size:12px; color:#9aa0a6; margin-bottom:8px; line-height:1.6; }
  .chart-wrap { position:relative; height:320px; }
  .legend-ph { display:flex; gap:12px; flex-wrap:wrap; font-size:12px; color:#c9ccd1; margin:4px 0 8px 0; }
  .legend-ph i { width:14px; height:10px; border-radius:2px; display:inline-block; margin-right:4px; vertical-align:middle; }
  .pos { color:#ff8787; } .neg { color:#74a7ff; }
  .warn-on { color:#ffa94d; font-weight:bold; }
  @media (max-width: 640px) { body { padding:16px; } .card.main { grid-column:span 1; } .chart-wrap { height:260px; } }
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <a class="back" href="relative_strength.html">종목·섹터 상대강도 순위 &rarr;</a>
  <h1>시장 국면 점수<span class="sub">밸류 하단 · 공격-방어 · 120일선 위 종목으로 본 6국면 (주간)</span></h1>
  <div class="updated">최종 갱신: __UPDATED__ &middot; 데이터 기준일: __ASOF__ &middot; 매주 목요일 기준(마지막 점은 기준일)</div>

  <div class="panel">
    <h3>모델 설정 <span style="font-size:12px;color:#9aa0a6;font-weight:normal">바꾸면 국면·점수·차트·검증표가 바로 다시 계산됩니다</span></h3>
    <div class="ctl">
      <button class="btn" data-preset="base">기본 (밸류3·공격방어2·120일선2, 13주/8주)</button>
      <button class="btn" data-preset="five">⑤ 중시 (밸류 하단만, 8주/4주)</button>
    </div>
    <div class="ctl" style="margin-top:10px">
      <label>밸류 하단 <select id="wC"></select></label>
      <label>공격-방어 <select id="wD"></select></label>
      <label>120일선 위 <select id="wX"></select></label>
      <label>점수 평균 기간 <select id="sm"><option>4</option><option>8</option><option>13</option><option>26</option></select>주</label>
      <label>방향 판단 기간 <select id="kk"><option>2</option><option>4</option><option>8</option><option>13</option></select>주 전과 비교</label>
      <label>높음·낮음 기준 <select id="thr"><option value="exp">누적(2018~ 전체, 기본)</option><option value="104">최근 2년</option><option value="156">최근 3년</option><option value="260">최근 5년</option><option value="fix">고정 33/67점</option></select></label>
    </div>
    <div class="note" id="cfgNote"></div>
  </div>

  <div class="cards" id="cards"></div>

  <div class="panel">
    <h3>반반 검증 (앞 절반으로 맞추고 뒤 절반으로 시험) &middot; 지금 설정 기준</h3>
    <div class="tblwrap"><table class="st" id="tblHalf"></table></div>
    <div class="note">
      정답지(사후) = 코스피·시장(전 종목 동일가중) 지수가 15% 이상 되돌린 상승·하락 구간을 진행률 1/3씩 ①~⑥으로 나눈 것. 중소형 = 매주 시가총액 상위 100 밖 종목 동일가중 지수.
      괄호 안 회색 숫자는 <b>기준선(아무 주나 골랐을 때)</b>입니다. 기준선보다 확실히 높아야 의미가 있습니다.<br>
      <b>2026-10-07 검증 요약</b>: 앞 절반에서 가장 잘 맞던 설정들(밸류 위주, 짧은 평균)은 뒤 절반에서 인접 정답률이 72% → 54~57%로 떨어져 <b>과적합</b>이었고,
      기본 설정은 양쪽 모두 64% 안팎으로 안정적이었습니다. <b>⑤ 중소형 하락</b>은 두 절반 모두에서 기준선보다 크게 높아 6국면 중 가장 믿을 만한 신호입니다.
      다만 ⑤는 중소형 고점보다 <b>5~8주 늦게</b> 나오는 확인 신호입니다(아래 하락 구간표 참고).
    </div>
  </div>

  <div class="panel">
    <h3>높음·낮음 기준 잡는 방식 비교 &middot; 지금 배점·기간 기준</h3>
    <div class="tblwrap"><table class="st" id="tblThr"></table></div>
    <div class="note">
      <b>누적</b> = 2018년부터 그 주까지의 점수 전체를 3등분(기본). <b>최근 N년</b> = 최근 N년 점수만 3등분해 최근 환경에 더 빨리 맞춤. <b>고정</b> = 33점·67점.
      판정 변경 = 국면이 전 주와 달라진 주의 비율(높을수록 자주 뒤집힘). 행을 누르면 그 방식으로 위 설정이 바뀝니다.
    </div>
  </div>

  <div class="panel">
    <h3>⑤ 중소형 하락 성적 &middot; 국면별 이후 13주 중소형 지수 (검증 전체 기간)</h3>
    <div class="tblwrap"><table class="st" id="tblPhase"></table></div>
    <h3 style="margin-top:14px">실제 중소형 15% 하락 구간별 ⑤ 판정 시점</h3>
    <div class="tblwrap"><table class="st" id="tblLegs"></table></div>
  </div>

  <div class="range-bar ctl" id="rangeBar" style="margin-bottom:12px">
    <span>차트 기간</span>
    <span id="rangeBtns"></span>
    <label>시작 <input type="date" id="dFrom"></label>
    <label>끝 <input type="date" id="dTo"></label>
  </div>

  <div class="chart-box">
    <h2>코스피와 국면 <span class="now" id="nowPhase"></span></h2>
    <div class="legend-ph" id="phLegend"></div>
    <div class="chart-wrap"><canvas id="cKospi"></canvas></div>
  </div>

  <div class="chart-box">
    <h2>⑤ 판정 vs 실제 중소형 지수 <span class="now" id="nowSm"></span></h2>
    <div class="desc">선 = 중소형 지수(2016-01 = 100, 로그). 빨간 배경 = 지금 설정의 ⑤ 판정 주, 아래 회색 띠 = 사후적으로 실제 중소형 15% 하락 구간이었던 주.</div>
    <div class="chart-wrap"><canvas id="cSm"></canvas></div>
  </div>

  <div class="chart-box">
    <h2>국면 점수 (0~100) <span class="now" id="nowScore"></span></h2>
    <div class="desc" id="descScore"></div>
    <div class="chart-wrap" style="height:260px"><canvas id="cScore"></canvas></div>
  </div>

  <div class="panel">
    <h3>고점 경고 (실험용) <span id="warnNow" style="font-size:12px;font-weight:normal;margin-left:6px"></span></h3>
    <div class="ctl">
      <label>코스피가 <select id="hW"><option value="26">26주</option><option value="52">52주</option></select> 최고치의</label>
      <label><select id="near"><option>3</option><option>5</option><option>10</option></select>% 이내인데</label>
      <label>상승/하락 종목비(ADR) <select id="aN"><option>10</option><option>20</option><option>40</option><option>60</option></select>일이</label>
      <label><select id="aT"><option>80</option><option>90</option><option>100</option><option>110</option></select> 미만</label>
      <label>+ 120일선 위 종목 <select id="xC"><option value="0">조건 없음</option><option value="60">60% 미만</option><option value="50">50% 미만</option><option value="40">40% 미만</option></select></label>
    </div>
    <div class="tblwrap"><table class="st" id="tblWarn"></table></div>
    <div class="note">
      지수는 신고가 근처인데 오르는 종목보다 내리는 종목이 많은(쏠림) 상태를 경고합니다. 경고 시작 주는 코스피 차트에 주황 삼각형, 실제 코스피 15% 고점은 흰 별로 표시됩니다.
      <b>주의</b>: 앞 절반에서 가장 잘 맞던 조건(ADR20 &lt; 90, 26주 최고치 3% 이내)은 적중률이 67%였지만 뒤 절반에서 11%(기준 13%)로 무너졌습니다.
      어떤 조건도 두 절반 모두에서 안정적이지 않아 <b>참고용</b>으로만 보세요.
    </div>
  </div>

  <div class="chart-box">
    <h2>공격-방어 상대강도 <span class="now" id="nowD"></span></h2>
    <div class="desc" id="descD"></div>
    <div class="chart-wrap"><canvas id="cD"></canvas></div>
  </div>

  <div class="chart-box">
    <h2>밸류 하단 종목 비율 <span class="now" id="nowC"></span></h2>
    <div class="desc" id="descC"></div>
    <div class="chart-wrap"><canvas id="cC"></canvas></div>
  </div>

  <div class="chart-box">
    <h2>120일선 위 종목 비율 <span class="now" id="nowX"></span></h2>
    <div class="desc">주가가 자기 120일 이동평균 위에 있는 코스피·코스닥 보통주(상장 250거래일 이상) 비율입니다. 높을수록 상승 국면 쪽입니다(점수에 그대로 반영).</div>
    <div class="chart-wrap"><canvas id="cX"></canvas></div>
  </div>

  <div class="exp">
    <b>점수 만드는 법</b>: 세 지표를 각각 <b>그 시점까지의 과거 값 대비 백분위(0~100점)</b>로 바꾼 뒤(미래 정보 없음) 배점대로 평균하고, N주 평균을 국면 점수로 씁니다.
    밸류 하단 비율은 거꾸로(비율이 낮을수록 높은 점수) 반영합니다. 점수 자체에는 코스피 지수 값이 들어가지 않습니다(코스피는 정답지·이후 수익률 측정에만 사용).<br>
    <b>국면 판정</b>: 점수가 그 시점까지 점수 분포의 하위 1/3이면 낮음, 상위 1/3이면 높음, 그 사이는 중간. K주 전보다 오르는 중이면
    낮음 ① 반등시작 · 중간 ② 중소형 확산 · 높음 ③ 전면 확산, 내리는 중이면 높음 ④ 고점 · 중간 ⑤ 중소형 하락 · 낮음 ⑥ 대형주까지 바닥입니다.<br>
    <b>주의</b>: 정확히 같은 국면을 맞히는 비율은 낮아 <b>대략적인 위치</b>로만 보세요. ④ 고점 판정 뒤에도 코스피가 더 오른 경우가 많아 매도 신호로 쓰면 안 됩니다.
    섹터·밸류 데이터는 현재 상장 종목 기준입니다.
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
const nn = v => v !== null && v !== undefined;
const fmt = (v, d) => !nn(v) || isNaN(v) ? '-' : v.toFixed(d === undefined ? 1 : d);
const sgn = (v, d) => !nn(v) || isNaN(v) ? '-' : (v >= 0 ? '+' : '') + v.toFixed(d === undefined ? 1 : d);
function lastIdx(arr) { for (let i = arr.length - 1; i >= 0; i--) if (nn(arr[i])) return i; return -1; }
function firstIdx(arr) { for (let i = 0; i < arr.length; i++) if (nn(arr[i])) return i; return -1; }
function idxOnOrAfter(d) { for (let i = 0; i < N; i++) if (DATA.dates[i] >= d) return i; return N; }
function idxOnOrBefore(d) { for (let i = N - 1; i >= 0; i--) if (DATA.dates[i] <= d) return i; return -1; }
const EVAL0 = idxOnOrAfter(DATA.evalStart), SPLIT = idxOnOrAfter(DATA.halfSplit);
const HALVES = [
  ['앞 절반 (' + DATA.evalStart.slice(2, 7).replace('-', '.') + '~' + DATA.dates[SPLIT - 1].slice(2, 7).replace('-', '.') + ')', i => i >= EVAL0 && i < SPLIT],
  ['뒤 절반 (' + DATA.halfSplit.slice(2, 7).replace('-', '.') + '~)', i => i >= SPLIT],
  ['전체', i => i >= EVAL0]
];
const DAY = 86400000;
const T = DATA.dates.map(d => Date.parse(d));

// ---------- 모델 (Python과 같은 계산)
function qa(a, p) { const pos = (a.length - 1) * p, f = Math.floor(pos), c = Math.ceil(pos); return a[f] + (a[c] - a[f]) * (pos - f); }
function computeModel(c) {
  const ws = c.wC + c.wD + c.wX;
  const comp = new Array(N).fill(null);
  for (let i = 0; i < N; i++) {
    if (nn(DATA.sC[i]) && nn(DATA.sD[i]) && nn(DATA.sX[i]))
      comp[i] = (DATA.sC[i] * c.wC + DATA.sD[i] * c.wD + DATA.sX[i] * c.wX) / ws;
  }
  const start = firstIdx(comp);
  const Ls = new Array(N).fill(null), lo = new Array(N).fill(null), hi = new Array(N).fill(null), ph = new Array(N).fill(null);
  const sorted = [];
  const q = p => { const pos = (sorted.length - 1) * p, f = Math.floor(pos), cc = Math.ceil(pos); return sorted[f] + (sorted[cc] - sorted[f]) * (pos - f); };
  for (let i = Math.max(start, 0); i < N && start >= 0; i++) {
    let s = 0, n = 0;
    for (let j = Math.max(start, i - c.sm + 1); j <= i; j++) if (nn(comp[j])) { s += comp[j]; n++; }
    if (n) Ls[i] = s / n;
    if (nn(Ls[i])) {
      let a = 0, b = sorted.length;
      while (a < b) { const m = (a + b) >> 1; if (sorted[m] < Ls[i]) a = m + 1; else b = m; }
      sorted.splice(a, 0, Ls[i]);
    }
    if (c.thr === 'fix') { if (nn(Ls[i])) { lo[i] = 100 / 3; hi[i] = 200 / 3; } }
    else if (c.thr !== 'exp') {
      const win = [];
      for (let j = Math.max(start, i - c.thr + 1); j <= i; j++) if (nn(Ls[j])) win.push(Ls[j]);
      if (win.length >= 26) { win.sort((a, b) => a - b); lo[i] = qa(win, 1 / 3); hi[i] = qa(win, 2 / 3); }
    } else if (sorted.length >= 26) { lo[i] = q(1 / 3); hi[i] = q(2 / 3); }
    const pi = i - c.k;
    if (nn(Ls[i]) && nn(lo[i]) && pi >= start && nn(Ls[pi])) {
      const level = Ls[i] < lo[i] ? 0 : (Ls[i] > hi[i] ? 2 : 1);
      ph[i] = (Ls[i] - Ls[pi]) > 0 ? level + 1 : 6 - level;
    }
  }
  return {Ls, lo, hi, ph};
}

// ---------- 검증 통계
function pct(a) { return a.length ? a.filter(x => x).length / a.length * 100 : NaN; }
function mean(a) { return a.length ? a.reduce((s, x) => s + x, 0) / a.length : NaN; }
function phaseStats(ph, inMask) {
  const out = {};
  const truths = [DATA.tK, DATA.tE];
  let adj = [], ex = [], dir = [];
  truths.forEach(t => {
    const p = [], tt = [];
    for (let i = 0; i < N; i++) if (inMask(i) && nn(ph[i]) && nn(t[i])) { p.push(ph[i]); tt.push(t[i]); }
    if (!p.length) return;
    let a = 0, e = 0, uu = 0, ut = 0, dd = 0, dt = 0;
    for (let j = 0; j < p.length; j++) {
      const d = Math.abs(p[j] - tt[j]), cyc = Math.min(d, 6 - d);
      if (cyc <= 1) a++;
      if (d === 0) e++;
      if (tt[j] <= 3) { ut++; if (p[j] <= 3) uu++; } else { dt++; if (p[j] > 3) dd++; }
    }
    adj.push(a / p.length * 100); ex.push(e / p.length * 100);
    dir.push(((ut ? uu / ut : 0) + (dt ? dd / dt : 0)) / 2 * 100);
  });
  out.n = 0; for (let i = 0; i < N; i++) if (inMask(i) && nn(ph[i])) out.n++;
  out.adj = mean(adj); out.ex = mean(ex); out.dir = mean(dir);
  // ⑤
  const sel = i => inMask(i) && ph[i] === 5;
  const base = i => inMask(i) && nn(ph[i]);
  const coll = (f, key, fn) => { const a = []; for (let i = 0; i < N; i++) if (f(i) && nn(DATA[key][i])) a.push(fn(DATA[key][i])); return a; };
  out.n5 = 0; for (let i = 0; i < N; i++) if (sel(i)) out.n5++;
  out.down5 = pct(coll(sel, 'tS', v => v >= 4)); out.downB = pct(coll(base, 'tS', v => v >= 4));
  out.neg5 = pct(coll(sel, 'fSM13', v => v < 0)); out.negB = pct(coll(base, 'fSM13', v => v < 0));
  out.avg5 = mean(coll(sel, 'fSM13', v => v)); out.avgB = mean(coll(base, 'fSM13', v => v));
  return out;
}
function cellVs(v, b, unit, higherGood, d) {
  if (isNaN(v)) return '<td>-</td>';
  const diff = higherGood ? v - b : b - v;
  const cls = isNaN(b) ? 'mid' : (diff >= 10 ? 'good' : (diff <= 0 ? 'bad' : 'mid'));
  return '<td><span class="' + cls + '">' + (unit === '%r' ? sgn(v, 1) + '%' : fmt(v, d === undefined ? 0 : d) + '%') + '</span><span class="b">(' + (unit === '%r' ? sgn(b, 1) + '%' : fmt(b, 0) + '%') + ')</span></td>';
}
function renderHalfTable(M) {
  const S = HALVES.map(([, f]) => phaseStats(M.ph, f));
  const row = (lbl, f) => '<tr><td>' + lbl + '</td>' + S.map(f).join('') + '</tr>';
  $('tblHalf').innerHTML = '<tr><th>항목</th>' + HALVES.map(h => '<th>' + h[0] + '</th>').join('') + '</tr>' +
    row('국면 판정 주수', s => '<td>' + s.n + '</td>') +
    row('인접 국면까지 맞힌 비율 <span class="b">(무작위 ≈ 50%)</span>', s => cellVs(s.adj, 50, '%', true)) +
    row('정확히 같은 국면 <span class="b">(무작위 ≈ 17%)</span>', s => cellVs(s.ex, 100 / 6, '%', true)) +
    row('상승·하락 구분 <span class="b">(무작위 50%)</span>', s => cellVs(s.dir, 50, '%', true)) +
    row('<b style="color:#e8684a">⑤ 판정 주수</b>', s => '<td>' + s.n5 + '</td>') +
    row('⑤ 주 중 실제 중소형 하락 구간', s => cellVs(s.down5, s.downB, '%', true)) +
    row('⑤ 뒤 13주 안에 중소형 지수 하락 확률', s => cellVs(s.neg5, s.negB, '%', true)) +
    row('⑤ 뒤 13주 중소형 지수 평균 수익률', s => cellVs(s.avg5, s.avgB, '%r', false));
}
function renderPhaseTable(M) {
  const all = i => i >= EVAL0;
  let h = '<tr><th>국면</th><th>주수</th><th>실제 중소형 하락 구간</th><th>13주 뒤 중소형 평균</th><th>13주 뒤 하락 확률</th></tr>';
  const baseDown = [], baseR = [];
  for (let i = 0; i < N; i++) if (all(i) && nn(M.ph[i])) { if (nn(DATA.tS[i])) baseDown.push(DATA.tS[i] >= 4); if (nn(DATA.fSM13[i])) baseR.push(DATA.fSM13[i]); }
  for (let p = 1; p <= 6; p++) {
    const dn = [], r = []; let n = 0;
    for (let i = 0; i < N; i++) if (all(i) && M.ph[i] === p) { n++; if (nn(DATA.tS[i])) dn.push(DATA.tS[i] >= 4); if (nn(DATA.fSM13[i])) r.push(DATA.fSM13[i]); }
    const st = p === 5 ? ' style="background:#2a1a17"' : '';
    h += '<tr' + st + '><td><span style="color:' + PHC[p] + '">■</span> ' + PH[p] + '</td><td>' + n + '</td><td>' + fmt(pct(dn), 0) + '%</td><td class="' + (mean(r) < 0 ? 'neg' : 'pos') + '">' + sgn(mean(r)) + '%</td><td>' + fmt(pct(r.map(x => x < 0)), 0) + '%</td></tr>';
  }
  h += '<tr><td class="b">전체(기준선)</td><td>' + baseR.length + '</td><td>' + fmt(pct(baseDown), 0) + '%</td><td>' + sgn(mean(baseR)) + '%</td><td>' + fmt(pct(baseR.map(x => x < 0)), 0) + '%</td></tr>';
  $('tblPhase').innerHTML = h;
  // 하락 구간별
  let g = '<tr><th>중소형 고점</th><th>저점</th><th>낙폭</th><th>첫 ⑤ (고점 뒤)</th><th>첫 ⑤·⑥ (고점 뒤)</th><th>구간 중 ⑤·⑥ 비율</th></tr>';
  const pv = DATA.pivS;
  for (let j = 0; j + 1 < pv.length; j++) {
    if (pv[j][1] !== 'H' || pv[j][0] < DATA.evalStart) continue;
    const i0 = idxOnOrAfter(pv[j][0]), i1 = idxOnOrBefore(pv[j + 1][0]);
    let f5 = -1, f56 = -1, c56 = 0, n = 0;
    for (let i = i0; i <= i1; i++) {
      n++;
      if (M.ph[i] === 5 && f5 < 0) f5 = i;
      if ((M.ph[i] === 5 || M.ph[i] === 6)) { c56++; if (f56 < 0) f56 = i; }
    }
    const wk = i => i < 0 ? '없음' : DATA.dates[i] + ' (' + Math.round((T[i] - Date.parse(pv[j][0])) / DAY / 7) + '주)';
    g += '<tr><td>' + pv[j][0] + '</td><td>' + pv[j + 1][0] + '</td><td class="neg">' + sgn((pv[j + 1][2] / pv[j][2] - 1) * 100, 0) + '%</td><td>' + wk(f5) + '</td><td>' + wk(f56) + '</td><td>' + fmt(n ? c56 / n * 100 : NaN, 0) + '%</td></tr>';
  }
  $('tblLegs').innerHTML = g;
}

// ---------- 고점 경고
function computeWarn() {
  const aN = $('aN').value, aT = +$('aT').value, hW = $('hW').value, near = +$('near').value / 100, xC = +$('xC').value;
  const adr = DATA['adr' + aN], hi = DATA['hi' + hW];
  const w = new Array(N).fill(false);
  for (let i = 0; i < N; i++) {
    if (!nn(DATA.kospi[i]) || !nn(adr[i]) || !nn(hi[i])) continue;
    w[i] = DATA.kospi[i] >= hi[i] * (1 - near) && adr[i] < aT && (!xC || (nn(DATA.X[i]) && DATA.X[i] < xC));
  }
  const starts = w.map((v, i) => v && !(i > 0 && w[i - 1]));
  return {w, starts, adr, hi};
}
function renderWarn(W) {
  const peaks = DATA.peaksK.map(d => idxOnOrAfter(d)).filter(i => i < N);
  const S = HALVES.map(([, f]) => {
    const st = [], hits = [], base = [];
    for (let i = 0; i < N; i++) {
      if (!f(i)) continue;
      if (nn(DATA.fK13mdd[i])) base.push(DATA.fK13mdd[i] <= -10);
      if (W.starts[i]) { st.push(i); if (nn(DATA.fK13mdd[i])) hits.push(DATA.fK13mdd[i] <= -10); }
    }
    const pk = peaks.filter(p => f(p));
    const caught = pk.filter(p => st.some(i => (T[i] - T[p]) / DAY >= -56 && (T[i] - T[p]) / DAY <= 28)).length;
    return {n: st.length, hit: pct(hits), nh: hits.length, base: pct(base), caught, npk: pk.length};
  });
  const row = (lbl, f) => '<tr><td>' + lbl + '</td>' + S.map(f).join('') + '</tr>';
  $('tblWarn').innerHTML = '<tr><th>항목</th>' + HALVES.map(h => '<th>' + h[0] + '</th>').join('') + '</tr>' +
    row('경고 횟수(새로 켜진 주)', s => '<td>' + s.n + '</td>') +
    row('경고 뒤 13주 안에 코스피 −10% 이상 하락', s => s.nh ? cellVs(s.hit, s.base, '%', true) : '<td>-</td>') +
    row('실제 코스피 15% 고점 포착 <span class="b">(고점 8주 전~4주 뒤에 경고)</span>', s => '<td>' + s.caught + ' / ' + s.npk + '</td>');
  const i = N - 1;
  const dist = nn(W.hi[i]) ? (DATA.kospi[i] / W.hi[i] - 1) * 100 : NaN;
  $('warnNow').innerHTML = '지금: ' + (W.w[i] ? '<span class="warn-on">경고 켜짐</span>' : '경고 없음') +
    ' · ADR' + $('aN').value + ' ' + fmt(W.adr[i]) + ' · 코스피 ' + $('hW').value + '주 최고치 대비 ' + sgn(dist) + '%';
}

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
function fiveBg(ph, ts) {
  return {
    id: 'fiveBg',
    beforeDatasetsDraw(chart) {
      const {ctx, chartArea: a, scales: {x}} = chart;
      const n = chart.data.labels.length;
      ctx.save();
      for (let i = 0; i < n; i++) {
        const xc = x.getPixelForValue(i);
        const x0 = i === 0 ? a.left : (x.getPixelForValue(i - 1) + xc) / 2;
        const x1 = i === n - 1 ? a.right : (xc + x.getPixelForValue(i + 1)) / 2;
        if (ph[i] === 5) { ctx.fillStyle = '#e8684a66'; ctx.fillRect(x0, a.top, x1 - x0, a.bottom - a.top - 12); }
        if (nn(ts[i]) && ts[i] >= 4) { ctx.fillStyle = '#9aa0a6'; ctx.fillRect(x0, a.bottom - 9, x1 - x0, 9); }
      }
      ctx.restore();
    }
  };
}
Chart.defaults.color = '#9aa0a6';
Chart.defaults.borderColor = '#23262e';
Chart.defaults.font.family = '-apple-system, "Malgun Gothic", sans-serif';
const RANGES = [['1년', 52], ['3년', 156], ['5년', 260], ['전체', 0]];
let RANGE = 0, FROM = null, TO = null;
const charts = {};
function windowOf(seriesList) {
  let f = N;
  seriesList.forEach(s => { const k = firstIdx(s); if (k >= 0) f = Math.min(f, k); });
  let e = N - 1;
  if (FROM || TO) {
    if (FROM) f = Math.max(f, idxOnOrAfter(FROM));
    if (TO) e = Math.max(f, idxOnOrBefore(TO));
  } else if (RANGE) f = Math.max(f, N - RANGE);
  return a => a.slice(f, e + 1);
}
function xAxis() {
  return {grid: {display: false}, ticks: {maxTicksLimit: window.innerWidth < 640 ? 4 : 10, maxRotation: 0, autoSkip: true,
    callback: function (v) { const l = this.getLabelForValue(v); return l ? l.slice(2, 4) + '.' + l.slice(5, 7) : l; }}};
}
function rolling(arr, n) {
  const out = new Array(arr.length).fill(null);
  for (let i = 0; i < arr.length; i++) {
    let s = 0, c = 0;
    for (let j = Math.max(0, i - n + 1); j <= i; j++) if (nn(arr[j])) { s += arr[j]; c++; }
    out[i] = (nn(arr[i]) && c) ? s / c : null;
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

let M = null, W = null, CFG = null;
function buildCharts() {
  Object.values(charts).forEach(c => c.destroy());
  const sl = windowOf([M.ph]);
  const phAt = sl(M.ph);
  const peakSet = new Set(DATA.peaksK.map(d => idxOnOrAfter(d)));
  const idxs = sl(DATA.dates.map((_, i) => i));
  const warnPts = idxs.map(i => W.starts[i] ? DATA.kospi[i] : null);
  const peakPts = idxs.map(i => peakSet.has(i) ? DATA.kospi[i] : null);
  charts.k = new Chart($('cKospi'), {type: 'line', plugins: [bgPlugin(phAt, '88')], data: {labels: sl(DATA.dates), datasets: [
    {label: '코스피', data: sl(DATA.kospi), borderColor: '#e6e6e6', borderWidth: 1.6, pointRadius: 0},
    {label: '고점 경고 시작', data: warnPts, showLine: false, pointStyle: 'triangle', pointRadius: 7, pointBackgroundColor: '#ffa94d', borderColor: '#ffa94d'},
    {label: '실제 코스피 15% 고점', data: peakPts, showLine: false, pointStyle: 'star', pointRadius: 9, borderColor: '#ffffff', borderWidth: 2}
  ]}, options: baseOpts({
    plugins: {legend: {labels: {boxWidth: 12, font: {size: 11}, filter: it => it.datasetIndex > 0}},
      tooltip: {filter: it => it.datasetIndex === 0, callbacks: {afterBody: items => { const q = phAt[items[0].dataIndex]; return q ? '국면: ' + PH[q] : ''; }}}},
    scales: {x: xAxis(), y: {type: 'logarithmic', ticks: {callback: v => Number(v).toLocaleString()}}}
  })});
  const slS = windowOf([DATA.smIdx, M.ph]);
  charts.sm = new Chart($('cSm'), {type: 'line', plugins: [fiveBg(slS(M.ph), slS(DATA.tS))], data: {labels: slS(DATA.dates), datasets: [
    {label: '중소형 지수', data: slS(DATA.smIdx), borderColor: '#ffd43b', borderWidth: 1.6, pointRadius: 0}
  ]}, options: baseOpts({
    plugins: {legend: {display: false}, tooltip: {callbacks: {afterBody: items => { const q = slS(M.ph)[items[0].dataIndex]; return q ? '국면: ' + PH[q] : ''; }}}},
    scales: {x: xAxis(), y: {type: 'logarithmic', ticks: {callback: v => Number(v).toFixed(0)}}}
  })});
  charts.s = new Chart($('cScore'), {type: 'line', plugins: [bgPlugin(phAt, '33')], data: {labels: sl(DATA.dates), datasets: [
    {label: '국면 점수', data: sl(M.Ls), borderColor: '#ffd43b', borderWidth: 2, pointRadius: 0},
    {label: '높음 기준', data: sl(M.hi), borderColor: '#ff8787', borderWidth: 1, borderDash: [5, 4], pointRadius: 0},
    {label: '낮음 기준', data: sl(M.lo), borderColor: '#74a7ff', borderWidth: 1, borderDash: [5, 4], pointRadius: 0}
  ]}, options: baseOpts({scales: {x: xAxis(), y: {min: 0, max: 100}}})});
  const comp = (id, key, label, color, unit, ref, smooth) => {
    const s2 = windowOf([DATA[key]]);
    const ds = [];
    if (smooth) ds.push({label: '13주 평균', data: s2(smooth), borderColor: color, borderWidth: 2.4, pointRadius: 0, yAxisID: 'y', order: 0});
    ds.push({label: label, data: s2(DATA[key]), borderColor: smooth ? color + '66' : color, borderWidth: smooth ? 1 : 1.8, pointRadius: 0, yAxisID: 'y', order: 1});
    if (ref !== undefined) ds.push({label: '기준 ' + ref + unit, data: s2(DATA[key]).map(() => ref), borderColor: '#9aa0a6', borderWidth: 1, borderDash: [4, 4], pointRadius: 0, yAxisID: 'y', order: 2});
    ds.push(kospiDataset(s2));
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

function renderCards() {
  const iP = lastIdx(M.ph), p = M.ph[iP];
  const iS = lastIdx(M.Ls), s = M.Ls[iS], sk = M.Ls[iS - CFG.k];
  const strip = M.ph.slice(Math.max(0, iP - 25), iP + 1).map((q, k, a) => '<span title="' + DATA.dates[iP - (a.length - 1 - k)] + ' ' + (q ? PH[q] : '-') + '" style="background:' + (q ? PHC[q] : '#2a2e37') + '"></span>').join('');
  const cd = (lbl, val, sub) => '<div class="card"><div class="lbl">' + lbl + '</div><div class="val">' + val + '</div><div class="sub">' + sub + '</div></div>';
  const iC = lastIdx(DATA.C), iD = lastIdx(DATA.D), iX = lastIdx(DATA.X);
  $('cards').innerHTML =
    '<div class="card main"><div class="lbl">현재 국면 (' + DATA.dates[iP] + ', 지금 설정)</div><span class="badge" style="background:' + PHC[p] + '">' + PH[p] + '</span>' +
    '<div class="sub">' + PHDESC[p] + '</div><div class="sub">최근 26주 국면 흐름(왼쪽이 과거)</div><div class="strip">' + strip + '</div></div>' +
    cd('국면 점수 (' + CFG.sm + '주 평균)', fmt(s) + '<span style="font-size:13px;color:#9aa0a6"> / 100</span>', '낮음 &lt; ' + fmt(M.lo[iS]) + ' · 높음 &gt; ' + fmt(M.hi[iS]) + '<br>' + CFG.k + '주 전 ' + fmt(sk) + ' → <span class="' + (s - sk >= 0 ? 'pos' : 'neg') + '">' + sgn(s - sk) + '</span>') +
    cd('공격-방어 상대강도(1달)', '<span class="' + (DATA.D[iD] >= 0 ? 'pos' : 'neg') + '">' + sgn(DATA.D[iD]) + '%p</span>', '점수 ' + fmt(DATA.sD[iD], 0) + '점 · 배점 ' + CFG.wD) +
    cd('밸류 하단 종목 비율', fmt(DATA.C[iC]) + '%', '점수 ' + fmt(DATA.sC[iC], 0) + '점 · 배점 ' + CFG.wC + ' (비율 낮을수록 점수 높음) · ' + DATA.cN.toLocaleString() + '종목') +
    cd('120일선 위 종목 비율', fmt(DATA.X[iX]) + '%', '점수 ' + fmt(DATA.sX[iX], 0) + '점 · 배점 ' + CFG.wX);
  $('nowPhase').textContent = '현재 ' + PH[p];
  $('nowScore').textContent = '현재 ' + fmt(s) + ' (' + CFG.k + '주 전 ' + fmt(sk) + ')';
  $('descScore').textContent = '배점 밸류 하단 ' + CFG.wC + ' · 공격-방어 ' + CFG.wD + ' · 120일선 위 ' + CFG.wX + ', ' + CFG.sm + '주 평균. 점선은 그 시점까지 점수 분포의 하위 1/3(낮음 기준)·상위 1/3(높음 기준)입니다.';
  const iSm = lastIdx(DATA.smIdx);
  $('nowSm').textContent = '현재 국면 ' + PH[p] + ' · 중소형 지수 ' + fmt(DATA.smIdx[iSm], 0);
  $('nowD').textContent = '현재 ' + sgn(DATA.D[iD]) + '%p · ' + fmt(DATA.sD[iD], 0) + '점';
  $('nowC').textContent = '현재 ' + fmt(DATA.C[iC]) + '% · ' + fmt(DATA.sC[iC], 0) + '점';
  $('nowX').textContent = '현재 ' + fmt(DATA.X[iX]) + '% · ' + fmt(DATA.sX[iX], 0) + '점';
}

const thrVal = v => (v === 'exp' || v === 'fix') ? v : +v;
function readCfg() { return {wC: +$('wC').value, wD: +$('wD').value, wX: +$('wX').value, sm: +$('sm').value, k: +$('kk').value, thr: thrVal($('thr').value)}; }
function setCfg(c) { $('wC').value = c.wC; $('wD').value = c.wD; $('wX').value = c.wX; $('sm').value = c.sm; $('kk').value = c.k; $('thr').value = String(c.thr); }
const PRESETS = {base: {wC: DATA.weights.C, wD: DATA.weights.D, wX: DATA.weights.X, sm: DATA.smooth, k: DATA.dirWeeks, thr: 'exp'}, five: {wC: 3, wD: 0, wX: 0, sm: 8, k: 4, thr: 'exp'}};
const THRS = [['exp', '누적(2018~ 전체)'], [104, '최근 2년'], [156, '최근 3년'], [260, '최근 5년'], ['fix', '고정 33/67점']];
function renderThrTable() {
  const H = HALVES.slice(0, 2);
  let h = '<tr><th>기준 방식</th>' + ['인접 정답률', '상승·하락 구분', '⑤ 주 중 실제 중소형 하락', '⑤ 뒤 13주 중소형 하락 확률', '판정 변경'].map(t => '<th colspan="2">' + t + '</th>').join('') + '<th>지금 국면</th><th>지금 낮음/높음 기준</th></tr>';
  h += '<tr><th></th>' + [0, 1, 2, 3, 4].map(() => H.map(x => '<th>' + x[0].split(' (')[0] + '</th>').join('')).join('') + '<th></th><th></th></tr>';
  THRS.forEach(([v, lbl]) => {
    const m = computeModel(Object.assign({}, CFG, {thr: v}));
    const S = H.map(([, f]) => phaseStats(m.ph, f));
    const chg = H.map(([, f]) => { let c = 0, n = 0; for (let i = 1; i < N; i++) if (f(i) && nn(m.ph[i]) && nn(m.ph[i - 1])) { n++; if (m.ph[i] !== m.ph[i - 1]) c++; } return n ? c / n * 100 : NaN; });
    const iP = lastIdx(m.ph), cur = v === CFG.thr;
    h += '<tr data-thr="' + v + '" style="cursor:pointer' + (cur ? ';background:#1c2a3a' : '') + '"><td>' + (cur ? '▶ ' : '') + lbl + '</td>' +
      S.map(s => cellVs(s.adj, 50, '%', true)).join('') + S.map(s => cellVs(s.dir, 50, '%', true)).join('') +
      S.map(s => cellVs(s.down5, s.downB, '%', true)).join('') + S.map(s => cellVs(s.neg5, s.negB, '%', true)).join('') +
      chg.map(x => '<td>' + fmt(x, 0) + '%</td>').join('') +
      '<td><span style="color:' + PHC[m.ph[iP]] + '">' + PH[m.ph[iP]] + '</span></td><td>' + fmt(m.lo[iP]) + ' / ' + fmt(m.hi[iP]) + '</td></tr>';
  });
  $('tblThr').innerHTML = h;
}
$('tblThr').addEventListener('click', e => { const r = e.target.closest('tr[data-thr]'); if (!r) return; $('thr').value = r.dataset.thr; recalc(); });
function recalc() {
  CFG = readCfg();
  if (CFG.wC + CFG.wD + CFG.wX === 0) { $('cfgNote').textContent = '배점이 모두 0입니다. 하나 이상 0보다 크게 두세요.'; return; }
  const same = k => Object.keys(PRESETS[k]).every(x => PRESETS[k][x] === CFG[x]);
  document.querySelectorAll('[data-preset]').forEach(b => b.classList.toggle('active', same(b.dataset.preset)));
  $('cfgNote').innerHTML = same('base') ? '기본 설정: 2018~2026 전체 백테스트로 정한 배점이며 반반 검증에서 두 절반 성적이 비슷했습니다.'
    : same('five') ? '<b>⑤ 중시</b>: 앞 절반에서 ⑤ 성적으로 고른 설정이며, 뒤 절반(표본 밖)에서도 ⑤ 성적이 가장 좋았습니다. 대신 다른 국면 구분은 기본 설정보다 거칩니다.'
    : '직접 고른 설정입니다. 아래 반반 검증표에서 <b>앞·뒤 절반 성적이 비슷한지</b> 확인하세요(한쪽만 좋으면 과적합).';
  M = computeModel(CFG);
  W = computeWarn();
  renderCards(); renderHalfTable(M); renderThrTable(); renderPhaseTable(M); renderWarn(W); buildCharts();
}
['wC', 'wD', 'wX'].forEach(id => { $(id).innerHTML = [0, 1, 2, 3].map(v => '<option>' + v + '</option>').join(''); });
setCfg(PRESETS.base);
document.querySelectorAll('[data-preset]').forEach(b => b.addEventListener('click', () => { setCfg(PRESETS[b.dataset.preset]); recalc(); }));
['wC', 'wD', 'wX', 'sm', 'kk', 'thr'].forEach(id => $(id).addEventListener('change', recalc));
$('hW').value = '52'; $('near').value = '3'; $('aN').value = '20'; $('aT').value = '90';
['hW', 'near', 'aN', 'aT', 'xC'].forEach(id => $(id).addEventListener('change', () => { W = computeWarn(); renderWarn(W); buildCharts(); }));
$('phLegend').innerHTML = Object.keys(PH).map(k => '<span><i style="background:' + PHC[k] + '"></i>' + PH[k] + '</span>').join('');
$('descD').innerHTML = '공격 섹터(코스피 대비 민감도 상위 ' + DATA.aggr.length + '개: ' + DATA.aggr.join(', ') + ') 동일가중 1달(20거래일) 수익률에서 ' +
  '방어 섹터(' + DATA.dfn.join(', ') + ') 1달 수익률을 뺀 값입니다. 높을수록 위험 선호(상승 국면 쪽)입니다. 섹터는 내 섹터 정리 기준입니다.';
$('descC').innerHTML = 'OP밴드 배수(시가총액 ÷ 12개월 선행 영업이익 추정치)가 <b>자기 최근 3년 하위 10% 아래</b>인 종목 비율입니다(그 시점까지 데이터만 사용, 2년 이상 이력이 있는 종목). ' +
  '많을수록 시장이 밸류 바닥권이라는 뜻이라 점수에는 <b>거꾸로</b>(비율이 낮을수록 높은 점수) 반영됩니다.';
$('rangeBtns').innerHTML = RANGES.map(([l, w]) => '<button class="btn range-btn' + (w === RANGE ? ' active' : '') + '" data-w="' + w + '">' + l + '</button>').join(' ');
$('dFrom').min = $('dTo').min = DATA.dates[0]; $('dFrom').max = $('dTo').max = DATA.dates[N - 1];
$('rangeBtns').addEventListener('click', e => {
  const b = e.target.closest('button[data-w]');
  if (!b) return;
  RANGE = parseInt(b.dataset.w, 10); FROM = TO = null; $('dFrom').value = $('dTo').value = '';
  document.querySelectorAll('.range-btn').forEach(x => x.classList.toggle('active', x === b));
  buildCharts();
});
['dFrom', 'dTo'].forEach(id => $(id).addEventListener('change', () => {
  FROM = $('dFrom').value || null; TO = $('dTo').value || null;
  document.querySelectorAll('.range-btn').forEach(x => x.classList.toggle('active', !FROM && !TO && +x.dataset.w === RANGE));
  buildCharts();
}));
recalc();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
