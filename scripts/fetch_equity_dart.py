"""
상장사 분기별 자본총계(원) 수집 - PBR 밴드 페이지(build_pbr_band.py)용 (2026-11-01 사용자 요청).
data/screening/cache/equity_dart.pkl = {"eq": {(종목코드, 'YYYYMM'): 자본총계 원}, "ni": {(종목코드, 'YYYYMM'): 당기순이익 연초 누적 원},
                                        "done": set((연도, 보고서코드, 묶음번호))}
- DART fnlttMultiAcnt(100사씩)로 2015년~현재 사업/1Q/반기/3Q 보고서의 자본총계를 받는다. 연결(CFS)이 있으면 연결, 없으면 별도(OFS).
- 당기순이익은 연초부터의 누적값으로 저장한다(1Q=3개월, 반기·3Q=thstrm_add_amount, 사업=연간). ROE=최근 4분기 합 ÷ 자본.
- 자본총계·당기순이익은 비지배지분을 포함한다(지배주주지분만 따로 뽑지 않음).
- 이미 받은 묶음은 건너뛰므로 중간에 끊겨도 이어받는다. 올해 보고서는 월요일마다 다시 받는다(정정공시·신규 제출 반영).
"""
import io
import os
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import date

import pandas as pd
import requests
from dotenv import load_dotenv

BASE = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(BASE, ".env"))
STORE = os.path.join(BASE, "data", "screening", "cache", "equity_dart.pkl")
KRX_DIR = os.path.join(BASE, "data", "screening", "cache", "research", "krx")
KEY = os.environ.get("DART_API_KEY")
REPORTS = {"11013": "03", "11012": "06", "11014": "09", "11011": "12"}   # 1Q, 반기, 3Q, 사업
START_YEAR = 2015
CHUNK = 100


def corp_map():
    r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": KEY}, timeout=120)
    x = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(x.read(x.namelist()[0]))
    m = {}
    for e in root.iter("list"):
        sc = (e.findtext("stock_code") or "").strip()
        if sc:
            m[sc] = e.findtext("corp_code")
    return m


def universe():
    """가장 최근 KRX 캐시에 있는 보통주(코드 끝자리 0) 전체."""
    files = sorted(f for f in os.listdir(KRX_DIR) if f.endswith(".pkl"))
    codes = set()
    for f in files[-6:]:
        x = pd.read_pickle(os.path.join(KRX_DIR, f))
        if len(x) and "ISU_CD" in x.columns:
            codes |= set(x["ISU_CD"].astype(str).str.zfill(6))
    return sorted(c for c in codes if c.endswith("0"))


def fetch(corp_codes, year, rc):
    for k in range(4):
        try:
            r = requests.get("https://opendart.fss.or.kr/api/fnlttMultiAcnt.json",
                             params={"crtfc_key": KEY, "corp_code": ",".join(corp_codes), "bsns_year": str(year), "reprt_code": rc},
                             timeout=60).json()
            st = r.get("status")
            if st == "000":
                return r.get("list", [])
            if st == "013":      # 조회된 데이터 없음
                return []
            if st == "020":
                raise SystemExit("DART 일일 호출 한도 초과 - 내일 이어받기")
            time.sleep(2 + 3 * k)
        except SystemExit:
            raise
        except Exception:
            time.sleep(2 + 3 * k)
    return None


def main():
    if not KEY:
        raise SystemExit("DART_API_KEY가 .env에 없습니다.")
    st = pd.read_pickle(STORE) if os.path.exists(STORE) else {"eq": {}, "ni": {}, "done": set()}
    eq, ni, done = st["eq"], st.setdefault("ni", {}), st["done"]
    cm = corp_map()
    codes = [c for c in universe() if c in cm]
    print(f"대상 {len(codes)}종목")
    chunks = [codes[i:i + CHUNK] for i in range(0, len(codes), CHUNK)]
    today = date.today()
    n_call = 0
    for year in range(START_YEAR, today.year + 1):
        for rc, mm in REPORTS.items():
            if year == today.year and int(mm) > today.month:
                continue
            for ci, ch in enumerate(chunks):
                key = (year, rc, ci)
                recent = year >= today.year and today.weekday() == 0     # 올해 보고서는 월요일마다 다시 받아 정정공시 반영
                if key in done and not recent:
                    continue
                rows = fetch([cm[c] for c in ch], year, rc)
                n_call += 1
                if rows is None:
                    print(f"  실패 {key} - 다음에 재시도")
                    continue
                best, best_ni = {}, {}
                for a in rows:
                    nm = a.get("account_nm") or ""
                    if nm == "자본총계":
                        kind = "eq"
                    elif ("순이익" in nm or "순손익" in nm) and not any(w in nm for w in ("법인세차감전", "포괄", "지배", "중단")):
                        kind = "ni"        # 당기순이익(손실)·분기순이익·반기순이익 등
                    else:
                        continue
                    sc = a.get("stock_code")
                    col = "thstrm_amount"
                    if kind == "ni" and rc in ("11012", "11014"):
                        col = "thstrm_add_amount"       # 반기·3Q는 누적값
                    v = pd.to_numeric(str(a.get(col, "")).replace(",", ""), errors="coerce")
                    if pd.isna(v):
                        continue
                    pr = 0 if a.get("fs_div") == "CFS" else 1     # 연결 우선
                    tgt = best if kind == "eq" else best_ni
                    if sc not in tgt or pr < tgt[sc][0]:
                        tgt[sc] = (pr, float(v))
                for sc, (_, v) in best.items():
                    eq[(sc, f"{year}{mm}")] = v
                for sc, (_, v) in best_ni.items():
                    ni[(sc, f"{year}{mm}")] = v
                done.add(key)
                time.sleep(0.15)
            pd.to_pickle({"eq": eq, "ni": ni, "done": done}, STORE)
            print(f"{year} {REPORTS[rc]}월 완료 (누적 {len(eq)}건, 호출 {n_call})", flush=True)
    pd.to_pickle({"eq": eq, "ni": ni, "done": done}, STORE)
    print(f"저장 완료: {STORE}")


if __name__ == "__main__":
    main()
