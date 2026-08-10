"""
코스피/코스닥 선행 PER 트래커 공용 로직. fetch_kospi_per_tracker.py / fetch_kosdaq_per_tracker.py 에서 가져다 쓴다.

방법론:
1) KRX Open API(stk_bydd_trd=코스피, ksq_bydd_trd=코스닥)로 그날 전종목 종가/시가총액을 받아
   우선주를 제외하고 시가총액 상위 N종목을 뽑는다.
2) 각 종목의 컨센서스 EPS(당해년도 2026E, 차년도 2027E)와 최근 확정 실적 EPS(TTM 대용)를
   네이버(WiseReport) 컨센서스 API에서 가져온다. 무료/로그인 불필요, 개별종목 페이지가 쓰는
   바로 그 API다: /company/ajax/c1050001_data.aspx (연도별 실적표, (A)=확정 (E)=추정).
3) 종목별 PER = 종가 / EPS. 지수 대표값은 시가총액 가중 조화평균으로 묶는다:
     지수 PER = Σ(시총) / Σ(시총 / 종목PER)   (PER<=0인 적자 종목은 집계 제외)
4) 컨센서스 기반 선행 PER은 과거로 되돌릴 수 없어(그 시점 컨센서스를 알 수 없음) 실행한 날짜부터
   하루씩 누적된다. 이미 오늘자 행이 있으면 덮어써서(최신 확정치로 갱신) 최신화한다.

주의: 시가총액 상위 N 자체 계산이라 KRX 공식 지수 산출식과는 정확히 일치하지 않는 근사치다.
"""
import os
import re
import time
from datetime import datetime

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

KRX_OPEN_API_KEY = os.environ.get("KRX_OPEN_API_KEY")
WISEREPORT_CONSENSUS_URL = "https://navercomp.wisereport.co.kr/company/ajax/c1050001_data.aspx"
PREF_SUFFIX_RE = re.compile(r"(\d?우[A-Z]?)$")  # 삼성전자우, 현대차2우B 등 우선주 이름 패턴


def latest_business_day():
    d = datetime.today()
    while d.weekday() >= 5:  # 토(5)/일(6)
        d = d - pd.Timedelta(days=1)
    return d.strftime("%Y%m%d")


def fetch_top_n(basDd, market_url, market_name, top_n):
    if not KRX_OPEN_API_KEY:
        raise SystemExit("KRX_OPEN_API_KEY가 없습니다.")
    headers = {"AUTH_KEY": KRX_OPEN_API_KEY}
    d = basDd
    for _ in range(10):
        r = requests.get(market_url, params={"basDd": d}, headers=headers, timeout=20)
        data = r.json().get("OutBlock_1", []) if r.status_code == 200 else []
        if data:
            break
        d = (datetime.strptime(d, "%Y%m%d") - pd.Timedelta(days=1)).strftime("%Y%m%d")
    else:
        raise SystemExit("최근 영업일 데이터를 못 찾았습니다.")

    rows = []
    for item in data:
        if item.get("MKT_NM") != market_name:
            continue
        name = item.get("ISU_NM", "")
        if PREF_SUFFIX_RE.search(name):
            continue
        mktcap = pd.to_numeric(item.get("MKTCAP"), errors="coerce")
        price = pd.to_numeric(item.get("TDD_CLSPRC"), errors="coerce")
        if pd.isna(mktcap) or pd.isna(price):
            continue
        rows.append({"code": item.get("ISU_CD"), "name": name, "price": price, "mktcap": mktcap})

    df = pd.DataFrame(rows).sort_values("mktcap", ascending=False).head(top_n).reset_index(drop=True)
    return df, d


def fetch_consensus(code, basDd):
    """반환: (trailing_eps, eps_2026, eps_2027, trailing_bps, bps_2026, bps_2027) - 없으면 각각 None.
    같은 API 응답에 EPS와 BPS(주당순자산)가 함께 들어있어 한 번에 뽑는다(PER/PBR 공용)."""
    try:
        r = requests.get(
            WISEREPORT_CONSENSUS_URL,
            params={"flag": "2", "cmp_cd": code, "finGubun": "MAIN", "frq": "0", "sDT": basDd, "chartType": "svg"},
            headers={"User-Agent": "Mozilla/5.0", "Referer": f"https://navercomp.wisereport.co.kr/v2/company/c1050001.aspx?cmp_cd={code}"},
            timeout=15,
        )
        rows = r.json().get("JsonData", [])
    except Exception:
        return None, None, None, None, None, None

    def to_num(v):
        try:
            return float(str(v).replace(",", ""))
        except (TypeError, ValueError):
            return None

    trailing_eps = trailing_bps = None
    eps_2026 = eps_2027 = bps_2026 = bps_2027 = None
    for row in rows:
        yymm = row.get("YYMM", "")
        eps = to_num(row.get("EPS"))
        bps = to_num(row.get("BPS"))
        if yymm.startswith("2026") and "(E)" in yymm:
            eps_2026, bps_2026 = eps, bps
        elif yymm.startswith("2027") and "(E)" in yymm:
            eps_2027, bps_2027 = eps, bps
        elif "(A)" in yymm:
            trailing_eps, trailing_bps = eps, bps  # 마지막 (A) 행이 가장 최근 확정연도로 덮어써짐(리스트가 연도 오름차순)
    return trailing_eps, eps_2026, eps_2027, trailing_bps, bps_2026, bps_2027


def cap_weighted_harmonic_per(df, per_col):
    valid = df[df[per_col] > 0]
    if valid.empty:
        return None, 0
    denom = (valid["mktcap"] / valid[per_col]).sum()
    if denom == 0:
        return None, 0
    return valid["mktcap"].sum() / denom, len(valid)


def run_tracker(market_url, market_name, top_n, out_filename, label):
    out_path = os.path.join(DATA_DIR, out_filename)

    basDd = latest_business_day()
    print(f"기준일: {basDd}")
    top, actual_basDd = fetch_top_n(basDd, market_url, market_name, top_n)
    print(f"{label} 시총 상위 {len(top)}종목 (실제 기준일 {actual_basDd})")

    trailing_epss, eps2026s, eps2027s = [], [], []
    trailing_bpss, bps2026s, bps2027s = [], [], []
    for i, row in top.iterrows():
        t, e26, e27, tb, b26, b27 = fetch_consensus(row["code"], actual_basDd)
        trailing_epss.append(t)
        eps2026s.append(e26)
        eps2027s.append(e27)
        trailing_bpss.append(tb)
        bps2026s.append(b26)
        bps2027s.append(b27)
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(top)} 종목 컨센서스 조회 완료")
        time.sleep(0.1)

    top["trailing_eps"] = trailing_epss
    top["eps_2026"] = eps2026s
    top["eps_2027"] = eps2027s
    top["trailing_bps"] = trailing_bpss
    top["bps_2026"] = bps2026s
    top["bps_2027"] = bps2027s
    top["per_trailing"] = top["price"] / top["trailing_eps"].replace(0, pd.NA)
    top["per_2026"] = top["price"] / top["eps_2026"].replace(0, pd.NA)
    top["per_2027"] = top["price"] / top["eps_2027"].replace(0, pd.NA)
    top["pbr_trailing"] = top["price"] / top["trailing_bps"].replace(0, pd.NA)
    top["pbr_2026"] = top["price"] / top["bps_2026"].replace(0, pd.NA)
    top["pbr_2027"] = top["price"] / top["bps_2027"].replace(0, pd.NA)

    per_trailing, n_trailing = cap_weighted_harmonic_per(top, "per_trailing")
    per_2026, n_2026 = cap_weighted_harmonic_per(top, "per_2026")
    per_2027, n_2027 = cap_weighted_harmonic_per(top, "per_2027")
    pbr_trailing, np_trailing = cap_weighted_harmonic_per(top, "pbr_trailing")
    pbr_2026, np_2026 = cap_weighted_harmonic_per(top, "pbr_2026")
    pbr_2027, np_2027 = cap_weighted_harmonic_per(top, "pbr_2027")

    print(f"후행 PER: {per_trailing} ({n_trailing}종목) / 당해선행(2026E): {per_2026} ({n_2026}종목) / 차년선행(2027E): {per_2027} ({n_2027}종목)")
    print(f"후행 PBR: {pbr_trailing} ({np_trailing}종목) / 당해선행(2026E): {pbr_2026} ({np_2026}종목) / 차년선행(2027E): {pbr_2027} ({np_2027}종목)")

    record = {
        "date": pd.to_datetime(actual_basDd),
        "per_trailing": round(per_trailing, 3) if per_trailing else None,
        "per_2026e": round(per_2026, 3) if per_2026 else None,
        "per_2027e": round(per_2027, 3) if per_2027 else None,
        "n_trailing": n_trailing, "n_2026e": n_2026, "n_2027e": n_2027,
        "pbr_trailing": round(pbr_trailing, 3) if pbr_trailing else None,
        "pbr_2026e": round(pbr_2026, 3) if pbr_2026 else None,
        "pbr_2027e": round(pbr_2027, 3) if pbr_2027 else None,
        "np_trailing": np_trailing, "np_2026e": np_2026, "np_2027e": np_2027,
    }

    if os.path.exists(out_path):
        existing = pd.read_csv(out_path, parse_dates=["date"])
        existing = existing[existing["date"] != record["date"]]  # 오늘자 있으면 최신치로 교체
        combined = pd.concat([existing, pd.DataFrame([record])], ignore_index=True)
    else:
        combined = pd.DataFrame([record])

    combined = combined.sort_values("date").reset_index(drop=True)
    combined.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {out_path} (누적 {len(combined)}행)")
