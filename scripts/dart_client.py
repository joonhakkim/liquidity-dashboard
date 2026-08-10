"""
DART(전자공시시스템) OpenAPI 공용 헬퍼.

- corpCode.xml(전체 상장사 고유번호 매핑)을 받아 로컬에 캐시하고, 종목명 -> corp_code
  조회 함수를 제공한다.
- 단일회사 전체 재무제표(fnlttSinglAcntAll) 조회 함수를 제공한다.

문서: https://opendart.fss.or.kr/guide/main.do
"""
import io
import os
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

DART_API_KEY = os.environ.get("DART_API_KEY")
if not DART_API_KEY:
    raise SystemExit("DART_API_KEY가 .env에 설정되어 있지 않습니다.")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CACHE_DIR = os.path.join(DATA_DIR, "screening", "cache")
CORP_CODE_CACHE = os.path.join(CACHE_DIR, "corpCode.xml")

BASE_URL = "https://opendart.fss.or.kr/api"


def _download_corp_code_xml():
    os.makedirs(CACHE_DIR, exist_ok=True)
    r = requests.get(f"{BASE_URL}/corpCode.xml", params={"crtfc_key": DART_API_KEY}, timeout=60)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    xml_bytes = zf.read(zf.namelist()[0])
    with open(CORP_CODE_CACHE, "wb") as f:
        f.write(xml_bytes)
    return xml_bytes


def load_corp_code_map(max_age_days=7):
    """종목명 -> corp_code 딕셔너리. 상장사(stock_code 존재)만 포함, 이름 중복 시 첫 항목 유지."""
    need_download = True
    if os.path.exists(CORP_CODE_CACHE):
        age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(CORP_CODE_CACHE))
        need_download = age > timedelta(days=max_age_days)

    if need_download:
        print("DART corpCode.xml 다운로드 중...")
        xml_bytes = _download_corp_code_xml()
    else:
        with open(CORP_CODE_CACHE, "rb") as f:
            xml_bytes = f.read()

    root = ET.fromstring(xml_bytes)
    name_to_code = {}
    stock_code_to_corp_code = {}
    name_to_stock_code = {}
    for item in root.findall("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        if not stock_code:
            continue
        name = item.findtext("corp_name").strip()
        corp_code = item.findtext("corp_code").strip()
        if name not in name_to_code:
            name_to_code[name] = corp_code
            name_to_stock_code[name] = stock_code
        stock_code_to_corp_code[stock_code] = corp_code
    return name_to_code, stock_code_to_corp_code, name_to_stock_code


def get_financials(corp_code, bsns_year, reprt_code, fs_div="CFS"):
    """단일회사 전체 재무제표. reprt_code: 11013=1분기, 11012=반기, 11014=3분기, 11011=사업보고서(연간).
    fs_div: CFS(연결) 우선 시도, 없으면 OFS(개별)로 재시도.
    """
    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bsns_year": str(bsns_year),
        "reprt_code": reprt_code,
        "fs_div": fs_div,
    }
    r = requests.get(f"{BASE_URL}/fnlttSinglAcntAll.json", params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("status") == "013":  # 데이터 없음
        return []
    if data.get("status") != "000":
        if fs_div == "CFS":
            return get_financials(corp_code, bsns_year, reprt_code, fs_div="OFS")
        return []
    return data.get("list", [])


def _find_row(rows, account_names, sj_divs):
    for row in rows:
        if row.get("sj_div") not in sj_divs:
            continue
        nm = (row.get("account_nm") or "").replace(" ", "")
        for cand in account_names:
            if cand.replace(" ", "") == nm:
                return row
    return None


def extract_account(rows, account_names, sj_divs=("IS", "CIS")):
    """rows(재무제표 list)에서 sj_div(IS/CIS=손익·포괄손익계산서)와 계정명으로 금액을 찾는다.
    account_names: 후보 계정명 리스트(회사마다 표기가 달라 '매출액'/'수익(매출액)' 등 여러 후보를 시도).
    DART는 회사에 따라 개별손익계산서(IS)만 쓰거나 포괄손익계산서(CIS)만 쓴다 - 둘 다 확인한다.

    반환값(thstrm_amount)의 의미는 reprt_code에 따라 다르다:
      11013(1분기) -> 1분기 단독(=누적과 동일), 11012(반기)/11014(3분기) -> 해당 분기 단독 3개월,
      11011(사업보고서) -> 연간 합계. 분기 "누적"이 필요하면 extract_account_cumulative를 쓴다.
    """
    row = _find_row(rows, account_names, sj_divs)
    if row is None:
        return None
    val = row.get("thstrm_amount", "").replace(",", "")
    try:
        return int(val)
    except ValueError:
        return None


def extract_account_cumulative(rows, account_names, sj_divs=("IS", "CIS")):
    """thstrm_add_amount(당기누적) 값. 11012=반기(6개월)누적, 11014=3분기(9개월)누적.
    11013/11011은 누적=단독이라 add_amount가 비어있거나 없을 수 있어 extract_account로 대체 조회한다.
    """
    row = _find_row(rows, account_names, sj_divs)
    if row is None:
        return None
    val = (row.get("thstrm_add_amount") or "").replace(",", "")
    if not val:
        return extract_account(rows, account_names, sj_divs)
    try:
        return int(val)
    except ValueError:
        return None
