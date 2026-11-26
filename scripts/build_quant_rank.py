"""
퀀트 종합 순위 페이지(docs/quant_rank.html) - 2026-11-19 사용자 요청, 같은 날 11년 백테스트 결과로 개편

국면(코스피 200일선, 1주 지연, ±2%p 밴드):
  - 200일선보다 2% 넘게 위로 가면 '상승형', 2% 넘게 아래로 가면 '하락형'. 그 사이에선 직전 국면 유지(헛전환 106→26회).
  - 1주 지연 = 판단에 5거래일 전 코스피/200일선 값을 쓴다(백테스트와 같은 규칙).
가중치(백테스트 2016~20에서 국면별로 고르고 2021~26에서 검증):
  - 상승형: 모멘텀 0.5, 추정치 상향 1.5, 이익 성장 가속 1, 3개월 추정치 상향 0.5, 외국인 보유율 0.5
  - 하락형: 추정치 상향 3, 밸류 1, 이익 성장(YoY) 1, 이익 성장 가속 1,
            수급 3개 각 0.5(2026-11-26 추가: 기타법인 60일/시총, 개인 20일/거래대금, 기관 20일 역/거래대금 - naver_flow.pkl)
항목(각각 그날 유니버스 안 백분위, 모멘텀·추정치·밸류는 묶음 평균):
  - 모멘텀: 1개월 하락(하락률 클수록 좋음), 6·12개월 상승률, 6M-1M·12M-1M
  - 추정치 상향: FY1·FY2 영업이익 추정치 1개월 변화(1월 기준연도 전환 보정)
  - 밸류: 시총/FY1·FY2 영업이익(낮을수록, FY1 적자면 FY2만)
  - 이익 성장: 실적 영업이익(TTM) 1년 변화율, 가속 = 그 값의 3개월 변화
  - 3개월 추정치 상향: FY1 추정치 3개월 변화
  - 외국인 보유율: 네이버 일별(fetch_foreign_hold.py)
  - 이익 성장·3개월 상향·보유율이 없으면 중간값(50)으로 둔다(백테스트와 같음)
유니버스: FY2 영업이익 추정치 흑자(FY1 적자·FY2 흑자 = 내년 흑전도 포함), 우선주 제외. 시총 하한 기본 1,000억(페이지에서 변경).
수급 항목은 11년 검증에서 비용 차감 후 손해라 뺐다.
데이터: 기업 밴드 찾기 2(+갱신용) 일별 시총·영업이익 추정치·실적 TTM, data/kospi_index_cache.csv, foreign_hold.pkl.
주의: 수익률은 시가총액 변화로 계산하므로 유·무상증자 등 주식수 변화가 섞인다.
"""
import json
import os
import pickle
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from build_op_band import load_band_workbook  # noqa: E402
from build_relative_strength import assign_sectors  # noqa: E402

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
OUT_PATH = os.path.join(BASE_DIR, "docs", "quant_rank.html")
SECTOR_MAP_PATH = os.path.join(BASE_DIR, "data", "sector_map.csv")
KOSPI_PATH = os.path.join(BASE_DIR, "data", "kospi_index_cache.csv")
FH_PATH = os.path.join(BASE_DIR, "data", "screening", "cache", "foreign_hold.pkl")
BRIEF_PATH = os.path.join(BASE_DIR, "data", "quant_rank_brief.json")   # 아침 브리핑 3쪽용(현재 국면 상위 40)
HIST_PATH = os.path.join(BASE_DIR, "data", "quant_rank_hist.json")     # 날짜별 상위 40 기록(1주 전 순위 비교용)
D1M, D3M, D6M, D12M = 21, 63, 126, 252
BAND_TH, LAG = 0.02, 5
# 페이지 JS와 같은 규칙(바꿀 때 둘 다 바꿀 것)
GRP = {"M": [(0, -1), (1, 1), (2, 1), (3, 1), (4, 1)], "R": [(5, 1), (6, 1)], "V": [(7, -1), (8, -1)],
       "Y": [(9, 1)], "A": [(10, 1)], "R3": [(11, 1)], "H": [(13, 1)], "O": [(14, 1)], "P": [(15, 1)], "IR": [(16, 1)]}
MID = {"Y", "A", "R3", "H", "O", "P", "IR"}
# 2026-11-26: 하락형에 수급 3개(10년 수급 연구 생존 신호) 각 0.5 추가 - 11년 백테스트 연 27.2→31.0%, 2016~20 27.9→33.6,
# 2021~26 26.6→29.0, 하락 국면 11.2→20.6, 2022 -12→-5%. 상승형에 넣으면 오히려 나빠져서(27.2→24.3) 하락형만.
WS = {"up": {"M": 0.5, "R": 1.5, "V": 0, "Y": 0, "A": 1, "R3": 0.5, "H": 0.5, "O": 0, "P": 0, "IR": 0},
      "down": {"M": 0, "R": 3, "V": 1, "Y": 1, "A": 1, "R3": 0, "H": 0, "O": 0.5, "P": 0.5, "IR": 0.5}}
GN = {"M": "모멘텀", "R": "추정치 상향", "V": "밸류", "Y": "이익 성장", "A": "성장 가속", "R3": "3개월 상향", "H": "외국인 보유율",
      "O": "기타법인 60일", "P": "개인 20일", "IR": "기관 20일 역"}
FLOW_PATH = os.path.join(BASE_DIR, "data", "screening", "cache", "naver_flow.pkl")


def score_rows(rows, state, min_cap=1000):
    """페이지 calc()와 같은 점수. 반환: 총점 내림차순 DataFrame(c, n, s, cap, v, 묶음 점수, tot)."""
    rs = [r for r in rows if r["cap"] >= min_cap]
    df = pd.DataFrame(rs)
    for g, items in GRP.items():
        ps = []
        for k, d in items:
            x = pd.Series([r["v"][k] for r in rs], dtype=float) * d
            ok = x.notna()
            p = pd.Series(np.nan, index=x.index)
            m = int(ok.sum())
            if m:
                p[ok] = (x[ok].rank(method="first") - 1) / (m - 1) * 100 if m > 1 else 50.0
            ps.append(p)
        P = pd.concat(ps, axis=1).mean(axis=1, skipna=True)
        df[g] = P.fillna(50.0) if g in MID else P
    W = WS[state]
    sw = sum(W.values())
    df["tot"] = sum(W[g] * df[g] for g in GRP if W[g] > 0) / sw
    return df.dropna(subset=["tot"]).sort_values("tot", ascending=False).reset_index(drop=True)


def save_brief(out, min_cap=1000, keep=40):
    state = out["reg"]["state"]
    df = score_rows(out["rows"], state, min_cap)
    top = df.head(keep)
    hist = {}
    if os.path.exists(HIST_PATH):
        try:
            hist = json.load(open(HIST_PATH, encoding="utf-8"))
        except Exception:
            hist = {}
    hist[out["asof"]] = {"state": state, "top": [[r.c, r.n] for r in top.itertuples()]}
    hist = dict(sorted(hist.items())[-60:])
    json.dump(hist, open(HIST_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    # 1주 전 = 기준일 7일 전 이전의 마지막 기록
    cut = (pd.Timestamp(out["asof"]) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    prev_key = max([d for d in hist if d <= cut], default=None)
    prev = hist[prev_key]["top"] if prev_key else None
    prank = {c: i + 1 for i, (c, _) in enumerate(prev)} if prev else {}
    now40 = set(top.c)
    dropped = [[c, n, i + 1] for i, (c, n) in enumerate(prev[:20]) if c not in now40] if prev else []
    rows = []
    for i, r in enumerate(top.itertuples(), 1):
        rows.append({"rk": i, "c": r.c, "n": r.n, "s": r.s, "cap": r.cap, "tot": round(r.tot, 1),
                     "g": {g: (None if pd.isna(getattr(r, g)) else round(float(getattr(r, g)))) for g in GRP},
                     "v": r.v, "prev": prank.get(r.c) if prev else None})
    brief = {"asof": out["asof"], "fh_asof": out["fh_asof"], "reg": out["reg"], "min_cap": min_cap, "n_uni": len(df),
             "weights": {GN[g]: w for g, w in WS[state].items() if w > 0}, "prev_date": prev_key,
             "rows": rows, "dropped": dropped}
    json.dump(brief, open(BRIEF_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    return df


def load_band_arrays():
    """반환: 시총(원), FY1·FY2 영업이익 추정치(원), 실적 TTM 영업이익(원), 코드→이름."""
    bk = load_band_workbook(verbose=False)
    sh = bk.store["sheets"]["기업밴드"] if hasattr(bk, "store") else None
    if sh is None:
        from build_op_band import BAND_STORE_PATH
        sh = pickle.load(open(BAND_STORE_PATH, "rb"))["sheets"]["기업밴드"]
    head, mat = sh["head"], sh["mat"]
    dates = pd.DatetimeIndex(sh["dates"])
    cols = {"S102100": {}, "NFY1": {}, "NFY2": {}, "TTM": {}}
    names = {}
    for k in range(len(head[7]) - 1):
        code, item, base = head[7][k + 1], head[9][k + 1], head[11][k + 1]
        if not code:
            continue
        c = str(code)[-6:]
        if head[8][k + 1]:
            names[c] = str(head[8][k + 1])
        if item == "S102100":
            cols["S102100"][c] = k
        elif item == "E121500.M" and base in ("NFY1", "NFY2"):
            cols[base][c] = k
        elif item == "M121505.M":
            cols["TTM"][c] = k

    def frame(m):
        return pd.DataFrame({c: mat[:, k] for c, k in m.items()}, index=dates)
    return (frame(cols["S102100"]), frame(cols["NFY1"]) * 1000.0, frame(cols["NFY2"]) * 1000.0,
            frame(cols["TTM"]) * 1000.0, names)


def regime():
    """코스피 200일선 국면: 5거래일 전 이격으로 ±2%p 밴드 판단. 반환 dict."""
    k = pd.read_csv(KOSPI_PATH, dtype={"date": str})
    k["date"] = pd.to_datetime(k["date"].str[:8])
    k = k.drop_duplicates("date").set_index("date")["close"].astype(float).sort_index()
    ma = k.rolling(200).mean()
    gap = (k / ma - 1)
    lag = gap.shift(LAG)
    state, since, hist = None, None, []
    for d, g in lag.dropna().items():
        new = state
        if state is None:
            new = "up" if g > 0 else "down"
        elif g > BAND_TH:
            new = "up"
        elif g < -BAND_TH:
            new = "down"
        if new != state:
            if state is not None:
                hist.append((d.strftime("%Y-%m-%d"), new))
            state, since = new, d
    return {"state": state, "since": since.strftime("%Y-%m-%d"), "asof": k.index[-1].strftime("%Y-%m-%d"),
            "close": round(float(k.iloc[-1]), 2), "ma200": round(float(ma.iloc[-1]), 2),
            "gap": round(float(gap.iloc[-1]) * 100, 1), "gap_lag": round(float(lag.iloc[-1]) * 100, 1),
            "recent": hist[-6:], "n_switch_5y": sum(1 for d, _ in hist if d >= (k.index[-1] - pd.DateOffset(years=5)).strftime("%Y-%m-%d"))}


def main():
    cap, op1, op2, ttm, bnames = load_band_arrays()
    cap = cap.sort_index()
    ok_rows = cap.notna().sum(axis=1) >= 0.5 * cap.notna().sum(axis=1).max()
    idx = cap.index[ok_rows]
    cap, op1, op2, ttm = cap.loc[idx], op1.reindex(idx), op2.reindex(idx), ttm.reindex(idx)
    # 추정치는 5일까지만 채운다(무기한 채우면 커버리지가 끊긴 종목의 몇 년 전 추정치가 남아 유니버스가 2.7배로 부풀었다 - 2026-11-19 발견)
    capf, op1f, op2f, ttmf = cap.ffill(limit=3), op1.ffill(limit=5), op2.ffill(limit=5), ttm.ffill(limit=5)
    T = len(capf) - 1
    fhF = pd.read_pickle(FH_PATH).sort_index() if os.path.exists(FH_PATH) else None
    flow = pickle.load(open(FLOW_PATH, "rb")) if os.path.exists(FLOW_PATH) else None
    fhF = (fhF, flow)
    sm = pd.read_csv(SECTOR_MAP_PATH, dtype=str)
    meta = pd.DataFrame({"name": sm["name"].values}, index=sm["code"].str.zfill(6).values)
    meta = meta[~meta.index.duplicated()]
    naver = dict(zip(sm["code"].str.zfill(6), sm["sector"]))
    sectors, _ = assign_sectors(meta)
    sec_of = {c: (s if src == "U" else None) for c, (s, src) in sectors.items()}
    out = make_rows(T, capf, op1f, op2f, ttmf, fhF, meta, naver, sec_of, bnames)
    # 2026-11-26: 전일·1주 전 순위(그날 데이터로 지금 가중치를 다시 계산 - 가중치를 바꿔도 비교가 맞게)
    prev = {}
    for key, back in (("d1", 1), ("d5", 5)):
        po = make_rows(T - back, capf, op1f, op2f, ttmf, fhF, meta, naver, sec_of, bnames)
        prev[key] = {"date": po["asof"],
                     "rank": {st: {r.c: i + 1 for i, r in enumerate(score_rows(po["rows"], st).itertuples())} for st in WS}}
    out["prev"] = prev
    reg = regime()
    out["reg"] = reg
    asof, fh_asof, rows = pd.Timestamp(out["asof"]), out["fh_asof"], out["rows"]
    sdf = save_brief(out)
    print("브리핑용 상위 5:", ", ".join(sdf["n"].head(5)))
    html = (TEMPLATE.replace("__UPDATED__", datetime.now().strftime("%Y-%m-%d %H:%M")).replace("__ASOF__", out["asof"])
            .replace("__FHASOF__", fh_asof).replace("__KASOF__", reg["asof"]).replace("__FLASOF__", out["fl_asof"])
            .replace("__DATA__", json.dumps(out, ensure_ascii=False, separators=(",", ":"))))
    with open(OUT_PATH, "w", encoding="utf-8") as fh_out:
        fh_out.write(html)
    print(f"종목 {len(rows)}개(시총 하한 전), 기준일 {out['asof']}, 전일 {prev['d1']['date']}, 1주 전 {prev['d5']['date']}, 보유율 {fh_asof}, 국면 {reg['state']}(이격 {reg['gap']}%, 1주 전 {reg['gap_lag']}%, {reg['since']}부터) | 저장: {OUT_PATH} ({len(html) // 1024}KB)")


def make_rows(t, capf, op1f, op2f, ttmf, fhF, meta, naver, sec_of, bnames):
    """기준 행 t의 항목 값. 반환 {"asof", "fh_asof", "rows"}."""
    asof = capf.index[t]
    c0 = capf.iloc[t]
    ret = lambda a, b: capf.iloc[a] / capf.iloc[b] - 1  # noqa: E731
    m1 = ret(t, t - D1M)
    m6, m12 = ret(t, t - D6M), ret(t, t - D12M)
    m6x, m12x = capf.iloc[t - D1M] / capf.iloc[t - D6M] - 1, capf.iloc[t - D1M] / capf.iloc[t - D12M] - 1
    # 추정치 기준연도는 매년 1월 첫 거래일에 한 해씩 넘어간다. 비교 구간 안에 넘어간 종목은
    # FY1 변화 = 지금 FY1 / 그때 FY2, FY2 변화는 비교 대상이 없어 비운다(추정치 묶음은 FY1만으로).
    sw = ((op1f / op2f.shift(1) - 1).abs() < 0.02) & ((op1f / op1f.shift(1) - 1).abs() > 0.02)
    sw1 = sw.iloc[t - D1M + 1:t + 1].any()
    sw3 = sw.iloc[t - D3M + 1:t + 1].any()
    a1, a2 = op1f.iloc[t], op2f.iloc[t]
    b1, b2 = op1f.iloc[t - D1M], op2f.iloc[t - D1M]
    e1, e2 = op1f.iloc[t - D3M], op2f.iloc[t - D3M]
    cols = op1f.columns
    r1 = pd.Series(np.where(sw1, a1 / b2 - 1, a1 / b1 - 1), index=cols).where(np.where(sw1, b2 > 0, b1 > 0))
    r2 = (a2 / b2 - 1).where(~sw1 & (b2 > 0))
    r3m = pd.Series(np.where(sw3, a1 / e2 - 1, a1 / e1 - 1), index=cols).where(np.where(sw3, e2 > 0, e1 > 0))
    per1, per2 = (c0 / a1).where(a1 > 0), (c0 / a2).where(a2 > 0)

    def yoy_at(i):
        now, base = ttmf.iloc[i], ttmf.iloc[i - D12M]
        return ((now - base) / base.abs()).where(base.abs() > 0).clip(-5, 5)
    yoy = yoy_at(t)
    yacc = yoy - yoy_at(t - D3M)
    fy1neg = (a1 <= 0)
    # 외국인 보유율(최근 5거래일 안 마지막 값)
    fhF, flow = fhF
    # 수급(하락형): 기타법인 60일 순매수/시총, 개인 20일 순매수/거래대금, -기관 20일 순매수/거래대금 (백테스트 bt_ext3와 같은 정의)
    oth60 = ind20 = inst20r = pd.Series(dtype=float)
    fl_asof = "-"
    if flow is not None and len(flow["F"]):
        Fq, Iq, Pq, TV = (flow[k].loc[:asof] for k in ("F", "I", "P", "tv"))
        if len(Fq) and (asof - Fq.index[-1]).days <= 7:
            fl_asof = Fq.index[-1].strftime("%Y-%m-%d")
            Oq = -(Fq.fillna(0) + Iq.fillna(0) + Pq.fillna(0)).where(Fq.notna() | Iq.notna() | Pq.notna())
            tv20 = TV.iloc[-20:].sum(min_count=20)
            oth60 = (Oq.iloc[-60:].sum(min_count=40) / c0.reindex(Oq.columns))
            ind20 = (Pq.iloc[-20:].sum(min_count=15) / tv20).where(tv20 > 0)
            inst20r = (-Iq.iloc[-20:].sum(min_count=15) / tv20).where(tv20 > 0)
    fh, fh_asof = pd.Series(dtype=float), "-"
    if fhF is not None:
        F = fhF.loc[:asof]
        if len(F):
            fh = F.ffill(limit=5).iloc[-1]
            fh_asof = F.index[-1].strftime("%Y-%m-%d")
    rows = []
    for c in capf.columns:
        name = meta["name"].get(c) or bnames.get(c)
        if not name or name.endswith("우") or name.endswith("우B") or name.endswith("우(전환)"):
            continue
        o1, o2 = a1.get(c, np.nan), a2.get(c, np.nan)
        if not (np.isfinite(o1) and np.isfinite(o2) and o2 > 0):
            continue
        cc = c0.get(c, np.nan)
        need = [m1.get(c), m6.get(c), m12.get(c), m6x.get(c), m12x.get(c), per2.get(c), cc]
        if not all(v is not None and np.isfinite(v) for v in need):
            continue
        # 0 1M 1 6M 2 12M 3 6M-1M 4 12M-1M 5 FY1 1M 6 FY2 1M 7 PER1 8 PER2 9 YoY 10 YoY가속 11 FY1 3M 12 내년흑전 13 외인보유율
        vals = [m1.get(c), m6.get(c), m12.get(c), m6x.get(c), m12x.get(c), r1.get(c), r2.get(c), per1.get(c), per2.get(c),
                yoy.get(c), yacc.get(c), r3m.get(c), float(fy1neg.get(c, False)), fh.get(c, np.nan),
                oth60.get(c, np.nan), ind20.get(c, np.nan), inst20r.get(c, np.nan)]
        scale = [100] * 7 + [1, 1] + [100, 100, 100, 1, 1] + [100, 100, 100]

        def rd(v, s):
            return None if (v is None or not np.isfinite(v)) else round(float(v) * s, 2)
        rows.append({"c": c, "n": name, "s": sec_of.get(c) or naver.get(c) or "-", "cap": int(round(cc / 1e8)),
                     "v": [rd(v, s) for v, s in zip(vals, scale)]})
    return {"asof": asof.strftime("%Y-%m-%d"), "fh_asof": fh_asof, "fl_asof": fl_asof, "rows": rows}


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
  .reg { display:flex; gap:18px; flex-wrap:wrap; align-items:center; }
  .reg .big { font-size:20px; font-weight:bold; padding:4px 12px; border-radius:8px; }
  .reg .up { background:#3b1d1d; color:#ff8787; } .reg .down { background:#1b2740; color:#74a7ff; }
  .reg .kv { font-size:13px; color:#c9ccd1; } .reg .kv b { color:#e6e6e6; }
  .wt { font-size:12px; color:#9aa0a6; margin-top:8px; line-height:1.7; } .wt b { color:#e6e6e6; }
  .note { font-size:12px; color:#9aa0a6; line-height:1.75; background:#1a1d24; border-radius:10px; padding:12px 16px; margin-bottom:14px; } .note b { color:#ffa94d; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  th, td { padding:5px 7px; border-bottom:1px solid #23262e; text-align:right; white-space:nowrap; }
  th:nth-child(4), td:nth-child(4), th:nth-child(5), td:nth-child(5) { text-align:left; }
  th { color:#9aa0a6; font-weight:normal; cursor:pointer; position:sticky; top:0; background:#12151b; } th.sorted { color:#4dabf7; }
  tr.keep td { background:#151922; } tr.cut td { border-top:2px solid #4dabf7; }
  .pos { color:#ff8787; } .neg { color:#74a7ff; } .sub { color:#7c828b; }
  .tag { font-size:10px; color:#ffa94d; border:1px solid #6b4a1f; border-radius:4px; padding:0 4px; margin-left:4px; }
  .tblwrap { overflow:auto; max-height:760px; }
  .bar { display:inline-block; height:6px; background:#4dabf7; border-radius:3px; vertical-align:middle; margin-right:5px; }
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>퀀트 종합 순위 <span style="font-size:13px;color:#9aa0a6;font-weight:normal">코스피 200일선 국면별 가중치</span></h1>
  <div class="updated">최종 갱신: __UPDATED__ &middot; 시총·추정치 기준일 __ASOF__ &middot; 외국인 보유율 __FHASOF__ &middot; 코스피 __KASOF__ &middot; 기업 밴드 찾기 2 + 네이버</div>
  <div class="box">
    <div class="reg" id="reg"></div>
    <div class="wt" id="wt"></div>
  </div>
  <div class="note">
    <b>국면</b> 코스피가 200일선보다 <b>2% 넘게 위</b>면 상승형, <b>2% 넘게 아래</b>면 하락형, 그 사이는 직전 국면 유지. 판단은 1주(5거래일) 전 값 기준.<br>
    <b>운용</b> 주 1회 상위 20종목 동일 비중. <b>들고 있는 종목은 40위 안이면 유지</b>하고 빠진 자리만 상위에서 채운다(표에서 21~40위 음영).<br>
    <b>11년 백테스트</b>(2016~2026.11, 시총 1,000억 이상, 주 1회 다음 날 종가, 비용 0.25%): 이 규칙 연 31%(2016~20 34%, 2021~26 29%), 최대 낙폭 -43%, 코스피 연 10%.
    하락 국면에서 하락형 연 21%(수급 넣기 전 11%), 상승 국면에서 상승형 연 39%. 급락 초기엔 코스피보다 더 빠질 수 있음.<br>
    <b>수급(하락형만, 2026-11-26 추가)</b> 기타법인 60일 순매수/시총 &middot; 개인 20일 순매수/거래대금 &middot; 기관 20일 순매도/거래대금(기관이 판 종목이 고점수) 각 0.5.
    10년 수급 연구에서 살아남은 신호. 외국인·기관 순매수를 그대로 넣으면 오히려 나빴고, 상승형에 넣어도 나빠져서 하락형만. 기타법인에는 자사주 매입이 섞임. 네이버 일별 수급(__FLASOF__).<br>
    <b>항목</b> 모멘텀(1개월은 하락률 클수록 고점수, 6·12개월, 최근 1개월 제외 6·12개월) &middot; 추정치 상향(FY1·FY2 영업이익 1개월) &middot; 밸류(시총/영업이익 FY1·FY2, 낮을수록) &middot;
    이익 성장(실적 TTM 영업이익 1년 변화) &middot; 성장 가속(그 값의 3개월 변화) &middot; 3개월 추정치 상향(FY1) &middot; 외국인 보유율. 각 항목은 유니버스 안 백분위(0~100).
    유니버스 = FY2 영업이익 추정치 흑자(올해 적자·내년 흑자 포함, <span class="tag">흑전</span> 표시). <b>수익률은 시총 변화</b>라 증자·분할 때 왜곡될 수 있음.
  </div>
  <div class="ctl">
    가중치 <select id="mode"><option value="auto">현재 국면</option><option value="up">상승형으로 보기</option><option value="down">하락형으로 보기</option></select>
    시총 하한(억) <input type="number" id="minCap" value="1000" step="100">
    섹터 <select id="sec"><option value="">전체</option></select>
    검색 <input type="text" id="q" placeholder="종목명">
    표시 <select id="topn"><option selected>20</option><option>40</option><option>100</option><option>9999</option></select>
    <span id="cnt" class="sub"></span>
  </div>
  <div class="box" id="chg"></div>
  <div class="box"><div class="tblwrap"><table id="tbl"></table></div></div>
<script>
const D = __DATA__;
// v 인덱스: 0 1M 1 6M 2 12M 3 6M-1M 4 12M-1M 5 FY1 1M 6 FY2 1M 7 PER1 8 PER2 9 YoY 10 YoY가속 11 FY1 3M 12 내년흑전(0/1) 13 외인보유율
//           14 기타법인 60일/시총% 15 개인 20일/거래대금% 16 -기관 20일/거래대금%
const GRP = {M:[[0,-1],[1,1],[2,1],[3,1],[4,1]], R:[[5,1],[6,1]], V:[[7,-1],[8,-1]], Y:[[9,1]], A:[[10,1]], R3:[[11,1]], H:[[13,1]],
             O:[[14,1]], P:[[15,1]], IR:[[16,1]]};
const MID = {Y:50, A:50, R3:50, H:50, O:50, P:50, IR:50};    // 값 없으면 중간값(백테스트와 같음)
const WS = { up:   {M:0.5, R:1.5, V:0, Y:0, A:1, R3:0.5, H:0.5, O:0,   P:0,   IR:0},
             down: {M:0,   R:3,   V:1, Y:1, A:1, R3:0,   H:0,   O:0.5, P:0.5, IR:0.5} };
const GN = {M:'모멘텀', R:'추정치 상향', V:'밸류', Y:'이익 성장', A:'성장 가속', R3:'3개월 상향', H:'외국인 보유율',
            O:'기타법인 60일', P:'개인 20일', IR:'기관 20일 역'};
const RG = D.reg;
let sortKey = 'rk', sortDir = 1;
const secs = [...new Set(D.rows.map(r=>r.s))].sort();
document.getElementById('sec').innerHTML += secs.map(s=>`<option>${s}</option>`).join('');
const f = (v,d=1)=> v==null? '-' : v.toFixed(d);
const cl = v => v>0? 'pos' : v<0? 'neg' : '';
const regName = s => s==='up'? '상승형' : '하락형';
document.getElementById('reg').innerHTML =
  `<span class="big ${RG.state}">${regName(RG.state)}</span>
   <span class="kv">코스피 <b>${RG.close.toLocaleString()}</b> / 200일선 <b>${RG.ma200.toLocaleString()}</b> &rarr; 이격 <b class="${cl(RG.gap)}">${f(RG.gap)}%</b></span>
   <span class="kv">판단 기준(1주 전) <b class="${cl(RG.gap_lag)}">${f(RG.gap_lag)}%</b></span>
   <span class="kv"><b>${RG.since}</b>부터 ${regName(RG.state)} &middot; ${RG.state==='down'? '200일선 +2% 위로 가면(1주 지연) 상승형' : '200일선 -2% 아래로 가면(1주 지연) 하락형'}</span>
   <span class="kv sub">최근 전환: ${RG.recent.slice().reverse().map(([d,s])=>d+' '+regName(s)).join(' · ')} (최근 5년 ${RG.n_switch_5y}회)</span>`;
function pct(arr, dir){            // 백분위 0~100 (좋을수록 높음), 값 없으면 null
  const idx = arr.map((v,i)=>i).filter(i=>arr[i]!=null).sort((a,b)=>dir*(arr[a]-arr[b]));
  const n = idx.length, s = new Array(arr.length).fill(null); idx.forEach((i,k)=> s[i] = n>1? k/(n-1)*100 : 50); return s;
}
function weights(){ const m = document.getElementById('mode').value; return WS[m==='auto'? RG.state : m]; }
function calc(){
  const minCap = +document.getElementById('minCap').value||0, W = weights();
  let rows = D.rows.filter(r => r.cap >= minCap);
  const G = {};
  for (const g in GRP){
    const P = GRP[g].map(([k,dir]) => pct(rows.map(r=>r.v[k]), dir));
    G[g] = rows.map((_,i)=>{ const xs = P.map(p=>p[i]).filter(x=>x!=null); return xs.length? xs.reduce((a,b)=>a+b,0)/xs.length : (g in MID? MID[g] : null); });
  }
  const sw = Object.values(W).reduce((a,b)=>a+b,0);
  rows = rows.map((r,i)=>{
    const o = {...r}; let tot = 0, bad = false;
    for (const g in GRP){ o[g] = G[g][i]; if (W[g]>0){ if (G[g][i]==null) bad = true; else tot += W[g]*G[g][i]; } }
    o.tot = bad? null : tot/sw; return o; });
  rows.sort((a,b)=>(b.tot??-1)-(a.tot??-1)); rows.forEach((r,i)=>r.rk = r.tot==null? null : i+1);
  const st = curState();
  rows.forEach(r=>{ for (const k of ['d1','d5']){ const p = D.prev[k].rank[st][r.c]; r[k+'p'] = p??null; r[k] = (p!=null && r.rk!=null)? p - r.rk : null; } });
  return rows;
}
function curState(){ const m = document.getElementById('mode').value; return m==='auto'? RG.state : m; }
function mv(r, k){                  // 순위 변화 표시: ▲ 오름 / ▼ 내림 / 신규
  if (r.rk==null) return '';
  if (r[k+'p']==null) return '<span class="tag">신규</span>';
  const d = r[k]; return d>0? `<span class="pos">▲${d}</span>` : d<0? `<span class="neg">▼${-d}</span>` : '<span class="sub">-</span>';
}
function renderChg(all){
  const st = curState(), P = D.prev.d1, top = all.filter(r=>r.rk!=null && r.rk<=20);
  const inn = top.filter(r=>r.d1p==null || r.d1p>20);
  const byC = Object.fromEntries(all.map(r=>[r.c, r]));
  const out = Object.entries(P.rank[st]).filter(([c,p])=>p<=20 && !(byC[c] && byC[c].rk!=null && byC[c].rk<=20))
                .map(([c,p])=>({c, p, r: byC[c]})).sort((a,b)=>a.p-b.p);
  const top100 = all.filter(r=>r.rk!=null && r.rk<=100 && r.d1!=null);
  const up = top100.slice().sort((a,b)=>b.d1-a.d1).filter(r=>r.d1>0).slice(0,8);
  const dn = all.filter(r=>r.rk!=null && r.d1p!=null && r.d1p<=100 && r.d1<0).sort((a,b)=>a.d1-b.d1).slice(0,8);
  const nm = r => `<b>${r.n}</b>`;
  document.getElementById('chg').innerHTML =
    `<div style="font-size:13px;margin-bottom:6px"><b>전일(${P.date}) 대비</b> <span class="sub">&middot; 같은 가중치로 그날 데이터를 다시 계산(시총 1,000억 기준) &middot; 표의 '전일'·'1주' 칸 = 순위 변화</span></div>
     <div class="wt" style="margin-top:0">
     <b>상위 20 진입</b> ${inn.length? inn.map(r=>`${nm(r)}(${r.d1p? r.d1p+'→' : '신규→'}${r.rk})`).join(', ') : '없음'}<br>
     <b>상위 20 이탈</b> ${out.length? out.map(o=>`${o.r? nm(o.r) : o.c}(${o.p}→${o.r && o.r.rk? o.r.rk : '제외'})`).join(', ') : '없음'}<br>
     <b>많이 오름</b>(100위 안) ${up.length? up.map(r=>`${nm(r)} ${r.d1p}→${r.rk}`).join(' &middot; ') : '없음'}<br>
     <b>많이 내림</b>(전일 100위 안) ${dn.length? dn.map(r=>`${nm(r)} ${r.d1p}→${r.rk}`).join(' &middot; ') : '없음'}</div>`;
}
function render(){
  const W = weights();
  document.getElementById('wt').innerHTML = `<b>${regName(document.getElementById('mode').value==='auto'? RG.state : document.getElementById('mode').value)} 가중치</b> &nbsp;` +
    Object.entries(W).filter(([g,w])=>w>0).map(([g,w])=>`${GN[g]} <b>${w}</b>`).join(' &middot; ');
  const all = calc(), sec = document.getElementById('sec').value;
  renderChg(all);
  const q = document.getElementById('q').value.trim(), topn = +document.getElementById('topn').value;
  let rows = all.filter(r=>r.rk!=null && (!sec||r.s===sec) && (!q||r.n.includes(q)));
  const key = sortKey, gv = (r)=> key==='n'||key==='s'? r[key] : (key.startsWith('v')? r.v[+key.slice(1)] : r[key]);
  rows.sort((a,b)=>{ const x=gv(a), y=gv(b); if(x==null) return 1; if(y==null) return -1; return (x>y?1:x<y?-1:0)*sortDir; });
  document.getElementById('cnt').textContent = `유니버스 ${all.filter(r=>r.rk!=null).length}종목 중 ${rows.length}개`;
  const on = g => W[g]>0? '' : ' style="opacity:.45"';
  const H = [['rk','#'],['d1','전일'],['d5','1주'],['n','종목'],['s','섹터'],['cap','시총(억)'],['tot','총점'],['M','모멘텀'],['R','추정치'],['V','밸류'],['Y','이익성장'],['A','성장가속'],['R3','3M상향'],['H','외인보유'],['O','기타법인'],['P','개인'],['IR','기관역'],
    ['v0','1M%'],['v1','6M%'],['v2','12M%'],['v5','FY1 OP 1M%'],['v6','FY2 OP 1M%'],['v11','FY1 OP 3M%'],['v9','TTM OP YoY%'],['v13','외인%'],['v7','시총/OP1'],['v8','시총/OP2']];
  const gcol = {M:1,R:1,V:1,Y:1,A:1,R3:1,H:1,O:1,P:1,IR:1};
  let h = '<tr>'+H.map(([k,t])=>`<th data-k="${k}" class="${k===sortKey?'sorted':''}"${k in gcol? on(k):''}>${t}</th>`).join('')+'</tr>';
  const byRank = sortKey==='rk' && !sec && !q;
  h += rows.slice(0, topn).map(r=>{
    const trc = byRank && r.rk>20 && r.rk<=40 ? (r.rk===21? 'keep cut':'keep') : '';
    return `<tr class="${trc}"><td>${r.rk}</td><td>${mv(r,'d1')}</td><td>${mv(r,'d5')}</td><td>${r.n}${r.v[12]===1?'<span class="tag">흑전</span>':''}</td><td class="sub">${r.s}</td><td>${r.cap.toLocaleString()}</td>
    <td><span class="bar" style="width:${r.tot*0.6}px"></span>${f(r.tot)}</td>` +
    ['M','R','V','Y','A','R3','H','O','P','IR'].map(g=>`<td${on(g)}>${f(r[g],0)}</td>`).join('') +
    [0,1,2,5,6,11,9].map(k=>`<td class="${cl(r.v[k])}">${k===9 && r.v[k]!=null && Math.abs(r.v[k])>=500? (r.v[k]>0?'500+':'-500') : f(r.v[k])}</td>`).join('') +
    `<td>${f(r.v[13])}</td><td>${f(r.v[7])}</td><td>${f(r.v[8])}</td></tr>`; }).join('');
  const t = document.getElementById('tbl'); t.innerHTML = h;
  t.querySelectorAll('th').forEach(th=>th.onclick=()=>{ const k=th.dataset.k; if(sortKey===k) sortDir*=-1; else {sortKey=k; sortDir=(k==='rk'||k==='n'||k==='s')?1:-1;} render(); });
}
['mode','minCap','sec','q','topn'].forEach(id=>document.getElementById(id).addEventListener('input',render));
render();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
