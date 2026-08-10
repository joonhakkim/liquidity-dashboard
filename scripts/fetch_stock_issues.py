"""
시가총액 상위 종목(기본 50개)에 한해 "이 종목/이슈를 움직일 만한 트리거"를
DART 공시(수시공시 등) + 네이버 종목뉴스 헤드라인에서 모아 data/screening/stock_issues.json 으로 저장한다.

전 종목(~2,500개)을 매일 다 긁으면 DART 일일 호출 한도(2만 건)와 네이버 스크래핑 부담이
너무 커서, 시총 상위 N개로만 범위를 좁혔다(사용자 확인).

- DART: list.json(수시공시 등, pblntf_ty 미지정=전체 유형)에서 최근 60일 내 최신 5건.
- 네이버: finance.naver.com/item/news_news.naver(종목뉴스 탭, 비공식 - Referer 필요) 최신 5건.
두 소스를 합쳐 최신순 최대 8건만 남긴다.
"""
import json
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

from dart_client import DART_API_KEY, BASE_URL, load_corp_code_map

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SCREEN_PATH = os.path.join(DATA_DIR, "screening", "op_growth_screen.csv")
OUT_PATH = os.path.join(DATA_DIR, "screening", "stock_issues.json")

TOP_N = 50
DART_LOOKBACK_DAYS = 60
MAX_PER_SOURCE = 5
MAX_TOTAL = 8


def fetch_dart_disclosures(corp_code):
    end = datetime.today()
    start = end - timedelta(days=DART_LOOKBACK_DAYS)
    params = {
        "crtfc_key": DART_API_KEY, "corp_code": corp_code,
        "bgn_de": start.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
        "page_count": 20,
    }
    try:
        r = requests.get(f"{BASE_URL}/list.json", params=params, timeout=20)
        data = r.json()
    except Exception:
        return []
    if data.get("status") != "000":
        return []
    items = []
    for it in data.get("list", [])[:MAX_PER_SOURCE]:
        rcept_dt = it.get("rcept_dt", "")
        items.append({
            "date": f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}" if len(rcept_dt) == 8 else rcept_dt,
            "title": it.get("report_nm", ""),
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it.get('rcept_no', '')}",
            "source": "DART",
        })
    return items


def fetch_naver_news(stock_code):
    try:
        r = requests.get(
            "https://finance.naver.com/item/news_news.naver",
            params={"code": stock_code, "page": "1", "sm": "title_entity_id.basic", "clusterId": ""},
            headers={"User-Agent": "Mozilla/5.0", "Referer": f"https://finance.naver.com/item/main.naver?code={stock_code}"},
            timeout=15,
        )
        r.encoding = "euc-kr"
    except Exception:
        return []
    import re
    html = r.text
    rows = re.findall(
        r'<a href="(/item/news_read\.naver\?article_id=\d+&office_id=\d+[^"]*)" class="tit"[^>]*>(.*?)</a>.*?'
        r'<td class="info">([^<]*)</td>\s*<td class="date">\s*([\d.]+ [\d:]+)',
        html, re.DOTALL,
    )
    items = []
    for href, title, office, date in rows[:MAX_PER_SOURCE]:
        clean_title = re.sub(r"<[^>]+>", "", title)
        clean_title = (clean_title.replace("&lsquo;", "'").replace("&rsquo;", "'")
                       .replace("&ldquo;", '"').replace("&rdquo;", '"')
                       .replace("&hellip;", "...").replace("&amp;", "&"))
        items.append({
            "date": date.strip()[:10],
            "title": clean_title.strip(),
            "url": "https://finance.naver.com" + href,
            "source": office.strip(),
        })
    return items


def main():
    screen = pd.read_csv(SCREEN_PATH)
    top = screen.head(TOP_N)
    name_to_code, _, name_to_stock_code = load_corp_code_map()

    result = {}
    for i, row in top.iterrows():
        name = row["종목명"]
        corp_code = name_to_code.get(name)
        stock_code = name_to_stock_code.get(name)
        if not corp_code and not stock_code:
            continue

        items = []
        if corp_code:
            items.extend(fetch_dart_disclosures(corp_code))
        if stock_code:
            items.extend(fetch_naver_news(stock_code))

        items.sort(key=lambda x: x["date"], reverse=True)
        result[name] = items[:MAX_TOTAL]
        print(f"  [{i+1}/{len(top)}] {name}: DART/뉴스 {len(items)}건 -> {len(result[name])}건 저장")
        time.sleep(0.1)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"\n저장 완료: {OUT_PATH} ({len(result)}개 종목)")


if __name__ == "__main__":
    main()
