"""
종목별 일별 거래대금(원) 저장소 - 섹터 거래대금 페이지(build_sector_trading_value.py)용 (2026-10-29 사용자 요청).
data/screening/cache/trading_value.pkl = DataFrame(index=날짜, columns=종목코드 6자리, 값=ACC_TRDVAL 원).
- 처음엔 연구용 KRX 캐시(research/krx/{YYYYMMDD}_{stk|ksq}.pkl, 2010~2026-10-19)에서 2023-01-01부터 채우고,
- 그 뒤 비어 있는 영업일은 KRX Open API(stk_bydd_trd=코스피, ksq_bydd_trd=코스닥)로 이어받는다.
- 휴장일(양 시장 모두 빈 응답)은 'empty'에 기억해서 다시 조회하지 않는다. 오늘(장 마감 전) 값은 넣지 않는다.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import holidays
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()
BASE = os.path.join(os.path.dirname(__file__), "..")
STORE = os.path.join(BASE, "data", "screening", "cache", "trading_value.pkl")
HIST_DIR = os.path.join(BASE, "data", "screening", "cache", "research", "krx")
START = date(2023, 1, 2)
EP = {"stk": "stk_bydd_trd", "ksq": "ksq_bydd_trd"}


def load_store():
    if os.path.exists(STORE):
        return pd.read_pickle(STORE)
    return {"df": pd.DataFrame(), "empty": set()}


def from_cache(d):
    """연구용 캐시에서 하루치. 없으면 None, 휴장(빈 파일)이면 빈 Series."""
    parts = []
    found = 0
    for ep in EP:
        p = os.path.join(HIST_DIR, f"{d:%Y%m%d}_{ep}.pkl")
        if not os.path.exists(p):
            continue
        found += 1
        x = pd.read_pickle(p)
        if len(x) and "ACC_TRDVAL" in x.columns:
            parts.append(pd.Series(pd.to_numeric(x["ACC_TRDVAL"], errors="coerce").values, index=x["ISU_CD"].astype(str).str.zfill(6)))
    if found < 2:
        return None
    return pd.concat(parts) if parts else pd.Series(dtype=float)


def from_api(d):
    key = os.environ.get("KRX_OPEN_API_KEY")
    if not key:
        raise SystemExit("KRX_OPEN_API_KEY가 .env에 설정되어 있지 않습니다.")
    parts = []
    for ep in EP.values():
        for k in range(4):
            try:
                r = requests.get(f"https://data-dbg.krx.co.kr/svc/apis/sto/{ep}", params={"basDd": f"{d:%Y%m%d}"}, headers={"AUTH_KEY": key}, timeout=30)
                if r.status_code != 200:
                    raise RuntimeError(r.status_code)
                rows = r.json().get("OutBlock_1", [])
                break
            except Exception:
                rows = None
                time.sleep(2 + 3 * k)
        if rows is None:
            return None            # 한 시장이라도 못 받으면 오늘은 건너뜀(다음 실행 때 다시)
        if rows:
            x = pd.DataFrame(rows)
            parts.append(pd.Series(pd.to_numeric(x["ACC_TRDVAL"], errors="coerce").values, index=x["ISU_CD"].astype(str).str.zfill(6)))
    return pd.concat(parts) if parts else pd.Series(dtype=float)


def business_days(start, end):
    kr = holidays.KR(years=range(start.year, end.year + 1))
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in kr:
            yield d
        d += timedelta(days=1)


def main():
    st = load_store()
    df, empty = st["df"], st["empty"]
    have = set(df.index)
    today = datetime.now().date()
    todo = [d for d in business_days(START, today - timedelta(days=0)) if pd.Timestamp(d) not in have and d not in empty]
    if datetime.now().hour < 20:    # 오늘 확정치는 저녁에야 나옴
        todo = [d for d in todo if d < today]
    print(f"채울 날짜 {len(todo)}일")
    rows = {}

    def one(d):
        s = from_cache(d)
        return d, (s if s is not None else from_api(d))

    with ThreadPoolExecutor(4) as ex:
        for d, s in ex.map(one, todo):
            if s is None:
                print(f"  {d} 못 받음(다음에 재시도)")
            elif len(s) == 0:
                empty.add(d)
            else:
                rows[pd.Timestamp(d)] = s[~s.index.duplicated()]
    if rows:
        new = pd.DataFrame(rows).T
        df = pd.concat([df, new]).sort_index()
        df = df[~df.index.duplicated(keep="last")]
    pd.to_pickle({"df": df.astype("float64"), "empty": empty}, STORE)
    print(f"저장 완료: {STORE} ({df.shape[0]}일 x {df.shape[1]}종목, 마지막 {df.index.max():%Y-%m-%d})")


if __name__ == "__main__":
    main()
