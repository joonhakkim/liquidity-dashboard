"""
일회성 스크립트(2026-09-09) - 코스닥 롱숏(개별종목)/코스닥 롱숏(지수) 두 펀드를
사용자가 지정한 새 목표리스트로 9/8 종가 기준 전면 리밸런싱한다.

기준(2026-09-09 확정된 컨벤션): 9/8 종가로 기존 보유분을 마크투마켓한 AUM을
그대로 리밸런싱 기준으로 사용(직전거래일이 아니라 "리밸런싱에 쓰는 그 날" 종가 기준).
- 코스닥 롱숏(개별종목) AUM = 951,740,840원 (2026-09-08 마크투마켓, 사전 계산 완료)
- 코스닥 롱숏(지수) AUM = 973,581,280원 (2026-09-08 마크투마켓, 사전 계산 완료 -
  단, 이 중 인버스ETF(251340) 비중은 건드리지 않음 - "숏은 그대로 두기" 사용자 지시)

목표 롱북(18종목, 두 펀드 공통, 합계 95%):
  알테오젠8/실리콘투2/동국제약4/씨젠5/코세스6/오이솔루션2/에스티팜5/원익IPS6/
  한중엔시에스5/상신이디피5/리노공업9/에프에스티10/주성엔지니어링5/로보티즈7/
  에스피지2/케이아이엔엑스6/파마리서치5/RF머트리얼즈3
목표 숏북(18종목, 개별종목 펀드만, 합계 100%):
  타이거일렉8/브이엠7/고영8/워트5/루닛8/오름테라퓨틱8/올릭스7/피노5/마녀공장6/
  엔젤로보틱스4/티엑스알로보틱스5/에스오에스랩5/세종텔레콤5/큐브엔터3/푸드나무4/
  액스비스5/YTN2/바이오비쥬5

현재 보유분 중 새 목표리스트에 없는 종목(전량 편출/커버):
  개별종목: 롱 - 한스바이오메드/와이지-원/파인엠텍, 숏 - 우리기술/원익홀딩스
  개별종목: 에스피지는 숏 보유중이었으나 신규 롱으로 전환 - 먼저 COVER, 이후 신규 BUY
  지수: 롱 - 한스바이오메드/와이지-원/파인엠텍 (인버스ETF는 미변경)
"""
import os

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MANUAL_DIR = os.path.join(DATA_DIR, "manual")
DATE = "2026-09-09"  # 매매 기록 날짜(오늘 지시받아 실행하지만 기준은 9/8 종가)
PRICE_DATE = pd.Timestamp("2026-09-08")

# --- 목표 롱북(코드, 이름, 섹터, 비중%) ---
LONG_TARGET = [
    ("196170", "알테오젠", "제약바이오", 8),
    ("257720", "실리콘투", "화장품", 2),
    ("086450", "동국제약", "제약", 4),
    ("096530", "씨젠", "진단키트", 5),
    ("089890", "코세스", "기타", 6),
    ("138080", "오이솔루션", "광학기기및장비", 2),
    ("237690", "에스티팜", "제약", 5),
    ("240810", "원익IPS", "반도체소부장", 6),
    ("107640", "한중엔시에스", "전기장비", 5),
    ("091580", "상신이디피", "배터리소재", 5),
    ("058470", "리노공업", "반도체소부장", 9),
    ("036810", "에프에스티", "반도체소부장", 10),
    ("036930", "주성엔지니어링", "반도체소부장", 5),
    ("108490", "로보티즈", "기계", 7),
    ("058610", "에스피지", "전기제품", 2),
    ("093320", "케이아이엔엑스", "인터넷서비스", 6),
    ("214450", "파마리서치", "주사제", 5),
    ("327260", "RF머트리얼즈", "통신", 3),
]

# --- 목표 숏북(코드, 이름, 섹터, 비중%(양수로 표기, 실제 부호는 SHORT)) - 개별종목 펀드만 ---
SHORT_TARGET = [
    ("219130", "타이거일렉", "전자부품", 8),
    ("089970", "브이엠", "반도체와반도체장비", 7),
    ("098460", "고영", "반도체소부장", 8),
    ("396470", "워트", "반도체소부장", 5),
    ("328130", "루닛", "헬스케어AI", 8),
    ("475830", "오름테라퓨틱", "제약바이오", 8),
    ("226950", "올릭스", "제약바이오", 7),
    ("033790", "피노", "배터리소재", 5),
    ("439090", "마녀공장", "화장품", 6),
    ("455900", "엔젤로보틱스", "기계", 4),
    ("484810", "티엑스알로보틱스", "기계", 5),
    ("464080", "에스오에스랩", "전자장비및기기", 5),
    ("036630", "세종텔레콤", "통신", 5),
    ("182360", "큐브엔터", "미디어", 3),
    ("290720", "푸드나무", "음식료", 4),
    ("0011A0", "액스비스", "기타", 5),
    ("040300", "YTN", "미디어", 2),
    ("489460", "바이오비쥬", "화장품", 5),
]

assert sum(w for *_x, w in LONG_TARGET) == 95
assert sum(w for *_x, w in SHORT_TARGET) == 100

# 코스닥 롱숏(개별종목) - 현재 보유(부호: +롱/-숏), 2026-09-08 마크투마켓 AUM=951,740,840
INDIV_AUM = 951_740_840
INDIV_CURRENT = {
    "086450": 9270, "032820": -9418, "058470": 1503, "226950": -1044, "219130": -1657,
    "089890": 4510, "240810": 872, "030530": -4449, "439090": -6253, "098460": -3588,
    "089970": -1896, "328130": -11124, "257720": 2085, "475830": -1791, "237690": 989,
    "042520": 2550, "091580": 2424, "096530": 1569, "058610": -531, "196170": 173,
    "019210": 3510, "441270": 5354, "033790": -4908,
}
INDIV_EXIT_ONLY = {  # 신규 리스트에 없어서 전량 편출/커버만 하는 종목(섹터는 기존 CSV 참고)
    "042520": ("한스바이오메드", "의료기기", "SELL"),
    "019210": ("와이지-원", "기계", "SELL"),
    "441270": ("파인엠텍", "핸드셋", "SELL"),
    "032820": ("우리기술", "우주항공과국방", "COVER"),
    "030530": ("원익홀딩스", "반도체소부장", "COVER"),
}

# 코스닥 롱숏(지수) - 현재 보유(전부 롱, 251340은 인버스ETF=미변경), AUM=973,581,280
INDEX_AUM = 973_581_280
INDEX_CURRENT = {
    "086450": 9517, "058470": 1543, "089890": 4630, "240810": 895, "257720": 2141,
    "237690": 1015, "042520": 2618, "091580": 2488, "096530": 1611, "196170": 178,
    "019210": 3604, "441270": 5496,
}
INDEX_EXIT_ONLY = {
    "042520": ("한스바이오메드", "의료기기", "SELL"),
    "019210": ("와이지-원", "기계", "SELL"),
    "441270": ("파인엠텍", "핸드셋", "SELL"),
}


def fetch_price_on(code, date):
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
            rows.append((pd.to_datetime(parts[0], format="%Y%m%d"), float(parts[4])))
        except ValueError:
            continue
    df = pd.DataFrame(rows, columns=["date", "close"])
    row = df[df["date"] == date]
    if row.empty:
        raise SystemExit(f"{code}: {date.date()} 종가를 못 찾음(휴장/거래정지/상장일 확인 필요) - 받아온 날짜들: {df['date'].dt.strftime('%Y-%m-%d').tolist()}")
    return float(row["close"].iloc[0])


def build_trades_for_fund(long_target, short_target, current, exit_only, aum, has_short_book):
    """current: {code: signed_shares}. exit_only: {code: (name, sector, action)}.
    반환: list of trade row dicts."""
    all_codes = set(current) | {c for c, *_ in long_target} | ({c for c, *_ in short_target} if has_short_book else set())
    prices = {c: fetch_price_on(c, PRICE_DATE) for c in all_codes}

    rows = []

    # 1) 신규 목표에 없는 기존 보유분 전량 편출/커버
    for code, (name, sector, action) in exit_only.items():
        shares = abs(current[code])
        price = prices[code]
        amount = round(shares * price)
        rows.append({"date": DATE, "code": code, "name": name, "action": action,
                      "price": price, "amount": amount, "sector": sector})

    # 2) 에스피지처럼 "숏 보유 -> 롱 신규전환" 같은 케이스: exit_only에 없지만 현재 부호와
    #    목표 부호가 다르면(숏이었는데 롱 목표, 혹은 그 반대) 먼저 전량 반대매매로 청산.
    name_sector = {c: (n, s) for c, n, s, _w in long_target}
    if has_short_book:
        name_sector.update({c: (n, s) for c, n, s, _w in short_target})
    long_codes = {c for c, *_ in long_target}
    short_codes = {c for c, *_ in short_target} if has_short_book else set()
    for code, cur_shares in current.items():
        if code in exit_only:
            continue
        if cur_shares > 0 and code in short_codes and code not in long_codes:
            # 롱 보유 -> 신규 숏 목표: 롱 전량 매도 후 아래 3)에서 신규 숏 진입
            price = prices[code]
            amount = round(cur_shares * price)
            name, sector = name_sector[code]
            rows.append({"date": DATE, "code": code, "name": name, "action": "SELL",
                         "price": price, "amount": amount, "sector": sector})
        elif cur_shares < 0 and code in long_codes and code not in short_codes:
            # 숏 보유 -> 신규 롱 목표: 숏 전량 커버 후 아래 3)에서 신규 롱 진입
            price = prices[code]
            amount = round(abs(cur_shares) * price)
            name, sector = name_sector[code]
            rows.append({"date": DATE, "code": code, "name": name, "action": "COVER",
                         "price": price, "amount": amount, "sector": sector})

    # 3) 롱 목표비중 조정/신규진입
    for code, name, sector, w in long_target:
        price = prices[code]
        target_shares = round(w / 100 * aum / price)
        cur = current.get(code, 0)
        cur_long = cur if cur > 0 else 0  # 위 2)에서 숏->롱 전환분은 이미 청산했으니 순수 롱 기준으로 delta 계산
        delta = target_shares - cur_long
        if delta == 0:
            continue
        action = "BUY" if delta > 0 else "SELL"
        qty = abs(delta)
        amount = round(qty * price)
        rows.append({"date": DATE, "code": code, "name": name, "action": action,
                      "price": price, "amount": amount, "sector": sector})

    # 4) 숏 목표비중 조정/신규진입
    if has_short_book:
        for code, name, sector, w in short_target:
            price = prices[code]
            target_shares = round(w / 100 * aum / price)
            cur = current.get(code, 0)
            cur_short_mag = abs(cur) if cur < 0 else 0
            delta = target_shares - cur_short_mag
            if delta == 0:
                continue
            action = "SHORT" if delta > 0 else "COVER"
            qty = abs(delta)
            amount = round(qty * price)
            rows.append({"date": DATE, "code": code, "name": name, "action": action,
                          "price": price, "amount": amount, "sector": sector})

    return rows


def main():
    print("=== 코스닥 롱숏(개별종목) ===")
    indiv_rows = build_trades_for_fund(LONG_TARGET, SHORT_TARGET, INDIV_CURRENT, INDIV_EXIT_ONLY, INDIV_AUM, has_short_book=True)
    df1 = pd.DataFrame(indiv_rows)
    print(df1.to_string(index=False))
    buy = df1[df1.action.isin(["BUY"])]["amount"].sum()
    sell = df1[df1.action.isin(["SELL"])]["amount"].sum()
    short_ = df1[df1.action.isin(["SHORT"])]["amount"].sum()
    cover = df1[df1.action.isin(["COVER"])]["amount"].sum()
    print(f"BUY={buy:,} SELL={sell:,} SHORT={short_:,} COVER={cover:,}")

    print("\n=== 코스닥 롱숏(지수) ===")
    index_rows = build_trades_for_fund(LONG_TARGET, [], INDEX_CURRENT, INDEX_EXIT_ONLY, INDEX_AUM, has_short_book=False)
    df2 = pd.DataFrame(index_rows)
    print(df2.to_string(index=False))

    p1 = os.path.join(MANUAL_DIR, "kosdaq_long_short_trades.csv")
    p2 = os.path.join(MANUAL_DIR, "kosdaq_long_short_index_trades.csv")
    existing1 = pd.read_csv(p1, dtype={"code": str})
    existing2 = pd.read_csv(p2, dtype={"code": str})
    pd.concat([existing1, df1], ignore_index=True).to_csv(p1, index=False, encoding="utf-8")
    pd.concat([existing2, df2], ignore_index=True).to_csv(p2, index=False, encoding="utf-8")
    print(f"\n저장 완료: {p1}, {p2}")


if __name__ == "__main__":
    main()
