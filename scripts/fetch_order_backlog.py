"""
수주잔고(受注殘高) - DART에 이걸 위한 전용 구조화 API는 없다. 대신 정기보고서(분기/반기/사업보고서)
본문 안에 회사마다 다른 표기로("기말수주잔고" 표, "OOO계약 수주잔고" 문구 등) 들어있는 걸
문서를 통째로 받아 텍스트에서 "수주잔고" 라벨 주변 숫자를 휴리스틱으로 뽑아낸다.
(조선/건설/방산/플랜트처럼 수주 기반 업종만 이 공시가 있고, 대부분 기업은 없다 - 없으면 건너뛴다.)

추출 규칙(find_backlog_total):
1) "수주잔고" 라벨 근처(앞뒤)에 "합계"/"합 계" 행이 있으면 그 행의 마지막 숫자를 총합으로 본다
   (조선사처럼 품목별로 나열되다 마지막에 합계가 나오는 표 구조).
2) 합계 마커가 없으면 라벨 바로 뒤에 오는 첫 숫자를 그대로 총합으로 본다(건설사처럼 라벨=숫자
   직접 붙는 구조). 이때 같은 라벨이 여러 번 나와도(당분기/전분기 비교 등) 첫 번째(=최신) 것만 쓴다.

2단계로 나눠 돈다:
- 1단계(scan): 전체 종목의 "가장 최근 정기보고서" 1건만 확인해서 수주잔고 공시가 있는 회사를 찾는다.
- 2단계(backfill): 1단계에서 찾은 회사만 최근 N개 분기 정기보고서를 추가로 훑어 시계열을 만든다.
"""
import io
import os
import re
import sys
import time
import zipfile

import pandas as pd
import requests

from dart_client import DART_API_KEY, BASE_URL, load_corp_code_map

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SCREEN_PATH = os.path.join(DATA_DIR, "screening", "op_growth_screen.csv")
SCAN_OUT_PATH = os.path.join(DATA_DIR, "screening", "order_backlog_latest.csv")
HISTORY_OUT_PATH = os.path.join(DATA_DIR, "screening", "order_backlog_history.csv")

REPORT_TYPES = ["분기보고서", "반기보고서", "사업보고서"]
NUM_RE = re.compile(r"^\(?-?[\d,]{4,}\)?$")


def to_num(t):
    t = t.strip().replace(",", "")
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    if not t.isdigit():
        return None
    v = int(t)
    return -v if neg else v


def get_tokens(rcept_no):
    r = requests.get(f"{BASE_URL}/document.xml", params={"crtfc_key": DART_API_KEY, "rcept_no": rcept_no}, timeout=30)
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    content = zf.read(zf.namelist()[0]).decode("utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", "|", content)
    return [t.strip() for t in text.split("|") if t.strip()]


BACKLOG_LABEL_RE = re.compile(r"수주잔고|수주잔액|계약\s*분기말잔액|계약\s*기말잔액|건설계약\s*수주잔고")


def find_backlog_total(tokens):
    idxs = [i for i, t in enumerate(tokens) if BACKLOG_LABEL_RE.search(t) and "=" not in t]
    # "...수주잔고... 기재를 생략하였습니다" 같은 면책 문구에 낀 경우는 실제 수치가 아니므로 제외
    idxs = [i for i in idxs if "생략" not in tokens[i]]
    if not idxs:
        return None
    for i in idxs:
        lo, hi = max(0, i - 10), min(len(tokens), i + 80)
        for j in range(lo, hi):
            if tokens[j] in ("합계", "합 계", "부문  합계", "부문 합계"):
                nums = []
                for t in tokens[j + 1:j + 20]:
                    v = to_num(t)
                    if v is not None:
                        nums.append(v)
                    elif t not in ("-", "　"):
                        break
                if nums:
                    return nums[-1]
    i = idxs[0]
    for t in tokens[i + 1:i + 15]:
        v = to_num(t)
        if v is not None:
            return v
        if t not in ("-", "　"):
            break
    return None


def list_reports(corp_code, bgn_de, end_de, page_count=20):
    r = requests.get(
        f"{BASE_URL}/list.json",
        params={"crtfc_key": DART_API_KEY, "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de,
                "pblntf_ty": "A", "page_count": page_count},
        timeout=20,
    )
    data = r.json()
    if data.get("status") != "000":
        return []
    return [it for it in data.get("list", []) if any(rt in it["report_nm"] for rt in REPORT_TYPES) and "기재정정" not in it["report_nm"]]


def scan_latest(names, name_to_code):
    rows = []
    for i, name in enumerate(names, 1):
        corp_code = name_to_code.get(name)
        if not corp_code:
            continue
        try:
            reports = list_reports(corp_code, "20250101", "20260810")
        except Exception as e:
            print(f"[{i}/{len(names)}] {name}: 목록 조회 실패 ({e})")
            continue
        if not reports:
            continue
        reports.sort(key=lambda it: it["rcept_dt"], reverse=True)
        latest = reports[0]
        try:
            tokens = get_tokens(latest["rcept_no"])
            time.sleep(0.15)
        except Exception as e:
            print(f"[{i}/{len(names)}] {name}: 문서 조회 실패 ({e})")
            continue
        total = find_backlog_total(tokens)
        if total is not None:
            print(f"[{i}/{len(names)}] {name}: 수주잔고 {total:,}백만원 ({latest['rcept_dt']})")
            rows.append({"종목명": name, "기준일": latest["rcept_dt"], "수주잔고(백만원)": total, "report_nm": latest["report_nm"]})
        if i % 50 == 0:
            print(f"  진행 {i}/{len(names)}, 발견 {len(rows)}개")
    return pd.DataFrame(rows)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
    screened = pd.read_csv(SCREEN_PATH)
    names = screened["종목명"].tolist()
    name_to_code, _, _ = load_corp_code_map()

    if mode == "scan":
        print(f"1단계(scan): {len(names)}개 종목의 최신 정기보고서에서 수주잔고 탐색")
        df = scan_latest(names, name_to_code)
        os.makedirs(os.path.dirname(SCAN_OUT_PATH), exist_ok=True)
        df.to_csv(SCAN_OUT_PATH, index=False, encoding="utf-8-sig")
        print(f"\n수주잔고 공시 있는 종목: {len(df)}개 / {len(names)}개")
        print(f"저장 완료: {SCAN_OUT_PATH}")
    else:
        print("backfill 모드는 fetch_order_backlog_history.py 에서 처리")


if __name__ == "__main__":
    main()
