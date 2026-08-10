"""
PER 밴드 + PBR 밴드를 함께 만든다 (기존 fetch_per_band.py를 대체).

기존 버전은 "연간(사업보고서) EPS 1개 점 x 최대 10개" 만 써서 표본이 너무 적고,
밴드선이 계단식으로 1년에 한 번만 바뀌어 주가와 잘 안 맞물리는 문제가 있었다.
이번엔 분기 단위(TTM EPS/최근 BS 자기자본)로 훨씬 촘촘한 표본을 만들어
"실제 그 종목이 과거에 거래됐던 PER/PBR 분포"에서 밴드 배수를 뽑는다.

방법:
1) 최근 4개년 x 4개 보고서(1분기/반기/3분기/사업보고서) 재무제표를 회사당 한 번씩만 조회해서
   - EPS(개별 분기, 손익계산서라 분기 단독값을 그대로 씀 - dart_client 참고)
   - 자본총계/지배주주지분(재무상태표, 그 시점 스냅샷이라 분기 구분 없이 그대로 씀)
   둘 다 같은 응답에서 뽑는다(추가 API 호출 없음).
2) TTM EPS = 최근 4개 분기 EPS 합. BPS = 그 시점 자본(지배주주지분 우선) / 발행주식수(최신 1회 조회).
3) 각 분기가 "실제 공시돼 있었을 시점"(공시 지연 반영: 분기말+45일, 사업보고서는 +90일)에
   맞춰 월별 주가와 매칭해 그 시점의 PER=주가/TTM EPS, PBR=주가/BPS 를 계산 -> 이 분포의
   최소~최대를 5단계로 등분한 게 밴드 배수. 계단이 아니라 분기마다 갱신되니 주가와 훨씬 잘 맞는다.

출력:
- data/screening/per_band.csv, data/screening/pbr_band.csv: 요약(목록 정렬용)
- docs/screening_data/<종목코드>.json: 상세 밴드 차트용(주가 + PER 밴드선 5개 + PBR 밴드선 5개)
"""
import json
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

from dart_client import DART_API_KEY, BASE_URL, extract_account, get_financials, load_corp_code_map

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
SCREEN_PATH = os.path.join(DATA_DIR, "screening", "op_growth_screen.csv")
PER_SUMMARY_PATH = os.path.join(DATA_DIR, "screening", "per_band.csv")
PBR_SUMMARY_PATH = os.path.join(DATA_DIR, "screening", "pbr_band.csv")
DETAIL_OUT_DIR = os.path.join(DOCS_DIR, "screening_data")

EPS_NAMES = ["기본주당이익(손실)", "기본주당이익"]
EQUITY_NAMES = ["지배기업의 소유주에게 귀속되는 자본", "지배기업 소유주지분", "자본총계"]

YEARS = [2023, 2024, 2025, 2026]
REPRT_CODES = ["11013", "11012", "11014", "11011"]
QUARTER_END = {"11013": (3, 31), "11012": (6, 30), "11014": (9, 30), "11011": (12, 31)}
REPORT_LAG_DAYS = {"11013": 45, "11012": 45, "11014": 45, "11011": 90}
N_BANDS = 5


def fetch_shares_outstanding(corp_code):
    """가장 최근에 조회되는 사업보고서 기준 보통주 발행주식수(자기주식 제외 유통주식수 우선)."""
    for year in sorted(YEARS, reverse=True):
        params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"}
        try:
            r = requests.get(f"{BASE_URL}/stockTotqySttus.json", params=params, timeout=20)
            data = r.json()
        except Exception:
            continue
        if data.get("status") != "000":
            continue
        for row in data.get("list", []):
            if row.get("se") == "보통주":
                raw = row.get("distb_stock_co") or row.get("istc_totqy")
                if raw and raw != "-":
                    try:
                        return int(raw.replace(",", ""))
                    except ValueError:
                        continue
    return None


def fetch_price_history(stock_code, count=3650):
    r = requests.get(
        "https://fchart.stock.naver.com/sise.nhn",
        params={"symbol": stock_code, "timeframe": "day", "count": count, "requestType": 0},
        timeout=20,
    )
    r.raise_for_status()
    rows = []
    for line in r.text.split('<item data="')[1:]:
        raw = line.split('"')[0]
        parts = raw.split("|")
        if len(parts) < 5:
            continue
        try:
            rows.append((datetime.strptime(parts[0], "%Y%m%d").date(), float(parts[4])))
        except ValueError:
            continue
    return rows


def monthly_close(price_history):
    by_month = {}
    for d, close in price_history:
        by_month[(d.year, d.month)] = (d, close)
    return sorted(by_month.values())


def price_at_or_before(price_history, target_date):
    candidates = [p for d, p in price_history if d <= target_date]
    return candidates[-1] if candidates else None


def collect_quarterly_fundamentals(corp_code):
    """분기별 discrete EPS와 그 시점 자본(BS) 스냅샷. {(year,reprt_code): (eps, equity, known_date)}"""
    out = {}
    for year in YEARS:
        for rc in REPRT_CODES:
            try:
                rows = get_financials(corp_code, year, rc)
            except Exception as e:
                print(f"    경고: {year} {rc} 조회 실패 ({e})")
                rows = []
            time.sleep(0.15)
            if not rows:
                continue
            eps = extract_account(rows, EPS_NAMES, sj_divs=("IS", "CIS"))
            equity = extract_account(rows, EQUITY_NAMES, sj_divs=("BS",))
            m, d = QUARTER_END[rc]
            quarter_end_date = datetime(year, m, d).date()
            known_date = quarter_end_date + timedelta(days=REPORT_LAG_DAYS[rc])
            out[(year, rc)] = (eps, equity, quarter_end_date, known_date)
    return out


def ttm_eps_series(quarterly):
    """(quarter_end_date, known_date, ttm_eps) 리스트. 사업보고서(11011)의 eps는 '연간 합계'라
    Q4 단독이 아니므로, TTM은 직전 3개 분기(Q1~Q3) + 사업보고서 annual_total - Q1~Q3합 로 구한다.
    """
    by_year = {}
    for (year, rc), (eps, equity, qend, known) in quarterly.items():
        by_year.setdefault(year, {})[rc] = (eps, qend, known)

    # 연도별 Q4(discrete) = 사업보고서 총합 - (1분기+반기+3분기 discrete 합)
    quarter_points = []  # (qend_date, known_date, discrete_eps)
    for year, rcs in by_year.items():
        q1 = rcs.get("11013", (None, None, None))[0]
        q2 = rcs.get("11012", (None, None, None))[0]
        q3 = rcs.get("11014", (None, None, None))[0]
        fy = rcs.get("11011", (None, None, None))[0]
        if "11013" in rcs:
            quarter_points.append((rcs["11013"][1], rcs["11013"][2], q1))
        if "11012" in rcs:
            quarter_points.append((rcs["11012"][1], rcs["11012"][2], q2))
        if "11014" in rcs:
            quarter_points.append((rcs["11014"][1], rcs["11014"][2], q3))
        if "11011" in rcs and fy is not None and None not in (q1, q2, q3):
            q4 = fy - q1 - q2 - q3
            quarter_points.append((rcs["11011"][1], rcs["11011"][2], q4))

    quarter_points = [p for p in quarter_points if p[2] is not None]
    quarter_points.sort(key=lambda p: p[0])

    ttm_points = []
    for i in range(3, len(quarter_points)):
        window = quarter_points[i - 3:i + 1]
        ttm = sum(w[2] for w in window)
        qend, known = quarter_points[i][0], quarter_points[i][1]
        ttm_points.append((qend, known, ttm))
    return ttm_points


def bps_series(quarterly, shares):
    if not shares:
        return []
    points = []
    for (year, rc), (eps, equity, qend, known) in quarterly.items():
        if equity is not None:
            points.append((qend, known, equity / shares))
    points.sort(key=lambda p: p[0])
    return points


def value_as_of(points, target_date):
    """points: (quarter_end, known_date, value) 정렬됨. target_date 이전에 이미 공시됐던(known_date<=target_date)
    것 중 가장 최근 값을 돌려준다."""
    candidates = [v for qend, known, v in points if known <= target_date]
    return candidates[-1] if candidates else None


def build_band(price_history, value_points, months, current_price):
    """value_points: ttm_eps_series 또는 bps_series 결과. 반환: (multiples, band_series_by_key, current_ratio, summary)"""
    if not value_points:
        return None  # EPS/BPS 자체를 전혀 못 구함(재무제표 조회 실패 등) - 진짜 데이터 없음

    ratios = []
    for d, close in months:
        v = value_as_of(value_points, d)
        if v is not None and v > 0:
            ratios.append(close / v)
    if not ratios:
        # 흑자전환 이력이 아예 없음(EPS/BPS가 계속 0 이하) - 빈 차트로 두지 않고 밴드선을
        # 0에 눌러서 그려 "밴드 맨 밑바닥"이라는 걸 그래프로도 보여준다.
        zero_line = [[d.isoformat(), 0.0] for d, _ in months]
        return [0.0], {"x0.0": zero_line}, {
            "current": None, "band_min": 0.0, "band_max": 0.0, "percentile": 0.0, "n_obs": 0,
        }

    latest_value = value_as_of(value_points, months[-1][0]) if months else None
    current_ratio = (current_price / latest_value) if latest_value and latest_value > 0 else None

    ratios_sorted = sorted(ratios)
    lo, hi = ratios_sorted[0], ratios_sorted[-1]
    if len(ratios_sorted) == 1:
        lo, hi = lo * 0.7, hi * 1.3
    if current_ratio is not None:
        lo, hi = min(lo, current_ratio * 0.95), max(hi, current_ratio * 1.05)
    multiples = [lo + (hi - lo) * k / (N_BANDS - 1) for k in range(N_BANDS)]

    band_series = {f"x{round(m, 2)}": [] for m in multiples}
    for d, close in months:
        v = value_as_of(value_points, d)
        valid_v = v if (v is not None and v > 0) else None
        for m in multiples:
            key = f"x{round(m, 2)}"
            band_series[key].append([d.isoformat(), round(valid_v * m, 1)] if valid_v is not None else [d.isoformat(), None])

    below = sum(1 for v in ratios_sorted if v <= current_ratio) if current_ratio is not None else None
    percentile = (below / len(ratios_sorted) * 100) if below is not None else None

    summary = {
        "current": round(current_ratio, 2) if current_ratio is not None else None,
        "band_min": round(ratios_sorted[0], 2),
        "band_max": round(ratios_sorted[-1], 2),
        "percentile": round(percentile, 1) if percentile is not None else None,
        "n_obs": len(ratios_sorted),
    }
    return [round(m, 2) for m in multiples], band_series, summary


def main():
    screened = pd.read_csv(SCREEN_PATH)
    names = screened["종목명"].tolist()
    print(f"대상 종목: {len(names)}개")

    name_to_code, _, name_to_stock_code = load_corp_code_map()
    os.makedirs(DETAIL_OUT_DIR, exist_ok=True)

    per_summary_rows = []
    pbr_summary_rows = []

    for i, name in enumerate(names, 1):
        corp_code = name_to_code.get(name)
        stock_code = name_to_stock_code.get(name)
        print(f"[{i}/{len(names)}] {name}")
        if not corp_code or not stock_code:
            per_summary_rows.append({"종목명": name, "상태": "code_not_found"})
            pbr_summary_rows.append({"종목명": name, "상태": "code_not_found"})
            continue

        quarterly = collect_quarterly_fundamentals(corp_code)
        shares = fetch_shares_outstanding(corp_code)
        time.sleep(0.15)

        try:
            price_history = fetch_price_history(stock_code)
            time.sleep(0.1)
        except Exception as e:
            print(f"  경고: {name} 주가 조회 실패 ({e})")
            per_summary_rows.append({"종목명": name, "상태": f"price_error: {e}"})
            pbr_summary_rows.append({"종목명": name, "상태": f"price_error: {e}"})
            continue

        if not price_history:
            per_summary_rows.append({"종목명": name, "상태": "no_price_data"})
            pbr_summary_rows.append({"종목명": name, "상태": "no_price_data"})
            continue

        months = monthly_close(price_history)
        current_price = price_history[-1][1]

        eps_points = ttm_eps_series(quarterly)
        per_result = build_band(price_history, eps_points, months, current_price)
        if per_result is None:
            per_summary_rows.append({"종목명": name, "상태": "no_data"})
            per_multiples, per_bands = None, None
        else:
            per_multiples, per_bands, s = per_result
            per_summary_rows.append({
                "종목명": name, "상태": "no_positive_ttm_eps" if s["n_obs"] == 0 else "ok",
                "현재PER": s["current"],
                "밴드_최저PER": s["band_min"], "밴드_최고PER": s["band_max"],
                "밴드내_위치_percentile": s["percentile"], "관측치수": s["n_obs"],
            })

        bps_points = bps_series(quarterly, shares)
        pbr_result = build_band(price_history, bps_points, months, current_price)
        if pbr_result is None:
            pbr_summary_rows.append({"종목명": name, "상태": "no_data" if shares else "shares_not_found"})
            pbr_multiples, pbr_bands = None, None
        else:
            pbr_multiples, pbr_bands, s = pbr_result
            pbr_summary_rows.append({
                "종목명": name, "상태": "no_positive_bps" if s["n_obs"] == 0 else "ok",
                "현재PBR": s["current"],
                "밴드_최저PBR": s["band_min"], "밴드_최고PBR": s["band_max"],
                "밴드내_위치_percentile": s["percentile"], "관측치수": s["n_obs"],
            })

        price_series = [[d.isoformat(), round(c, 1)] for d, c in months]
        detail = {"name": name, "stockCode": stock_code, "price": price_series}
        if per_multiples:
            detail["perMultiples"] = per_multiples
            detail["perBands"] = per_bands
        if pbr_multiples:
            detail["pbrMultiples"] = pbr_multiples
            detail["pbrBands"] = pbr_bands
        with open(os.path.join(DETAIL_OUT_DIR, f"{stock_code}.json"), "w", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False)

        if i % 10 == 0 or i == len(names):
            pd.DataFrame(per_summary_rows).to_csv(PER_SUMMARY_PATH, index=False, encoding="utf-8-sig")
            pd.DataFrame(pbr_summary_rows).to_csv(PBR_SUMMARY_PATH, index=False, encoding="utf-8-sig")
            print(f"  중간 저장 완료 ({i}/{len(names)})")

    pd.DataFrame(per_summary_rows).to_csv(PER_SUMMARY_PATH, index=False, encoding="utf-8-sig")
    pd.DataFrame(pbr_summary_rows).to_csv(PBR_SUMMARY_PATH, index=False, encoding="utf-8-sig")
    print(f"\n최종 저장 완료: {PER_SUMMARY_PATH}, {PBR_SUMMARY_PATH}, {DETAIL_OUT_DIR}/*.json")


if __name__ == "__main__":
    main()
