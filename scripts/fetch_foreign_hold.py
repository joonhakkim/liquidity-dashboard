"""
종목별 외국인 보유율(%) 일별 저장소 이어받기 - 2026-11-19 추가 (퀀트 종합 순위 상승 국면 가중치용)

출처: 네이버 모바일 API https://m.stock.naver.com/api/stock/{code}/trend?pageSize=N  (최근 N거래일, N 최대 60)
      응답의 foreignerHoldRatio("45.91%"). 연구용 스크립트(data/screening/cache/research/scripts/fetch_naver_trend.py)와 같은 경로.
저장: data/screening/cache/foreign_hold.pkl  - DataFrame(index=날짜, columns=6자리 코드, 값=보유율 %)
      처음 실행 때는 연구용 패널(research/panel.pkl, ~2026-10-19)의 2025년 이후 보유율로 시작하고 그 뒤를 이어받는다.
대상: 기업 밴드 찾기 2 종목 중 최근 시총 300억 이상(퀀트 순위 시총 하한보다 넉넉하게).
속도: 스레드 4개 + 요청마다 0.15초 쉼 - 1,500종목 기준 2~4분. 장 마감 뒤(아침 파이프라인)에만 실행.
"""
import os
import pickle
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
CACHE = os.path.join(BASE_DIR, "data", "screening", "cache")
STORE = os.path.join(CACHE, "foreign_hold.pkl")
FLOW_STORE = os.path.join(CACHE, "naver_flow.pkl")   # 외국인·기관·개인 순매수·거래대금(원) - 퀀트 순위 하락형 수급 항목용
PANEL = os.path.join(CACHE, "research", "panel.pkl")
BAND_STORE = os.path.join(CACHE, "band_store.pkl")
H = {"User-Agent": "Mozilla/5.0"}
MIN_CAP = 300e8
lock = threading.Lock()
errs = [0]


def num(s):
    try:
        return float(str(s).replace(",", "").replace("%", "").replace("+", ""))
    except Exception:
        return np.nan


def target_codes():
    sh = pickle.load(open(BAND_STORE, "rb"))["sheets"]["기업밴드"]
    head, mat = sh["head"], sh["mat"]
    codes = []
    for k in range(len(head[7]) - 1):
        code, item = head[7][k + 1], head[9][k + 1]
        if not code or item != "S102100":
            continue
        col = pd.Series(mat[:, k]).dropna()
        if len(col) and col.iloc[-1] >= MIN_CAP:
            codes.append(str(code)[-6:])
    return sorted(set(codes))


def load_store():
    if os.path.exists(STORE):
        return pd.read_pickle(STORE)
    if os.path.exists(PANEL):
        fh = pd.read_pickle(PANEL)["FH"]
        fh = fh.loc["2025-01-01":].dropna(how="all", axis=1).dropna(how="all")
        print(f"처음 실행: 연구용 패널에서 {fh.index[0].date()}~{fh.index[-1].date()} 보유율 {fh.shape[1]}종목으로 시작")
        return fh.astype("float32")
    return pd.DataFrame(dtype="float32")


def load_flow():
    """수급 저장소 {F, I, P, tv}: 외국인·기관·개인 순매수 금액(원, 수량×종가), 거래대금(원, 거래량×종가).
    처음엔 연구용 패널(~2026-10-19, KRX 거래대금)의 2026년분으로 시작."""
    if os.path.exists(FLOW_STORE):
        return pd.read_pickle(FLOW_STORE)
    if os.path.exists(PANEL):
        p = pd.read_pickle(PANEL)
        out = {k: p[k].loc["2026-01-01":].dropna(how="all", axis=1).astype("float64") for k in ("F", "I", "P", "tv")}
        print(f"수급 처음 실행: 연구용 패널에서 {out['F'].index[0].date()}~{out['F'].index[-1].date()}로 시작")
        return out
    return {k: pd.DataFrame(dtype="float64") for k in ("F", "I", "P", "tv")}


def one(args):
    code, n = args
    u = f"https://m.stock.naver.com/api/stock/{code}/trend?pageSize={n}"
    for k in range(3):
        try:
            r = requests.get(u, headers=H, timeout=15)
            if r.status_code == 200:
                time.sleep(0.15)
                d = {}
                for x in r.json():
                    px = num(x.get("closePrice"))
                    d[pd.Timestamp(x["bizdate"])] = (num(x.get("foreignerHoldRatio")), num(x.get("foreignerPureBuyQuant")) * px,
                                                    num(x.get("organPureBuyQuant")) * px, num(x.get("individualPureBuyQuant")) * px,
                                                    num(x.get("accumulatedTradingVolume")) * px)
                return code, d
            if r.status_code in (400, 404):
                return code, {}
        except Exception:
            pass
        time.sleep(1 + 2 * k)
    with lock:
        errs[0] += 1
    return code, None


def main():
    st = load_store()
    fl = load_flow()
    codes = target_codes()
    last = st.index.max() if len(st) else pd.Timestamp("2025-01-01")
    flast = fl["F"].index.max() if len(fl["F"]) else pd.Timestamp("2025-01-01")
    last = min(last, flast)   # 수급 저장소가 더 뒤처져 있으면 그만큼 더 받음
    gap_days = int(np.busday_count(last.date(), pd.Timestamp.today().date()))
    n = int(min(60, max(5, gap_days + 5)))
    print(f"저장소 마지막 {last.date()} | 대상 {len(codes)}종목 | 종목당 최근 {n}거래일 요청")
    t0 = time.time()
    got = {}
    with ThreadPoolExecutor(4) as ex:
        for i, (code, d) in enumerate(ex.map(one, [(c, n) for c in codes]), 1):
            if d:
                got[code] = d
            if errs[0] > 50:
                print("오류가 너무 많아 중단(저장 안 함)")
                sys.exit(1)
            if i % 300 == 0:
                print(f"  {i}/{len(codes)} 오류 {errs[0]} {time.time() - t0:.0f}s", flush=True)
    part = lambda j: pd.DataFrame({c: {t: v[j] for t, v in d.items()} for c, d in got.items()}).sort_index()  # noqa: E731
    new = part(0).astype("float32")
    if new.empty:
        print("받은 데이터 없음")
        return
    # 수급(2026-11-26 추가, 퀀트 순위 하락형용): 같은 응답의 외국인·기관·개인 순매수, 거래대금
    flo = {}
    for j, k in ((1, "F"), (2, "I"), (3, "P"), (4, "tv")):
        nk = part(j)
        flo[k] = (nk.combine_first(fl[k]) if len(fl[k]) else nk).sort_index().loc["2026-01-01":]
        flo[k].index.name = None
    pickle.dump(flo, open(FLOW_STORE, "wb"))
    print(f"수급 저장소 {flo['F'].index[0].date()}~{flo['F'].index[-1].date()} {flo['F'].shape}")
    # 새로 받은 값이 우선(같은 날짜는 덮어씀)
    out = new.combine_first(st) if len(st) else new
    out = out.sort_index()
    out.index.name = None
    out.to_pickle(STORE)
    latest = new.index.max()
    print(f"완료 {len(got)}종목, 오류 {errs[0]} | 최신 {latest.date()} 보유율 있는 종목 {int(new.loc[latest].notna().sum())} | "
          f"저장소 {out.index[0].date()}~{out.index[-1].date()} {out.shape} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
