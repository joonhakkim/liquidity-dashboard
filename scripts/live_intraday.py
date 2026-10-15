"""
장중 실시간(15분마다) 잠정치 - 2026-10-15 사용자 요청("별 두 개짜리까지는 그렇게 하자", 이어서 "실시간 정보가 차트 등 뭐든 바로 반영되게,
확정된 값은 그 날짜가 끝나면 적용").
네이버 모바일 API로 전 종목 현재가·등락률·시총과 코스피·코스닥 지수를 한 번 받아 오늘 하루치 값을 다시 계산하고
docs/live/intraday.json 하나로 저장한다. 각 페이지 JS가 이 파일을 읽어 확정 시계열 끝에 '오늘(잠정)' 값으로 붙인다
(차트·카드·표가 모두 그대로 반영됨). 페이지 확정 데이터가 그 날짜를 따라잡으면(다음 날 아침 정규 갱신) 잠정값은 자동으로 빠진다.
 1) 시장 국면: 밸류 하단 비율·공격-방어·120일선 위 비율과 각 점수(sC·sD·sX), 꺾임 판단 점수·주의선·꺾임 주의, 중소형 지수.
    느린 점수 국면(65일 EMA)은 페이지 JS가 오늘 점수를 넣어 다시 계산(아래 slow는 같은 식으로 계산한 로그용 값).
 2) 섹터 이평선 위 비율(모든 이평선, 종목 수·시총 가중) + 종목별 이격 + 섹터 등락률 - build_sector_ma_breadth.live_payload
    (페이지와 같은 계산 함수, 어제까지 시계열 x 오늘 등락률).
 3) OP밴드: 종목별 오늘 등락률(opret) - 페이지 JS가 현재 배수·바텀 대비 %를 다시 계산.
"""
import bisect
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
import build_market_phase as bmp  # noqa: E402
import build_sector_ma_breadth as bsm  # noqa: E402

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
OUT_PATH = os.path.join(BASE_DIR, "docs", "live", "intraday.json")
DAILY_PATH = os.path.join(BASE_DIR, "data", "screening", "market_phase_daily.csv")
OPB_PATH = os.path.join(BASE_DIR, "data", "screening", "op_band_summary.csv")


def fetch_snapshot():
    s = requests.Session()
    s.trust_env = False
    h = {"User-Agent": "Mozilla/5.0"}
    rows, ts = [], None

    def num(x):
        try:
            return float(str(x).replace(",", ""))
        except (TypeError, ValueError):
            return np.nan
    for mk in ("KOSPI", "KOSDAQ"):
        for page in range(1, 40):
            st = s.get(f"https://m.stock.naver.com/api/stocks/marketValue/{mk}", params={"page": page, "pageSize": 100}, headers=h, timeout=20).json().get("stocks", [])
            if not st:
                break
            for x in st:
                rows.append({"code": x.get("itemCode"), "name": x.get("stockName"), "ret": num(x.get("fluctuationsRatio")) / 100,
                             "cap": num(x.get("marketValue")), "market": mk})
                ts = max(ts or "", x.get("localTradedAt") or "") or None   # 가장 최근 체결 시각(거래 없는 종목은 옛 시각이라 마지막 값이 아니라 최댓값)
            time.sleep(0.1)
    idx = {}
    for i in ("KOSPI", "KOSDAQ"):
        j = s.get(f"https://m.stock.naver.com/api/index/{i}/basic", headers=h, timeout=15).json()
        idx[i] = [num(j.get("closePrice")), num(j.get("fluctuationsRatio"))]
    snap = pd.DataFrame(rows).dropna(subset=["code"]).drop_duplicates("code").set_index("code")
    return snap, idx, ts


def pct_vs(hist, x):
    """그 시점까지 과거 값 대비 백분위(운영 exp_pct와 같은 식: 오늘 값을 넣은 뒤 bisect_right / 개수)."""
    h = sorted(v for v in hist if v == v)
    return (bisect.bisect_right(h, x) + 1) / (len(h) + 1) * 100


def phase_live(snap, base, daily):
    Lt = base["L_tail"]; codes = Lt.columns
    ret = snap["ret"].reindex(codes)
    ret = ret.where(ret.abs() <= bmp.MAX_DAILY_MOVE)
    traded = ret.notna()
    # X: 120일선 위 비율
    L_today = Lt.iloc[-1] + np.log1p(ret.fillna(0))
    ma120 = (Lt.iloc[-119:].sum() + L_today) / 120
    act = base["active_last"].reindex(codes).fillna(False) & traded
    X = float(((L_today > ma120) & act).sum() / act.sum())
    # D: 공격-방어 1달(20거래일) 수익률 차
    R_hist = np.expm1(Lt.diff().iloc[-19:])
    R_all = pd.concat([R_hist, ret.fillna(0).to_frame(Lt.index[-1] + pd.Timedelta(days=1)).T])
    def basket(secs):
        sr = []
        for sec in secs:
            cs = [c for c in base["members"].get(sec, []) if c in codes and base["active_last"].get(c, False)]
            if len(cs) >= 3:
                sr.append(R_all[cs].mean(axis=1))
        return float(np.prod(1 + pd.concat(sr, axis=1).mean(axis=1).values) - 1)
    D = basket(base["aggr"]) - basket(base["dfn"])
    # C: 밸류 하단 비율(어제 워크북 시총 x (1 + 오늘 등락률) / 최신 12개월 선행 영업이익 < 3년 하위 10% 배수)
    fl = []
    for code, (op, p10, elig, cap_last) in base["val"].items():
        r = snap["ret"].get(code, np.nan)
        cap = cap_last * (1 + (r if r == r and abs(r) <= bmp.MAX_DAILY_MOVE else 0.0))
        if not elig or not (op > 0) or p10 != p10 or cap != cap:
            continue
        fl.append(cap / op < p10)
    C = float(np.mean(fl)) if fl else np.nan
    # 국면 점수 재료(페이지와 같은 식: 그날까지 과거 값 대비 백분위, 소수 둘째 자리)
    sC = round(100 - pct_vs(daily["C"].values, C), 2)
    sD = round(pct_vs(daily["D"].values, D), 2)
    sX = round(pct_vs(daily["X"].values, X), 2)
    w = bmp.WEIGHTS
    # 꺾임 판단용 일별 점수(밸류 하단 비율만 3일 평균)
    C3_hist = daily["C"].rolling(bmp.CAUTION_SMOOTH_C, min_periods=1).mean()
    C3 = float(np.mean([daily["C"].iloc[-2], daily["C"].iloc[-1], C]))
    sC3 = 100 - pct_vs(C3_hist.values, C3)
    raw = (sC3 * w["C"] + sD * w["D"] + sX * w["X"]) / sum(w.values())
    hist = daily["cau_raw"].dropna()
    mx = max(float(hist.iloc[-(bmp.CAUTION_WIN - 1):].max()), raw)
    prev = bool(daily["caution"].iloc[-1] == 1)
    if not prev and raw <= mx - bmp.CAUTION_DROP:
        cau = True
    elif prev and raw >= mx - bmp.CAUTION_REL:
        cau = False
    else:
        cau = prev
    # 느린 점수 국면(65일 EMA에 오늘 점수 한 번 더 - 기준선은 어제 값, 페이지 JS는 정확히 다시 계산)
    comp = (sC * w["C"] + sD * w["D"] + sX * w["X"]) / sum(w.values())
    sc = daily["score"].dropna()
    P = 5 if bmp.GRID == "D" else 1   # 1주 = 5거래일(일간 판정)
    L = float(sc.iloc[-1]) + 2.0 / (bmp.SMOOTH_WEEKS * P + 1) * (comp - float(sc.iloc[-1]))
    ph_y = int(daily["phase"].iloc[-1])
    lev = {1: 0, 6: 0, 2: 1, 5: 1, 3: 2, 4: 2}[ph_y]
    a_, b_ = float(daily["lo"].iloc[-1]), float(daily["hi"].iloc[-1])
    rawlv = 0 if L < a_ else (2 if L > b_ else 1)
    if abs(rawlv - lev) == 2:
        lev = rawlv
    elif rawlv > lev and L > (a_ if lev == 0 else b_) + bmp.LEVEL_BAND:
        lev = rawlv
    elif rawlv < lev and L < (b_ if lev == 2 else a_) - bmp.LEVEL_BAND:
        lev = rawlv
    rising = ph_y in (1, 2, 3)
    k = bmp.DIR_WEEKS * P
    chg = L - float(sc.iloc[-k]) if len(sc) >= k else 0.0
    if abs(chg) > bmp.DIR_DEADBAND:
        rising = chg > 0
    slow = (lev + 1) if rising else (6 - lev)
    down = cau or slow == 5
    lv2 = {1: 0, 6: 0, 2: 1, 5: 1, 3: 2, 4: 2}[slow]
    shown = (6 - lv2) if down else (lv2 + 1)
    three = 1 if shown <= 3 else (2 if shown == 4 else 3)
    return {"C": round(C * 100, 2), "D": round(D * 100, 2), "X": round(X * 100, 2), "sC": sC, "sD": sD, "sX": sX, "score": round(L, 2),
            "C_y": round(float(daily["C"].iloc[-1]) * 100, 1), "D_y": round(float(daily["D"].iloc[-1]) * 100, 1), "X_y": round(float(daily["X"].iloc[-1]) * 100, 1),
            "raw": round(raw, 1), "raw_y": round(float(hist.iloc[-1]), 1), "max20": round(mx, 1), "line": round(mx - bmp.CAUTION_DROP, 1),
            "release": round(mx - bmp.CAUTION_REL, 1), "caution": int(cau), "caution_y": int(prev), "slow": int(slow),
            "shown": int(shown), "three": three, "target": 60 if shown in (5, 6) else 100,
            "target_y": 60 if int(daily["stage"].iloc[-1]) == 1 else 100}


def sm_live(snap, base, daily):
    codes = base["L_tail"].columns
    ret = snap["ret"].reindex(codes)
    ret = ret.where(ret.abs() <= bmp.MAX_DAILY_MOVE)
    small = [c for c in codes if c not in base["large"]]
    r = float(ret[small].mean())
    hist = daily["sm_idx"].dropna()
    v = float(hist.iloc[-1]) * (1 + r)
    ma = (float(hist.iloc[-119:].sum()) + v) / 120
    ma_y = float(hist.iloc[-120:].mean())
    return {"idx": round(v, 2), "chg": round(r * 100, 2), "ma120": round(ma, 2), "below": bool(v < ma), "below_y": bool(hist.iloc[-1] < ma_y)}


def opband_live(snap):
    """OP밴드 종목의 오늘 등락률(%, 소수 둘째 자리) - 페이지 JS가 현재 배수 = 확정 배수 x (1 + 등락률)로 바텀 대비 %를 다시 계산."""
    o = pd.read_csv(OPB_PATH, dtype={"code": str})
    o["c6"] = o["code"].str[-6:]
    o["ret"] = o["c6"].map(snap["ret"])
    o = o[o["ret"].notna() & (o["ret"].abs() <= bmp.MAX_DAILY_MOVE)]
    live = o["latest_mult"] * (1 + o["ret"])
    ok = (o["b_3y_p10"] > 0) & (o["latest_mult"] > 0)
    below_now = int(((live < o["b_3y_p10"]) & ok).sum())
    below_y = int(((o["latest_mult"] < o["b_3y_p10"]) & ok).sum())
    return {"ret": {c: round(float(r) * 100, 2) for c, r in zip(o["c6"], o["ret"])}, "below_now": below_now, "below_y": below_y, "n": int(ok.sum())}


def build():
    snap, idx, ts = fetch_snapshot()
    base = pd.read_pickle(bmp.LIVE_BASE_PATH)
    daily = pd.read_csv(DAILY_PATH, parse_dates=["date"], index_col="date")
    daily = daily.loc[:base["asof"]]
    date = (ts or "")[:10]
    out = {"ts": ts, "date": date, "built": datetime.now().strftime("%Y-%m-%d %H:%M"), "base_date": str(base["asof"].date()),
           "index": idx, "n_live": int(snap["cap"].notna().sum())}
    out["phase"] = phase_live(snap, base, daily)
    out["sm"] = sm_live(snap, base, daily)
    out["sector"] = bsm.live_payload(snap["ret"], date, {"코스피": idx["KOSPI"][0], "코스닥": idx["KOSDAQ"][0]})
    out["opband"] = opband_live(snap)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"), default=lambda x: None if x != x else x)
    return out


if __name__ == "__main__":
    o = build()
    p = o["phase"]
    print(f"{o['ts']} | 코스피 {o['index']['KOSPI']} 코스닥 {o['index']['KOSDAQ']} | 기초값 {o['base_date']}")
    print(f"  꺾임 판단 점수 {p['raw']} (어제 {p['raw_y']}, 20일 고점 {p['max20']}, 켜지는 선 {p['line']}) -> 꺾임 주의 {'켜짐' if p['caution'] else '꺼짐'}(어제 {'켜짐' if p['caution_y'] else '꺼짐'})")
    print(f"  밸류 하단 {p['C']}% (어제 {p['C_y']}) · 공격-방어 {p['D']}%p (어제 {p['D_y']}) · 120일선 위 {p['X']}% (어제 {p['X_y']})")
    print(f"  국면(세부 {p['shown']}, 느린 {p['slow']}) -> 목표 비중 {p['target']}% (어제 {p['target_y']}%)")
    s = o["sm"]; print(f"  중소형 지수 {s['idx']} ({s['chg']:+.2f}%), 120일선 {s['ma120']} {'아래' if s['below'] else '위'}")
    ob = o["opband"]; print(f"  OP밴드 바텀 아래 종목 {ob['below_now']}개 (어제 {ob['below_y']}개, 대상 {ob['n']})")
    print(f"저장 완료: {OUT_PATH}")
