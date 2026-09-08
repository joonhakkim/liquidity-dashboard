"""
클리블랜드 연은 "Inflation Nowcasting" 페이지(공식 다운로드 API 없음, 자체 확인함
2026-09-08)를 매일 스크래핑해서 data/inflation_nowcast_raw.csv 에 누적 저장한다.

페이지 구조(2026-09-08 기준, pandas.read_html로 확인):
  - 테이블 0 = MoM(전월비) 예측 - 컬럼 [Month, CPI, Core CPI, PCE, Core PCE, Updated]
  - 테이블 1 = YoY(전년동월비) 예측 - 컬럼 동일
  - 테이블 2 = 분기 연율화 예측 - 컬럼 [Quarter, ...] (이 스크립트에서는 안 씀)
  값 자체는 정적 HTML에 그대로 박혀있어(JS 렌더링 아님) requests+pandas.read_html로
  충분 - Selenium 등 브라우저 자동화 불필요. 표 순서가 안 바뀌었다는 걸 MoM 값은
  보통 <1.5, YoY 값은 보통 >1.5라는 범위로 매 실행마다 검증(assert)해서, 나중에
  페이지 구조가 바뀌면 값이 뒤바뀌는 대신 에러로 바로 알아챌 수 있게 한다.

매일 실행해도 같은 (fetch_date, target_month) 조합이면 덮어쓰기(그날 재실행 시
최신값으로 갱신), 다른 날짜면 새로 누적 - 나우캐스트가 매 영업일 값이 바뀌는 걸
날짜별로 추적하기 위함.
"""
import io
import os
from datetime import datetime

import pandas as pd
import requests

URL = "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "inflation_nowcast_raw.csv")

COLS = ["CPI", "Core CPI", "PCE", "Core PCE"]
COL_MAP = {"CPI": "cpi", "Core CPI": "core_cpi", "PCE": "pce", "Core PCE": "core_pce"}


def fetch_tables():
    r = requests.get(URL, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    tables = pd.read_html(io.StringIO(r.text))
    if len(tables) < 2:
        raise SystemExit(f"예상과 다른 테이블 개수({len(tables)}개) - 페이지 구조가 바뀐 것 같습니다.")
    mom_raw, yoy_raw = tables[0], tables[1]

    def clean(t):
        t = t.copy()
        t = t[t["Month"].astype(str).str.match(r"^[A-Za-z]+ \d{4}$")]  # "Note: ..." 각주행 제거
        for c in COLS:
            t[c] = pd.to_numeric(t[c], errors="coerce")
        return t

    mom = clean(mom_raw)
    yoy = clean(yoy_raw)

    # 표 순서가 바뀌었는지 값 범위로 검증(MoM은 보통 -1.5~1.5%, YoY는 보통 1.5% 초과)
    if not mom[COLS].stack().abs().lt(2.0).all():
        raise SystemExit("MoM 테이블 값이 예상 범위(<2%) 밖입니다 - 표 순서가 바뀌었을 수 있습니다. 확인 필요.")
    if not (yoy[COLS].stack().abs() > 1.0).all():
        raise SystemExit("YoY 테이블 값이 예상 범위(>1%) 밖입니다 - 표 순서가 바뀌었을 수 있습니다. 확인 필요.")

    return mom, yoy


def main():
    mom, yoy = fetch_tables()
    fetch_date = datetime.now().strftime("%Y-%m-%d")

    rows = []
    months = sorted(set(mom["Month"]) | set(yoy["Month"]))
    for month in months:
        row = {"fetch_date": fetch_date, "target_month": month}
        mrow = mom[mom["Month"] == month]
        yrow = yoy[yoy["Month"] == month]
        updated = None
        for c in COLS:
            row[f"{COL_MAP[c]}_mom"] = mrow[c].iloc[0] if not mrow.empty else None
            row[f"{COL_MAP[c]}_yoy"] = yrow[c].iloc[0] if not yrow.empty else None
        if not mrow.empty:
            updated = mrow["Updated"].iloc[0]
        elif not yrow.empty:
            updated = yrow["Updated"].iloc[0]
        row["source_updated"] = updated
        rows.append(row)

    new_df = pd.DataFrame(rows)

    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(OUT_PATH):
        old = pd.read_csv(OUT_PATH)
        # 같은 (fetch_date, target_month) 조합은 오늘자로 덮어쓰기
        key_cols = ["fetch_date", "target_month"]
        old_keys = set(map(tuple, old[key_cols].values))
        new_keys = set(map(tuple, new_df[key_cols].values))
        old = old[~old[key_cols].apply(tuple, axis=1).isin(new_keys)]
        combined = pd.concat([old, new_df], ignore_index=True)
    else:
        combined = new_df

    combined = combined.sort_values(["fetch_date", "target_month"]).reset_index(drop=True)
    combined.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUT_PATH} (총 {len(combined)}행)")
    print(new_df.to_string(index=False))


if __name__ == "__main__":
    main()
