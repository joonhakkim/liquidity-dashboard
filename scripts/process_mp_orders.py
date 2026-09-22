"""
MP 트래커용 "지시서" 자동 해석 - data/manual/mp_orders.csv에 사용자가 직접 적어둔 줄을
그날 종가 확정 후(파이프라인이 fetch_troy_mp_prices.py를 먼저 돌린 다음) 읽어서, 실제
매매일지(troy_mp_trades.csv 등)에 정확한 가격·수량으로 기록한다(2026-09-16 사용자 요청 -
"매일 말 안 해도 직접입력하면 자동으로 분석해서 읽을 수 있는거 없냐").

지시서 형식 (data/manual/mp_orders.csv, 사용자가 직접 편집):
  date,portfolio,action,name,code,pct,note
  2026-09-17,troy_mp,reduce_pct,삼성전자,,2,
  2026-09-17,troy_mp,increase_pct,SK하이닉스,,1,
  2026-09-17,troy_mp,new_entry_pct,한화오션,,3,
  2026-09-17,troy_mp,exit,코세스,,,
  2026-09-17,momentum_mp,rebalance_equal,,,4,전체 4%로
  2026-09-21,momentum_mp,swap,한올바이오파마,,,한미사이언스

- date: YYYY-MM-DD. 오늘 날짜인 지시만 처리한다(파이프라인이 매일 도니까 미래 날짜로
  미리 써둬도 그날이 되면 자동 처리됨).
- portfolio: mp_portfolios.py의 id (troy_mp/momentum_mp/mingu_mp/kosdaq_long_short_index/my_mp).
- action:
    reduce_pct / increase_pct - 현재 보유 종목의 비중을 그날 AUM 기준 pct%p만큼 축소/확대
    new_entry_pct - 신규 편입, 비중 pct%
    exit - 전량 편출(pct 무시, 보유수량 전부 매도)
    rebalance_equal - 그 포트폴리오의 "이날 처리된 다른 지시까지 반영한 뒤" 남은 보유종목
      전부를 pct%로 균등 리밸런싱(코드/이름 없이 한 줄로 포트폴리오 전체에 적용)
    swap - name(보유중)을 전량매도하고, 그 매도금액 그대로(같은 비중) note에 적은 종목을
      신규편입(2026-09-21 추가 - "A 편출하고 그 자리에 B로 교체" 요청이 반복돼서 만듦).
      note에 종목명만 적으면 자동완성으로 코드 조회(모호하면 스킵). "새이름|새코드" 형식으로
      코드까지 직접 적어도 됨.
- code: 비워두면 종목명으로 네이버 자동완성에서 찾는다(모호하면 처리 안 하고 에러 로그).
- sector는 sector_map.csv에서 code 기준으로 자동 조회한다(못 찾으면 "기타").

처리 후 그 줄은 "processed" 컬럼에 처리 시각을 적어서 다시 처리되지 않게 막는다(멱등).
숏(SHORT/COVER)은 지원 안 함 - 롱숏 포트폴리오는 당분간 채팅으로 처리.

이 스크립트는 run_troy_mp_pipeline.py에서 fetch_troy_mp_prices.py 다음, build_troy_mp_page.py
전에 실행된다 - 그래야 그날 확정 종가를 가지고 정확한 수량을 계산할 수 있다.
"""
import os
import sys
from datetime import datetime

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
from mp_portfolios import ALL_PORTFOLIOS, PRIVATE_PORTFOLIOS, TOTAL_CAPITAL  # noqa: E402
from build_troy_mp_page import load_trades, compute_holdings_table  # noqa: E402
from fetch_troy_mp_prices import fetch_price_history  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
ORDERS_PATH = os.path.join(DATA_DIR, "manual", "mp_orders.csv")
SECTOR_MAP_PATH = os.path.join(DATA_DIR, "sector_map.csv")
LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "mp_orders.log")

PORTFOLIO_BY_ID = {p["id"]: p for p in ALL_PORTFOLIOS + PRIVATE_PORTFOLIOS}


def log(lines):
    ts = datetime.now().isoformat()
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(f"[{ts}] {line}\n")
    for line in lines:
        print(line)


def resolve_code(name):
    """종목명 -> 6자리 코드. 네이버 자동완성에서 정확히 일치하는 종목명이 1개일 때만 반환."""
    try:
        r = requests.get("https://ac.stock.naver.com/ac", params={"q": name, "target": "stock"},
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        items = r.json().get("items", [])
    except Exception:
        return None
    exact = [it for it in items if it.get("name") == name and it.get("nationCode") == "KOR"]
    if len(exact) == 1:
        return exact[0]["code"]
    return None


def get_price_or_fetch(code, prices):
    """포트폴리오 자체 가격 캐시(prices, 그 포트폴리오의 *_prices.csv)에 없으면 네이버에서
    그 종목 하나만 즉시 조회해 최신 종가를 쓴다.

    fetch_troy_mp_prices.py는 각 포트폴리오의 "지금까지의 매매일지"에 이미 등장한 종목만
    받아오는데, new_entry_pct/swap 지시로 그 포트폴리오에 "처음" 편입하려는 종목은 구조적으로
    아직 매매일지에 없다 - 매매일지에 넣으려면 가격이 있어야 하고, 가격은 매매일지에 있어야
    받아오는 순환 의존이라 "오늘 가격 없음"으로 영영 스킵됐다(2026-09-23, mingu_mp에
    RF머트리얼즈 신규편입 지시가 troy_mp에만 그 종목이 있어서 이틀 연속 스킵된 걸 발견).
    다른 포트폴리오가 이미 그 종목을 갖고 있어도 가격 캐시가 포트폴리오별로 따로라 공유되지
    않으므로, 못 찾으면 그냥 이 종목 하나만 가볍게 즉시 조회한다."""
    price = prices.get(code)
    if price is not None:
        return price
    try:
        df = fetch_price_history(code, count=5)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    return float(df.sort_values("date").iloc[-1]["close"])


def load_sector_map():
    if not os.path.exists(SECTOR_MAP_PATH):
        return {}
    d = pd.read_csv(SECTOR_MAP_PATH, dtype={"code": str})
    return dict(zip(d["code"], d["sector"]))


def get_holdings(portfolio):
    trades_path, prices_path = portfolio["trades_path"], portfolio["prices_path"]
    if not os.path.exists(trades_path) or not os.path.exists(prices_path):
        return None
    trades = load_trades(trades_path)
    if trades.empty:
        return {"trades": trades, "holdings": [], "aum": TOTAL_CAPITAL, "prices": {}, "date": None}
    prices = pd.read_csv(prices_path, dtype={"code": str}, parse_dates=["date"])
    if prices.empty:
        return None
    prices["code"] = prices["code"].str.zfill(6)
    prices_wide = prices.pivot_table(index="date", columns="code", values="close").ffill()
    dates = prices_wide.index
    if len(dates) < 2:
        return None
    latest_date, prev_date = dates[-1], dates[-2]
    latest_prices = {c: prices_wide.at[latest_date, c] for c in prices_wide.columns
                      if not pd.isna(prices_wide.at[latest_date, c])}
    prev_prices = {c: prices_wide.at[prev_date, c] for c in prices_wide.columns
                    if not pd.isna(prices_wide.at[prev_date, c])}
    name_map = dict(zip(trades["code"], trades["name"]))
    sector_map = dict(zip(trades["code"], trades["sector"]))
    holdings, total_eval = compute_holdings_table(trades, latest_prices, prev_prices, name_map, sector_map)
    return {"trades": trades, "holdings": [h for h in holdings if h["shares"] is not None],
            "aum": total_eval, "prices": latest_prices, "date": latest_date.strftime("%Y-%m-%d")}


def append_trade(trades_path, date, code, name, action, price, amount, sector):
    line = f"{date},{code},{name},{action},{price:.0f},{amount:.1f},{sector}\n"
    with open(trades_path, "a", encoding="utf-8") as f:
        f.write(line)


def main():
    if not os.path.exists(ORDERS_PATH):
        print("mp_orders.csv 없음 - 처리할 지시 없음")
        return
    orders = pd.read_csv(ORDERS_PATH, dtype={"code": str})
    if "processed" not in orders.columns:
        orders["processed"] = ""
    today = datetime.now().strftime("%Y-%m-%d")
    # date == today가 아니라 <= today로 잡는다(2026-09-17 버그 수정) - 지시를 넣은 날짜의
    # 파이프라인 실행 시각을 이미 지나쳐서(예: 20:05 자동 실행 이후에 추가) 그날 못 걸린
    # 지시가 다음날 이후로도 영영 처리 안 되는 문제가 있었다(사용자가 "민구MP 변동이
    # 없는데"라고 지적해서 발견). 밀린 지시는 그 지시에 적힌 날짜(예: "9/16 종가로")가
    # 아니라 실제 처리되는 날의 종가를 쓰게 된다 - 하루 이상 밀리면 사용자에게 다시 확인.
    todo = orders[(orders["date"] <= today) & (orders["processed"].isna() | (orders["processed"] == ""))]
    if todo.empty:
        print(f"오늘({today}) 처리할 지시 없음")
        return

    sector_map = load_sector_map()
    summary = [f"===== {today} mp_orders 처리 시작 ====="]

    for pid, group in todo.groupby("portfolio"):
        portfolio = PORTFOLIO_BY_ID.get(pid)
        if portfolio is None:
            summary.append(f"[{pid}] 알 수 없는 포트폴리오 id, 스킵")
            continue
        state = get_holdings(portfolio)
        if state is None:
            summary.append(f"[{pid}] 가격 데이터를 아예 못 읽음 - 처리 보류(다음 실행에 재시도)")
            continue
        # 원래는 "오늘 가격이 아직 없으면 보류"였는데, 지시가 처리 예정일보다 늦게 들어와
        # 하루 이상 밀렸을 때도 이 조건 때문에 영영 처리가 안 되는 버그가 있었다(2026-09-17,
        # "민구MP 변동이 없는데" 지적으로 발견). 대신 "확보된 가장 최근 종가"를 그냥 쓰고,
        # 그게 오늘 날짜가 아니면(=지시가 밀렸으면) 로그에 명확히 남긴다.
        if state["date"] != today:
            summary.append(f"[{pid}] 참고: 최신 확보 종가가 {state['date']}(오늘 {today} 아님) - 이 날짜 기준으로 처리")

        trades_path = portfolio["trades_path"]
        aum = state["aum"]
        by_name = {h["name"]: h for h in state["holdings"]}
        prices = state["prices"]
        done_idx = []

        rebalance_row = None
        for idx, row in group.iterrows():
            action = row["action"]
            if action == "rebalance_equal":
                rebalance_row = (idx, row)
                continue  # 다른 지시부터 먼저 처리한 뒤 마지막에 적용

            name = row.get("name")
            code = row.get("code") if isinstance(row.get("code"), str) and row.get("code") else None
            if not code:
                code = resolve_code(name) if name and isinstance(name, str) else None
                if not code:
                    summary.append(f"[{pid}] '{name}' 종목코드 확인 실패 - 스킵(수동 확인 필요)")
                    continue
            code = code.zfill(6)
            sector = sector_map.get(code, "기타")

            if action == "exit":
                h = by_name.get(name) or next((x for x in state["holdings"] if x["code"] == code), None)
                if h is None:
                    summary.append(f"[{pid}] '{name}' 편출 지시인데 보유중 아님 - 스킵")
                    continue
                amt = h["shares"] * h["cur_price"]
                append_trade(trades_path, state["date"], code, h["name"], "SELL", h["cur_price"], amt, sector)
                summary.append(f"[{pid}] {h['name']} 전량편출 {h['shares']:.0f}주 @ {h['cur_price']:.0f} = {amt:,.0f}원")
                done_idx.append(idx)
                continue

            if action == "swap":
                h = by_name.get(name) or next((x for x in state["holdings"] if x["code"] == code), None)
                if h is None:
                    summary.append(f"[{pid}] '{name}' 교체 지시인데 보유중 아님 - 스킵")
                    continue
                note_val = row.get("note")
                if not isinstance(note_val, str) or not note_val.strip():
                    summary.append(f"[{pid}] '{name}' 교체 지시인데 note(새 종목)가 없음 - 스킵")
                    continue
                if "|" in note_val:
                    new_name, new_code = [x.strip() for x in note_val.split("|", 1)]
                else:
                    new_name, new_code = note_val.strip(), None
                if not new_code:
                    new_code = resolve_code(new_name)
                if not new_code:
                    summary.append(f"[{pid}] '{new_name}' 종목코드 확인 실패 - 스킵(수동 확인 필요)")
                    continue
                new_code = new_code.zfill(6)
                new_price = get_price_or_fetch(new_code, prices)
                if new_price is None:
                    summary.append(f"[{pid}] {new_name}({new_code}) 오늘 가격 없음 - 스킵(가격 이력 백필 필요)")
                    continue
                amt = h["shares"] * h["cur_price"]
                new_sector = sector_map.get(new_code, "기타")
                append_trade(trades_path, state["date"], h["code"], h["name"], "SELL", h["cur_price"], amt, sector_map.get(h["code"], "기타"))
                append_trade(trades_path, state["date"], new_code, new_name, "BUY", new_price, amt, new_sector)
                summary.append(f"[{pid}] {h['name']} -> {new_name} 교체매매 {amt:,.0f}원 (SELL @ {h['cur_price']:,.0f} / BUY @ {new_price:,.0f})")
                done_idx.append(idx)
                continue

            price = get_price_or_fetch(code, prices)
            if price is None:
                summary.append(f"[{pid}] {name}({code}) 오늘 가격 없음 - 스킵")
                continue
            pct = row.get("pct")
            if pd.isna(pct):
                summary.append(f"[{pid}] {name} pct 값 없음 - 스킵")
                continue
            target_amt = aum * float(pct) / 100.0

            if action in ("reduce_pct", "increase_pct"):
                act = "SELL" if action == "reduce_pct" else "BUY"
                sell_amt = target_amt
                if act == "SELL":
                    # target_amt(=pct%*AUM)가 실제 보유평가금액보다 크면 포지션이 마이너스로
                    # 뒤집히면서 원가가 0이 돼 이후 ret_pct 계산에서 ZeroDivisionError가 난다
                    # (2026-09-21, 파마리서치가 실제로는 AUM의 ~1.9%밖에 안 됐는데 "2%p 축소"
                    # 지시가 그대로 2%*AUM을 팔아버려서 발생 - 보유금액으로 캡을 씌워 전량매도로
                    # 자동 대체한다).
                    h = by_name.get(name) or next((x for x in state["holdings"] if x["code"] == code), None)
                    if h is not None:
                        cur_value = h["shares"] * h["cur_price"]
                        if sell_amt > cur_value:
                            sell_amt = cur_value
                append_trade(trades_path, state["date"], code, name, act, price, sell_amt, sector)
                summary.append(f"[{pid}] {name} {action} {pct}%p ({act}) {sell_amt:,.0f}원 @ {price:,.0f}"
                                + (" (보유금액 초과로 전량매도 처리)" if sell_amt != target_amt else ""))
                done_idx.append(idx)
            elif action == "new_entry_pct":
                append_trade(trades_path, state["date"], code, name, "BUY", price, target_amt, sector)
                summary.append(f"[{pid}] {name} 신규편입 {pct}% {target_amt:,.0f}원 @ {price:,.0f}")
                done_idx.append(idx)
            else:
                summary.append(f"[{pid}] 알 수 없는 action '{action}' - 스킵")

        if rebalance_row is not None:
            idx, row = rebalance_row
            pct = row.get("pct")
            if pd.isna(pct):
                summary.append(f"[{pid}] rebalance_equal pct 없음 - 스킵")
            else:
                # 방금 처리한 exit/reduce/new_entry까지 반영한 최신 보유현황을 다시 계산
                state2 = get_holdings(portfolio)
                target_amt = state2["aum"] * float(pct) / 100.0
                for h in state2["holdings"]:
                    cur_amt = h["shares"] * h["cur_price"]
                    delta = target_amt - cur_amt
                    if abs(delta) < h["cur_price"] * 0.5:
                        continue
                    act = "BUY" if delta > 0 else "SELL"
                    append_trade(trades_path, state2["date"], h["code"], h["name"], act, h["cur_price"], abs(delta), h["sector"])
                    summary.append(f"[{pid}] {h['name']} 균등리밸런싱 -> {pct}% ({act} {abs(delta):,.0f}원)")
                done_idx.append(idx)

        orders.loc[done_idx, "processed"] = datetime.now().isoformat()

    orders.to_csv(ORDERS_PATH, index=False, encoding="utf-8-sig")
    summary.append(f"===== {today} mp_orders 처리 종료 =====")
    log(summary)


if __name__ == "__main__":
    main()
