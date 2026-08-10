"""
코스피 선행 PER 트래커 - 매 실행마다(하루 1회 권장) 그날의 지수 선행/후행 PER을 계산해
data/kospi_per_tracker.csv 에 한 행씩 누적한다.

방법론:
1) KRX Open API(stk_bydd_trd)로 그날 코스피 전종목 종가/시가총액을 받아 우선주를 제외하고
   시가총액 상위 50종목을 뽑는다.
2) 각 종목의 컨센서스 EPS(당해년도 2026E, 차년도 2027E)와 최근 확정 실적 EPS(TTM 대용)를
   네이버(WiseReport) 컨센서스 API에서 가져온다. 무료/로그인 불필요, 개별종목 페이지가 쓰는
   바로 그 API다: /company/ajax/c1050001_data.aspx (연도별 실적표, (A)=확정 (E)=추정).
3) 종목별 PER = 종가 / EPS. 지수 대표값은 시가총액 가중 조화평균으로 묶는다:
     지수 PER = Σ(시총) / Σ(시총 / 종목PER)   (PER<=0인 적자 종목은 집계 제외)
4) 컨센서스 기반 선행 PER은 과거로 되돌릴 수 없어(그 시점 컨센서스를 알 수 없음) 이 스크립트를
   실행한 날짜부터 하루씩 누적된다. 이미 오늘자 행이 있으면 덮어써서(최신 확정치로 갱신) 최신화한다.

주의: 시가총액 상위 50 자체 계산이라 KRX 공식 지수 산출식과는 정확히 일치하지 않는 근사치다.
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
OUT_PATH = os.path.join(DATA_DIR, "kospi_per_tracker.csv")

KRX_OPEN_API_KEY = os.environ.get("KRX_OPEN_API_KEY")
STK_BYDD_TRD_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
WISEREPORT_CONSENSUS_URL = "https://navercomp.wisereport.co.kr/company/ajax/c1050001_data.aspx"

TOP_N = 50
PREF_SUFFIX_RE = re.compile(r"(\d?우[A-Z]?)$")  # 삼성전자우, 현대차2우B 등 우선주 이름 패턴


def latest_business_day():
    d = datetime.today()
    while d.weekday() >= 5:  # 토(5)/일(6)
        d = d - pd.Timedelta(days=1)
    return d.strftime("%Y%m%d")


def fetch_kospi_top50(basDd):
    if not KRX_OPEN_API_KEY:
        raise SystemExit("KRX_OPEN_API_KEY가 없습니다.")
    headers = {"AUTH_KEY": KRX_OPEN_API_KEY}
    d = basDd
    for _ in range(10):
        r = requests.get(STK_BYDD_TRD_URL, params={"basDd": d}, headers=headers, timeout=20)
        data = r.json().get("OutBlock_1", []) if r.status_code == 200 else []
        if data:
            break
        d = (datetime.strptime(d, "%Y%m%d") - pd.Timedelta(days=1)).strftime("%Y%m%d")
    else:
        raise SystemExit("최근 영업일 데이터를 못 찾았습니다.")

    rows = []
    for item in data:
        if item.get("MKT_NM") != "KOSPI":
            continue
        name = item.get("ISU_NM", "")
        if PREF_SUFFIX_RE.search(name):
            continue
        mktcap = pd.to_numeric(item.get("MKTCAP"), errors="coerce")
        price = pd.to_numeric(item.get("TDD_CLSPRC"), errors="coerce")
        if pd.isna(mktcap) or pd.isna(price):
            continue
        rows.append({"code": item.get("ISU_CD"), "name": name, "price": price, "mktcap": mktcap})

    df = pd.DataFrame(rows).sort_values("mktcap", ascending=False).head(TOP_N).reset_index(drop=True)
    return df, d


def fetch_consensus_eps(code, basDd):
    """반환: (trailing_eps, eps_2026, eps_2027) - 없으면 각각 None."""
    try:
        r = requests.get(
            WISEREPORT_CONSENSUS_URL,
            params={"flag": "2", "cmp_cd": code, "finGubun": "MAIN", "frq": "0", "sDT": basDd, "chartType": "svg"},
            headers={"User-Agent": "Mozilla/5.0", "Referer": f"https://navercomp.wisereport.co.kr/v2/company/c1050001.aspx?cmp_cd={code}"},
            timeout=15,
        )
        rows = r.json().get("JsonData", [])
    except Exception:
        return None, None, None

    def to_eps(v):
        try:
            return float(str(v).replace(",", ""))
        except (TypeError, ValueError):
            return None

    trailing_eps = None
    eps_2026 = eps_2027 = None
    for row in rows:
        yymm = row.get("YYMM", "")
        eps = to_eps(row.get("EPS"))
        if yymm.startswith("2026") and "(E)" in yymm:
            eps_2026 = eps
        elif yymm.startswith("2027") and "(E)" in yymm:
            eps_2027 = eps
        elif "(A)" in yymm:
            trailing_eps = eps  # 마지막 (A) 행이 가장 최근 확정연도로 덮어써짐(리스트가 연도 오름차순)
    return trailing_eps, eps_2026, eps_2027


def cap_weighted_harmonic_per(df, per_col):
    valid = df[df[per_col] > 0]
    if valid.empty:
        return None, 0
    denom = (valid["mktcap"] / valid[per_col]).sum()
    if denom == 0:
        return None, 0
    return valid["mktcap"].sum() / denom, len(valid)


def main():
    basDd = latest_business_day()
    print(f"기준일: {basDd}")
    top50, actual_basDd = fetch_kospi_top50(basDd)
    print(f"코스피 시총 상위 {len(top50)}종목 (실제 기준일 {actual_basDd})")

    trailing_epss, eps2026s, eps2027s = [], [], []
    for i, row in top50.iterrows():
        t, e26, e27 = fetch_consensus_eps(row["code"], actual_basDd)
        trailing_epss.append(t)
        eps2026s.append(e26)
        eps2027s.append(e27)
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(top50)} 종목 컨센서스 조회 완료")
        time.sleep(0.1)

    top50["trailing_eps"] = trailing_epss
    top50["eps_2026"] = eps2026s
    top50["eps_2027"] = eps2027s
    top50["per_trailing"] = top50["price"] / top50["trailing_eps"].replace(0, pd.NA)
    top50["per_2026"] = top50["price"] / top50["eps_2026"].replace(0, pd.NA)
    top50["per_2027"] = top50["price"] / top50["eps_2027"].replace(0, pd.NA)

    per_trailing, n_trailing = cap_weighted_harmonic_per(top50, "per_trailing")
    per_2026, n_2026 = cap_weighted_harmonic_per(top50, "per_2026")
    per_2027, n_2027 = cap_weighted_harmonic_per(top50, "per_2027")

    print(f"후행 PER: {per_trailing} ({n_trailing}종목) / 당해선행(2026E): {per_2026} ({n_2026}종목) / 차년선행(2027E): {per_2027} ({n_2027}종목)")

    record = {
        "date": pd.to_datetime(actual_basDd),
        "per_trailing": round(per_trailing, 3) if per_trailing else None,
        "per_2026e": round(per_2026, 3) if per_2026 else None,
        "per_2027e": round(per_2027, 3) if per_2027 else None,
        "n_trailing": n_trailing, "n_2026e": n_2026, "n_2027e": n_2027,
    }

    if os.path.exists(OUT_PATH):
        existing = pd.read_csv(OUT_PATH, parse_dates=["date"])
        existing = existing[existing["date"] != record["date"]]  # 오늘자 있으면 최신치로 교체
        combined = pd.concat([existing, pd.DataFrame([record])], ignore_index=True)
    else:
        combined = pd.DataFrame([record])

    combined = combined.sort_values("date").reset_index(drop=True)
    combined.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUT_PATH} (누적 {len(combined)}행)")


if __name__ == "__main__":
    main()
