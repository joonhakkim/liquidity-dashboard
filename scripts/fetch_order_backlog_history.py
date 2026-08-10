"""
order_backlog_latest.csv 에서 수주잔고가 확인된 종목만 대상으로, 최근 8개 정기보고서(분기/반기/
사업보고서)를 훑어 시계열을 만든다. 1차 스캔(fetch_order_backlog.py)에서 이미 "수주잔고 공시가
있는 회사"로 좁혀놨기 때문에 전체를 다시 훑지 않고 이 좁은 대상만 처리해 API 호출을 아낀다.
"""
import os
import time

import pandas as pd

from dart_client import load_corp_code_map
from fetch_order_backlog import find_backlog_total, get_tokens, list_reports

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
LATEST_PATH = os.path.join(DATA_DIR, "screening", "order_backlog_latest.csv")
OUT_PATH = os.path.join(DATA_DIR, "screening", "order_backlog_history.csv")

MAX_REPORTS = 8


def main():
    latest = pd.read_csv(LATEST_PATH)
    names = latest["종목명"].tolist()
    print(f"수주잔고 확인된 {len(names)}개 종목의 과거 분기 백필")

    name_to_code, _, _ = load_corp_code_map()
    rows = []
    for i, name in enumerate(names, 1):
        corp_code = name_to_code.get(name)
        if not corp_code:
            continue
        try:
            reports = list_reports(corp_code, "20230101", "20260810", page_count=30)
        except Exception as e:
            print(f"[{i}/{len(names)}] {name}: 목록 실패 ({e})")
            continue
        reports.sort(key=lambda it: it["rcept_dt"], reverse=True)
        found = 0
        for rep in reports[:MAX_REPORTS]:
            try:
                tokens = get_tokens(rep["rcept_no"])
                time.sleep(0.12)
            except Exception:
                continue
            total = find_backlog_total(tokens)
            if total is not None:
                rows.append({"종목명": name, "기준일": rep["rcept_dt"], "수주잔고(억원)": round(total / 100, 1)})
                found += 1
        print(f"[{i}/{len(names)}] {name}: {found}개 분기 확보")
        if i % 20 == 0:
            pd.DataFrame(rows).to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
            print(f"  중간 저장 ({i}/{len(names)})")

    df = pd.DataFrame(rows).drop_duplicates(subset=["종목명", "기준일"])
    df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n저장 완료: {OUT_PATH} ({len(df)}행, {df['종목명'].nunique()}개 종목)")


if __name__ == "__main__":
    main()
