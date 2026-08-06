"""
KOSPI 관련 일별 데이터를 가져와 data/krx_raw.csv 로 저장한다.

조사 결과 요약 (자세한 경위는 대화 로그 참고):
- data.krx.co.kr(pykrx 포함)의 투자자별 순매매대금 / 지수 OHLCV / 외국인 보유비중
  API들은 모두 로그인 세션이 필요하다. pykrx의 KRX_ID/KRX_PW 자동 로그인은
  KRX가 비밀번호를 브라우저 JS(nppfs, INITECH류 E2E 암호화 모듈)로 암호화해
  전송하도록 바뀌면서 더 이상 동작하지 않는다 (평문 전송 -> "패스워드 불일치").
- KRX Open API(openapi.krx.co.kr, AUTH_KEY 방식)는 API별로 활용신청 승인이
  필요하다. "코스피 시리즈 일별시세정보"(kospi_dd_trd, 지수 종가/거래대금)는
  아직 승인 안 됐지만, "유가증권 일별매매정보"(stk_bydd_trd, 종목별 시세)는
  승인됐다. 그래서:
  - 코스피 종가/거래대금: kospi_dd_trd를 먼저 시도하고, 401이면 네이버 금융
    (finance.naver.com) 일별시세 페이지 파싱으로 자동 폴백한다.
  - 코스피 시가총액: stk_bydd_trd로 유가증권시장(KOSPI) 전종목의 MKTCAP을
    일자별로 합산해서 구한다 (지수 API에는 없지만 종목별 API에는 있음).
- 투자자별 순매매대금은 KOFIA FreeSIS 수동 다운로드(fetch_kofia.py)로 채워진다.
  외국인 보유비중은 아직 무료/로그인 없이 가져올 방법을 못 찾아 비워둔다.
"""
import os
import time
from datetime import datetime, timedelta
from io import StringIO

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

KRX_OPEN_API_KEY = os.environ.get("KRX_OPEN_API_KEY")
KOSPI_DD_TRD_URL = "https://data-dbg.krx.co.kr/svc/apis/idx/kospi_dd_trd"
STK_BYDD_TRD_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
NAVER_INDEX_URL = "https://finance.naver.com/sise/sise_index_day.naver"

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "krx_raw.csv")

BACKFILL_YEARS = 5
UNAVAILABLE_COLUMNS = [
    "indiv_net_value",
    "foreign_net_value",
    "inst_net_value",
    "other_corp_net_value",
    "foreign_ownership_ratio",
]


def determine_date_range():
    today = datetime.today()
    existing = pd.read_csv(OUT_PATH, parse_dates=["date"]) if os.path.exists(OUT_PATH) else None
    if existing is not None and len(existing) > 0:
        last_date = existing["date"].max()
        start = (last_date + timedelta(days=1)).date()
        print(f"기존 데이터 발견: 마지막 저장일 {last_date.date()} 다음날부터 증분 수집")
    else:
        start = (today - timedelta(days=365 * BACKFILL_YEARS)).date()
        print(f"기존 데이터 없음: 최근 {BACKFILL_YEARS}년 백필")
    end = today.date()
    return existing, start, end


def fetch_kospi_index_krx_open_api(start, end):
    """KRX Open API (활용신청 승인 필요). 승인 안 됐으면 빈 DataFrame 반환."""
    if not KRX_OPEN_API_KEY:
        return pd.DataFrame(columns=["date", "kospi_close", "kospi_trading_value", "kospi_market_cap"])

    print(f"코스피 지수(Open API kospi_dd_trd) 시도 중 ({start} ~ {end})...")
    headers = {"AUTH_KEY": KRX_OPEN_API_KEY}
    rows = []
    day = start
    warned_401 = False
    while day <= end:
        basDd = day.strftime("%Y%m%d")
        try:
            r = requests.get(KOSPI_DD_TRD_URL, params={"basDd": basDd}, headers=headers, timeout=15)
        except requests.RequestException as e:
            print(f"  {basDd}: 요청 실패 ({e})")
            day += timedelta(days=1)
            continue

        if r.status_code == 401:
            if not warned_401:
                print("  401 Unauthorized - kospi_dd_trd 활용신청이 아직 승인되지 않음. 네이버 금융으로 폴백합니다.")
                warned_401 = True
            break  # 승인 안 된 상태에서 날짜별로 계속 시도할 필요 없음

        if r.status_code != 200:
            day += timedelta(days=1)
            continue

        data = r.json().get("OutBlock_1", [])
        for item in data:
            if item.get("IDX_NM") != "코스피":
                continue
            rows.append({
                "date": pd.to_datetime(item["BAS_DD"]),
                "kospi_close": pd.to_numeric(item.get("CLSPRC_IDX"), errors="coerce"),
                "kospi_trading_value": pd.to_numeric(item.get("ACC_TRDVAL"), errors="coerce"),
                "kospi_market_cap": pd.to_numeric(item.get("MKTCAP"), errors="coerce"),
            })
        day += timedelta(days=1)
        time.sleep(0.1)

    return pd.DataFrame(rows)


def fetch_kospi_index_naver(start, end):
    """finance.naver.com 일별시세 페이지를 페이지네이션하며 파싱 (로그인/키 불필요).
    거래대금 단위는 백만원(원본 표시 그대로)."""
    print(f"코스피 지수(네이버 금융) 수집 중 ({start} ~ {end})...")
    session = requests.Session()
    session.trust_env = False
    rows = []
    page = 1
    while True:
        try:
            r = session.get(
                NAVER_INDEX_URL, params={"code": "KOSPI", "page": page},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=15,
            )
            r.encoding = "euc-kr"
            tables = pd.read_html(StringIO(r.text))
        except Exception as e:
            print(f"  page {page}: 요청/파싱 실패 ({e})")
            break

        df = tables[0].dropna().copy()
        if df.empty:
            break
        df.columns = ["date_str", "close", "change", "pct", "volume_k", "value_m"]
        df["date"] = pd.to_datetime(df["date_str"], format="%Y.%m.%d")

        for _, row in df.iterrows():
            d = row["date"].date()
            if start <= d <= end:
                rows.append({
                    "date": row["date"],
                    "kospi_close": pd.to_numeric(row["close"], errors="coerce"),
                    "kospi_trading_value": pd.to_numeric(row["value_m"], errors="coerce"),
                })

        if df["date"].min().date() < start:
            break
        page += 1
        if page > 600:  # 안전장치: 약 16년치, 이 이상은 안 감
            break
        time.sleep(0.15)

    return pd.DataFrame(rows)


def fetch_kospi_index(start, end):
    df = fetch_kospi_index_krx_open_api(start, end)
    if df.empty:
        df = fetch_kospi_index_naver(start, end)
    return df


def fetch_kospi_market_cap(start, end):
    """stk_bydd_trd(유가증권 일별매매정보, 종목별)로 KOSPI 전종목 시가총액을 합산.
    지수 API(kospi_dd_trd)는 아직 활용신청 승인이 안 됐지만 이 종목별 API는 승인됨."""
    if not KRX_OPEN_API_KEY:
        return pd.DataFrame(columns=["date", "kospi_market_cap"])

    print(f"코스피 시가총액(Open API stk_bydd_trd 합산) 수집 중 ({start} ~ {end})...")
    headers = {"AUTH_KEY": KRX_OPEN_API_KEY}
    rows = []
    day = start
    n_days = 0
    while day <= end:
        basDd = day.strftime("%Y%m%d")
        try:
            r = requests.get(STK_BYDD_TRD_URL, params={"basDd": basDd}, headers=headers, timeout=20)
        except requests.RequestException as e:
            print(f"  {basDd}: 요청 실패 ({e})")
            day += timedelta(days=1)
            continue

        if r.status_code != 200:
            day += timedelta(days=1)
            continue

        data = r.json().get("OutBlock_1", [])
        if data:
            total = sum(
                pd.to_numeric(item.get("MKTCAP"), errors="coerce") or 0
                for item in data if item.get("MKT_NM") == "KOSPI"
            )
            if total:
                rows.append({"date": pd.to_datetime(basDd), "kospi_market_cap": total})

        n_days += 1
        if n_days % 30 == 0:
            print(f"  진행: {day} 까지 ({n_days}일 처리)")
        day += timedelta(days=1)
        time.sleep(0.1)

    return pd.DataFrame(rows)


def main():
    existing, start, end = determine_date_range()

    if start > end:
        print("증분 수집할 새 날짜가 없습니다. 종료.")
        return

    index_df = fetch_kospi_index(start, end)
    market_cap_df = fetch_kospi_market_cap(start, end)

    if index_df.empty and market_cap_df.empty:
        merged = pd.DataFrame(columns=["date", "kospi_close", "kospi_trading_value", "kospi_market_cap"] + UNAVAILABLE_COLUMNS)
    else:
        merged = index_df.merge(market_cap_df, on="date", how="outer") if not market_cap_df.empty else index_df
        if "kospi_market_cap" not in merged.columns:
            merged["kospi_market_cap"] = pd.NA
        for col in UNAVAILABLE_COLUMNS:
            merged[col] = pd.NA

    merged = merged.sort_values("date").reset_index(drop=True)

    got_new_data = len(index_df) > 0 or len(market_cap_df) > 0
    if not got_new_data:
        print("이번 실행에서 수집된 새 데이터가 없습니다.")

    if existing is not None:
        if got_new_data:
            merged = pd.concat([existing, merged], ignore_index=True)
            merged = merged.drop_duplicates(subset="date", keep="last").sort_values("date")
        else:
            merged = existing

    os.makedirs(DATA_DIR, exist_ok=True)
    merged.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"\n저장 완료: {OUT_PATH}")
    print(f"행 수: {len(merged)}")
    if len(merged):
        print(f"기간: {merged['date'].min()} ~ {merged['date'].max()}")
        print(merged.tail(3).to_string(index=False))


if __name__ == "__main__":
    main()
