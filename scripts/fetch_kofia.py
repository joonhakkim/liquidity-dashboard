"""
금융투자협회(KOFIA) FreeSIS(freesis.kofia.or.kr)에서 증시자금/신용공여/CMA/MMF
데이터를 자동으로 가져와 data/kofia_raw.csv 로 저장한다.

경위: FreeSIS는 공식 Open API가 없지만, 실제 화면(좌측 메뉴 트리를 통해 정상
진입한 경우)이 호출하는 내부 API를 브라우저 개발자도구로 확인했다.
(예전에 홈페이지 "바로가기" 링크로 들어갔을 때는 화면 렌더링 자체가 깨져서
API를 못 찾았는데, 좌측 메뉴 트리로 정상 진입하니 문제없이 렌더링됐다.)

발견한 API:
  POST https://freesis.kofia.or.kr/meta/getMetaDataList.do
  Content-Type: application/json
  Body: {"dmSearch": {...파라미터...}}
  응답: {"unit":"", "ds1": [{"TMPV1":"20260804","TMPV2":...,...}, ...]}
  (TMPV1=날짜 YYYYMMDD, TMPV2부터 지표 값. 인증/쿠키 전혀 필요 없는 순수 POST API)

이용약관: freesis.kofia.or.kr / kofia.or.kr 어디에도 자동조회를 명시적으로
금지하거나 허용한 이용약관 페이지가 없었다 (앞서 확인, robots.txt도 없음).
애매한 상태이나 사용자 확인 후 진행하기로 함.

4개 서비스(OBJ_NM)를 사용:
  1. STATSCU0100000060BO - 증시자금추이 (투자자예탁금/파생예수금/RP매도잔고/미수금/반대매매)
  2. STATSCU0100000070BO - 신용공여 잔고 추이 (신용융자·신용대주 전체/코스피/코스닥, 예탁증권담보융자)
  3. STATSCU0100000110BO - 운용대상별 CMA잔고 추이 (RP형/MMF형/종금형/발행어음형/기타형/합계)
  4. STATFND0400000051BO - 기간 MMF규모 추이 (전체/개인/법인)

단위는 전부 백만원(tmpV40=1000000)으로 통일해서 요청한다.
한 번 호출로 최대 몇 년 치가 오는지 테스트해보니 2.5년(632행)도 잘려나가지
않고 한번에 왔다 - 그래서 날짜 범위를 나눠 여러 번 호출할 필요가 없다.
"""
import os
from datetime import datetime, timedelta

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "kofia_raw.csv")

API_URL = "https://freesis.kofia.or.kr/meta/getMetaDataList.do"
BACKFILL_YEARS = 2

# (컬럼명 리스트, OBJ_NM, 날짜 파라미터 이름(시작,종료), 추가 고정 파라미터)
SOURCES = [
    (
        ["investor_deposit", "deriv_deposit", "broker_rp_balance",
         "margin_call_unpaid", "margin_call_liquidation", "margin_liquidation_ratio"],
        "STATSCU0100000060BO",
        ("tmpV45", "tmpV46"),
        {"tmpV1": "D", "tmpV40": "1000000", "tmpV41": "1"},
    ),
    (
        ["credit_loan_total", "credit_loan_kospi", "credit_loan_kosdaq",
         "credit_short_total", "credit_short_kospi", "credit_short_kosdaq",
         "subscription_loan", "pledged_securities_loan"],
        "STATSCU0100000070BO",
        ("tmpV45", "tmpV46"),
        {"tmpV1": "D", "tmpV40": "1000000", "tmpV41": "1"},
    ),
    (
        ["cma_rp", "cma_mmf", "cma_jonggeum", "cma_note", "cma_other", "cma_balance"],
        "STATSCU0100000110BO",
        ("tmpV45", "tmpV46"),
        {"tmpV1": "D", "tmpV40": "1000000", "tmpV41": "1", "tmpV59": ""},
    ),
    (
        ["mmf_total", "mmf_indiv", "mmf_corp", "mmf_other"],
        "STATFND0400000051BO",
        ("tmpV30", "tmpV31"),
        {"tmpV39": "1", "tmpV40": "1000000", "tmpV41": "1", "tmpV11": ""},
    ),
]


def fetch_source(columns, obj_nm, date_param_names, fixed_params, start, end):
    start_field, end_field = date_param_names
    body = {"dmSearch": {**fixed_params, start_field: start, end_field: end, "OBJ_NM": obj_nm}}
    r = requests.post(API_URL, json=body, timeout=30)
    r.raise_for_status()
    data = r.json()
    rows = data.get("ds1", [])
    if not rows:
        return pd.DataFrame(columns=["date"] + columns)

    records = []
    for row in rows:
        rec = {"date": pd.to_datetime(row["TMPV1"], format="%Y%m%d")}
        for i, col in enumerate(columns, start=2):
            rec[col] = row.get(f"TMPV{i}")
        records.append(rec)
    return pd.DataFrame(records)


def determine_date_range():
    today = datetime.today()
    existing = pd.read_csv(OUT_PATH, parse_dates=["date"]) if os.path.exists(OUT_PATH) else None
    if existing is not None and len(existing) > 0:
        last_date = existing["date"].max()
        start = (last_date - timedelta(days=3)).date()  # 며칠 겹치게 재수집(수정치 반영용)
        print(f"기존 데이터 발견: {last_date.date()} 부근부터 다시 수집(최근 수정치 반영)")
    else:
        start = (today - timedelta(days=365 * BACKFILL_YEARS)).date()
        print(f"기존 데이터 없음: 최근 {BACKFILL_YEARS}년 백필")
    return existing, start, today.date()


def main():
    existing, start, end = determine_date_range()
    start_s, end_s = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    merged = None
    for columns, obj_nm, date_params, fixed_params in SOURCES:
        print(f"수집 중: {obj_nm} ({', '.join(columns[:3])}...) [{start_s} ~ {end_s}]")
        try:
            df = fetch_source(columns, obj_nm, date_params, fixed_params, start_s, end_s)
        except requests.RequestException as e:
            print(f"  경고: {obj_nm} 요청 실패 ({e}), 스킵")
            continue
        print(f"  {len(df)}행 수신")
        merged = df if merged is None else merged.merge(df, on="date", how="outer")

    if merged is None or merged.empty:
        print("수집된 데이터가 없습니다.")
        return

    merged = merged.sort_values("date").reset_index(drop=True)

    if existing is not None and len(existing) > 0:
        combined = pd.concat([existing, merged], ignore_index=True)
        combined = combined.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
    else:
        combined = merged

    os.makedirs(DATA_DIR, exist_ok=True)
    combined.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"\n저장 완료: {OUT_PATH}")
    print(f"행 수: {len(combined)}, 기간: {combined['date'].min().date()} ~ {combined['date'].max().date()}")
    print(combined.tail(3).to_string(index=False))


if __name__ == "__main__":
    main()
