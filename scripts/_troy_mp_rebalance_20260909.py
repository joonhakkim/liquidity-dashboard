"""
일회성 스크립트(2026-09-09) - 트로이MP를 사용자가 지정한 31종목·목표비중(합계 99%,
현금 1%)으로 전면 리밸런싱한다. 오늘(9/9) 종가가 확정된 뒤 실행.

기존 21종목은 전부 새 목표리스트에 포함되어 있어서(사용자 확인 완료 - 전량매도 없음),
전부 "목표비중까지 조정"(현재 주식수 -> 새 목표주식수, 차이만큼 매수/매도)이고, 신규
10종목(엘티씨/삼성에스디에스/BNK금융지주/팬오션/GS건설/미래에셋생명/티씨머티리얼즈/
코세스/RF머트리얼즈/한화엔진)은 순수 신규 매수다.

AUM 기준(2026-09-09 사용자 지시로 변경): 직전거래일 종가가 아니라, "오늘(9/9) 종가로
수익률/평가액을 먼저 다 계산한 다음, 그 계산된 AUM"을 리밸런싱 기준으로 쓴다 - 즉
기존 21종목을 전부 오늘 종가로 마크투마켓한 평가액 + 현금(16,393,920원)을 오늘 새로
계산해서 AUM_BASIS로 쓴다(8/24 대규모 리밸런싱 때와 동일한 방식 - 그때도 직전거래일이
아니라 당일 종가 기준으로 계산했었음). 아래 AUM_BASIS 상수는 참고용 폴백일 뿐, main()에서
실제로는 오늘 종가로 다시 계산해서 덮어쓴다.
"""
import os
import time

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
TRADES_PATH = os.path.join(DATA_DIR, "manual", "troy_mp_trades.csv")

CASH_BEFORE = 16_393_920  # 2026-09-08 종가 기준 캐스케이딩 계산으로 확인된 현금(오늘 리밸런싱 전 매매 없었다는 전제)
AUM_BASIS_FALLBACK = 1_009_817_350  # 참고용(직전거래일 종가 기준) - 실제로는 아래에서 오늘 종가로 재계산

CURRENT_SHARES = {
    "000660": 106, "005930": 692, "086450": 3137, "159010": 1490, "010950": 211,
    "003350": 2557, "003490": 1105, "196170": 117, "322000": 239, "306200": 216,
    "316140": 932, "096770": 226, "006400": 58, "375500": 389, "222800": 246,
    "001450": 588, "326030": 352, "036810": 1151, "257720": 602, "017670": 294,
    "006260": 87,
}

# (코드, 종목명, 섹터, 목표비중%)
TARGET = [
    ("005930", "삼성전자", "반도체", 15),
    ("000660", "SK하이닉스", "반도체", 15),
    ("159010", "아스플로", "반도체소부장", 3),
    ("222800", "심텍", "반도체소부장", 2),
    ("036810", "에프에스티", "반도체소부장", 2),
    ("170920", "엘티씨", "반도체소부장", 2),
    ("018260", "삼성에스디에스", "IT서비스", 2),
    ("017670", "SK텔레콤", "통신", 3),
    ("086450", "동국제약", "제약", 5),
    ("257720", "실리콘투", "화장품", 2),
    ("003350", "한국화장품제조", "화장품", 3),
    ("316140", "우리금융지주", "은행", 3),
    ("138930", "BNK금융지주", "은행", 2),
    ("028670", "팬오션", "해운", 2),
    ("375500", "DL이앤씨", "건설", 2),
    ("006360", "GS건설", "건설", 2),
    ("001450", "현대해상", "보험", 2),
    ("085620", "미래에셋생명", "보험", 2),
    ("196170", "알테오젠", "제약바이오", 2),
    ("326030", "SK바이오팜", "제약바이오", 2),
    ("006260", "LS", "전력기기", 2),
    ("125020", "티씨머티리얼즈", "반도체소부장", 2),
    ("010950", "S-OIL", "정유화학", 3),
    ("096770", "SK이노베이션", "정유화학", 3),
    ("322000", "HD현대에너지솔루션", "신재생", 2),
    ("306200", "세아제강", "철강", 3),
    ("003490", "대한항공", "항공", 3),
    ("006400", "삼성SDI", "배터리", 2),
    ("089890", "코세스", "기타", 2),
    ("327260", "RF머트리얼즈", "통신", 2),
    ("082740", "한화엔진", "조선", 2),
]

assert sum(w for *_x, w in TARGET) == 99, "목표비중 합계가 99%가 아닙니다"


def get_close_price(code):
    for attempt in range(3):
        r = requests.get(f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}", timeout=10)
        d = r.json()["datas"][0]
        status = d["marketStatus"]
        price = int(d["closePrice"].replace(",", ""))
        if status == "CLOSE":
            return price, status
        time.sleep(5)
    return price, status  # CLOSE 아니어도 마지막 값 반환(호출측에서 상태 확인)


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

    # 오늘(9/9) 종가로 기존 21종목을 마크투마켓해서 AUM을 새로 계산(사용자 지시,
    # 8/24 대규모 리밸런싱 때와 동일하게 직전거래일이 아니라 당일 종가 기준).
    mv_today = sum(CURRENT_SHARES[code] * prices[code] for code in CURRENT_SHARES if code in prices)
    aum_basis = CASH_BEFORE + mv_today
    print(f"\n오늘(9/9) 종가 마크투마켓: 현금 {CASH_BEFORE:,} + 평가액 {mv_today:,} = AUM {aum_basis:,.0f}")
    print(f"(참고: 직전거래일 종가 기준이었다면 {AUM_BASIS_FALLBACK:,}원)")

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
            "date": "2026-09-09", "code": code, "name": name, "action": action,
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
