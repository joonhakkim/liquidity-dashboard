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
유니버스: FY2 영업이익 추정치 흑자(FY1 적자·FY2 흑자 = 내년 흑전도 포함), 우선주 제외. 시총 하한 기본 2,000억(2026-11-26 사용자: 그 밑은 못 삼)(페이지에서 변경).
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
       "Y": [(9, 1)], "A": [(10, 1)], "R3": [(11, 1)], "H": [(13, 1)], "O": [(14, 1)], "P": [(15, 1)], "IR": [(16, 1)],
       "MR": [(0, 1), (1, -1), (2, -1), (3, -1), (4, -1)], "B5": [(17, 1)],
       "PB": [(19, 1)], "OB": [(18, -1)], "FG": [(20, -1)], "TV": [(21, -1)], "NEG": [(12, 1)], "P1": [(7, -1)]}
MID = {"Y", "A", "R3", "H", "O", "P", "IR", "B5", "PB", "OB", "FG", "TV", "P1"}
RAW = {"NEG"}   # 백분위가 아니라 값 그대로(0/1 → 0/100) 쓰는 가점
# 2026-11-26: 하락형에 수급 3개(10년 수급 연구 생존 신호) 각 0.5 추가 - 11년 백테스트 연 27.2→31.0%, 2016~20 27.9→33.6,
# 2021~26 26.6→29.0, 하락 국면 11.2→20.6, 2022 -12→-5%. 상승형에 넣으면 오히려 나빠져서(27.2→24.3) 하락형만.
# 2026-11-26(2차): 1년 수익률 대신 1주·2주·1달·3달 성과(코스피 대비 평균 초과·하위 10%)로 다시 고름(사용자 요청, 안2).
#   상승형: 모멘텀 0.5→1.5, 성장 가속 1→0.5, 보유율 0.5→1, '5일 중 코스피 이긴 날' 0.5 추가
#   하락형: 밸류 1→0.5, '모멘텀 역방향'(6·12개월 낙폭 + 최근 1개월 덜 빠짐) 2 추가
#   11년: 연 31.0→35.8%, 1달 평균 초과 +1.59→+1.91%p, 1달 하위10% -4.1→-3.4, 3달 하위10% -5.7→-3.9, 최악의 달 초과 -19.6→-9.8.
#   하락형 변경은 2016~20·2021~26 둘 다 개선. 상승형 변경은 2021~26 개선, 2016~20 소폭 악화(모멘텀이 최근에 더 통함).
# 2026-11-26(3차, 신안): 사용자 요청으로 짧은 기간 기준 전 과정 재검증(개별 항목 단독 → 백지 조합 탐색 → 다듬기 →
#   유니버스·시총 → 국면 규칙 → 운용 규칙 → 연도별·가중치 흔들기). 점수 = 1달 평균 초과 + 0.5×1달 하위10%
#   + 2주(1달 환산) + 3달/3, 두 기간(2016~20, 2021~26) 중 나쁜 쪽을 키움.
#   상승형: 추정치 상향 1 · PBR밴드 위치 높음(자기 3년 중 비싸게 평가받는 중) 1 · 성장 가속 0.25 · 내년 흑전 가점 0.25
#   하락형: FY1 추정/TTM 낮음(실적이 이미 추정치를 따라잡음) 1 · 성장 가속 1 · 거래대금 감소 0.5 · 기관 20일 역 0.625 · OP밴드 낮음 0.25
#   11년 연 48%(안2 36%), 1달 평균 초과 +2.65%p(이긴 비율 71%), 1달 하위10% -3.4, 3달 하위10% -1.7, MDD -46%(안2 -40%).
#   11년 중 9년 안2보다 점수 좋음. 시총 500억·2000억(탐색에 안 쓴 조건)에서도 안2보다 두 기간 모두 좋음.
#   주의: 상승형은 PBR밴드 비중에 민감(반으로 줄이면 크게 나빠짐). 2016~20만으로 고른 가중치는 2021~26에 안 통했음(과최적화 위험).
# 2026-11-26(4차, 2,000억안): 사용자 "2,000억 밑은 못 산다" → 유니버스 2,000억으로 같은 과정 다시(단독→백지 탐색 8개→다듬기→흔들기→경계).
#   상승형: 추정치 상향 0.5 · PBR밴드 위치 높음 1 (추정치:PBR밴드 0.4~0.75 사이 비슷, 0.25면 무너짐 → 가운데 0.5)
#   하락형: 추정/TTM 낮음 1 · 성장 가속 1 · 거래대금 감소 0.5 · OP밴드 낮음 0.5 · PER(FY1) 낮음 0.25 · 기관 20일 역 0.25 (±50% 흔들어도 튼튼)
#   2,000억 11년: 연 45.5%(신안 41.6, 안2 33.1), 점수 16-20 2.88 / 21-26 3.54(신안 2.11/2.81), MDD -45, 주 4.2종목 교체.
#   이긴 날 수(5·7·10·20일, 하락일·상승일)·강도(누적 초과) 변수는 어느 국면에 넣어도 나빠짐. 2,000억 아래로 빠진 보유 종목을 1,500억까지 들고 가도 이득 없음.
WS = {"up": {"M": 0, "R": 0.5, "V": 0, "Y": 0, "A": 0, "R3": 0, "H": 0, "O": 0, "P": 0, "IR": 0, "MR": 0, "B5": 0,
             "PB": 1, "OB": 0, "FG": 0, "TV": 0, "NEG": 0, "P1": 0},
      "down": {"M": 0, "R": 0, "V": 0, "Y": 0, "A": 1, "R3": 0, "H": 0, "O": 0, "P": 0, "IR": 0.25, "MR": 0, "B5": 0,
               "PB": 0, "OB": 0.5, "FG": 1, "TV": 0.5, "NEG": 0, "P1": 0.25}}
GN = {"M": "모멘텀", "R": "추정치 상향", "V": "밸류", "Y": "이익 성장", "A": "성장 가속", "R3": "3개월 상향", "H": "외국인 보유율",
      "O": "기타법인 60일", "P": "개인 20일", "IR": "기관 20일 역", "MR": "낙폭 후 반등", "B5": "5일 이긴 날",
      "PB": "PBR밴드 위치 높음", "OB": "OP밴드 낮음", "FG": "추정/TTM 낮음", "TV": "거래대금 감소", "NEG": "내년 흑전 가점", "P1": "PER(FY1) 낮음"}
FLOW_PATH = os.path.join(BASE_DIR, "data", "screening", "cache", "naver_flow.pkl")


def score_rows(rows, state, min_cap=2000):
    """페이지 calc()와 같은 점수. 반환: 총점 내림차순 DataFrame(c, n, s, cap, v, 묶음 점수, tot)."""
    rs = [r for r in rows if r["cap"] >= min_cap]
    df = pd.DataFrame(rs)
    for g, items in GRP.items():
        if g in RAW:
            df[g] = pd.Series([r["v"][items[0][0]] for r in rs], dtype=float).fillna(0) * 100
            continue
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


def save_brief(out, min_cap=2000, keep=40):
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
    # 5일 이긴 날: 그날 시총 변화율 > 코스피 등락률이면 1 (빈 날은 셈에서 뺌, 백테스트 beat_feat.py와 같은 정의)
    k = pd.read_csv(KOSPI_PATH, dtype={"date": str})
    k["date"] = pd.to_datetime(k["date"].str[:8])
    kr = k.drop_duplicates("date").set_index("date")["close"].astype(float).sort_index().pct_change()
    cr = cap.pct_change(fill_method=None)
    ex = cr.sub(kr.reindex(cap.index), axis=0)
    win = (ex > 0).astype(float).where(ex.notna())
    fhF = (fhF, flow, win, make_ext(capf, op1f, op2f))
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


TV_PATH = os.path.join(BASE_DIR, "data", "screening", "cache", "trading_value.pkl")
EQ_PATH = os.path.join(BASE_DIR, "data", "screening", "cache", "equity_dart.pkl")


def make_ext(capf, op1f, op2f):
    """2026-11-26 전면 재검증(신안)용 원자료: OP밴드용 시총/12개월 선행 OP, PBR(DART 자본), 거래대금(원). 백테스트 bt_ext3와 같은 정의."""
    idx, cols = capf.index, capf.columns
    w = ((pd.Series(idx.month, index=idx) - 1) / 12.0).values[:, None]
    ntm = op1f * (1 - w) + op2f * w
    opm = (capf / ntm).where(ntm > 0)
    pbr = pd.DataFrame(index=idx, columns=cols, dtype=float)
    if os.path.exists(EQ_PATH):
        import build_pbr_band as pb
        st = pd.read_pickle(EQ_PATH)
        eq, ni = st["eq"], st.get("ni", {})
        have = {c for (c, _) in eq}
        E = {}
        for c in cols:
            if c not in have:
                continue
            recs = pb.build_fund(c, eq, ni)
            if not recs:
                continue
            r = pd.DataFrame(recs, columns=["d", "e", "ttm", "avg"]).sort_values("d").drop_duplicates("d", keep="last").set_index("d")
            E[c] = r["e"].reindex(idx, method="ffill")
        if E:
            E = pd.DataFrame(E).reindex(columns=cols)
            pbr = (capf / E).where(E > 0)
    tv = pd.DataFrame()
    if os.path.exists(TV_PATH):
        tv = pd.read_pickle(TV_PATH)["df"].sort_index()
        tv.index = pd.to_datetime(tv.index)
    return {"opm": opm, "pbr": pbr, "tv": tv}


def band_pct(df, t, win=756, minp=252):
    """t 시점 값이 자기 과거 win일(오늘 포함) 안에서 몇 % 위치인지(0~1, pandas rolling rank pct와 같은 평균 순위)."""
    W = df.iloc[max(0, t - win + 1):t + 1]
    cur = W.iloc[-1]
    n = W.notna().sum()
    less = (W.lt(cur, axis=1)).sum()
    eq = (W.eq(cur, axis=1)).sum()
    return ((less + (eq + 1) / 2) / n).where((n >= minp) & cur.notna())


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
    fhF, flow, win, ext = fhF
    opband, pbrband = band_pct(ext["opm"], t), band_pct(ext["pbr"], t)
    tt = ttmf.iloc[t]
    fwdg = (op1f.iloc[t] / tt - 1).where(tt > 0).clip(-5, 5)
    tvs = pd.Series(dtype=float)
    TVq = ext["tv"].loc[:asof] if len(ext["tv"]) else ext["tv"]
    if len(TVq) >= 120 and (asof - TVq.index[-1]).days <= 7:
        tvs = TVq.iloc[-20:].mean() / TVq.iloc[-120:].mean()
    w5 = win.iloc[t - 4:t + 1]
    n5 = w5.notna().sum()
    b5 = (w5.sum() / n5).where(n5 >= 3)
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
        # 14 기타법인 15 개인 16 기관역 17 5일 중 코스피 이긴 날 비율(%)
        # 18 OP밴드 위치(%, 3년 안) 19 PBR밴드 위치(%) 20 FY1 OP/TTM OP-1(%) 21 거래대금 20일/120일 평균
        vals = [m1.get(c), m6.get(c), m12.get(c), m6x.get(c), m12x.get(c), r1.get(c), r2.get(c), per1.get(c), per2.get(c),
                yoy.get(c), yacc.get(c), r3m.get(c), float(fy1neg.get(c, False)), fh.get(c, np.nan),
                oth60.get(c, np.nan), ind20.get(c, np.nan), inst20r.get(c, np.nan), b5.get(c, np.nan),
                opband.get(c, np.nan), pbrband.get(c, np.nan), fwdg.get(c, np.nan), tvs.get(c, np.nan)]
        scale = [100] * 7 + [1, 1] + [100, 100, 100, 1, 1] + [100, 100, 100, 100] + [100, 100, 100, 1]

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
    <b>가중치는 짧은 기간 성과로 처음부터 다시 고름(2026-11-26)</b> 1주·2주·1달·3달 구간마다 코스피 대비 초과수익(평균과 가장 나쁜 10%)을 보고, 2016~20과 2021~26 둘 다에서 좋은 조합만 남김.<br>
    <b>11년 백테스트</b>(2016~2026.11, <b>시총 2,000억 이상</b>(2026-11-26 기준 변경, 그 아래는 못 삼), 주 1회 다음 날 종가, 비용 0.25%): 연 45%, 최대 낙폭 -45%, 코스피 연 10%. 코스피 대비 1달 평균 +2.4%p(이긴 비율 69%), 가장 나쁜 10% 구간 -3.3%p, 3달 평균 +7.4%p(이긴 비율 83%).
    상승 국면 성과는 1~2주는 잡음에 가깝고 2~3달 단위로 판단. 급락 초기엔 코스피보다 더 빠질 수 있음. 2,000억 아래로 내려간 보유 종목을 더 들고 가도 이득이 없어 2,000억 기준 그대로.<br>
    <b>상승형</b> 추정치 상향 0.5 + <b>PBR밴드 위치 높음</b> 1(자기 3년 PBR 중 높은 쪽 = 재평가 받는 중). 둘 비율이 0.4~0.75 사이면 비슷, PBR밴드 쪽으로 더 치우치면 크게 나빠짐.<br>
    <b>하락형</b> <b>추정/TTM 낮음</b>(실적 TTM이 FY1 추정치를 이미 따라잡음) 1 + 성장 가속 1 + <b>거래대금 감소</b>(20일/120일) 0.5 + OP밴드 낮음 0.5 + PER(FY1) 낮음 0.25 + 기관 20일 순매도 0.25.<br>
    <b>코스피 이긴 날 수</b>(5·7·10·20일, 하락일·상승일 따로)와 강도(누적 초과)는 어느 국면에 넣어도 나빠져서 뺌.<br>
    <b>수급</b> 하락형에 기관 20일 순매도/거래대금(기관이 판 종목이 고점수)만 남음. 외국인·기관 순매수는 넣으면 나빠짐. 네이버 일별 수급(__FLASOF__).
    회색 칸은 지금 가중치 0인 참고 항목(이전 모델 항목 포함).<br>
    <b>항목</b> 모멘텀(1개월은 하락률 클수록 고점수, 6·12개월, 최근 1개월 제외 6·12개월) &middot; 추정치 상향(FY1·FY2 영업이익 1개월) &middot; 밸류(시총/영업이익 FY1·FY2, 낮을수록) &middot;
    이익 성장(실적 TTM 영업이익 1년 변화) &middot; 성장 가속(그 값의 3개월 변화) &middot; 3개월 추정치 상향(FY1) &middot; 외국인 보유율. 각 항목은 유니버스 안 백분위(0~100).
    유니버스 = FY2 영업이익 추정치 흑자(올해 적자·내년 흑자 포함, <span class="tag">흑전</span> 표시). <b>수익률은 시총 변화</b>라 증자·분할 때 왜곡될 수 있음.
  </div>
  <div class="ctl">
    가중치 <select id="mode"><option value="auto">현재 국면</option><option value="up">상승형으로 보기</option><option value="down">하락형으로 보기</option></select>
    시총 하한(억) <input type="number" id="minCap" value="2000" step="100">
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
//           14 기타법인 60일/시총% 15 개인 20일/거래대금% 16 -기관 20일/거래대금% 17 5일 중 코스피 이긴 날%
const GRP = {M:[[0,-1],[1,1],[2,1],[3,1],[4,1]], R:[[5,1],[6,1]], V:[[7,-1],[8,-1]], Y:[[9,1]], A:[[10,1]], R3:[[11,1]], H:[[13,1]],
             O:[[14,1]], P:[[15,1]], IR:[[16,1]], MR:[[0,1],[1,-1],[2,-1],[3,-1],[4,-1]], B5:[[17,1]],
             PB:[[19,1]], OB:[[18,-1]], FG:[[20,-1]], TV:[[21,-1]], NEG:[[12,1]], P1:[[7,-1]]};
const MID = {Y:50, A:50, R3:50, H:50, O:50, P:50, IR:50, B5:50, PB:50, OB:50, FG:50, TV:50, P1:50};    // 값 없으면 중간값(백테스트와 같음)
const RAW = {NEG:1};   // 백분위 대신 값 그대로(0/1 → 0/100)
const WS = { up:   {M:0, R:0.5, V:0, Y:0, A:0, R3:0, H:0, O:0, P:0, IR:0,    MR:0, B5:0, PB:1, OB:0,   FG:0, TV:0,   NEG:0, P1:0},
             down: {M:0, R:0,   V:0, Y:0, A:1, R3:0, H:0, O:0, P:0, IR:0.25, MR:0, B5:0, PB:0, OB:0.5, FG:1, TV:0.5, NEG:0, P1:0.25} };
const GN = {M:'모멘텀', R:'추정치 상향', V:'밸류', Y:'이익 성장', A:'성장 가속', R3:'3개월 상향', H:'외국인 보유율',
            O:'기타법인 60일', P:'개인 20일', IR:'기관 20일 역', MR:'낙폭 후 반등', B5:'5일 이긴 날',
            PB:'PBR밴드 위치 높음', OB:'OP밴드 낮음', FG:'추정/TTM 낮음', TV:'거래대금 감소', NEG:'내년 흑전 가점', P1:'PER(FY1) 낮음'};
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
    if (g in RAW){ G[g] = rows.map(r=> (r.v[GRP[g][0][0]]||0)*100); continue; }
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
    `<div style="font-size:13px;margin-bottom:6px"><b>전일(${P.date}) 대비</b> <span class="sub">&middot; 같은 가중치로 그날 데이터를 다시 계산(시총 2,000억 기준) &middot; 표의 '전일'·'1주' 칸 = 순위 변화</span></div>
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
  const H = [['rk','#'],['d1','전일'],['d5','1주'],['n','종목'],['s','섹터'],['cap','시총(억)'],['tot','총점'],['M','모멘텀'],['R','추정치'],['V','밸류'],['Y','이익성장'],['A','성장가속'],['R3','3M상향'],['H','외인보유'],['O','기타법인'],['P','개인'],['IR','기관역'],['MR','낙폭반등'],['B5','5일승'],['PB','PBR밴드↑'],['OB','OP밴드↓'],['FG','추정/TTM↓'],['TV','거래대금↓'],['P1','PER1↓'],
    ['v19','PBR밴드%'],['v18','OP밴드%'],['v20','FY1/TTM%'],['v21','거래대금20/120'],['v17','5일 이긴%'],['v0','1M%'],['v1','6M%'],['v2','12M%'],['v5','FY1 OP 1M%'],['v6','FY2 OP 1M%'],['v11','FY1 OP 3M%'],['v9','TTM OP YoY%'],['v13','외인%'],['v7','시총/OP1'],['v8','시총/OP2']];
  const gcol = {M:1,R:1,V:1,Y:1,A:1,R3:1,H:1,O:1,P:1,IR:1,MR:1,B5:1,PB:1,OB:1,FG:1,TV:1,P1:1};
  let h = '<tr>'+H.map(([k,t])=>`<th data-k="${k}" class="${k===sortKey?'sorted':''}"${k in gcol? on(k):''}>${t}</th>`).join('')+'</tr>';
  const byRank = sortKey==='rk' && !sec && !q;
  h += rows.slice(0, topn).map(r=>{
    const trc = byRank && r.rk>20 && r.rk<=40 ? (r.rk===21? 'keep cut':'keep') : '';
    return `<tr class="${trc}"><td>${r.rk}</td><td>${mv(r,'d1')}</td><td>${mv(r,'d5')}</td><td>${r.n}${r.v[12]===1?'<span class="tag">흑전</span>':''}</td><td class="sub">${r.s}</td><td>${r.cap.toLocaleString()}</td>
    <td><span class="bar" style="width:${r.tot*0.6}px"></span>${f(r.tot)}</td>` +
    ['M','R','V','Y','A','R3','H','O','P','IR','MR','B5','PB','OB','FG','TV','P1'].map(g=>`<td${on(g)}>${f(r[g],0)}</td>`).join('') +
    `<td>${f(r.v[19],0)}</td><td>${f(r.v[18],0)}</td><td>${f(r.v[20],0)}</td><td>${f(r.v[21],2)}</td><td>${f(r.v[17],0)}</td>` + [0,1,2,5,6,11,9].map(k=>`<td class="${cl(r.v[k])}">${k===9 && r.v[k]!=null && Math.abs(r.v[k])>=500? (r.v[k]>0?'500+':'-500') : f(r.v[k])}</td>`).join('') +
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
