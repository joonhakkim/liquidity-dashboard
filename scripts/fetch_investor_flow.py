"""
코스피/코스닥 투자자별(개인/기관/외국인) 순매수를 네이버 금융
"투자자별 매매동향" 일별 페이지에서 받아와 data/investor_flow_raw.csv 로 저장한다.

  https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate=YYYYMMDD&sosok=01(코스피)/02(코스닥)
  표: 날짜 | 개인 | 외국인 | 기관계 | (기관 세부/기타법인/기타외국인...)  ← 단위 억원
  한 페이지에 bizdate 기준 최근 10영업일 정도가 나오고, bizdate를 더 과거로
  주면 그 이전 구간이 나온다. 필요한 만큼 과거로 페이지네이션한다.

과거 경위(중요):
- 원래는 KRX data.krx.co.kr 투자자별 API를 썼는데 로그인 세션이 필요해지면서 죽었고,
  그 뒤 사용자가 데이터터미널에서 내려받는 "수급정리*.xlsm"을 수동 파싱했다. 이건
  파일을 사람이 직접 갱신해야 해서 자주 밀렸다(2026-09, 8/10에서 한 달 정지).
- 2026-09-11: 네이버 집계 페이지로 교체. 로그인/키 불필요, 매일 자동 갱신.
  단위를 백만원으로 맞추기 위해 억원 × 100 을 저장한다(기존 xlsm 산출값과 동일 스케일).
  종목 단위가 아니라 시장 집계라 코스피/코스닥 세부는 페이지가 주는 값을 그대로 쓴다.

외국인 보유비중(%)은 이 소스에도 없어 계속 비워둔다.
"""
import os
import time
from datetime import datetime, timedelta
from io import StringIO

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "investor_flow_raw.csv")

URL = "https://finance.naver.com/sise/investorDealTrendDay.naver"
SOSOK = {"kospi": "01", "kosdaq": "02"}
HEADERS = {"User-Agent": "Mozilla/5.0"}
EOK_TO_MILLION = 100.0  # 억원 -> 백만원


def fetch_market(sosok_code, start_date, end_date):
    """[start_date, end_date] 구간의 (날짜, 개인, 외국인, 기관계)를 백만원 단위로."""
    rows = {}
    bizdate = end_date
    for _ in range(400):  # 안전장치
        r = requests.get(URL, params={"bizdate": bizdate.strftime("%Y%m%d"), "sosok": sosok_code},
                         headers=HEADERS, timeout=15)
        r.encoding = "euc-kr"
        try:
            tbl = pd.read_html(StringIO(r.text))[0]
        except (ValueError, IndexError):
            break
        sub = tbl.iloc[:, [0, 1, 2, 3]].copy()
        sub.columns = ["date", "indiv", "foreign", "inst"]
        sub = sub.dropna()
        if sub.empty:
            break

        page_dates = []
        for _, row in sub.iterrows():
            try:
                d = datetime.strptime(str(row["date"]).strip(), "%y.%m.%d").date()
            except ValueError:
                continue
            page_dates.append(d)
            if start_date <= d <= end_date:
                rows[d] = {
                    "indiv": pd.to_numeric(row["indiv"], errors="coerce") * EOK_TO_MILLION,
                    "foreign": pd.to_numeric(row["foreign"], errors="coerce") * EOK_TO_MILLION,
                    "inst": pd.to_numeric(row["inst"], errors="coerce") * EOK_TO_MILLION,
                }
        if not page_dates or min(page_dates) <= start_date:
            break
        bizdate = min(page_dates) - timedelta(days=1)
        time.sleep(0.2)
    return rows


def main():
    existing = pd.read_csv(OUT_PATH, parse_dates=["date"]) if os.path.exists(OUT_PATH) else None
    if existing is not None and len(existing):
        start = (existing["date"].max() + timedelta(days=1)).date()
    else:
        start = (datetime.today() - timedelta(days=365 * 6)).date()
    end = datetime.today().date()
    if start > end:
        print("증분 수집할 새 날짜가 없습니다. 종료.")
        return

    print(f"네이버 투자자별 매매동향 수집 ({start} ~ {end})...")
    kospi = fetch_market(SOSOK["kospi"], start, end)
    kosdaq = fetch_market(SOSOK["kosdaq"], start, end)
    dates = sorted(set(kospi) | set(kosdaq))
    if not dates:
        print("이번 실행에서 수집된 새 데이터가 없습니다.")
        return

    recs = []
    for d in dates:
        k = kospi.get(d, {})
        q = kosdaq.get(d, {})
        rec = {"date": pd.Timestamp(d)}
        for pfx, key in (("indiv", "indiv"), ("inst", "inst"), ("foreign", "foreign")):
            kv, qv = k.get(key), q.get(key)
            rec[f"{pfx}_net_kospi"] = kv
            rec[f"{pfx}_net_kosdaq"] = qv
            rec[f"{pfx}_net_total"] = (kv or 0) + (qv or 0) if (kv is not None or qv is not None) else None
        recs.append(rec)
    merged = pd.DataFrame(recs).sort_values("date").reset_index(drop=True)

    if existing is not None:
        combined = pd.concat([existing, merged], ignore_index=True)
        combined = combined.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
    else:
        combined = merged

    os.makedirs(DATA_DIR, exist_ok=True)
    combined.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n저장 완료: {OUT_PATH}")
    print(f"행 수: {len(combined)}, 기간: {combined['date'].min().date()} ~ {combined['date'].max().date()}")
    print(combined.tail(4).to_string(index=False))


if __name__ == "__main__":
    main()
