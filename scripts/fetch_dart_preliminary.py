"""
DART "영업(잠정)실적(공정공시)" 를 찾아 2분기 매출액/영업이익 YoY를 가져온다.
정식 반기보고서(제출 마감 8/14)보다 훨씬 빨리(보통 실적 발생 익월 말) 나오는 자율/공정공시라
전체 상장사가 다 내는 건 아니지만(주로 시총 큰 곳), 있으면 이걸 우선 쓴다.

문서(document.xml)는 HTML 표로 오는데 태그를 걷어내면 다음 순서로 값이 나온다(실제 응답으로 확인):
  매출액 | 당해실적 | 당기값 | 전기값 | 전기대비% | (흑자/적자전환 표시) | 전년동기값 | 전년동기대비%
  영업이익 | 당해실적 | 당기값 | 전기값 | 전기대비% | (흑자/적자전환 표시) | 전년동기값 | 전년동기대비%
단위는 보통 "억원"(1억원=100,000,000원)으로 통일해서 온다 - 표 상단 "단위 : 억원, %" 로 확인.
"""
import os
import re
import time
import zipfile
import io

import pandas as pd
import requests

from dart_client import DART_API_KEY, BASE_URL, load_corp_code_map

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SCREEN_PATH = os.path.join(DATA_DIR, "screening", "op_growth_screen.csv")
OUT_PATH = os.path.join(DATA_DIR, "screening", "dart_preliminary_q2.csv")

SEARCH_START = "20260601"
SEARCH_END = "20260810"


def find_preliminary_rcept(corp_code):
    params = {
        "crtfc_key": DART_API_KEY, "corp_code": corp_code,
        "bgn_de": SEARCH_START, "end_de": SEARCH_END, "page_count": 100,
        "pblntf_ty": "I",  # 거래소공시(자율/공정공시) - 잠정실적이 여기 속함, 노이즈(임원 소유상황보고 등) 제거
    }
    r = requests.get(f"{BASE_URL}/list.json", params=params, timeout=20)
    data = r.json()
    if data.get("status") != "000":
        return None
    candidates = [it for it in data.get("list", []) if "잠정" in it.get("report_nm", "") and "실적" in it.get("report_nm", "")]
    if not candidates:
        return None
    candidates.sort(key=lambda it: it["rcept_dt"], reverse=True)
    return candidates[0]["rcept_no"], candidates[0]["report_nm"], candidates[0]["rcept_dt"]


NUM_RE = re.compile(r"^-?[\d,]+(\.\d+)?$")


def to_num(tok):
    tok = tok.strip()
    if not tok or tok == "-":
        return None
    if not NUM_RE.match(tok):
        return None
    return float(tok.replace(",", ""))


def _extract_metric(tokens, label):
    """label('매출액'/'영업이익') 다음 '당해실적' 뒤에 고정 순서로 7개 셀이 온다:
    [당기, 전기, 전기대비%, 흑자적자전환여부, 전년동기, 전년동기대비%, 흑자적자전환여부]
    흑자전환/적자전환처럼 텍스트인 셀은 None으로 둔다(고정 위치라서 밀리지 않음).
    """
    try:
        idx = tokens.index(label)
        idx = tokens.index("당해실적", idx)
    except ValueError:
        return None
    window = tokens[idx + 1: idx + 8]
    if len(window) < 7:
        return None
    current, prior_q, qoq_pct, _flag1, yoy_base, yoy_pct, _flag2 = window
    return {
        "당기": to_num(current), "전기": to_num(prior_q), "전기대비%": to_num(qoq_pct),
        "전년동기": to_num(yoy_base), "전년동기대비%": to_num(yoy_pct),
        "흑자적자전환_QoQ": _flag1 if "전환" in _flag1 else None,
        "흑자적자전환_YoY": _flag2 if "전환" in _flag2 else None,
    }


UNIT_TO_EOKWON = {"억원": 1, "조원": 10000, "백만원": 0.01, "원": 0.00000001}


def _detect_unit_multiplier(tokens):
    """'단위 : 억원, %' 같은 선언 토큰을 찾아 억원 기준 배수를 돌려준다. 못 찾으면 억원으로 가정."""
    for t in tokens:
        if "단위" in t:
            for unit, mult in UNIT_TO_EOKWON.items():
                if unit in t:
                    return mult
    return 1


def parse_preliminary(rcept_no):
    r = requests.get(f"{BASE_URL}/document.xml", params={"crtfc_key": DART_API_KEY, "rcept_no": rcept_no}, timeout=20)
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    content = zf.read(zf.namelist()[0]).decode("utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", "|", content)
    tokens = [t.strip() for t in text.split("|") if t.strip()]

    unit_mult = _detect_unit_multiplier(tokens)
    revenue = _extract_metric(tokens, "매출액")
    op = _extract_metric(tokens, "영업이익")
    for metric in (revenue, op):
        if metric:
            for key in ("당기", "전기", "전년동기"):
                if metric[key] is not None:
                    metric[key] = round(metric[key] * unit_mult, 2)  # 억원 기준으로 정규화
    return revenue, op


def main():
    screened = pd.read_csv(SCREEN_PATH)
    names = screened["종목명"].tolist()
    print(f"대상 종목: {len(names)}개")

    name_to_code, _, _ = load_corp_code_map()

    rows = []
    for i, name in enumerate(names, 1):
        corp_code = name_to_code.get(name)
        print(f"[{i}/{len(names)}] {name}")
        if not corp_code:
            rows.append({"종목명": name, "상태": "code_not_found"})
            continue
        try:
            found = find_preliminary_rcept(corp_code)
            time.sleep(0.15)
        except Exception as e:
            print(f"  경고: 조회 실패 ({e})")
            rows.append({"종목명": name, "상태": f"list_error: {e}"})
            continue
        if not found:
            rows.append({"종목명": name, "상태": "no_disclosure"})
            continue
        rcept_no, report_nm, rcept_dt = found
        try:
            revenue, op = parse_preliminary(rcept_no)
            time.sleep(0.15)
        except Exception as e:
            print(f"  경고: 파싱 실패 ({e})")
            rows.append({"종목명": name, "상태": f"parse_error: {e}", "공시일": rcept_dt})
            continue
        if not revenue and not op:
            rows.append({"종목명": name, "상태": "parse_empty", "공시일": rcept_dt})
            continue
        rows.append({
            "종목명": name, "상태": "ok", "공시일": rcept_dt,
            "매출액_당기(억원)": revenue["당기"] if revenue else None,
            "매출액_YoY(%)": revenue["전년동기대비%"] if revenue else None,
            "매출액_흑자적자전환_YoY": revenue["흑자적자전환_YoY"] if revenue else None,
            "영업이익_당기(억원)": op["당기"] if op else None,
            "영업이익_YoY(%)": op["전년동기대비%"] if op else None,
            "영업이익_흑자적자전환_YoY": op["흑자적자전환_YoY"] if op else None,
        })

        if i % 10 == 0 or i == len(names):
            pd.DataFrame(rows).to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
            print(f"  중간 저장 완료 ({i}/{len(names)})")

    pd.DataFrame(rows).to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n최종 저장 완료: {OUT_PATH}")


if __name__ == "__main__":
    main()
