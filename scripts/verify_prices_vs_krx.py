"""
MP 트래커 가격 시계열을 KRX Open API 공식 정규장 종가(stk_bydd_trd/ksq_bydd_trd)와
주기적으로 대조해서 차이를 로그에 남긴다(2026-09-15 사용자 요청 - "주기적으로 점검하게
해달라").

2026-09-14부터 KRX가 애프터마켓(16:00~20:00 접속매매)을 도입하면서, 우리가 MP
지수 계산에 쓰는 "당일 종가"의 정의를 정규장(15:30) 마감가가 아니라 애프터마켓
마감가(20:00)로 쓰기로 했다(fetch_troy_mp_prices.py/run_troy_mp_pipeline.py 20:05 실행
참고) - 그래서 이 스크립트는 차이를 발견해도 "우리가 틀렸다"고 단정해서 자동으로
고치지 않는다. 정규장 종가와 애프터마켓 종가는 이제 원래 다른 값일 수 있어서(애프터마켓
중 추가로 오르내림) 어느 정도 차이는 정상이다. 대신:
  - 평소보다 훨씬 큰 차이(과거 애프터마켓 변동폭 대비 이상치)만 눈에 띄게 표시해서
    사람이 검토하게 한다(데이터 자체가 잘못 들어온 경우 vs 애프터마켓에서 진짜
    그만큼 움직인 경우를 구분하려면 결국 사람 판단이 필요).
  - KRX Open API는 당일 데이터가 다음날 아침에야 올라오므로, 최근 며칠(기본 5거래일)치만
    확인한다 - 그날그날 최신 트레이딩을 검증하는 용도.

출력: 콘솔에만 출력(로그 파이프라인이 logs/YYYY-MM-DD.log에 자동으로 남김). 자동 수정 없음 -
사람이 보고 "이건 진짜 오류다" 싶으면 매매일지/가격 CSV를 직접 고쳐야 한다.
"""
import os
from datetime import datetime, timedelta

import pandas as pd
import requests
from dotenv import load_dotenv

from mp_portfolios import ALL_PORTFOLIOS, PRIVATE_PORTFOLIOS

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
KRX_OPEN_API_KEY = os.environ.get("KRX_OPEN_API_KEY")
STK_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
KSQ_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"

LOOKBACK_TRADING_DAYS = 5
# 정규장 종가 vs 애프터마켓 종가 차이가 이 비율(%)을 넘으면 "확인 필요"로 강조 표시.
# 과거 애프터마켓 변동폭 실측치가 쌓이기 전까지는 보수적으로 2%로 잡아둔다.
FLAG_THRESHOLD_PCT = 2.0


def fetch_krx_closes(basDd):
    """그 날짜의 코스피+코스닥 전종목 정규장 종가 {코드: 종가}. 데이터 없으면 빈 dict."""
    if not KRX_OPEN_API_KEY:
        return {}
    headers = {"AUTH_KEY": KRX_OPEN_API_KEY}
    closes = {}
    for url in (STK_URL, KSQ_URL):
        try:
            r = requests.get(url, params={"basDd": basDd}, headers=headers, timeout=20)
        except requests.RequestException:
            continue
        if r.status_code != 200:
            continue
        for item in r.json().get("OutBlock_1", []):
            code = item.get("ISU_CD")
            price = item.get("TDD_CLSPRC")
            if code and price:
                closes[code] = float(price)
    return closes


def main():
    if not KRX_OPEN_API_KEY:
        print("KRX_OPEN_API_KEY가 없어 검증을 건너뜁니다.")
        return

    recent_dates = [(datetime.today() - timedelta(days=d)).strftime("%Y%m%d")
                     for d in range(1, LOOKBACK_TRADING_DAYS * 2)]
    krx_by_date = {}

    total_checked = 0
    total_flagged = 0
    print(f"=== 가격 시계열 vs KRX 공식 정규장 종가 대조 (최근 {LOOKBACK_TRADING_DAYS}거래일) ===")
    for portfolio in ALL_PORTFOLIOS + PRIVATE_PORTFOLIOS:
        prices_path = portfolio["prices_path"]
        if not os.path.exists(prices_path):
            continue
        prices = pd.read_csv(prices_path, dtype={"code": str})
        if prices.empty:
            continue
        prices["code"] = prices["code"].str.zfill(6)
        our_dates = sorted(prices["date"].unique())[-LOOKBACK_TRADING_DAYS:]

        rows_for_portfolio = []
        for d in our_dates:
            basDd = d.replace("-", "")
            if basDd not in krx_by_date:
                krx_by_date[basDd] = fetch_krx_closes(basDd)
            krx_closes = krx_by_date[basDd]
            if not krx_closes:
                continue  # 아직 KRX에 안 올라온 날짜(당일~전날)는 스킵
            day_rows = prices[prices["date"] == d]
            for _, row in day_rows.iterrows():
                krx_v = krx_closes.get(row["code"])
                if krx_v is None:
                    continue
                total_checked += 1
                diff_pct = (row["close"] - krx_v) / krx_v * 100
                if abs(diff_pct) >= FLAG_THRESHOLD_PCT:
                    total_flagged += 1
                    rows_for_portfolio.append((d, row["code"], row["close"], krx_v, diff_pct))

        if rows_for_portfolio:
            print(f"\n[{portfolio['name']}] 확인 필요({FLAG_THRESHOLD_PCT}% 이상 차이):")
            for d, code, ours, krx_v, diff_pct in rows_for_portfolio:
                print(f"  {d} {code}: 우리값={ours:,.0f} KRX정규장종가={krx_v:,.0f} 차이={diff_pct:+.2f}%")

    print(f"\n대조 완료: {total_checked}건 확인, {total_flagged}건이 {FLAG_THRESHOLD_PCT}% 이상 차이"
          f"(애프터마켓 변동일 수도 있으니 참고용 - 자동 수정 안 함)")


if __name__ == "__main__":
    main()
