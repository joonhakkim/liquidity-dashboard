"""
일회성 스크립트(2026-09-10) - 트로이MP 4건 조정을 9/10 종가 기준으로 실행.
  삼성전자 -2%p 축소 / SK하이닉스 -2%p 축소 / 엘앤에프(066970) 2% 신규편입 /
  대한유화(006650) 2% 신규편입
AUM 기준: 오늘(9/10) 종가로 기존 보유분을 마크투마켓한 값(최근 리밸런싱들과 동일 컨벤션).
"""
import os
import time

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
TRADES_PATH = os.path.join(DATA_DIR, "manual", "troy_mp_trades.csv")
DATE = "2026-09-10"
CASH_BEFORE = 11_016_130

CURRENT_SHARES = {
    "005930": 574.0, "000660": 83.0, "159010": 1280.0, "170920": 569.0, "222800": 156.0,
    "017670": 339.0, "257720": 463.0, "316140": 924.0, "001450": 421.0, "196170": 74.0,
    "006260": 63.0, "006360": 581.0, "010950": 187.0, "322000": 144.0, "306200": 210.0,
    "028670": 3435.0, "006400": 36.0, "089890": 821.0, "003350": 2321.0, "003490": 1039.0,
    "086450": 2614.0, "096770": 202.0, "326030": 246.0, "036810": 800.0, "375500": 254.0,
    "018260": 92.0, "138930": 1330.0, "085620": 1071.0, "125020": 3308.0, "327260": 488.0,
    "082740": 420.0,
}

REDUCE = [  # (코드, 종목명, 섹터, 축소%p)
    ("005930", "삼성전자", "반도체", 2.0),
    ("000660", "SK하이닉스", "반도체", 2.0),
]
NEW_ENTRY = [  # (코드, 종목명, 섹터, 목표비중%)
    ("066970", "엘앤에프", "배터리소재", 2.0),
    ("006650", "대한유화", "정유화학", 2.0),
]

ALL_CODES = set(CURRENT_SHARES) | {c for c, *rest in REDUCE} | {c for c, *rest in NEW_ENTRY}


def get_close_price(code):
    for attempt in range(3):
        r = requests.get(f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}", timeout=10)
        d = r.json()["datas"][0]
        status = d["marketStatus"]
        price = int(d["closePrice"].replace(",", ""))
        if status == "CLOSE":
            return price, status
        time.sleep(5)
    return price, status


def main():
    print("종가 확인 중...")
    prices = {}
    bad = []
    for code in ALL_CODES:
        price, status = get_close_price(code)
        prices[code] = price
        if status != "CLOSE":
            bad.append((code, status))
        print(f"  {code}: {price:,}원 (status={status})")
        time.sleep(0.15)

    if bad:
        print("\n경고: 장이 아직 안 닫힌 종목이 있습니다:", bad)
        print("전체 재확인 후 다시 실행하세요. 중단합니다.")
        return

    mv_today = sum(CURRENT_SHARES[c] * prices[c] for c in CURRENT_SHARES if c in prices)
    aum_basis = CASH_BEFORE + mv_today
    print(f"\n오늘(9/10) 종가 마크투마켓: 현금 {CASH_BEFORE:,} + 평가액 {mv_today:,.0f} = AUM {aum_basis:,.0f}")

    rows = []
    for code, name, sector, reduce_pp in REDUCE:
        price = prices[code]
        before_shares = CURRENT_SHARES[code]
        weight_before = before_shares * price / aum_basis * 100
        weight_after = weight_before - reduce_pp
        target_amount_after = weight_after / 100 * aum_basis
        shares_after = round(target_amount_after / price)
        shares_sold = before_shares - shares_after
        amount = shares_sold * price
        print(f"{name}: 현재비중 {weight_before:.2f}% -> 목표 {weight_after:.2f}%, 매도 {shares_sold:.0f}주, 금액 {amount:,.0f}")
        rows.append({"date": DATE, "code": code, "name": name, "action": "SELL",
                      "price": price, "amount": amount, "sector": sector})

    for code, name, sector, w in NEW_ENTRY:
        price = prices[code]
        target_amount = w / 100 * aum_basis
        shares = round(target_amount / price)
        amount = shares * price
        print(f"{name}: 신규 매수 {shares}주, 금액 {amount:,.0f}, 목표비중 {w}%")
        rows.append({"date": DATE, "code": code, "name": name, "action": "BUY",
                      "price": price, "amount": amount, "sector": sector})

    new_df = pd.DataFrame(rows)
    print("\n", new_df.to_string(index=False))

    existing = pd.read_csv(TRADES_PATH, dtype={"code": str})
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined.to_csv(TRADES_PATH, index=False, encoding="utf-8")
    print(f"\n저장 완료: {TRADES_PATH} (총 {len(combined)}행)")


if __name__ == "__main__":
    main()
