"""
op_growth_screen.csv 116개 종목에 대해 5~10년 PER 밴드와 현재 위치를 계산한다.

방법(간이):
1) DART 사업보고서(연간)에서 최근 10개년 '기본주당이익(EPS)' 을 가져온다.
2) 네이버 증권 일별시세(fchart.stock.naver.com)에서 10년치 일봉을 가져와
   각 회계연도 말(가장 가까운 직전 거래일) 종가를 매칭한다.
3) 연도별 PER = 그 해 말 종가 / 그 해 EPS (EPS<=0인 해는 제외 - 적자라 PER 의미없음).
4) 현재 PER = 최근 종가 / 최근 확정 연간 EPS.
5) 밴드 내 위치(percentile) = 현재 PER이 과거 연도별 PER 분포에서 몇 %ile인지
   (낮을수록 "밴드 하단"에 가깝다는 뜻).

주의: 정식 PER 밴드(월별/분기별 롤링 트레일링 PER)보다 거친 근사치다 - 연말 스냅샷 기준.
"""
import os
import time
from datetime import datetime

import pandas as pd
import requests

from dart_client import extract_account, get_financials, load_corp_code_map

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SCREEN_PATH = os.path.join(DATA_DIR, "screening", "op_growth_screen.csv")
OUT_PATH = os.path.join(DATA_DIR, "screening", "per_band.csv")

EPS_NAMES = ["기본주당이익(손실)", "기본주당이익"]
CURRENT_YEAR = 2026
YEARS = list(range(CURRENT_YEAR - 10, CURRENT_YEAR))  # 최근 10개년


def fetch_price_history(stock_code, count=3650):
    r = requests.get(
        "https://fchart.stock.naver.com/sise.nhn",
        params={"symbol": stock_code, "timeframe": "day", "count": count, "requestType": 0},
        timeout=20,
    )
    r.raise_for_status()
    text = r.text
    rows = []
    for line in text.split('<item data="')[1:]:
        raw = line.split('"')[0]
        parts = raw.split("|")
        if len(parts) < 5:
            continue
        date_s, o, h, l, c = parts[0], parts[1], parts[2], parts[3], parts[4]
        try:
            rows.append((datetime.strptime(date_s, "%Y%m%d").date(), float(c)))
        except ValueError:
            continue
    return rows  # [(date, close), ...] 오름차순


def price_at_or_before(price_history, target_date):
    candidates = [p for d, p in price_history if d <= target_date]
    return candidates[-1] if candidates else None


def main():
    screened = pd.read_csv(SCREEN_PATH)
    names = screened["종목명"].tolist()
    print(f"대상 종목: {len(names)}개")

    name_to_code, _, name_to_stock_code = load_corp_code_map()

    rows_out = []
    for i, name in enumerate(names, 1):
        corp_code = name_to_code.get(name)
        stock_code = name_to_stock_code.get(name)
        print(f"[{i}/{len(names)}] {name}")
        if not corp_code or not stock_code:
            rows_out.append({"종목명": name, "상태": "code_not_found"})
            continue

        eps_by_year = {}
        for year in YEARS:
            try:
                rows = get_financials(corp_code, year, "11011")
                time.sleep(0.15)
            except Exception as e:
                print(f"  경고: {name} {year} EPS 조회 실패 ({e})")
                continue
            if not rows:
                continue
            eps = extract_account(rows, EPS_NAMES)
            if eps is not None:
                eps_by_year[year] = eps

        if not eps_by_year:
            rows_out.append({"종목명": name, "상태": "no_eps_data"})
            continue

        try:
            price_history = fetch_price_history(stock_code)
            time.sleep(0.1)
        except Exception as e:
            print(f"  경고: {name} 주가 조회 실패 ({e})")
            rows_out.append({"종목명": name, "상태": f"price_error: {e}"})
            continue

        if not price_history:
            rows_out.append({"종목명": name, "상태": "no_price_data"})
            continue

        per_by_year = {}
        for year, eps in eps_by_year.items():
            if eps <= 0:
                continue
            year_end_price = price_at_or_before(price_history, datetime(year, 12, 31).date())
            if year_end_price:
                per_by_year[year] = year_end_price / eps  # DART EPS·네이버 종가 둘 다 원 단위

        latest_price = price_history[-1][1]
        latest_year = max(eps_by_year)
        latest_eps = eps_by_year[latest_year]
        current_per = latest_price / latest_eps if latest_eps > 0 else None

        if len(per_by_year) < 2:
            rows_out.append({
                "종목명": name, "상태": "insufficient_history",
                "현재PER": round(current_per, 2) if current_per else None,
            })
            continue

        per_values = sorted(per_by_year.values())
        band_min, band_max = per_values[0], per_values[-1]
        if current_per is not None:
            below = sum(1 for v in per_values if v <= current_per)
            percentile = below / len(per_values) * 100
        else:
            percentile = None

        rows_out.append({
            "종목명": name,
            "상태": "ok",
            "현재PER": round(current_per, 2) if current_per else None,
            "밴드_최저PER": round(band_min, 2),
            "밴드_최고PER": round(band_max, 2),
            "밴드내_위치_percentile": round(percentile, 1) if percentile is not None else None,
            "연도별_PER": ";".join(f"{y}:{round(v,2)}" for y, v in sorted(per_by_year.items())),
            "관측_연도수": len(per_by_year),
        })

        if i % 10 == 0 or i == len(names):
            pd.DataFrame(rows_out).to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
            print(f"  중간 저장 완료 ({i}/{len(names)})")

    pd.DataFrame(rows_out).to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n최종 저장 완료: {OUT_PATH}")


if __name__ == "__main__":
    main()
