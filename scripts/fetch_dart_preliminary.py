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
from datetime import datetime

import pandas as pd
import requests

from dart_client import DART_API_KEY, BASE_URL, load_corp_code_map

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SCREEN_PATH = os.path.join(DATA_DIR, "screening", "op_growth_screen.csv")
OUT_PATH = os.path.join(DATA_DIR, "screening", "dart_preliminary_q2.csv")

SEARCH_START = "20260601"
SEARCH_END = datetime.today().strftime("%Y%m%d")  # 예전엔 날짜가 하드코딩돼있어서 매일 갱신이 안 됐음


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


TERMINAL_STATUSES = {"ok", "code_not_found"}
# 매일 전 종목을 스킵 없이 처음부터 다시 긁고 있어서 DART 호출량을 상당히 잡아먹고 있었다
# (2,543종목 x 1~2건 호출 = 매일 최소 2,500건 이상, 매출/영업이익 백필보다 우선순위 낮은데도
# 먼저 소모함). "ok"(잠정실적 찾아서 파싱 성공)나 "code_not_found"(영구 실패)는 결과가 안
# 바뀌니 건너뛰고, "no_disclosure"(아직 공시 안 함 - 나중에 나올 수 있음)/에러 상태만 재시도.


def main():
    screened = pd.read_csv(SCREEN_PATH)
    names = screened["종목명"].tolist()

    rows = []
    done_names = set()
    if os.path.exists(OUT_PATH):
        existing = pd.read_csv(OUT_PATH)
        rows = existing.to_dict("records")
        done_names = {row["종목명"] for row in rows if row.get("상태") in TERMINAL_STATUSES}
        print(f"기존 결과 {len(existing)}개 중 완료(재시도 불필요) {len(done_names)}개는 건너뜁니다.")

    todo = [n for n in names if n not in done_names]
    rows = [r for r in rows if r["종목명"] not in todo]  # 재시도 대상은 기존 행 빼고 새로 채움
    print(f"대상 종목: {len(todo)}개 (전체 {len(names)}개 중)")

    name_to_code, _, _ = load_corp_code_map()

    for i, name in enumerate(todo, 1):
        corp_code = name_to_code.get(name)
        print(f"[{i}/{len(todo)}] {name}")
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

        if i % 10 == 0 or i == len(todo):
            pd.DataFrame(rows).to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
            print(f"  중간 저장 완료 ({i}/{len(todo)})")

    final_df = pd.DataFrame(rows).drop_duplicates(subset=["종목명"], keep="last")
    final_df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n최종 저장 완료: {OUT_PATH} ({len(final_df)}행)")


if __name__ == "__main__":
    main()
