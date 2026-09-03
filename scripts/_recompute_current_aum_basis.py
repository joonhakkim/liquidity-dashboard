"""
일회성 마이그레이션 스크립트(2026-09-03) - 트로이 MP / 민구 MP의 과거 매매 전체를
"10억원 고정 기준"에서 "그 시점 직전 거래일 종가 기준 총평가액(AUM) 기준"으로 재계산해서
data/manual/{troy,mingu}_mp_trades.csv를 덮어쓴다.

배경: 지금까지 신규 편입/비중 확대 지시("OO 3% 편입" 등)를 항상 TOTAL_CAPITAL(10억원) 고정
기준으로 계산해왔는데, 사용자가 "포트폴리오 편입편출은 원래 현재 총평가액 기준으로 하는 게
일반적인 관행 아니냐"고 지적 - 맞는 말이라 트로이/민구 두 MP는 과거 매매까지 소급해서
다시 계산하기로 함(2026-09-03).

방법론:
1) 각 매매 row에 대해:
   - BUY: 원래 그 거래 금액 / TOTAL_CAPITAL = "의도했던 비중%"으로 역산. 이 비중%를 그대로
     유지한 채, 그 거래일 "직전 거래일 종가 기준 AUM"에 곱해서 새 금액을 산정(whole-share
     반올림). 신규 편입이든 기존 종목 추가매수든 동일하게 적용 - 어차피 "그 시점에 AUM의 X%를
     이 매매에 투입한다"는 의미로 통일해서 해석(다르게 볼 근거가 없는 과거 매매도 많아서).
   - SELL: 원래 그 거래가 "그 시점 보유 주식의 몇 %를 매도했는지" 비율을 역산(전량매도면
     100%=1.0). 이 비율을 새로 계산된(재계산된) 보유 주식수에 동일하게 적용.
2) 편입일(inception) 자체는 매매 전이라 AUM이 정확히 10억원이므로, 이 알고리즘을 편입일부터
   그대로 적용해도 편입일 매매는 자동으로 원래 금액과 동일하게 나온다(별도 분기 불필요).
3) 날짜 순으로 캐스케이딩 재구성 - 앞선 매매가 바뀌면 그 이후 매매들의 "직전일 AUM" 기준점도
   같이 바뀐다. 가격은 실제 시장가 그대로(변경 없음), 바뀌는 건 오직 각 매매의 금액/주식수뿐.
"""
import os
import shutil
from datetime import datetime

import pandas as pd

TOTAL_CAPITAL = 1_000_000_000
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MANUAL_DIR = os.path.join(DATA_DIR, "manual")


def load_prices(path):
    df = pd.read_csv(path, parse_dates=["date"], dtype={"code": str})
    df.columns = ["date", "price", "code"]
    return df.pivot(index="date", columns="code", values="price").sort_index()


def recompute(trades_path, prices_path):
    trades = pd.read_csv(trades_path, dtype={"code": str})
    trades["date"] = pd.to_datetime(trades["date"])
    prices_wide = load_prices(prices_path)
    inception = trades["date"].min()
    all_dates = [d for d in prices_wide.index if d >= inception]

    # --- pass 1: 원본(10억 고정) 궤적 - SELL 비율 역산용 ---
    orig_shares_running = {}
    row_info = []
    for idx, row in trades.iterrows():
        c = row["code"]
        qty = row["amount"] / row["price"]
        before = orig_shares_running.get(c, 0.0)
        if row["action"] == "BUY":
            weight_pct = row["amount"] / TOTAL_CAPITAL
            orig_shares_running[c] = before + qty
            row_info.append({"idx": idx, "mode": "BUY", "weight_pct": weight_pct})
        elif row["action"] == "SELL":
            ratio = qty / before if before else 1.0
            orig_shares_running[c] = before - qty
            row_info.append({"idx": idx, "mode": "SELL", "ratio": ratio})
        else:
            row_info.append({"idx": idx, "mode": row["action"]})

    # --- pass 2: 재계산 궤적, 날짜순 캐스케이딩 ---
    trades_idx_by_date = {}
    for i, row in trades.iterrows():
        trades_idx_by_date.setdefault(row["date"], []).append(i)

    revised_shares = {}
    revised_cash = TOTAL_CAPITAL
    prior_aum = TOTAL_CAPITAL
    new_trades = trades.copy()

    for d in all_dates:
        if d in trades_idx_by_date:
            for i in trades_idx_by_date[d]:
                row = trades.loc[i]
                c = row["code"]
                info = next(x for x in row_info if x["idx"] == i)
                price = row["price"]
                if info["mode"] == "BUY":
                    target_amount = info["weight_pct"] * prior_aum
                    shares = round(target_amount / price)
                    new_amount = shares * price
                    revised_shares[c] = revised_shares.get(c, 0.0) + shares
                    revised_cash -= new_amount
                    new_trades.at[i, "amount"] = int(new_amount)
                elif info["mode"] == "SELL":
                    before = revised_shares.get(c, 0.0)
                    shares = round(before * info["ratio"])
                    new_amount = shares * price
                    revised_shares[c] = before - shares
                    revised_cash += new_amount
                    new_trades.at[i, "amount"] = int(new_amount)
        mv = 0.0
        for c, sh in revised_shares.items():
            if sh != 0 and c in prices_wide.columns and not pd.isna(prices_wide.at[d, c]):
                mv += sh * prices_wide.at[d, c]
        prior_aum = revised_cash + mv

    return trades, new_trades, revised_cash, prior_aum


def main():
    targets = [
        ("troy_mp_trades.csv", os.path.join(DATA_DIR, "troy_mp_prices.csv")),
        ("mingu_mp_trades.csv", os.path.join(DATA_DIR, "mingu_mp_prices.csv")),
    ]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for fname, ppath in targets:
        tpath = os.path.join(MANUAL_DIR, fname)
        backup_path = tpath + f".bak_{ts}"
        shutil.copy2(tpath, backup_path)

        orig, new, cash, aum = recompute(tpath, ppath)
        diff = new.copy()
        diff["orig_amount"] = orig["amount"]
        diff["diff"] = diff["amount"] - diff["orig_amount"]
        print(f"=== {fname} ===")
        print(f"백업: {backup_path}")
        print(f"최종 현금: {cash:,.0f} / 최종 AUM: {aum:,.0f} / 현금비중(AUM기준): {cash/aum*100:.2f}%")
        print(diff[["date", "name", "action", "orig_amount", "amount", "diff"]].to_string(index=False))
        print()

        new.to_csv(tpath, index=False, encoding="utf-8")
        print(f"저장 완료: {tpath}\n")


if __name__ == "__main__":
    main()
