"""
일회성 스크립트(2026-09-09) - 모멘텀MP 1:1 금액 스왑 4건을 9/9 종가로 실행.
모멘텀MP 컨벤션(트로이/민구MP와 다름): 정수 주식수 반올림 없이, 파는 종목의 보유수량 x
오늘종가 = 정확한 매도대금을 그대로 사는 종목 매수금액으로 쓴다(1:1 금액 스왑).

스왑 목록:
  메리츠금융지주(138040) -> 인텍플러스(064290)
  롯데렌탈(089860) -> 피에스케이홀딩스(031980)
  BGF리테일(282330) -> 삼화콘덴서(001820)
  펌텍코리아(251970) -> 가온전선(000500)
"""
import os
import time

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
TRADES_PATH = os.path.join(DATA_DIR, "manual", "momentum_mp_trades.csv")
DATE = "2026-09-09"

SWAPS = [
    # (매도코드, 매도명, 매도섹터, 매수코드, 매수명, 매수섹터)
    ("138040", "메리츠금융지주", "금융지주", "064290", "인텍플러스", "반도체소부장"),
    ("089860", "롯데렌탈", "렌탈", "031980", "피에스케이홀딩스", "반도체와반도체장비"),
    ("282330", "BGF리테일", "편의점유통", "001820", "삼화콘덴서", "전자부품"),
    ("251970", "펌텍코리아", "화장품용기", "000500", "가온전선", "전기장비"),
]


def get_close_price(code):
    for attempt in range(3):
        r = requests.get(f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}", timeout=10)
        d = r.json()["datas"][0]
        status = d["marketStatus"]
        price = float(d["closePrice"].replace(",", ""))
        if status == "CLOSE":
            return price, status
        time.sleep(5)
    return price, status


def main():
    trades = pd.read_csv(TRADES_PATH, dtype={"code": str})

    # 현재 보유 주식수 계산(전체 히스토리 재생)
    shares = {}
    for _, row in trades.iterrows():
        c = row["code"]
        qty = row["amount"] / row["price"]
        if row["action"] == "BUY":
            shares[c] = shares.get(c, 0.0) + qty
        else:
            shares[c] = shares.get(c, 0.0) - qty

    codes_needed = set()
    for sell_code, _n1, _s1, buy_code, _n2, _s2 in SWAPS:
        codes_needed.add(sell_code)
        codes_needed.add(buy_code)

    print("종가 확인 중...")
    prices = {}
    bad = []
    for code in codes_needed:
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

    rows = []
    for sell_code, sell_name, sell_sector, buy_code, buy_name, buy_sector in SWAPS:
        sell_shares = shares.get(sell_code, 0.0)
        sell_price = prices[sell_code]
        proceeds = sell_shares * sell_price
        buy_price = prices[buy_code]
        rows.append({"date": DATE, "code": sell_code, "name": sell_name, "action": "SELL",
                      "price": sell_price, "amount": proceeds, "sector": sell_sector})
        rows.append({"date": DATE, "code": buy_code, "name": buy_name, "action": "BUY",
                      "price": buy_price, "amount": proceeds, "sector": buy_sector})

    new_df = pd.DataFrame(rows)
    print("\n", new_df.to_string(index=False))

    combined = pd.concat([trades, new_df], ignore_index=True)
    combined.to_csv(TRADES_PATH, index=False, encoding="utf-8")
    print(f"\n저장 완료: {TRADES_PATH} (총 {len(combined)}행)")


if __name__ == "__main__":
    main()
