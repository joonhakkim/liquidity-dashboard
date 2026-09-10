"""
일회성 스크립트(2026-09-10) - 코스닥 롱숏(지수) MP를 사용자 지시대로 완전히 새로 시작한다.
9/9 종가로 편입, NAV 10000부터 새로 시작(=과거 이력 전부 삭제하고 이 파일 자체가 새 시작점).

롱 20종목(합계 100%) + 숏은 인버스ETF 100%(기존 방식과 동일 - BUY로 담아서 이중반전 방지).
정수 주식수는 반올림이 아니라 내림(floor)으로 잡아서 매입금액이 목표치를 넘지 않게 한다
(기존 롱숏 펀드 컨벤션과 동일, mp_portfolios.py 주석 참고).
"""
import os

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "manual", "kosdaq_long_short_index_trades.csv")
DATE = "2026-09-09"
TOTAL_CAPITAL = 1_000_000_000

LONG = [  # (코드, 이름, 섹터, 비중%)
    ("036930", "주성엔지니어링", "반도체소부장", 7),
    ("058470", "리노공업", "반도체소부장", 7),
    ("240810", "원익IPS", "반도체소부장", 8),
    ("036810", "에프에스티", "반도체소부장", 7),
    ("086390", "유니테스트", "반도체와반도체장비", 5),
    ("196170", "알테오젠", "제약바이오", 10),
    ("141080", "리가켐바이오", "제약바이오", 4),
    ("298380", "에이비엘바이오", "제약바이오", 4),
    ("237690", "에스티팜", "제약", 6),
    ("107640", "한중엔시에스", "전기장비", 6),
    ("091580", "상신이디피", "배터리소재", 5),
    ("108490", "로보티즈", "기계", 7),
    ("058610", "에스피지", "전기제품", 2),
    ("214450", "파마리서치", "주사제", 2),
    ("086450", "동국제약", "제약", 5),
    ("089890", "코세스", "기타", 6),
    ("327260", "RF머트리얼즈", "통신", 3),
    ("138080", "오이솔루션", "광학기기및장비", 2),
    ("382900", "범한퓨얼셀", "전기장비", 2),
    ("125020", "티씨머티리얼즈", "전력기기", 2),
]
SHORT_ETF = ("251340", "KODEX 코스닥150선물인버스", "인버스ETF", 100)

assert sum(w for *_x, w in LONG) == 100


def fetch_price_on(code, date_str):
    r = requests.get(
        "https://fchart.stock.naver.com/sise.nhn",
        params={"symbol": code, "timeframe": "day", "count": 30, "requestType": 0},
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
            rows.append((parts[0], float(parts[4])))
        except ValueError:
            continue
    target = pd.to_datetime(date_str).strftime("%Y%m%d")
    for d, close in rows:
        if d == target:
            return close
    raise SystemExit(f"{code}: {date_str} 종가를 못 찾음 - 받은 날짜: {[d for d,_ in rows]}")


def main():
    rows = []
    for code, name, sector, w in LONG:
        price = fetch_price_on(code, DATE)
        target_amount = w / 100 * TOTAL_CAPITAL
        shares = int(target_amount // price)  # floor
        amount = shares * price
        print(f"{name}({code}): {price:,.0f}원 x {shares}주 = {amount:,.0f}원 (목표 {w}%, 실제 {amount/TOTAL_CAPITAL*100:.2f}%)")
        rows.append({"date": DATE, "code": code, "name": name, "action": "BUY",
                      "price": price, "amount": amount, "sector": sector})

    code, name, sector, w = SHORT_ETF
    price = fetch_price_on(code, DATE)
    target_amount = w / 100 * TOTAL_CAPITAL
    shares = int(target_amount // price)
    amount = shares * price
    print(f"{name}({code}): {price:,.0f}원 x {shares}주 = {amount:,.0f}원 (목표 {w}%, 실제 {amount/TOTAL_CAPITAL*100:.2f}%)")
    rows.append({"date": DATE, "code": code, "name": name, "action": "BUY",
                  "price": price, "amount": amount, "sector": sector})

    df = pd.DataFrame(rows)
    total_long = df[df["code"] != "251340"]["amount"].sum()
    total_short = df[df["code"] == "251340"]["amount"].sum()
    print(f"\n롱 합계: {total_long:,.0f} ({total_long/TOTAL_CAPITAL*100:.2f}%) / 숏(인버스ETF): {total_short:,.0f} ({total_short/TOTAL_CAPITAL*100:.2f}%)")

    df.to_csv(OUT_PATH, index=False, encoding="utf-8")
    print(f"\n저장 완료: {OUT_PATH} ({len(df)}행, 새로 시작)")


if __name__ == "__main__":
    main()
