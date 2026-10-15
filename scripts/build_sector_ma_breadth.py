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


def sector_returns(members, usable, series):
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
    res = {"date": px.index[-1].strftime("%Y-%m-%d")}
    for key, k in RET_PERIODS.items():
        if len(px) <= k:
            continue
        rk = (np.expm1(lr.iloc[-k:].sum(min_count=1)) * 100).where(traded)
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


def main():
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
    asof = series.index.max()
    print(f"섹터 {len(members)}개, 종목 {len(codes)}개(시총 {caps.shape[1]}, 종가 대체 {len(codes) - caps.shape[1] - len(no_data)}, 데이터 없음 {len(no_data)}) | 기준일 {asof.date()}")

    hist = series.index >= pd.Timestamp(HISTORY_FROM)
    dates = series.index[hist]
    capw = caps.reindex(columns=series.columns)          # 가중치용(종가 대체 종목은 NaN -> 가중에서 제외)
    out = {"asof": asof.strftime("%Y-%m-%d"), "dates": [d.strftime("%Y-%m-%d") for d in dates], "windows": WINDOWS,
           "default": DEFAULT_WINDOW, "weak": {str(k): v for k, v in LONG_WEAK.items()}, "sectors": [], "all": {}, "stocks": {}}
    gaps = {}
    above_by_w = {}
    for w in WINDOWS:
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
    out["all"] = {f"{k}{w}": pack(ratio(usable, w, k)) for w in WINDOWS for k in ("n", "c")}
    rows = []
    for sec in sorted(members):
        cols = [c for c in members[sec] if c not in no_data]
        if len(cols) < MIN_MEMBERS:
            continue
        rec = {"name": sec, "n": len(cols), "codes": cols}
        for w in WINDOWS:
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
    out["ret"] = sector_returns(members, usable, series)
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

  <div class="cards" id="cards"></div>

  <div class="box">
    <h2>섹터 등락률 <span id="retSub" style="font-size:12px;font-weight:normal;color:#9aa0a6"></span></h2>
    <div class="ctl" style="margin-bottom:8px"><span class="seg" id="segR"><button data-r="1" class="active">1일</button><button data-r="5">1주</button><button data-r="20">1달</button></span><span id="retHdr"></span></div>
    <div id="retBars"></div>
  </div>

  <div class="box">
    <h2>추이 <span id="chartTitle" style="font-size:12px;font-weight:normal;color:#9aa0a6"></span></h2>
    <div class="desc">아래 표에서 섹터 앞 체크박스로 골라 추가합니다. 점선 = 섹터 정리 전체 종목. <b style="color:#ffa94d">높을수록 강함</b>(대부분 종목이 이평선 위). <span id="weakNote"></span></div>
    <div class="chart-wrap"><canvas id="cLine"></canvas></div>
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
const N = D.dates.length;
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
    `<div class="card">섹터 정리 전체 종목 · ${LBL(W)} 위 비율<div class="val">${fmt(cur)}%</div>1주 전 ${fmt(valAgo(D.all[key], i, 5))}% · 1달 전 ${fmt(valAgo(D.all[key], i, 21))}%</div>` +
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
  $('tbl').querySelectorAll('input.chk').forEach(cb => cb.onclick = e => { e.stopPropagation(); cb.checked ? selected.add(cb.dataset.name) : selected.delete(cb.dataset.name); renderChart(); });
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
  $('retSub').textContent = (RP === '1' ? D.ret.date : R.from + ' → ' + D.ret.date) + ' 종가 · 구성종목 단순평균 · 섹터 정리 기준';
  $('retHdr').innerHTML = R.hdr.map(h => `${h.name} <b>${h.v.toLocaleString(undefined, {maximumFractionDigits: 1})}</b> <span class="${h.ch >= 0 ? 'pos' : 'neg'}">${h.ch >= 0 ? '+' : ''}${h.ch.toFixed(1)}%</span>`).join(' &nbsp;·&nbsp; ');
  const mx = Math.max(...R.rows.map(x => Math.abs(x.r)), 0.1);
  $('retBars').innerHTML = R.rows.map(x => {
    const w = Math.abs(x.r) / mx * 40, pos = x.r >= 0, lbl = (pos ? '+' : '') + x.r.toFixed(1) + '%';
    const bar = pos ? `<span class="b" style="left:50%;width:${w}%;background:#ff6b6b"></span><span class="v pos" style="left:calc(50% + ${w}% + 4px)">${lbl}</span>`
                    : `<span class="b" style="right:50%;width:${w}%;background:#4dabf7"></span><span class="v neg" style="right:calc(50% + ${w}% + 4px)">${lbl}</span>`;
    return `<div class="rb"><span class="nm">${x.name}</span><span class="sb">${x.n}중 ${x.up}↑</span><span class="tr"><span class="z"></span>${bar}</span></div>`;
  }).join('');
}
$('segR').onclick = e => { const b = e.target.closest('button'); if (!b) return; RP = b.dataset.r; document.querySelectorAll('#segR button').forEach(x => x.classList.toggle('active', x === b)); renderRet(); };
renderRet();
renderAll();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
