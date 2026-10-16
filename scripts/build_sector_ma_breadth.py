"""
섹터별 이동평균선 위 종목 비율 페이지(docs/sector_ma_breadth.html) - 2026-10-14 사용자 요청
("섹터별로 이동평균선을 기준으로 아래에 있는 종목비율의 추이를 보고싶어 ... 섹터는 섹터 정리 엑셀,
기본 20일선, 이평선 조절 가능, 기업밴드찾기2 시가총액으로, 매일 자동 갱신, 추후 수정할 수 있게").

계산
- 섹터: data/manual/섹터 정리.xlsx(build_relative_strength.assign_sectors - 이름 별칭 포함)의 종목만.
- 종목별 일별 시계열: '기업 밴드 찾기 2' 워크북의 일별 시가총액(build_op_band_v2.py가 읽을 때
  data/screening/cache/op_band_mktcap_daily.pkl로 같이 저장). 시총은 액면분할에도 끊기지 않아
  미수정 종가보다 이평선 계산에 안전하다. 워크북에 없는 종목만 가격 패널(bollinger_prices.csv) 종가로 대신.
- 이동평균선 N일(10/15/20/60/120/200, 페이지에서 선택 - 기본 20): 시총(또는 종가)이 자기 N일 평균 이상이면 '위'.
- 섹터 비율 = 위 종목 수 / 이평선 계산 가능한 종목 수 (3종목 미만이면 빈칸).
  시총 가중 = 위 종목 시총 합 / 섹터 시총 합(워크북 시총이 있는 종목만).
- 위 비율이 높을수록 그 섹터가 강하다. 처음엔 '아래 비율'(낮을수록 강함)이었으나 같은 날 사용자 요청으로
  "낮은 게 좋은 거라고 생각하니까 별로다" - 위 비율로 뒤집음(위 = 100 - 아래).
파이프라인: run_pipeline.py에서 build_op_band_v2.py 다음(시총 캐시가 새로 저장된 뒤)에 실행.
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from build_relative_strength import assign_sectors, load_kospi_series  # noqa: E402

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(BASE_DIR, "data")
CAP_PATH = os.path.join(DATA_DIR, "screening", "cache", "op_band_mktcap_daily.pkl")
PRICES_PATH = os.path.join(DATA_DIR, "bollinger_prices.csv")
SECTOR_MAP_PATH = os.path.join(DATA_DIR, "sector_map.csv")
OUT_PATH = os.path.join(BASE_DIR, "docs", "sector_ma_breadth.html")
SUMMARY_PATH = os.path.join(DATA_DIR, "screening", "sector_ma_breadth_summary.csv")
WINDOWS = [10, 15, 20, 60, "20w", 120, 200]   # 20주(약 100거래일)는 기간 순서대로 60일과 120일 사이   # 2026-10-14 사용자 요청: 5일 빼고 15일 추가
DEFAULT_WINDOW = "20w"   # 2026-10-15 사용자: "20일선이 아니라 20주선이었어" - 20주선(지난 19주 주간 종가 + 오늘 값의 평균)이 기본
# 교차(골드·데드크로스) 비교용으로만 계산하는 이평선(위쪽 선택 버튼엔 없음). 2026-10-16 사용자 "5일 15일을 우선" -> 같은 날 조합 손익 비교
# (2016~, 골드 100%/데드 60%, 3일 분할, 288개) 결과 20일/40일·5일 평활이 중소형·코스닥 앞뒤 네 칸 모두 보유보다 나아 기본값으로(사용자 "그렇게 해봐").
CROSS_ONLY = [5, 40]
ALL_WINDOWS = WINDOWS + [w for w in CROSS_ONLY if w not in WINDOWS]
HISTORY_FROM = "2023-01-01"
MIN_MEMBERS = 3
# 장기 약세 기준(2026-10-14 검증, 섹터 정리 42섹터 2016~, 수익률 누적 로그가격): 120·200일선 위 비율이 40% 아래인 섹터는
# 이후 120거래일 다른 섹터 평균보다 200일선 -3.0%p(t-2.5, 2016~20) / -5.6%p(t-2.3, 2021~), 120일선 -3.2%p(t-2.7) / -3.0%p(t-1.2).
# 아래에 1~3달 머물수록 더 약함(200일선 3달+ -3.4 / -7.9%p). 10~60일선은 어느 문턱에서도 일관된 차이가 없어 기준을 두지 않음.
# '처음 내려간 날' 자체는 예측력이 없음(이후 반등도 잦음) - 머문 상태를 봐야 함.
# 20주선(2026-10-15 검증): 40% 아래 -2.1%p(t-1.9) / -2.0%p(t-0.9) - 방향은 같으나 120·200일선보다 약함. 같은 40% 선을 참고로 표시.
LONG_WEAK = {120: 40, 200: 40, "20w": 40}


def load_series(codes_needed):
    """code(앞 'A' 없음) -> 일별 시계열. 워크북 시총 우선, 없으면 가격 패널 종가."""
    caps = pd.read_pickle(CAP_PATH)
    caps.columns = [c[1:] if c.startswith("A") else c for c in caps.columns]
    caps = caps.sort_index()
    caps = caps[caps.notna().sum(axis=1) >= 0.5 * caps.notna().sum(axis=1).max()]   # 일부 종목만 찍힌 날 제거
    have = [c for c in codes_needed if c in caps.columns and caps[c].notna().sum() > 30]
    missing = [c for c in codes_needed if c not in have]
    px = pd.DataFrame(index=caps.index)
    if missing and os.path.exists(PRICES_PATH):
        df = pd.read_csv(PRICES_PATH, usecols=["date", "code", "close"], dtype={"date": "int32", "code": "str", "close": "float32"})
        df = df[df["code"].isin(missing) & (df["date"] >= int(caps.index.min().strftime("%Y%m%d")))]
        if len(df):
            df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
            px = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").reindex(caps.index)
    return caps[have], px, missing


RET_PERIODS = {"1": 1, "5": 5, "20": 20}   # 섹터 등락률: 1일 / 1주(5거래일) / 1달(20거래일)
WARN_INPUTS_PATH = os.path.join(DATA_DIR, "phase_warning_inputs.csv")
MERGED_PATH = os.path.join(DATA_DIR, "merged.csv")


def sector_returns(members, usable, series, live_idx=None):
    """섹터 등락률(구성종목 단순평균, 2026-10-14 사용자 요청 - 섹터 일간 등락률 막대 차트).
    수익률은 이평선 계산과 같은 시계열(기업밴드찾기2 일별 시총, 없으면 종가)의 일간 변화를 이어붙여 계산 -
    KRX 종가보다 하루 빨리 들어옴(장마감 당일 워크북 갱신분). 하루 31% 넘는 변동(분할·증자 등)은 제외."""
    px = series[usable].iloc[-40:]
    R = px.pct_change(fill_method=None)
    R[R.abs() > 0.31] = np.nan
    lr = np.log1p(R)
    traded = px.iloc[-1].notna()
    idx = {}
    try:
        idx["코스피"] = pd.Series({pd.Timestamp(k): float(v) for k, v in load_kospi_series().items()}).sort_index()
    except SystemExit:
        pass
    if os.path.exists(WARN_INPUTS_PATH):
        idx["코스닥"] = pd.read_csv(WARN_INPUTS_PATH, parse_dates=["date"]).set_index("date")["kosdaq_close"].dropna()
    if os.path.exists(MERGED_PATH):
        idx["원/달러"] = pd.read_csv(MERGED_PATH, usecols=["date", "usd_krw"], parse_dates=["date"]).set_index("date")["usd_krw"].dropna()
    if live_idx is not None:   # 장중: 오늘 지수 값을 붙이고, 오늘 값이 없는 지표(원/달러)는 빼서 어제 값이 오늘처럼 보이지 않게
        day = px.index[-1]
        for nm in list(idx):
            if nm in live_idx:
                idx[nm] = pd.concat([idx[nm].loc[:day - pd.Timedelta(days=1)], pd.Series({day: float(live_idx[nm])})])
            else:
                del idx[nm]
    res = {"date": px.index[-1].strftime("%Y-%m-%d"), "stk": {}}
    for key, k in RET_PERIODS.items():
        if len(px) <= k:
            continue
        rk = (np.expm1(lr.iloc[-k:].sum(min_count=1)) * 100).where(traded)
        for c, v in rk.dropna().items():   # 섹터 막대를 누르면 보여줄 종목별 등락률 [1일, 1주, 1달]
            res["stk"].setdefault(c, [None] * len(RET_PERIODS))[list(RET_PERIODS).index(key)] = round(float(v), 1)
        rows = []
        for sec, cs in members.items():
            v = rk.reindex(cs).dropna()
            if len(v) >= MIN_MEMBERS:
                rows.append({"name": sec, "r": round(float(v.mean()), 2), "n": int(len(v)), "up": int((v > 0).sum())})
        d1, d0 = px.index[-1], px.index[-1 - k]
        hdr = []
        for nm, ser in idx.items():
            a, b = ser.loc[:d1], ser.loc[:d0]
            if len(a) and len(b) and (d1 - a.index[-1]).days <= 3:
                hdr.append({"name": nm, "v": round(float(a.iloc[-1]), 1), "ch": round(float((a.iloc[-1] / b.iloc[-1] - 1) * 100), 1)})
        res[key] = {"from": d0.strftime("%Y-%m-%d"), "rows": sorted(rows, key=lambda x: -x["r"]), "hdr": hdr}
    return res


def load_universe():
    """섹터 정리 종목과 일별 시계열(워크북 시총 우선, 없으면 종가)."""
    sm = pd.read_csv(SECTOR_MAP_PATH, dtype=str)
    meta = pd.DataFrame({"name": sm.iloc[:, 1].values}, index=sm.iloc[:, 0].str.zfill(6).values)
    meta = meta[~meta.index.duplicated()]
    sectors, unmatched = assign_sectors(meta)
    members = {}
    for code, (sec, src) in sectors.items():
        if src == "U":
            members.setdefault(sec, []).append(code)
    codes = sorted({c for v in members.values() for c in v})
    caps, px, missing = load_series(codes)
    series = pd.concat([caps, px[[c for c in px.columns if c not in caps.columns]]], axis=1).ffill(limit=3)
    no_data = [c for c in codes if c not in series.columns or series[c].notna().sum() < 30]
    return meta, members, codes, caps, series, no_data, unmatched


def compute_out(meta, members, codes, caps, series, no_data, hist_from, live_idx=None):
    """페이지 데이터. hist_from 이후 날짜만 담는다(장중 계산은 오늘 하루만)."""
    asof = series.index.max()
    hist = series.index >= pd.Timestamp(hist_from)
    dates = series.index[hist]
    capw = caps.reindex(columns=series.columns)          # 가중치용(종가 대체 종목은 NaN -> 가중에서 제외)
    out = {"asof": asof.strftime("%Y-%m-%d"), "dates": [d.strftime("%Y-%m-%d") for d in dates], "windows": WINDOWS, "crossOnly": CROSS_ONLY,
           "default": DEFAULT_WINDOW, "weak": {str(k): v for k, v in LONG_WEAK.items()}, "sectors": [], "all": {}, "stocks": {}}
    gaps = {}
    above_by_w = {}
    for w in ALL_WINDOWS:
        if isinstance(w, str) and w.endswith("w"):   # N주선: 지난 N-1주 주간 종가(금요일 기준 마지막 값) + 오늘 값의 평균(HTS 주봉 이평과 같은 방식)
            n = int(w[:-1])
            wk = series.resample("W-FRI").last()
            prev = wk.shift(1).rolling(n - 1, min_periods=n - 1).sum()
            week_end = series.index.to_period("W-FRI").end_time.normalize()
            prev_d = prev.reindex(week_end)
            prev_d.index = series.index
            ma = (prev_d + series) / n
        else:
            ma = series.rolling(w, min_periods=w).mean()
        valid = series.notna() & ma.notna()
        above = (series >= ma) & valid
        above_by_w[w] = (above, valid)
        gaps[w] = (series / ma - 1) * 100
    def ratio(cols, w, kind):
        above, valid = above_by_w[w]
        b, v = above[cols], valid[cols]
        if kind == "n":
            n = v.sum(axis=1); r = b.sum(axis=1) / n.replace(0, np.nan) * 100
            return r.where(n >= MIN_MEMBERS)
        cw = capw[cols].where(v)
        tot = cw.sum(axis=1, min_count=1); r = cw.where(b).sum(axis=1) / tot * 100
        return r.where(v.sum(axis=1) >= MIN_MEMBERS)
    def pack(s):
        return [None if not np.isfinite(x) else round(float(x), 1) for x in s[hist].values]
    usable = [c for c in codes if c not in no_data]
    out["all"] = {f"{k}{w}": pack(ratio(usable, w, k)) for w in ALL_WINDOWS for k in ("n", "c")}
    rows = []
    for sec in sorted(members):
        cols = [c for c in members[sec] if c not in no_data]
        if len(cols) < MIN_MEMBERS:
            continue
        rec = {"name": sec, "n": len(cols), "codes": cols}
        for w in ALL_WINDOWS:
            for k in ("n", "c"):
                rec[f"{k}{w}"] = pack(ratio(cols, w, k))
        out["sectors"].append(rec)
        r20 = ratio(cols, DEFAULT_WINDOW, "n")
        rows.append({"sector": sec, "n": len(cols), f"above_{DEFAULT_WINDOW}": r20.iloc[-1], f"above_{DEFAULT_WINDOW}_1w": r20.iloc[-6] if len(r20) > 6 else np.nan,
                     f"above_{DEFAULT_WINDOW}_1m": r20.iloc[-22] if len(r20) > 22 else np.nan})   # 페이지와 같게 21거래일 전
    last = series.index[-1]
    for c in usable:
        nm = meta["name"].get(c, c)
        cap = capw[c].dropna()
        out["stocks"][c] = {"name": nm, "cap": round(float(cap.iloc[-1]) / 1e8) if len(cap) else None,
                            "src": "시총" if c in caps.columns else "종가",
                            "g": {str(w): (None if not np.isfinite(gaps[w].at[last, c]) else round(float(gaps[w].at[last, c]), 1)) for w in WINDOWS}}
    out["ret"] = sector_returns(members, usable, series, live_idx)
    out["idx"] = index_series(dates)
    return out, rows


def index_series(dates):
    """교차 차트에 겹칠 코스닥·코스피 종가(페이지 날짜에 맞춤, 없는 날은 직전 값)."""
    res = {}
    if os.path.exists(WARN_INPUTS_PATH):
        kq = pd.read_csv(WARN_INPUTS_PATH, parse_dates=["date"]).set_index("date")["kosdaq_close"].dropna()
        res["코스닥"] = kq
    try:
        res["코스피"] = pd.Series({pd.Timestamp(k): float(v) for k, v in load_kospi_series().items()}).sort_index()
    except SystemExit:
        pass
    return {nm: [None if not np.isfinite(x) else round(float(x), 2) for x in v.reindex(dates, method="ffill").values] for nm, v in res.items()}


LIVE_TAIL = 320   # 장중 계산은 최근 320거래일만(200일선·20주선에 충분)


def live_payload(ret, live_date, live_idx=None):
    """장중 실시간(live_intraday.py가 15분마다 호출, 2026-10-15 사용자 "차트 등 뭐든 바로 반영되게").
    ret: 종목코드(6자리) -> 오늘 등락률(소수). 어제까지 시계열에 오늘 값(마지막 값 x (1 + 등락률), 등락률 없으면 그대로)을 붙여
    페이지와 똑같은 계산(compute_out)으로 오늘 하루치만 돌려준다 - 페이지 JS가 시계열 끝에 붙인다."""
    meta, members, codes, caps, series, no_data, _ = load_universe()
    day = pd.Timestamp(live_date)
    series = series.loc[:day - pd.Timedelta(days=1)].iloc[-LIVE_TAIL:]
    r = ret.reindex(series.columns)
    r = r.where(r.abs() <= 0.31)
    last = series.iloc[-1]
    today = (last * (1 + r)).fillna(last)
    series = pd.concat([series, today.to_frame(day).T])
    caps = series[caps.columns]
    out, _ = compute_out(meta, members, codes, caps, series, no_data, day, live_idx)
    keys = [f"{k}{w}" for w in ALL_WINDOWS for k in ("n", "c")]
    return {"keys": keys, "all": [out["all"][k][0] for k in keys],
            "sec": {s["name"]: [s[k][0] for k in keys] for s in out["sectors"]},
            "stocks": {c: [st["cap"]] + [st["g"][str(w)] for w in WINDOWS] for c, st in out["stocks"].items()},
            "ret": out["ret"]}


def main():
    meta, members, codes, caps, series, no_data, unmatched = load_universe()
    asof = series.index.max()
    print(f"섹터 {len(members)}개, 종목 {len(codes)}개(시총 {caps.shape[1]}, 종가 대체 {len(codes) - caps.shape[1] - len(no_data)}, 데이터 없음 {len(no_data)}) | 기준일 {asof.date()}")
    out, rows = compute_out(meta, members, codes, caps, series, no_data, HISTORY_FROM)
    os.makedirs(os.path.dirname(SUMMARY_PATH), exist_ok=True)
    pd.DataFrame(rows).sort_values(f"above_{DEFAULT_WINDOW}", ascending=False).to_csv(SUMMARY_PATH, index=False, encoding="utf-8-sig", float_format="%.1f")
    html = (TEMPLATE.replace("__UPDATED__", datetime.now().strftime("%Y-%m-%d %H:%M")).replace("__ASOF__", out["asof"])
            .replace("__DATA__", json.dumps(out, ensure_ascii=False, separators=(",", ":"))))
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    if unmatched:
        print(f"섹터 정리 종목 중 코드 못 찾음 {len(unmatched)}개: {', '.join(unmatched[:15])}{' ...' if len(unmatched) > 15 else ''}")
    if no_data:
        print(f"데이터 없음 {len(no_data)}개: {', '.join(meta['name'].get(c, c) for c in no_data[:15])}")
    print(f"저장 완료: {OUT_PATH}")
    print(f"저장 완료: {SUMMARY_PATH}")


TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>섹터 이평선 위 비율</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body { font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  a.back { color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }
  h1 { font-size:20px; margin:8px 0 4px 0; } h1 .sub { font-size:13px; color:#9aa0a6; font-weight:normal; margin-left:6px; }
  .updated { color:#9aa0a6; font-size:13px; margin-bottom:14px; }
  .ctl { display:flex; gap:14px; flex-wrap:wrap; align-items:center; font-size:12px; color:#c9ccd1; margin-bottom:14px; }
  .ctl select { background:#0f1115; color:#e6e6e6; border:1px solid #2a2e37; border-radius:6px; padding:4px 6px; font-family:inherit; font-size:12px; }
  .seg button { background:#1a1d24; border:1px solid #2a2e37; color:#c9ccd1; padding:5px 11px; border-radius:999px; cursor:pointer; font-size:12px; font-family:inherit; margin-right:4px; }
  .seg button.active { background:#4dabf7; color:#0f1115; border-color:#4dabf7; font-weight:bold; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:12px; margin-bottom:16px; }
  .card { background:#1a1d24; border:1px solid #23262e; border-radius:12px; padding:12px 14px; font-size:12px; color:#9aa0a6; }
  .card .val { font-size:22px; font-weight:700; color:#e6e6e6; margin:4px 0; }
  .card .list { color:#c9ccd1; line-height:1.7; }
  .box { background:#12151b; border:1px solid #23262e; border-radius:12px; padding:14px 16px; margin-bottom:16px; }
  .box h2 { font-size:15px; margin:0 0 6px 0; } .box .desc { font-size:12px; color:#9aa0a6; margin-bottom:8px; line-height:1.6; }
  .chart-wrap { position:relative; height:340px; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  th, td { padding:6px 8px; border-bottom:1px solid #23262e; text-align:right; white-space:nowrap; }
  th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) { text-align:left; }
  th { color:#9aa0a6; font-weight:normal; cursor:pointer; } th.nosort { cursor:default; }
  tr.sec:hover { background:#1a1f28; cursor:pointer; }
  .heat td.h { width:14px; min-width:14px; padding:0; height:20px; }
  .bar { display:inline-block; height:8px; border-radius:4px; vertical-align:middle; margin-left:6px; }
  .pos { color:#ff8787; } .neg { color:#74a7ff; }
  .chk { cursor:pointer; }
  .members td { font-size:11px; color:#c9ccd1; background:#0f1218; }
  .tblwrap { overflow-x:auto; }
  .rb { display:grid; grid-template-columns:110px 70px 1fr; align-items:center; gap:6px; font-size:12px; height:23px; }
  .rb .nm { text-align:right; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .rb .sb { color:#7c828b; font-size:11px; white-space:nowrap; }
  .rb .tr { position:relative; height:16px; }
  .rb .z { position:absolute; left:50%; top:-4px; bottom:-4px; border-left:1px solid #6b7280; }
  .rb .b { position:absolute; top:2px; height:12px; border-radius:2px; }
  .rb .v { position:absolute; top:0; font-size:11px; font-weight:700; white-space:nowrap; }
  @media (max-width: 640px) { .rb { grid-template-columns:84px 56px 1fr; } }
  .note { font-size:12px; color:#9aa0a6; line-height:1.7; background:#1a1d24; border-radius:10px; padding:12px 16px; }
  .note b { color:#ffa94d; }
  @media (max-width: 640px) { body { padding:16px; } .chart-wrap { height:260px; } }
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <a class="back" href="relative_strength.html">종목·섹터 상대강도 순위 &rarr;</a>
  <h1>섹터 이평선 위 비율<span class="sub">섹터 정리 기준, 이동평균선 위 종목 비율로 본 섹터 강도</span></h1>
  <div class="updated">최종 갱신: __UPDATED__ &middot; 데이터 기준일: __ASOF__ &middot; 매일 OP밴드 갱신 뒤 자동 갱신</div>

  <div class="ctl">
    <span>이동평균선</span><span class="seg" id="segW"></span>
    <span>기준</span><span class="seg" id="segK"><button data-k="n" class="active">종목 수</button><button data-k="c">시총 가중</button></span>
    <span>차트 기간</span><span class="seg" id="segP"><button data-p="63">3달</button><button data-p="126" class="active">6달</button><button data-p="250">1년</button><button data-p="0">전체</button></span>
  </div>

  <div id="livePanel" style="display:none;background:#12151b;border:1px solid #4dabf7;border-radius:12px;padding:12px 16px;margin:10px 0 16px 0"></div>
  <div class="cards" id="cards"></div>

  <div class="box">
    <h2>섹터 등락률 <span style="font-size:12px;font-weight:normal;color:#9aa0a6">막대를 누르면 구성종목</span> <span id="retSub" style="font-size:12px;font-weight:normal;color:#9aa0a6"></span></h2>
    <div class="ctl" style="margin-bottom:8px"><span class="seg" id="segR"><button data-r="1" class="active">1일</button><button data-r="5">1주</button><button data-r="20">1달</button></span><span id="retHdr"></span></div>
    <div id="retBars"></div>
  </div>

  <div class="box">
    <h2>추이 <span id="chartTitle" style="font-size:12px;font-weight:normal;color:#9aa0a6"></span></h2>
    <div class="desc">아래 표에서 섹터 앞 체크박스로 골라 추가합니다. 점선 = 섹터 정리 전체 종목. <b style="color:#ffa94d">높을수록 강함</b>(대부분 종목이 이평선 위). <span id="weakNote"></span></div>
    <div class="chart-wrap"><canvas id="cLine"></canvas></div>
  </div>

  <div class="box">
    <h2>이평선 위 비율 교차 <span style="font-size:12px;font-weight:normal;color:#9aa0a6">빠른 선(짧은 이평선 위 비율)이 느린 선을 위로 뚫으면 골드 ▲, 아래로 뚫으면 데드 ▼</span></h2>
    <div class="ctl" style="margin-bottom:8px">
      <span>대상</span><select id="xT"></select>
      <span>빠른</span><select id="xF"></select>
      <span>느린</span><select id="xS"></select>
      <span>평활</span><select id="xM"><option value="1">없음</option><option value="3">3일 평균</option><option value="5" selected>5일 평균</option></select>
      <span>지수</span><select id="xI"><option value="코스닥">코스닥</option><option value="코스피">코스피</option><option value="">없음</option></select>
      <span class="seg" id="xP"><button data-p="63">3달</button><button data-p="126" class="active">6달</button><button data-p="250">1년</button><button data-p="0">전체</button></span>
    </div>
    <div class="desc" id="xNow"></div>
    <div class="chart-wrap"><canvas id="cCross"></canvas></div>
    <div class="tblwrap" style="margin-top:10px"><table id="xTbl"></table></div>
    <div class="desc" id="xStat" style="margin-top:6px"></div>
  </div>

  <div class="box">
    <h2>섹터 4사분면 <span style="font-size:12px;font-weight:normal;color:#9aa0a6">섹터별 종목 수 기준 비율 · 점 크기 = 종목 수 · 점을 누르면 추이 차트에 추가</span></h2>
    <div class="ctl" style="margin-bottom:8px">
      <span>빠른</span><select id="qF"></select>
      <span>느린</span><select id="qS"></select>
      <span>평활</span><select id="qM"><option value="1" selected>없음</option><option value="3">3일 평균</option><option value="5">5일 평균</option></select>
      <span>꼬리</span><span class="seg" id="qTail"><button data-t="0">없음</button><button data-t="5">1주</button><button data-t="10" class="active">2주</button><button data-t="20">4주</button></span>
      <span>기준일</span><input type="range" id="qDay" min="0" max="0" value="0" style="width:160px"><span id="qDayLbl"></span>
    </div>
    <div class="desc" id="qDesc"></div>
    <div class="chart-wrap" style="height:520px"><canvas id="cQuad"></canvas></div>
    <div class="tblwrap" style="margin-top:10px"><table id="qTbl"></table></div>
  </div>

  <div class="box">
    <h2>섹터 순위 <span style="font-size:12px;font-weight:normal;color:#9aa0a6">행을 누르면 소속 종목, 머리글을 누르면 정렬</span></h2>
    <div class="tblwrap"><table id="tbl"></table></div>
  </div>

  <div class="box">
    <h2>주간 추이 히트맵 <span style="font-size:12px;font-weight:normal;color:#9aa0a6">최근 26주(매주 마지막 거래일), 초록 = 위 비율 높음(강함) · 빨강 = 낮음(약함)</span></h2>
    <div class="tblwrap"><table id="heat" class="heat"></table></div>
  </div>

  <div class="note">
    <b>계산</b>: 섹터는 '섹터 정리' 엑셀의 종목만 씁니다. 종목별로 '기업 밴드 찾기 2'의 일별 시가총액이 자기 N일 이동평균 이상이면 '위'로 셉니다
    (시총은 액면분할에도 끊기지 않아 미수정 종가보다 안전합니다. 워크북에 없는 종목만 종가로 대신 계산).
    위 비율 = 위 종목 수 ÷ 계산 가능한 종목 수, 시총 가중 = 위 종목 시총 합 ÷ 섹터 시총 합. 3종목 미만인 날은 비웁니다.<br>
    <b>보는 법</b>: 비율이 높을수록 섹터 내 대부분 종목이 상승 추세(강함), 낮을수록 약함. 1주·1달 전 대비 변화로 강해지는 중인지 약해지는 중인지 봅니다.<br>
    <b>장기 약세 기준 40%</b>(20주·120·200일선, 빨간 점선 - 20주선은 근거가 더 약함): 2016년 이후 이 아래에 있던 섹터는 이후 6달 동안 다른 섹터보다 평균 3~6%p 약했습니다(앞뒤 두 구간 모두). 아래에 오래 머물수록 더 약했고, 처음 내려간 날 자체는 의미가 없었습니다. 10~60일선에는 이런 기준이 없어 선을 긋지 않습니다(단기 흐름용). 시장 전체가 약할 땐 대부분 섹터가 기준 아래라 섹터 간 비교로 보세요.
    기본은 20주선(약 100거래일, 주봉 기준)입니다. 10~20일선은 단기, 60일선·20주선은 중기, 120·200일선은 장기 추세입니다.
  </div>

<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
const nn = v => v !== null && v !== undefined && !isNaN(v);
const fmt = (v, d) => nn(v) ? v.toFixed(d === undefined ? 0 : d) : '-';
const sgn = v => nn(v) ? (v >= 0 ? '+' : '') + v.toFixed(0) : '-';
let N = D.dates.length;
let W = D.default, K = 'n', PERIOD = 126, SORT = 'cur', DESC = true;
let selected = null;
const COLORS = ['#ff8787', '#ffa94d', '#ffd43b', '#69db7c', '#4dabf7', '#9775fa', '#f783ac', '#63e6be', '#e599f7', '#a9e34b'];
function lastVal(a) { for (let i = a.length - 1; i >= 0; i--) if (nn(a[i])) return [a[i], i]; return [null, -1]; }
function valAgo(a, i, k) { const j = i - k; return j >= 0 ? a[j] : null; }
function heatColor(v) {
  if (!nn(v)) return '#1a1d24';
  const t = Math.max(0, Math.min(1, 1 - v / 100));     // 0 = 강함(초록, 위 비율 100%), 1 = 약함(빨강)
  const h = 130 - 130 * t; return `hsl(${h}, 55%, ${30 + 8 * Math.abs(t - 0.5)}%)`;
}
function stats() {
  const key = K + W;
  return D.sectors.map(s => {
    const a = s[key]; const [cur, i] = lastVal(a);
    return {s, cur, w1: valAgo(a, i, 5), m1: valAgo(a, i, 21), d1: nn(cur) && nn(valAgo(a, i, 5)) ? cur - valAgo(a, i, 5) : null,
            dm: nn(cur) && nn(valAgo(a, i, 21)) ? cur - valAgo(a, i, 21) : null};
  });
}
function renderCards(st) {
  const key = K + W; const [cur, i] = lastVal(D.all[key]);
  const ok = st.filter(x => nn(x.cur)).sort((a, b) => b.cur - a.cur);
  const strong = ok.slice(0, 5).map(x => `${x.s.name} ${fmt(x.cur)}%`).join('<br>');
  const weak = ok.slice(-5).reverse().map(x => `${x.s.name} ${fmt(x.cur)}%`).join('<br>');
  const up = st.filter(x => nn(x.d1)).sort((a, b) => b.d1 - a.d1).slice(0, 3).map(x => `${x.s.name} ${sgn(x.d1)}%p`).join('<br>');
  $('cards').innerHTML =
    `<div class="card">섹터 정리 전체 종목 · ${LBL(W)} 위 비율${D.live ? ' (오늘 잠정)' : ''}<div class="val">${fmt(cur)}%</div>1주 전 ${fmt(valAgo(D.all[key], i, 5))}% · 1달 전 ${fmt(valAgo(D.all[key], i, 21))}%</div>` +
    `<div class="card">가장 강한 섹터(위 비율 높음)<div class="list">${strong}</div></div>` +
    `<div class="card">가장 약한 섹터(위 비율 낮음)<div class="list">${weak}</div></div>` +
    `<div class="card">1주 새 가장 강해진 섹터<div class="list">${up}</div></div>`;
}
function renderTable(st) {
  const key = K + W;
  const sortKey = {cur: x => x.cur, w1: x => x.d1, m1: x => x.dm, n: x => x.s.n, name: x => x.s.name};
  const rows = st.slice().sort((a, b) => {
    const f = sortKey[SORT]; const va = f(a), vb = f(b);
    if (SORT === 'name') return DESC ? vb.localeCompare(va) : va.localeCompare(vb);
    const x = nn(va) ? va : (DESC ? -1e9 : 1e9), y = nn(vb) ? vb : (DESC ? -1e9 : 1e9); return DESC ? y - x : x - y;
  });
  const recent = 20;
  let h = `<tr><th class="nosort"></th><th data-s="name">섹터</th><th data-s="n">종목</th><th data-s="cur">${LBL(W)} 위</th><th class="nosort">1주 전</th><th class="nosort">1달 전</th><th data-s="w1">1주 변화</th><th data-s="m1">1달 변화</th><th class="nosort" style="text-align:left">최근 ${recent}거래일</th></tr>`;
  rows.forEach((x, r) => {
    const a = x.s[key]; const cells = a.slice(-recent).map(v => `<span title="${fmt(v)}%" style="display:inline-block;width:9px;height:14px;margin-right:1px;background:${heatColor(v)}"></span>`).join('');
    const on = selected.has(x.s.name);
    h += `<tr class="sec" data-name="${x.s.name}"><td><input type="checkbox" class="chk" data-name="${x.s.name}" ${on ? 'checked' : ''}></td><td>${r + 1}. ${x.s.name}</td><td>${x.s.n}</td>` +
      `<td><b>${fmt(x.cur)}%</b><span class="bar" style="width:${nn(x.cur) ? Math.round(x.cur * 0.6) : 0}px;background:${heatColor(x.cur)}"></span>${weakTag(a)}</td>` +
      `<td>${fmt(x.w1)}%</td><td>${fmt(x.m1)}%</td><td class="${nn(x.d1) && x.d1 > 0 ? 'pos' : 'neg'}">${sgn(x.d1)}%p</td><td class="${nn(x.dm) && x.dm > 0 ? 'pos' : 'neg'}">${sgn(x.dm)}%p</td><td style="text-align:left">${cells}</td></tr>`;
  });
  $('tbl').innerHTML = h;
  $('tbl').querySelectorAll('th[data-s]').forEach(th => th.onclick = () => { const s = th.dataset.s; if (SORT === s) DESC = !DESC; else { SORT = s; DESC = (s === 'cur' || s === 'w1' || s === 'm1' || s === 'n'); } renderAll(); });
  $('tbl').querySelectorAll('input.chk').forEach(cb => cb.onclick = e => { e.stopPropagation(); cb.checked ? selected.add(cb.dataset.name) : selected.delete(cb.dataset.name); renderChart(); if (typeof renderQuad === 'function' && qChart) renderQuad(); });
  $('tbl').querySelectorAll('tr.sec').forEach(tr => tr.onclick = () => toggleMembers(tr));
}
function toggleMembers(tr) {
  const nx = tr.nextElementSibling;
  if (nx && nx.classList.contains('members')) { nx.remove(); return; }
  const s = D.sectors.find(x => x.name === tr.dataset.name);
  const list = s.codes.map(c => D.stocks[c]).filter(Boolean).map(st => ({...st, g: st.g[String(W)]})).sort((a, b) => (nn(b.g) ? b.g : -1e9) - (nn(a.g) ? a.g : -1e9));
  const html = list.map(st => `<span style="display:inline-block;margin:2px 10px 2px 0" class="${nn(st.g) && st.g < 0 ? 'neg' : 'pos'}">${st.g < 0 ? '▼' : '▲'} ${st.name} ${nn(st.g) ? (st.g >= 0 ? '+' : '') + st.g.toFixed(1) + '%' : '-'}${st.cap ? ` <span style="color:#7c828b">(${st.cap.toLocaleString()}억${st.src === '종가' ? ', 종가' : ''})</span>` : ''}</span>`).join('');
  const row = document.createElement('tr'); row.className = 'members';
  row.innerHTML = `<td></td><td colspan="8" style="text-align:left;white-space:normal">${LBL(W)} 대비 이격(▲ 위, 높은 순): ${html}</td>`;
  tr.after(row);
}
let chart = null;
function LBL(w) { w = String(w); return w.endsWith('w') ? w.slice(0, -1) + '주선' : w + '일선'; }
function weakTag(a) {
  const thr = D.weak[String(W)]; if (!thr) return '';
  let k = 0; for (let i = a.length - 1; i >= 0 && nn(a[i]) && a[i] < thr; i--) k++;
  return k ? ` <span style="color:#ff8787;font-size:11px" title="${LBL(W)} 위 비율 ${thr}% 아래 연속 거래일">약세 ${k}일째</span>` : '';
}
function renderChart() {
  const key = K + W; const st = PERIOD ? Math.max(0, N - PERIOD) : 0;
  const labels = D.dates.slice(st);
  const ds = [{label: '섹터 정리 전체', data: D.all[key].slice(st), borderColor: '#9aa0a6', borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0}];
  [...selected].forEach((nm, i) => { const s = D.sectors.find(x => x.name === nm); if (s) ds.push({label: nm, data: s[key].slice(st), borderColor: COLORS[i % COLORS.length], borderWidth: 1.8, pointRadius: 0}); });
  const thr = D.weak[String(W)];
  if (thr) ds.push({label: `장기 약세 기준 ${thr}%`, data: labels.map(() => thr), borderColor: '#ff6b6b', borderDash: [6, 4], borderWidth: 1.2, pointRadius: 0});
  $('weakNote').innerHTML = thr ? `<b style="color:#ff8787">빨간 점선 = 장기 약세 기준 ${thr}%</b>(이 아래에 머문 섹터는 이후 6달 다른 섹터보다 평균 3~6%p 약했음).` : '10~60일선은 장기 약세를 가르는 기준이 없어 선이 없습니다(20주·120·200일선에서 표시).';
  if (chart) chart.destroy();
  chart = new Chart($('cLine'), {type: 'line', data: {labels, datasets: ds}, options: {responsive: true, maintainAspectRatio: false, animation: false,
    interaction: {mode: 'index', intersect: false},
    plugins: {legend: {labels: {color: '#e6e6e6', boxWidth: 12, font: {size: 11}}}, tooltip: {callbacks: {label: c => c.dataset.label + ': ' + (c.parsed.y === null ? '-' : c.parsed.y.toFixed(0) + '%')}}},
    scales: {x: {ticks: {color: '#9aa0a6', maxTicksLimit: 8, maxRotation: 0}, grid: {display: false}}, y: {min: 0, max: 100, ticks: {color: '#9aa0a6', callback: v => v + '%'}, grid: {color: '#23262e'}}}}});
  $('chartTitle').textContent = `${LBL(W)} 위 종목 비율 · ${K === 'n' ? '종목 수' : '시총 가중'} 기준`;
}
function renderHeat(st) {
  const key = K + W;
  // 매주 마지막 거래일 인덱스
  const idx = []; for (let i = 0; i < N; i++) { const d = new Date(D.dates[i]); const nx = i + 1 < N ? new Date(D.dates[i + 1]) : null; if (!nx || nx.getDay() < d.getDay() || (nx - d) > 3 * 864e5) idx.push(i); }
  const wk = idx.slice(-26);
  const rows = st.filter(x => nn(x.cur)).sort((a, b) => b.cur - a.cur);
  let h = '<tr><th class="nosort" style="text-align:left">섹터</th><th class="nosort">지금</th>' + wk.map(i => `<th class="nosort" style="font-size:10px;padding:2px">${D.dates[i].slice(5).replace('-', '/')}</th>`).join('') + '</tr>';
  const all = D.all[key];
  h += `<tr><td><i>섹터 정리 전체</i></td><td>${fmt(lastVal(all)[0])}%</td>` + wk.map(i => `<td class="h" title="${D.dates[i]} ${fmt(all[i])}%" style="background:${heatColor(all[i])}"></td>`).join('') + '</tr>';
  rows.forEach(x => { const a = x.s[key]; h += `<tr><td>${x.s.name}</td><td>${fmt(x.cur)}%</td>` + wk.map(i => `<td class="h" title="${D.dates[i]} ${fmt(a[i])}%" style="background:${heatColor(a[i])}"></td>`).join('') + '</tr>'; });
  $('heat').innerHTML = h;
}
function renderAll() {
  const st = stats();
  if (selected === null) selected = new Set();   // 처음엔 아무 섹터도 체크 안 함(2026-10-15 사용자 요청) - 전체 점선만
  renderCards(st); renderTable(st); renderChart(); renderHeat(st);
}
$('segW').innerHTML = D.windows.map(w => `<button data-w="${w}" class="${String(w) === String(W) ? 'active' : ''}">${String(w).endsWith('w') ? String(w).slice(0, -1) + '주' : w + '일'}</button>`).join('');
$('segW').onclick = e => { const b = e.target.closest('button'); if (!b) return; W = b.dataset.w; document.querySelectorAll('#segW button').forEach(x => x.classList.toggle('active', x === b)); renderAll(); };
$('segK').onclick = e => { const b = e.target.closest('button'); if (!b) return; K = b.dataset.k; document.querySelectorAll('#segK button').forEach(x => x.classList.toggle('active', x === b)); renderAll(); };
$('segP').onclick = e => { const b = e.target.closest('button'); if (!b) return; PERIOD = +b.dataset.p; document.querySelectorAll('#segP button').forEach(x => x.classList.toggle('active', x === b)); renderChart(); };
let RP = '1';
function renderRet() {
  const R = D.ret && D.ret[RP]; if (!R) { $('retBars').innerHTML = '<div class="desc">자료 없음</div>'; return; }
  $('retSub').textContent = (RP === '1' ? D.ret.date : R.from + ' → ' + D.ret.date) + (D.live ? ' ' + liveLabel(LIVE) + ' 잠정' : ' 종가') + ' · 구성종목 단순평균 · 섹터 정리 기준';
  $('retHdr').innerHTML = R.hdr.map(h => `${h.name} <b>${h.v.toLocaleString(undefined, {maximumFractionDigits: 1})}</b> <span class="${h.ch >= 0 ? 'pos' : 'neg'}">${h.ch >= 0 ? '+' : ''}${h.ch.toFixed(1)}%</span>`).join(' &nbsp;·&nbsp; ');
  const mx = Math.max(...R.rows.map(x => Math.abs(x.r)), 0.1);
  $('retBars').innerHTML = R.rows.map(x => {
    const w = Math.abs(x.r) / mx * 40, pos = x.r >= 0, lbl = (pos ? '+' : '') + x.r.toFixed(1) + '%';
    const bar = pos ? `<span class="b" style="left:50%;width:${w}%;background:#ff6b6b"></span><span class="v pos" style="left:calc(50% + ${w}% + 4px)">${lbl}</span>`
                    : `<span class="b" style="right:50%;width:${w}%;background:#4dabf7"></span><span class="v neg" style="right:calc(50% + ${w}% + 4px)">${lbl}</span>`;
    return `<div class="rb" data-name="${x.name}" style="cursor:pointer" title="눌러서 구성종목 보기"><span class="nm">${x.name}</span><span class="sb">${x.n}중 ${x.up}↑</span><span class="tr"><span class="z"></span>${bar}</span></div>`;
  }).join('');
  bindRetClicks();
}
let OPEN_RET = null;   // 펼쳐 둔 섹터(기간을 바꾸거나 새 값이 와도 유지)
function retMembers(name) {
  const sec = D.sectors.find(x => x.name === name), stk = (D.ret && D.ret.stk) || {}, j = ['1', '5', '20'].indexOf(RP);
  if (!sec) return '<div class="desc">구성종목 정보 없음</div>';
  const list = sec.codes.map(c => ({c, nm: (D.stocks[c] || {}).name || c, cap: (D.stocks[c] || {}).cap, r: stk[c] ? stk[c][j] : null}))
    .sort((a, b) => (nn(b.r) ? b.r : -1e9) - (nn(a.r) ? a.r : -1e9));
  return '<div style="margin:2px 0 8px 0;padding:8px 12px;background:#0f1218;border-radius:8px;font-size:12px;line-height:1.9">' +
    list.map(x => `<span style="display:inline-block;margin-right:14px;white-space:nowrap">${x.nm} <b class="${nn(x.r) && x.r < 0 ? 'neg' : 'pos'}">${nn(x.r) ? (x.r >= 0 ? '+' : '') + x.r.toFixed(1) + '%' : '-'}</b>${x.cap ? ` <span style="color:#7c828b">${x.cap.toLocaleString()}억</span>` : ''}</span>`).join('') + '</div>';
}
function bindRetClicks() {
  $('retBars').querySelectorAll('.rb').forEach(el => {
    el.onclick = () => { OPEN_RET = OPEN_RET === el.dataset.name ? null : el.dataset.name; renderRet(); };
    if (el.dataset.name === OPEN_RET) el.insertAdjacentHTML('afterend', retMembers(OPEN_RET));
  });
}
// ---------- 이평선 위 비율 교차(골드·데드크로스) - 2026-10-16 사용자 "X일 이평선 위 비율 차트 2개를 조합해 변곡점을, 5일 15일 우선"
const XW = [5, 10, 15, 20, 40, 60, '20w', 120, 200].filter(w => D.all['n' + w]);
let XF = '20', XS = '40', XM = 5, XT = '', XI = '코스닥', XP = 126, xChart = null;
$('xF').innerHTML = XW.map(w => `<option value="${w}">${LBL(w)}</option>`).join(''); $('xF').value = XF;
$('xS').innerHTML = XW.map(w => `<option value="${w}">${LBL(w)}</option>`).join(''); $('xS').value = XS;
$('xT').innerHTML = '<option value="">섹터 정리 전체</option>' + D.sectors.map(s => `<option value="${s.name}">${s.name}</option>`).join('');
function sma(a, m) { if (m <= 1) return a.slice(); return a.map((v, i) => { if (i < m - 1) return null; let t = 0; for (let j = i - m + 1; j <= i; j++) { if (!nn(a[j])) return null; t += a[j]; } return t / m; }); }
function crossData() {
  const src = XT ? D.sectors.find(s => s.name === XT) : D.all;
  const f = sma(src['n' + XF] || [], XM), s = sma(src['n' + XS] || [], XM);
  const ev = [];   // {i, up}
  for (let i = 1; i < N; i++) {
    if (!nn(f[i]) || !nn(s[i]) || !nn(f[i - 1]) || !nn(s[i - 1])) continue;
    const d0 = f[i - 1] - s[i - 1], d1 = f[i] - s[i];
    if (d0 <= 0 && d1 > 0) ev.push({i, up: true}); else if (d0 >= 0 && d1 < 0) ev.push({i, up: false});
  }
  return {f, s, ev};
}
function fwd(px, i, h) { return i + h < N && nn(px[i]) && nn(px[i + h]) ? (px[i + h] / px[i] - 1) * 100 : null; }
function renderCross() {
  const {f, s, ev} = crossData(), px = XI && D.idx ? D.idx[XI] : null;
  const st = XP ? Math.max(0, N - XP) : 0;
  const gold = new Array(N).fill(null), dead = new Array(N).fill(null);
  ev.forEach(e => (e.up ? gold : dead)[e.i] = f[e.i]);
  const ds = [
    {label: LBL(XF) + ' 위 비율', data: f.slice(st), borderColor: '#ffa94d', borderWidth: 1.8, pointRadius: 0, yAxisID: 'y'},
    {label: LBL(XS) + ' 위 비율', data: s.slice(st), borderColor: '#4dabf7', borderWidth: 1.8, pointRadius: 0, yAxisID: 'y'},
    {label: '골드 ▲', data: gold.slice(st), showLine: false, pointStyle: 'triangle', pointRadius: 7, backgroundColor: '#ff6b6b', borderColor: '#ff6b6b', yAxisID: 'y'},
    {label: '데드 ▼', data: dead.slice(st), showLine: false, pointStyle: 'triangle', rotation: 180, pointRadius: 7, backgroundColor: '#4dabf7', borderColor: '#74a7ff', yAxisID: 'y'}];
  if (px) ds.push({label: XI + '(오른쪽)', data: px.slice(st), borderColor: '#6b7280', borderWidth: 1, pointRadius: 0, yAxisID: 'y2'});
  if (xChart) xChart.destroy();
  xChart = new Chart($('cCross'), {type: 'line', data: {labels: D.dates.slice(st), datasets: ds}, options: {responsive: true, maintainAspectRatio: false, animation: false,
    interaction: {mode: 'index', intersect: false},
    plugins: {legend: {labels: {color: '#e6e6e6', boxWidth: 12, font: {size: 11}}}, tooltip: {filter: c => c.parsed.y !== null, callbacks: {label: c => c.dataset.label + ': ' + (c.dataset.yAxisID === 'y2' ? c.parsed.y.toLocaleString() : c.parsed.y.toFixed(0) + '%')}}},
    scales: {x: {ticks: {color: '#9aa0a6', maxTicksLimit: 8, maxRotation: 0}, grid: {display: false}}, y: {min: 0, max: 100, ticks: {color: '#9aa0a6', callback: v => v + '%'}, grid: {color: '#23262e'}},
      y2: {display: !!px, position: 'right', grid: {display: false}, ticks: {color: '#6b7280'}}}}});
  // 지금 상태
  const i = N - 1, last = ev.length ? ev[ev.length - 1] : null;
  const up = nn(f[i]) && nn(s[i]) ? f[i] > s[i] : null;
  $('xNow').innerHTML = up === null ? '' : `<b style="color:${up ? '#ff8787' : '#74a7ff'}">지금 ${up ? '골드' : '데드'} 상태</b>${last ? ` · ${D.dates[last.i]} ${last.up ? '골드' : '데드'}크로스 뒤 ${i - last.i}거래일째` : ''} · ${LBL(XF)} 위 ${fmt(f[i])}% vs ${LBL(XS)} 위 ${fmt(s[i])}% (차 ${sgn(f[i] - s[i])}%p, 1주 전 ${nn(f[i - 5]) && nn(s[i - 5]) ? sgn(f[i - 5] - s[i - 5]) : '-'}%p)${D.live ? ' · 오늘 잠정' : ''}`;
  // 최근 교차 표 + 이후 지수 수익률
  const rows = ev.slice(-12).reverse();
  const c = v => nn(v) ? `<span class="${v >= 0 ? 'pos' : 'neg'}">${v >= 0 ? '+' : ''}${v.toFixed(1)}%</span>` : '-';
  $('xTbl').innerHTML = '<tr><th class="nosort">날짜</th><th class="nosort">교차</th><th class="nosort">' + LBL(XF) + ' 위</th><th class="nosort">' + LBL(XS) + ' 위</th>' + (px ? `<th class="nosort">이후 ${XI} 5일</th><th class="nosort">20일</th>` : '') + '<th class="nosort">유지 거래일</th></tr>' +
    rows.map((e, k) => { const nx = k === 0 ? N - 1 : rows[k - 1].i; return `<tr><td>${D.dates[e.i]}</td><td style="color:${e.up ? '#ff8787' : '#74a7ff'}">${e.up ? '▲ 골드' : '▼ 데드'}</td><td>${fmt(f[e.i])}%</td><td>${fmt(s[e.i])}%</td>` +
      (px ? `<td>${c(fwd(px, e.i, 5))}</td><td>${c(fwd(px, e.i, 20))}</td>` : '') + `<td>${nx - e.i}${k === 0 ? '(진행 중)' : ''}</td></tr>`; }).join('');
  // 전체 기간 요약: 교차 뒤 지수 평균 vs 아무 날
  if (px) {
    const avg = a => { const b = a.filter(nn); return b.length ? b.reduce((x, y) => x + y, 0) / b.length : null; };
    const all20 = avg(D.dates.map((_, k) => fwd(px, k, 20))), g20 = avg(ev.filter(e => e.up).map(e => fwd(px, e.i, 20))), d20 = avg(ev.filter(e => !e.up).map(e => fwd(px, e.i, 20)));
    const yrs = N / 250;
    $('xStat').innerHTML = `${D.dates[0]} 이후: 골드 ${ev.filter(e => e.up).length}번 · 데드 ${ev.filter(e => !e.up).length}번(1년 약 ${(ev.length / 2 / yrs).toFixed(0)}번씩). 이후 20거래일 ${XI} 평균 - 골드 뒤 ${c(g20)}, 데드 뒤 ${c(d20)}, 아무 날 ${c(all20)}. ` +
      '<br><b>기본값 근거</b>(2016~, 골드 100% / 데드 60%, 3일에 나눠 실행·비용 포함, 조합 288개 비교): 20일/40일·5일 평활만 중소형·코스닥 × 2016~21·2022~ 네 칸 모두 그냥 보유보다 나았음(연 +0.8~+2.4%p, 최대낙폭 4~19%p 개선, 1년 약 12번 전환, 무작위 시점 대비 상위 1%). ' +
      '빠른 선 15~20일 × 느린 선 40~60일 근처가 대체로 좋았고, 5일·10일처럼 짧은 빠른 선은 교차가 잦아(5/15는 1년 47번) 어떤 조합이든 손해였습니다. 개선폭이 크지 않아 국면 판단의 보조 신호로 보세요.';
  } else $('xStat').innerHTML = '';
}
['xF', 'xS', 'xM', 'xT', 'xI'].forEach(id => $(id).onchange = () => { XF = $('xF').value; XS = $('xS').value; XM = +$('xM').value; XT = $('xT').value; XI = $('xI').value; renderCross(); });
$('xP').onclick = e => { const b = e.target.closest('button'); if (!b) return; XP = +b.dataset.p; document.querySelectorAll('#xP button').forEach(x => x.classList.toggle('active', x === b)); renderCross(); };
// ---------- 섹터 4사분면 - 2026-10-16 사용자 "섹터끼리의 변동, 4사분면에 어떤 섹터가 있는지"
// 가로 = 느린 선 위 비율(추세 수준, 50% 기준), 세로 = 빠른 - 느린(모멘텀, 0 기준). 교차 칸의 선택(XF·XS·XM)을 그대로 씀.
let QT = 10, QD = 0, qChart = null;   // QD = 마지막 날에서 며칠 전(0 = 최신)
let QF = '5', QS = '15', QM = 1;   // 4사분면 전용(2026-10-16 사용자 "15일로 해라" - 교차 칸의 20/40과 따로)
$('qF').innerHTML = XW.map(w => `<option value="${w}">${LBL(w)}</option>`).join(''); $('qF').value = QF;
$('qS').innerHTML = XW.map(w => `<option value="${w}">${LBL(w)}</option>`).join(''); $('qS').value = QS;
['qF', 'qS', 'qM'].forEach(id => $(id).onchange = () => { QF = $('qF').value; QS = $('qS').value; QM = +$('qM').value; renderQuad(); });
const QNAME = {1: '강세 지속', 2: '개선 중', 3: '약세', 4: '꺾이는 중'}, QCOL = {1: '#ff6b6b', 2: '#ffd43b', 3: '#4dabf7', 4: '#ffa94d'};
function quadOf(x, y) { return x >= 50 ? (y >= 0 ? 1 : 4) : (y >= 0 ? 2 : 3); }
function quadPts() {
  return D.sectors.map(sec => {
    const f = sma(sec['n' + QF] || [], QM), s = sma(sec['n' + QS] || [], QM);
    const at = k => { const i = N - 1 - QD - k; return i >= 0 && nn(f[i]) && nn(s[i]) ? {x: s[i], y: f[i] - s[i]} : null; };
    return {name: sec.name, n: sec.n, cur: at(0), wk: at(5), tail: QT ? [...Array(QT / 5 + 1).keys()].reverse().map(k => at(k * 5)).filter(Boolean) : []};
  }).filter(p => p.cur);
}
const quadBg = {id: 'quadBg', beforeDatasetsDraw(c) {
  const {ctx, chartArea: a, scales: {x, y}} = c, x0 = x.getPixelForValue(50), y0 = y.getPixelForValue(0);
  ctx.save();
  [[x0, a.top, a.right - x0, y0 - a.top, 1], [a.left, a.top, x0 - a.left, y0 - a.top, 2], [a.left, y0, x0 - a.left, a.bottom - y0, 3], [x0, y0, a.right - x0, a.bottom - y0, 4]].forEach(([l, t, w, h, q]) => {
    ctx.fillStyle = QCOL[q] + '12'; ctx.fillRect(l, t, w, h);
    ctx.fillStyle = QCOL[q] + 'aa'; ctx.font = 'bold 12px sans-serif'; ctx.textAlign = q === 1 || q === 4 ? 'right' : 'left'; ctx.textBaseline = q <= 2 ? 'top' : 'bottom';
    ctx.fillText(QNAME[q], q === 1 || q === 4 ? a.right - 6 : a.left + 6, q <= 2 ? a.top + 4 : a.bottom - 4);
  });
  ctx.strokeStyle = '#6b7280'; ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(x0, a.top); ctx.lineTo(x0, a.bottom); ctx.moveTo(a.left, y0); ctx.lineTo(a.right, y0); ctx.stroke();
  ctx.restore();
}, afterDatasetsDraw(c) {
  const {ctx} = c; ctx.save(); ctx.font = '11px sans-serif'; ctx.fillStyle = '#e6e6e6'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
  const ds = c.data.datasets.find(d => d.isCur); if (!ds) { ctx.restore(); return; }
  const meta = c.getDatasetMeta(c.data.datasets.indexOf(ds));
  meta.data.forEach((pt, i) => ctx.fillText(ds.data[i].name, pt.x + ds.data[i].r + 3, pt.y));
  ctx.restore();
}};
function renderQuad() {
  const qd = N - 1 - QD; $('qDay').max = Math.min(250, N - 1); $('qDay').value = QD;
  $('qDayLbl').textContent = D.dates[qd] + (D.live && QD === 0 ? ' 잠정' : '') + (QD ? ` (${QD}거래일 전)` : ' (최신)');
  const pts = quadPts(), maxN = Math.max(...pts.map(p => p.n));
  const ds = [];
  pts.forEach(p => { if (p.tail.length > 1) ds.push({type: 'line', data: p.tail, borderColor: QCOL[quadOf(p.cur.x, p.cur.y)] + '77', borderWidth: 1.2, pointRadius: p.tail.map((_, k) => k === p.tail.length - 1 ? 0 : 2), pointBackgroundColor: '#9aa0a688', tension: 0.3, label: '', isTail: true}); });
  ds.push({type: 'scatter', isCur: true, label: '섹터', data: pts.map(p => ({x: p.cur.x, y: p.cur.y, r: 4 + 8 * Math.sqrt(p.n / maxN), name: p.name, n: p.n})),
    pointRadius: pts.map(p => 4 + 8 * Math.sqrt(p.n / maxN)), backgroundColor: pts.map(p => QCOL[quadOf(p.cur.x, p.cur.y)] + 'cc'), borderColor: pts.map(p => selected && selected.has(p.name) ? '#ffffff' : '#0f1115'), borderWidth: pts.map(p => selected && selected.has(p.name) ? 2 : 1)});
  const ys = pts.flatMap(p => [p.cur.y, ...p.tail.map(t => t.y)]), ym = Math.max(10, Math.ceil(Math.max(...ys.map(Math.abs)) / 5) * 5 + 5);
  if (qChart) qChart.destroy();
  qChart = new Chart($('cQuad'), {type: 'scatter', data: {datasets: ds}, plugins: [quadBg], options: {responsive: true, maintainAspectRatio: false, animation: false,
    plugins: {legend: {display: false}, tooltip: {filter: c => c.dataset.isCur, callbacks: {label: c => { const d = c.raw; return `${d.name} (${d.n}종목): ${LBL(QS)} 위 ${d.x.toFixed(0)}%, ${LBL(QF)}-${LBL(QS)} ${d.y >= 0 ? '+' : ''}${d.y.toFixed(0)}%p`; }}}},
    onClick: (e, els) => { const el = els.find(x => qChart.data.datasets[x.datasetIndex].isCur); if (!el) return; const nm = qChart.data.datasets[el.datasetIndex].data[el.index].name; selected.has(nm) ? selected.delete(nm) : selected.add(nm); renderAll(); renderQuad(); },
    scales: {x: {type: 'linear', min: 0, max: 100, title: {display: true, text: LBL(QS) + ' 위 비율(%) - 추세 수준', color: '#9aa0a6'}, ticks: {color: '#9aa0a6'}, grid: {color: '#23262e'}},
      y: {type: 'linear', min: -ym, max: ym, title: {display: true, text: LBL(QF) + ' - ' + LBL(QS) + ' (%p) - 모멘텀', color: '#9aa0a6'}, ticks: {color: '#9aa0a6'}, grid: {color: '#23262e'}}}}});
  $('qDesc').innerHTML = `가로 = ${LBL(QS)} 위 비율(50% 기준), 세로 = ${LBL(QF)} 위 비율 − ${LBL(QS)} 위 비율${QM > 1 ? `(${QM}일 평균)` : ''}(0 기준, 위쪽 = 최근 개선). ` +
    '보통 개선 중(왼쪽 위) → 강세 지속(오른쪽 위) → 꺾이는 중(오른쪽 아래) → 약세(왼쪽 아래)로 시계 방향으로 돕니다. 꼬리 = 지난 이동 경로(5거래일 간격).';
  // 사분면별 표(1주 전 사분면에서 옮겨온 섹터 표시)
  const by = {1: [], 2: [], 3: [], 4: []};
  pts.forEach(p => by[quadOf(p.cur.x, p.cur.y)].push(p));
  const cell = q => by[q].sort((a, b) => b.cur.y - a.cur.y).map(p => { const w = p.wk ? quadOf(p.wk.x, p.wk.y) : q; return `<span style="display:inline-block;margin:2px 10px 2px 0;white-space:nowrap">${p.name}${w !== q ? ` <span style="color:${QCOL[w]};font-size:11px">← ${QNAME[w]}</span>` : ''}</span>`; }).join('') || '-';
  $('qTbl').innerHTML = [2, 1, 3, 4].map(q => `<tr><td style="width:110px;color:${QCOL[q]};font-weight:bold;vertical-align:top">${QNAME[q]} (${by[q].length})</td><td style="text-align:left;white-space:normal">${cell(q)}</td></tr>`).join('') +
    '<tr><td colspan="2" style="text-align:left;color:#7c828b;font-size:11px">← 표시 = 1주(5거래일) 전에는 다른 사분면에 있던 섹터</td></tr>';
}
$('qTail').onclick = e => { const b = e.target.closest('button'); if (!b) return; QT = +b.dataset.t; document.querySelectorAll('#qTail button').forEach(x => x.classList.toggle('active', x === b)); renderQuad(); };
$('qDay').oninput = () => { QD = +$('qDay').value; renderQuad(); };
$('segR').onclick = e => { const b = e.target.closest('button'); if (!b) return; RP = b.dataset.r; document.querySelectorAll('#segR button').forEach(x => x.classList.toggle('active', x === b)); renderRet(); };
renderRet();
renderAll();
renderCross();
renderQuad();


// ---------- 장중 실시간(잠정) 반영 - 2026-10-15 사용자 "실시간 정보가 차트 등 뭐든 바로 반영되게, 확정값은 그 날짜가 끝나면 적용".
// live/intraday.json(평일 09:05~15:35 15분마다 PC가 계산해 올림)의 오늘 값을 확정 시계열 끝에 붙여 다시 그린다.
// 페이지 확정 데이터가 그 날짜를 따라잡으면(다음 날 아침 정규 갱신) 붙이지 않음 = 확정값으로 자동 교체. 열어 두면 2분마다 새 값 확인.
let LIVE = null;
function liveLabel(j) { const t = (j.ts || '').slice(11, 16); return (t >= '15:30' ? '장 마감 ' : '장중 ') + t + ' 기준'; }
function liveBanner(j, on) {
  const el = document.getElementById('livePanel'); if (!el) return;
  if (!on) { el.style.display = 'none'; return; }
  const ix = k => { const v = j.index && j.index[k]; return v ? (k === 'KOSPI' ? '코스피 ' : '코스닥 ') + Number(v[0]).toLocaleString() + ' <span style="color:' + (v[1] >= 0 ? '#ff8787' : '#74a7ff') + '">' + (v[1] >= 0 ? '+' : '') + Number(v[1]).toFixed(2) + '%</span>' : ''; };
  el.style.display = '';
  el.innerHTML = '<div style="font-size:13px;line-height:1.7"><b style="color:#4dabf7">오늘(' + j.date.slice(5).replace('-', '/') + ') 잠정치 반영 중</b> · ' + liveLabel(j) + ' · ' + ix('KOSPI') + ' · ' + ix('KOSDAQ') +
    '<br><span style="color:#9aa0a6;font-size:12px">차트·카드·표의 마지막 날(' + j.date + ')이 잠정치입니다. 15분마다 다시 계산되고 페이지를 열어 두면 자동으로 바뀝니다. 확정값은 다음 날 아침 정규 갱신 때 들어오고 그때 잠정치는 빠집니다.</span></div>';
}
function livePoll(apply) {
  const go = () => fetch('live/intraday.json?t=' + Date.now()).then(r => r.ok ? r.json() : null).then(j => {
    if (j && (!LIVE || j.built !== LIVE.built)) { LIVE = j; apply(j); }
  }).catch(() => {});
  go();
  setInterval(() => { if (!document.hidden) go(); }, 120000);
}
const N0 = D.dates.length;
const KEYS = []; D.windows.concat(D.crossOnly || []).forEach(w => ['n', 'c'].forEach(k => KEYS.push(k + w)));
const STK0 = {}; Object.keys(D.stocks).forEach(c => STK0[c] = {cap: D.stocks[c].cap, g: D.stocks[c].g});
const RET0 = D.ret;
function applyLive(j) {
  D.dates.length = N0;
  KEYS.forEach(k => { if (D.all[k]) D.all[k].length = N0; D.sectors.forEach(s => { if (s[k]) s[k].length = N0; }); });
  Object.keys(STK0).forEach(c => { D.stocks[c].cap = STK0[c].cap; D.stocks[c].g = STK0[c].g; });
  D.ret = RET0; D.live = false;
  Object.values(D.idx || {}).forEach(a => a.length = N0);
  const L = j.sector;
  if (L && L.keys && j.date > D.asof) {
    const at = {}; L.keys.forEach((k, i) => at[k] = i);
    D.dates.push(j.date);
    KEYS.forEach(k => {
      if (D.all[k]) D.all[k].push(k in at ? L.all[at[k]] : null);
      D.sectors.forEach(s => { const v = L.sec[s.name]; if (s[k]) s[k].push(v && k in at ? v[at[k]] : null); });
    });
    Object.entries(L.stocks).forEach(([c, v]) => { const st = D.stocks[c]; if (!st) return; st.cap = v[0]; const g = {}; D.windows.forEach((w, i) => g[String(w)] = v[i + 1]); st.g = g; });
    if (L.ret) D.ret = L.ret;
    if (D.idx) { if (D.idx['코스피']) D.idx['코스피'].push(j.index.KOSPI ? j.index.KOSPI[0] : null); if (D.idx['코스닥']) D.idx['코스닥'].push(j.index.KOSDAQ ? j.index.KOSDAQ[0] : null); }
    D.live = true;
  }
  N = D.dates.length;
  renderRet(); renderAll(); renderCross(); renderQuad();
  liveBanner(j, D.live);
}
livePoll(applyLive);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
