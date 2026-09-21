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

BACKFILL_YEARS = 7  # 2026년 기준 2020년 초까지 (위험지수 모델 학습 표본 확보를 위해 확장)
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
        # 컬럼마다 소스 지연이 달라서(예: 네이버 폴백으로 kospi_close는 당일 채워지지만
        # KRX Open API 시가총액은 며칠 늦게 들어옴) 단순히 date.max() 다음날부터 받으면
        # 뒤늦게 들어오는 컬럼의 공백이 영영 안 메워진다. 살아있는 각 컬럼의
        # "마지막 유효값 날짜" 중 가장 이른 날 다음부터 다시 받는다. (중복은 keep=last로 정리)
        live_cols = [c for c in ("kospi_close", "kospi_trading_value", "kospi_market_cap")
                     if c in existing.columns and existing[c].notna().any()]
        last_valid = min(existing.loc[existing[c].notna(), "date"].max() for c in live_cols)
        start = (last_valid + timedelta(days=1)).date()
        print(f"기존 데이터 발견: 살아있는 컬럼 중 가장 이른 마지막 유효일 {last_valid.date()} 다음날부터 증분 수집")
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
        try:
            df["date"] = pd.to_datetime(df["date_str"], format="%Y.%m.%d")
        except ValueError:
            # 이 페이지 자체가 서비스 종료되면(2026-09-18 확인, "이 페이지는 더 이상
            # 제공되지 않습니다" 안내문이 표 형태로 내려옴) date_str이 날짜가 아니라
            # 안내 문구라 파싱이 그대로 죽었었다 - 예외 없이 빈 결과로 넘어가서
            # fetch_kospi_index()의 다음 폴백(야후 파이낸스)으로 넘어가게 한다.
            print(f"  page {page}: 이 소스가 더 이상 날짜 데이터를 안 줌(서비스 종료 추정) - 중단")
            break

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


def fetch_kospi_index_yahoo(start, end):
    """Yahoo Finance 차트 API(^KS11)로 코스피 종가만 받는다(로그인/키 불필요, fetch_gdx.py와
    동일 방식 - 2026-09-18 백테스트 작업 때 검증됨). KRX Open API(401, 미승인)와 네이버
    일별시세 페이지(서비스 종료, 2026-09-18 확인)가 둘 다 막혔을 때 마지막 폴백.
    거래대금은 이 API가 안 주는 단위(원화 거래대금이 아니라 주식수 거래량)라 여기서는
    kospi_close만 채우고 kospi_trading_value는 fetch_kospi_stock_agg(stk_bydd_trd 합산)에
    맡긴다."""
    print(f"코스피 지수(Yahoo Finance) 수집 중 ({start} ~ {end})...")
    p1 = int(datetime.combine(start, datetime.min.time()).timestamp())
    p2 = int((datetime.combine(end, datetime.min.time()) + timedelta(days=1)).timestamp())
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EKS11",
            params={"period1": p1, "period2": p2, "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=20,
        )
        r.raise_for_status()
        result = r.json()["chart"]["result"][0]
        ts = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except Exception as e:
        print(f"  야후 파이낸스 요청/파싱 실패 ({e})")
        return pd.DataFrame(columns=["date", "kospi_close"])

    df = pd.DataFrame({"date": pd.to_datetime(ts, unit="s").normalize(), "kospi_close": closes}).dropna()
    df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    df = df[(df["date"].dt.date >= start) & (df["date"].dt.date <= end)]
    return df


def fetch_kospi_index(start, end):
    df = fetch_kospi_index_krx_open_api(start, end)
    if df.empty:
        df = fetch_kospi_index_naver(start, end)
    if df.empty:
        df = fetch_kospi_index_yahoo(start, end)
    return df


def fetch_kospi_stock_agg(start, end):
    """stk_bydd_trd(유가증권 일별매매정보, 종목별)로 KOSPI 전종목의 시가총액(MKTCAP)과
    거래대금(ACC_TRDVAL)을 하루치 응답에서 한 번에 합산한다(예전엔 시가총액만 뽑았는데,
    2026-09-21에 네이버 일별시세 페이지가 죽어서 거래대금 소스가 아예 없어진 걸 발견하고
    - 이미 승인된 이 API에 거래대금도 들어있길래 - 같은 응답에서 같이 뽑도록 확장함.
    API 호출 횟수도 그대로라 더 비싸지지 않음).
    지수 API(kospi_dd_trd)는 아직 활용신청 승인이 안 됐지만 이 종목별 API는 승인됨."""
    if not KRX_OPEN_API_KEY:
        return pd.DataFrame(columns=["date", "kospi_market_cap", "kospi_trading_value"])

    print(f"코스피 시가총액·거래대금(Open API stk_bydd_trd 합산) 수집 중 ({start} ~ {end})...")
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
            kospi_items = [item for item in data if item.get("MKT_NM") == "KOSPI"]
            mktcap_total = sum(pd.to_numeric(item.get("MKTCAP"), errors="coerce") or 0 for item in kospi_items)
            trdval_total = sum(pd.to_numeric(item.get("ACC_TRDVAL"), errors="coerce") or 0 for item in kospi_items)
            if mktcap_total or trdval_total:
                rows.append({
                    "date": pd.to_datetime(basDd),
                    "kospi_market_cap": mktcap_total or None,
                    "kospi_trading_value": (trdval_total / 1e6) if trdval_total else None,  # 원 -> 백만원(기존 단위 통일)
                })

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
    stock_agg_df = fetch_kospi_stock_agg(start, end)

    if index_df.empty and stock_agg_df.empty:
        merged = pd.DataFrame(columns=["date", "kospi_close", "kospi_trading_value", "kospi_market_cap"] + UNAVAILABLE_COLUMNS)
    else:
        merged = index_df.merge(stock_agg_df, on="date", how="outer") if not stock_agg_df.empty else index_df
        for col in ("kospi_market_cap", "kospi_trading_value"):
            if col not in merged.columns:
                merged[col] = pd.NA
        for col in UNAVAILABLE_COLUMNS:
            merged[col] = pd.NA

    merged = merged.sort_values("date").reset_index(drop=True)

    got_new_data = len(index_df) > 0 or len(stock_agg_df) > 0
    if not got_new_data:
        print("이번 실행에서 수집된 새 데이터가 없습니다.")

    if existing is not None:
        if got_new_data:
            # 새로 받은 값이 우선하되, 새 값이 NaN인 칸은 기존 값을 보존한다
            # (재수집 구간에서 어떤 컬럼만 응답이 비었을 때 기존 유효값을 지우지 않도록).
            new_idx = merged.set_index("date")
            old_idx = existing.set_index("date")
            merged = new_idx.combine_first(old_idx).reset_index().sort_values("date")
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
