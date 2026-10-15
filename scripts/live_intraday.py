"""
장중 실시간(15분마다) 잠정치 - 2026-10-15 사용자 요청("별 두 개짜리까지는 그렇게 하자").
네이버 모바일 API로 전 종목 현재가·등락률·시총과 코스피·코스닥 지수를 한 번 받아 아래 다섯 가지를 다시 계산하고
docs/live/intraday.json 하나로 저장한다(각 페이지가 이 파일을 읽어 '장중 잠정' 칸에 표시).
 1) 시장 국면 꺾임 주의·대응 단계 미리보기: 아침 빌드가 저장한 기초값(phase_live_base.pkl)에 오늘 등락을 붙여
    밸류 하단 비율·공격-방어·120일선 위 비율 -> 일별 점수(꺾임 판단용) -> 꺾임 주의 -> 오늘 종가로 마감하면 될 대응 단계.
    느린 점수 국면(65일 평균)은 어제 확정값을 그대로 씀(하루 사이 거의 안 바뀜).
 2) 섹터 이평선 위 비율(20일·20주·60일·120일·200일, 지금 vs 어제) - live_sector_breadth.compute
 3) 섹터 등락률(오늘, 구성종목 단순평균)
 4) 중소형 지수(시총 상위 100 제외 동일가중)와 120일선
 5) OP밴드 3년 하위 10% 바텀 대비 %(장중 시총으로) - 바텀 아래 종목 수와 가까운 순 목록
장 마감 뒤 확정치는 다음 날 아침 정규 갱신이 기준이다.
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
import live_sector_breadth as lsb  # noqa: E402

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
    # C: 밸류 하단 비율(장중 시총 / 최신 12개월 선행 영업이익 < 3년 하위 10% 배수)
    fl = []
    for code, (op, p10, elig, cap_last) in base["val"].items():
        cap = snap["cap"].get(code, np.nan)
        if not elig or not (op > 0) or p10 != p10 or cap != cap:
            continue
        fl.append(cap / op < p10)
    C = float(np.mean(fl)) if fl else np.nan
    # 점수(꺾임 판단용: 밸류 하단 비율 3일 평균) - 과거 분포 대비 백분위
    C3_hist = daily["C"].rolling(bmp.CAUTION_SMOOTH_C, min_periods=1).mean()
    C3 = float(np.mean([daily["C"].iloc[-2], daily["C"].iloc[-1], C]))
    sC3 = 100 - pct_vs(C3_hist.values, C3)
    sD = pct_vs(daily["D"].values, D)
    sX = pct_vs(daily["X"].values, X)
    w = bmp.WEIGHTS
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
    slow = int(daily["phase"].iloc[-1])
    lev = {1: 0, 6: 0, 2: 1, 5: 1, 3: 2, 4: 2}[slow]
    down = cau or slow == 5
    shown = (6 - lev) if down else (lev + 1)
    three = 1 if shown <= 3 else (2 if shown == 4 else 3)
    return {"C": round(C * 100, 1), "D": round(D * 100, 1), "X": round(X * 100, 1),
            "C_y": round(float(daily["C"].iloc[-1]) * 100, 1), "D_y": round(float(daily["D"].iloc[-1]) * 100, 1), "X_y": round(float(daily["X"].iloc[-1]) * 100, 1),
            "raw": round(raw, 1), "raw_y": round(float(hist.iloc[-1]), 1), "max20": round(mx, 1), "line": round(mx - bmp.CAUTION_DROP, 1),
            "release": round(mx - bmp.CAUTION_REL, 1), "caution": int(cau), "caution_y": int(prev), "slow": slow,
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
    o = pd.read_csv(OPB_PATH, dtype={"code": str})
    o["c6"] = o["code"].str[-6:]
    o["cap_live"] = o["c6"].map(snap["cap"]) * 1e8
    o["ret"] = o["c6"].map(snap["ret"]) * 100
    o = o.dropna(subset=["cap_live", "latest_mult", "b_3y_p10", "latest_mktcap"])
    o = o[(o["b_3y_p10"] > 0) & (o["latest_mult"] > 0)]
    o["gap_live"] = (o["latest_mult"] * o["cap_live"] / o["latest_mktcap"] / o["b_3y_p10"] - 1) * 100
    top = o.sort_values("gap_live").head(40)
    return {"below_now": int((o["gap_live"] < 0).sum()), "below_y": int((o["gap_3y_p10"] < 0).sum()), "n": int(len(o)),
            "list": [{"code": r.c6, "name": r.name if isinstance(r.name, str) else "", "sector": r.sector if isinstance(r.sector, str) else "",
                      "gap": round(r.gap_live, 1), "gap_y": round(r.gap_3y_p10, 1) if r.gap_3y_p10 == r.gap_3y_p10 else None,
                      "ret": round(r.ret, 1) if r.ret == r.ret else None} for r in top.itertuples()]}


def build():
    snap, idx, ts = fetch_snapshot()
    base = pd.read_pickle(bmp.LIVE_BASE_PATH)
    daily = pd.read_csv(DAILY_PATH, parse_dates=["date"], index_col="date")
    daily = daily.loc[:base["asof"]]
    out = {"ts": ts, "date": (ts or "")[:10], "built": datetime.now().strftime("%Y-%m-%d %H:%M"), "base_date": str(base["asof"].date()),
           "index": idx, "n_live": int(snap["cap"].notna().sum())}
    out["phase"] = phase_live(snap, base, daily)
    out["sm"] = sm_live(snap, base, daily)
    T, last_day, n_live = lsb.compute(snap["cap"].dropna() * 1e8)
    members = {}
    for name in T["섹터"]:
        pass
    sec_ret = {}
    for sec, cs in base["members"].items():
        r = snap["ret"].reindex(cs).dropna()
        if len(r) >= 3:
            sec_ret[sec] = round(float(r.mean() * 100), 2)
    rows = []
    for r in T.to_dict("records"):
        rows.append({"name": r["섹터"], "n": int(r["종목"]), "r1d": sec_ret.get(r["섹터"]),
                     **{k: (None if r.get(f"{lb} 지금") is None or r.get(f"{lb} 지금") != r.get(f"{lb} 지금") else r.get(f"{lb} 지금")) for k, lb in (("w20", "20일"), ("w20w", "20w"), ("w60", "60일"), ("w200", "200일"))},
                     **{k + "_y": (None if r.get(f"{lb} 어제") != r.get(f"{lb} 어제") else r.get(f"{lb} 어제")) for k, lb in (("w20", "20일"), ("w20w", "20w"), ("w60", "60일"), ("w200", "200일"))}})
    out["sector"] = rows
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
