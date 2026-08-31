"""
MP(모델 포트폴리오) 트래커들에 편입된 전 종목의 일별 종가를 네이버 차트 API(fchart.stock.naver.com)에서
가져와 포트폴리오별로 data/*_prices.csv 로 저장한다. 로그인/키 불필요.

mp_portfolios.PORTFOLIOS를 순회하며 포트폴리오마다 따로 처리한다(2026-08-20, "모멘텀 MP" 추가하며
"트로이 MP" 전용이던 걸 일반화). 편입 종목 목록은 각 포트폴리오의 매매일지(trades_path)에 등장한
종목코드 전부 - 팀이 이 매매일지 파일에 행을 추가/수정하는 것 자체가 "포트폴리오 변경"이다(수급정리
엑셀과 동일한 수동 파일 편집 패턴). 종목이 하나도 없으면(아직 미편입) 그 포트폴리오만 건너뛴다.
"""
import os

import pandas as pd
import requests

from mp_portfolios import ALL_PORTFOLIOS

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def fetch_price_history(code, count=3650):
    r = requests.get(
        "https://fchart.stock.naver.com/sise.nhn",
        params={"symbol": code, "timeframe": "day", "count": count, "requestType": 0},
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
    return pd.DataFrame(rows, columns=["date", "close"])


def fetch_for_portfolio(trades_path, out_path, label):
    if not os.path.exists(trades_path):
        print(f"[{label}] 매매일지 파일이 없습니다: {trades_path}")
        return
    trades = pd.read_csv(trades_path, dtype={"code": str})
    trades = trades.dropna(subset=["code"])
    trades["code"] = trades["code"].str.zfill(6)
    codes = sorted(trades["code"].unique())
    if not codes:
        print(f"[{label}] 아직 편입된 종목이 없습니다. 건너뜁니다.")
        return

    frames = []
    for code in codes:
        print(f"  [{label}] 수집 중: {code} ...")
        df = fetch_price_history(code)
        if df.empty:
            print(f"    경고: {code} 가격 데이터 없음")
            continue
        df["code"] = code
        frames.append(df)

    if not frames:
        print(f"[{label}] 수집된 가격 데이터가 없습니다.")
        return

    combined = pd.concat(frames, ignore_index=True).sort_values(["code", "date"]).reset_index(drop=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    combined.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"[{label}] 저장 완료: {out_path} (종목 수: {len(codes)}, 행 수: {len(combined)})")


def main():
    for p in ALL_PORTFOLIOS:
        fetch_for_portfolio(p["trades_path"], p["prices_path"], p["name"])


if __name__ == "__main__":
    main()
