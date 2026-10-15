"""
섹터 이평선 위 비율 실시간(장중) 스냅샷 - 2026-10-15 사용자 요청("섹터 이평선 위 비율을 실시간으로 따오지는 못하나?").

어제까지는 섹터 이평선 페이지와 같은 시계열(기업밴드찾기2 일별 시가총액 캐시, 없으면 종가)을 쓰고,
오늘 값만 네이버 모바일 API(m.stock.naver.com/api/stocks/marketValue/{KOSPI|KOSDAQ})의 현재 시가총액으로 붙여
이동평균선(20일·60일·20주·200일 등) 위 종목 비율을 섹터별로 다시 계산한다. 한 번에 약 20초.
결과: 화면 출력 + data/screening/sector_ma_breadth_live.csv (섹터, 이평선별 지금 비율, 어제 비율, 변화).
"""
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
import build_sector_ma_breadth as bsm  # noqa: E402
from build_relative_strength import assign_sectors  # noqa: E402

OUT_PATH = os.path.join(bsm.DATA_DIR, "screening", "sector_ma_breadth_live.csv")
LIVE_WINDOWS = [20, 60, "20w", 120, 200]


def fetch_live_caps():
    s = requests.Session()
    s.trust_env = False
    rows, ts = {}, None
    for mk in ("KOSPI", "KOSDAQ"):
        for page in range(1, 40):
            r = s.get(f"https://m.stock.naver.com/api/stocks/marketValue/{mk}", params={"page": page, "pageSize": 100},
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            st = r.json().get("stocks", [])
            if not st:
                break
            for x in st:
                try:
                    rows[x["itemCode"]] = float(str(x["marketValue"]).replace(",", "")) * 1e8   # 억원 -> 원(캐시와 같은 단위)
                    ts = x.get("localTradedAt") or ts
                except (KeyError, ValueError):
                    pass
            time.sleep(0.1)
    return pd.Series(rows), ts


def ma_of(series, w):
    if isinstance(w, str) and w.endswith("w"):
        n = int(w[:-1])
        wk = series.resample("W-FRI").last()
        prev = wk.shift(1).rolling(n - 1, min_periods=n - 1).sum()
        we = series.index.to_period("W-FRI").end_time.normalize()
        p = prev.reindex(we)
        p.index = series.index
        return (p + series) / n
    return series.rolling(w, min_periods=w).mean()


def compute(live):
    """live: 종목코드 -> 현재 시총(원). 섹터별 이평선 위 비율 표(지금/어제)와 시계열 마지막 확정일을 돌려준다."""
    sm = pd.read_csv(bsm.SECTOR_MAP_PATH, dtype=str)
    meta = pd.DataFrame({"name": sm.iloc[:, 1].values}, index=sm.iloc[:, 0].str.zfill(6).values)
    meta = meta[~meta.index.duplicated()]
    sectors, _ = assign_sectors(meta)
    members = {}
    for code, (sec, src) in sectors.items():
        if src == "U":
            members.setdefault(sec, []).append(code)
    codes = sorted({c for v in members.values() for c in v})
    caps, px, _ = bsm.load_series(codes)
    series = pd.concat([caps, px[[c for c in px.columns if c not in caps.columns]]], axis=1).ffill(limit=3)
    series = series.loc["2025-01-01":]
    today = pd.Timestamp(datetime.now().date())
    if series.index.max() >= today:
        series = series.loc[:today - pd.Timedelta(days=1)]
    # 종가 대체 종목은 시총 대신 비율로 붙임(어제 값 x 오늘 시총/어제 시총은 알 수 없으므로 시총 있는 종목만 실시간 반영)
    row = pd.Series(np.nan, index=series.columns)
    have = [c for c in series.columns if c in live.index and c in caps.columns]
    row[have] = live[have]
    series = pd.concat([series, row.to_frame(today).T])
    out = []
    ratios = {}
    for w in LIVE_WINDOWS:
        ma = ma_of(series, w)
        up = (series >= ma) & series.notna() & ma.notna()
        v = series.notna() & ma.notna()
        ratios[w] = (up, v)
    for sec in sorted(members):
        cs = [c for c in members[sec] if c in series.columns]
        rec = {"섹터": sec, "종목": len(cs)}
        for w in LIVE_WINDOWS:
            up, v = ratios[w]
            n = v[cs].sum(axis=1)
            r = (up[cs].sum(axis=1) / n.replace(0, np.nan) * 100).where(n >= 3)
            lbl = w if isinstance(w, str) else f"{w}일"
            rec[f"{lbl} 지금"] = round(float(r.iloc[-1]), 1) if r.iloc[-1] == r.iloc[-1] else None
            rec[f"{lbl} 어제"] = round(float(r.iloc[-2]), 1) if r.iloc[-2] == r.iloc[-2] else None
        out.append(rec)
    allc = [c for c in codes if c in series.columns]
    tot = {"섹터": "섹터 정리 전체", "종목": len(allc)}
    for w in LIVE_WINDOWS:
        up, v = ratios[w]
        lbl = w if isinstance(w, str) else f"{w}일"
        tot[f"{lbl} 지금"] = round(float(up[allc].iloc[-1].sum() / v[allc].iloc[-1].sum() * 100), 1)
        tot[f"{lbl} 어제"] = round(float(up[allc].iloc[-2].sum() / v[allc].iloc[-2].sum() * 100), 1)
    T = pd.DataFrame([tot] + out)
    T["20일 변화"] = T["20일 지금"] - T["20일 어제"]
    return T, series.index[-2], len(have)


def main():
    live, ts = fetch_live_caps()
    T, last_day, n_live = compute(live)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    T.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"실시간 기준 {ts} (어제까지 {last_day.date()} 시계열 + 오늘 현재 시총, 실시간 반영 {n_live}종목)")
    pd.set_option("display.width", 300)
    print(T.to_string(index=False))
    print(f"저장 완료: {OUT_PATH}")


if __name__ == "__main__":
    main()
