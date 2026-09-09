"""
일회성 스크립트(2026-09-09) - 민구MP를 사용자가 지정한 33종목·목표비중(합계 82.2%,
현금 17.8%)으로 전면 리밸런싱한다. 오늘(9/9) 종가 확정 후 실행.

기존 24종목 전부 새 목표리스트에 포함되어 있어서(전량매도 없음), 전부 "목표비중까지
조정"이고, 신규 9종목(엘티씨/GS건설/삼성에스디에스/미래에셋생명/코세스/팬오션/
한화엔진/티씨머티리얼즈/BNK금융지주)은 순수 신규 매수다.

AUM 기준: 오늘(9/9) 종가로 기존 보유분을 마크투마켓한 값(트로이MP 리밸런싱과 동일한
컨벤션 - 직전거래일이 아니라 리밸런싱 당일 종가 기준).
"""
import os
import time

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
TRADES_PATH = os.path.join(DATA_DIR, "manual", "mingu_mp_trades.csv")
DATE = "2026-09-09"

CASH_BEFORE = 1_000_000_000  # 초기자본 - 아래에서 현재 보유분/현금을 재계산

CURRENT_SHARES = {
    "005930": 485.0, "000660": 75.0, "159010": 1762.0, "222800": 162.0, "017670": 219.0,
    "327260": 798.0, "086450": 2158.0, "257720": 387.0, "003350": 1403.0, "316140": 621.0,
    "001450": 413.0, "196170": 81.0, "006260": 67.0, "010950": 140.0, "322000": 148.0,
    "306200": 151.0, "003490": 747.0, "006400": 36.0, "124500": 882.0, "066970": 292.0,
    "096770": 155.0, "326030": 241.0, "036810": 1463.0, "375500": 264.0,
}

# (코드, 종목명, 섹터, 목표비중%)
TARGET = [
    ("005930", "삼성전자", "반도체", 10.5),
    ("000660", "SK하이닉스", "반도체", 10.4),
    ("086450", "동국제약", "제약", 3.5),
    ("010950", "S-OIL", "정유화학", 2.1),
    ("096770", "SK이노베이션", "정유화학", 2.1),
    ("159010", "아스플로", "반도체소부장", 4.2),
    ("003490", "대한항공", "항공", 2.1),
    ("003350", "한국화장품제조", "화장품", 2.1),
    ("316140", "우리금융지주", "은행", 2.1),
    ("017670", "SK텔레콤", "통신", 2.1),
    ("306200", "세아제강", "철강", 2.1),
    ("006260", "LS", "전력기기", 1.4),
    ("222800", "심텍", "반도체소부장", 1.4),
    ("196170", "알테오젠", "제약바이오", 1.4),
    ("006400", "삼성SDI", "배터리", 1.4),
    ("170920", "엘티씨", "반도체소부장", 1.4),
    ("006360", "GS건설", "건설", 1.4),
    ("018260", "삼성에스디에스", "IT서비스", 1.4),
    ("257720", "실리콘투", "화장품", 1.4),
    ("085620", "미래에셋생명", "보험", 1.4),
    ("089890", "코세스", "기타", 1.4),
    ("028670", "팬오션", "해운", 1.4),
    ("082740", "한화엔진", "조선", 1.4),
    ("327260", "RF머트리얼즈", "통신", 3.4),
    ("125020", "티씨머티리얼즈", "반도체소부장", 1.4),
    ("138930", "BNK금융지주", "은행", 1.4),
    ("036810", "에프에스티", "반도체소부장", 3.8),
    ("001450", "현대해상", "보험", 1.4),
    ("375500", "DL이앤씨", "건설", 1.4),
    ("326030", "SK바이오팜", "제약바이오", 1.4),
    ("322000", "HD현대에너지솔루션", "신재생", 1.4),
    ("066970", "엘앤에프", "배터리소재", 3.7),
    ("124500", "아이티센글로벌", "기타", 2.8),
]

assert abs(sum(w for *_x, w in TARGET) - 82.2) < 1e-6, "목표비중 합계가 82.2%가 아닙니다"


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
    for code, name, sector, w in TARGET:
        price, status = get_close_price(code)
        prices[code] = price
        if status != "CLOSE":
            bad.append((code, name, status))
        print(f"  {code} {name}: {price:,}원 (status={status})")
        time.sleep(0.15)

    if bad:
        print("\n경고: 장이 아직 안 닫힌 종목이 있습니다:", bad)
        print("전체 재확인 후 다시 실행하세요. 중단합니다.")
        return

    # 현재 현금: 전체 매매 히스토리를 재생해서 계산(초기 10억 - 누적 순매수)
    trades = pd.read_csv(TRADES_PATH, dtype={"code": str})
    cash = CASH_BEFORE
    for _, row in trades.iterrows():
        if row["action"] == "BUY":
            cash -= row["amount"]
        else:
            cash += row["amount"]

    mv_today = sum(CURRENT_SHARES[c] * prices[c] for c in CURRENT_SHARES if c in prices)
    aum_basis = cash + mv_today
    print(f"\n오늘(9/9) 종가 마크투마켓: 현금 {cash:,.0f} + 평가액 {mv_today:,.0f} = AUM {aum_basis:,.0f}")

    rows = []
    for code, name, sector, w in TARGET:
        price = prices[code]
        target_amount = w / 100 * aum_basis
        target_shares = round(target_amount / price)
        before_shares = CURRENT_SHARES.get(code, 0)
        delta = target_shares - before_shares
        if delta == 0:
            continue
        action = "BUY" if delta > 0 else "SELL"
        qty = abs(delta)
        amount = qty * price
        rows.append({
            "date": DATE, "code": code, "name": name, "action": action,
            "price": price, "amount": amount, "sector": sector,
        })

    new_df = pd.DataFrame(rows)
    print(f"\n총 {len(new_df)}건 매매 생성 (BUY {sum(new_df.action=='BUY')}건 / SELL {sum(new_df.action=='SELL')}건)")
    print(new_df.to_string(index=False))

    total_buy = new_df[new_df.action == "BUY"]["amount"].sum()
    total_sell = new_df[new_df.action == "SELL"]["amount"].sum()
    print(f"\n총 매수금액: {total_buy:,.0f} / 총 매도금액: {total_sell:,.0f} / 순현금유출: {total_buy - total_sell:,.0f}")

    existing = pd.read_csv(TRADES_PATH, dtype={"code": str})
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined.to_csv(TRADES_PATH, index=False, encoding="utf-8")
    print(f"\n저장 완료: {TRADES_PATH} (총 {len(combined)}행)")


if __name__ == "__main__":
    main()
