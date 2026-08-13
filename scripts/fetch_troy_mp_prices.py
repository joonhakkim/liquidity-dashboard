"""
트로이 MP(모델 포트폴리오)에 편입된 전 종목의 일별 종가를 네이버 차트 API(fchart.stock.naver.com)에서
가져와 data/troy_mp_prices.csv 로 저장한다. 로그인/키 불필요.

편입 종목 목록은 data/manual/troy_mp_trades.csv(매매일지)에 등장한 종목코드 전부를 대상으로 한다
- 팀이 이 매매일지 파일에 행을 추가/수정하는 것 자체가 "포트폴리오 변경"이다(수급정리 엑셀과 동일한
수동 파일 편집 패턴). 종목이 하나도 없으면(아직 미편입) 아무것도 안 하고 조용히 종료한다.
"""
import os

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
TRADES_PATH = os.path.join(DATA_DIR, "manual", "troy_mp_trades.csv")
OUT_PATH = os.path.join(DATA_DIR, "troy_mp_prices.csv")


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


def main():
    if not os.path.exists(TRADES_PATH):
        print("매매일지 파일이 없습니다:", TRADES_PATH)
        return
    trades = pd.read_csv(TRADES_PATH, dtype={"code": str})
    trades = trades.dropna(subset=["code"])
    trades["code"] = trades["code"].str.zfill(6)
    codes = sorted(trades["code"].unique())
    if not codes:
        print("트로이 MP에 아직 편입된 종목이 없습니다. 건너뜁니다.")
        return

    frames = []
    for code in codes:
        print(f"수집 중: {code} ...")
        df = fetch_price_history(code)
        if df.empty:
            print(f"  경고: {code} 가격 데이터 없음")
            continue
        df["code"] = code
        frames.append(df)

    if not frames:
        print("수집된 가격 데이터가 없습니다.")
        return

    combined = pd.concat(frames, ignore_index=True).sort_values(["code", "date"]).reset_index(drop=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    combined.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n저장 완료: {OUT_PATH}")
    print(f"종목 수: {len(codes)}, 행 수: {len(combined)}")


if __name__ == "__main__":
    main()
