"""
YoY 가속화 트래커 - docs/yoy_accel_tracker.html (2026-09-23 사용자 요청)

data/manual/QoQ 계산.xlsx(FnGuide DataGuide 내보내기, "기업 밴드 찾기" 파일과 같은 회사블록
포맷 - 행8=코드/9=이름/10=Item Code/12=기준일자, 15행부터 일별 데이터)에서 종목별
시가총액(S102100, 일별)과 분기 영업이익(M121500.M=실적, E121500.M=추정, 각 분기 컬럼에
YYYYMM 라벨이 헤더12행에 붙어 있음)을 뽑아, 전년동기대비(YoY) 영업이익 증감률이 "가속"되고
있는 종목을 스크리닝한다.

컨센서스(E121500.M, 애널리스트 추정치)가 하나라도 있는 종목만 포함한다 - 사용자 요청
("컨센서스가 있는 종목들만 선택해서") 그대로. 추정치가 전혀 없으면 이 트래커가 보려는
"앞으로도 이어질 성장세인지"를 가늠할 근거 자체가 없다.

종목 클릭 시 상세는 시가총액(선) + 분기 영업이익 YoY%(막대) 차트로 시작했다가, 숫자 자체를
바로 보고 싶다는 요청(2026-09-23, "그냥 숫자자체를 보여줄 수 있게 그래프는 지워도 좋아")으로
분기별 시기/영업이익/YoY 표로 바꿨고, 다시 차트를 보고 싶다는 요청(2026-09-28, "저번에
만들었던 시가총액이랑 막대그래프랑 같이 나오는 거 다시 해보자")으로 표 위에 차트를 복원했다.

부호가 섞이면(적자<->흑자 전환) 단순 비율이 정반대로 오해를 부르므로(OP밴드 v2/이익추정치
상향 트래커와 동일 원칙) 그 구간은 YoY%를 계산하지 않고 None으로 둔다.
"""
import calendar
import glob
import json
import os
from datetime import date, datetime

import openpyxl
import pandas as pd

from build_op_band import (
    HEADER_CODE_ROW, HEADER_NAME_ROW, HEADER_ITEM_ROW, HEADER_BASEDATE_ROW, DATA_START_ROW,
    MANUAL_DIR, detect_blocks, load_naver_sector_map, load_sector_map,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
SCREEN_DIR = os.path.join(DATA_DIR, "screening")
SUMMARY_PATH = os.path.join(SCREEN_DIR, "yoy_accel_summary.csv")
PAGE_OUT_PATH = os.path.join(DOCS_DIR, "yoy_accel_tracker.html")
DETAIL_OUT_DIR = os.path.join(DOCS_DIR, "yoy_accel_tracker_data")

MKTCAP_ITEM = "S102100"
OP_ACTUAL_ITEM = "M121500.M"
OP_EST_ITEM = "E121500.M"
MIN_QUARTERS_FOR_YOY = 5  # 최소 5분기(작년 동기 1개 비교 가능) 있어야 대상에 포함
MKTCAP_CHART_START = date(2025, 1, 1)  # 차트가 2023년말부터 다 그리면 눌려 보여서(2026-09-23
# 세션 초반 확인) 최근 구간만 남긴다 - 종목 클릭 시 차트 다시 보여달라는 요청(2026-09-28)으로 복원.


def month_end(y, m):
    return date(y, m, calendar.monthrange(y, m)[1])


def add_month(y, m):
    m2 = m + 1
    y2 = y
    if m2 > 12:
        m2 = 1
        y2 += 1
    return y2, m2


def find_workbook():
    candidates = glob.glob(os.path.join(MANUAL_DIR, "*QoQ*.xls*"))
    candidates = [c for c in candidates if not os.path.basename(c).startswith("~$")]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def process_sheet(ws):
    max_col = ws.max_column
    max_row = ws.max_row

    def read_row(r):
        return next(ws.iter_rows(min_row=r, max_row=r, max_col=max_col, values_only=True))

    row_codes = read_row(HEADER_CODE_ROW)
    row_names = read_row(HEADER_NAME_ROW)
    row_items = read_row(HEADER_ITEM_ROW)
    row_basedate = read_row(HEADER_BASEDATE_ROW)

    blocks = detect_blocks(row_codes)

    dates, data_rows = [], []
    for row in ws.iter_rows(min_row=DATA_START_ROW, max_row=max_row, max_col=max_col, values_only=True):
        d = row[0]
        if d is None or not isinstance(d, datetime):
            continue
        dates.append(d)
        data_rows.append(row)

    results = {}
    for code, start, end in blocks:
        mktcap_idx = None
        quarter_cols = []  # (col_idx, period_yyyymm, is_estimate)
        for k in range(start, end):
            item = row_items[k]
            if item == MKTCAP_ITEM:
                mktcap_idx = k
            elif item in (OP_ACTUAL_ITEM, OP_EST_ITEM):
                period = row_basedate[k]
                if period is not None:
                    quarter_cols.append((k, int(period), item == OP_EST_ITEM))
        if mktcap_idx is None or not quarter_cols:
            continue

        name = row_names[start] if start < len(row_names) else code

        # 시가총액은 월별로 다운샘플링(그 달의 마지막 거래일 값만)해서 상세 차트에 쓴다 -
        # 일별 그대로 쓰면 카테고리 수가 너무 많아져서 분기 막대가 눌린 실선처럼 안 보이는
        # 문제가 있었다(2026-09-23 세션 초반 확인). 종목 클릭 시 차트 복원 요청(2026-09-28
        # "저번에 만들었던 시가총액이랑 막대그래프랑 같이 나오는 거 다시 해보자")으로
        # 요약표용 latest_mktcap과 별개로 이 월별 시계열도 다시 모은다.
        latest_mktcap = None
        mc_by_month = {}
        for row_vals, d in zip(data_rows, dates):
            v = row_vals[mktcap_idx]
            if v is not None:
                latest_mktcap = v
                if d.date() >= MKTCAP_CHART_START:
                    mc_by_month[(d.year, d.month)] = v

        quarters = []
        for col_idx, period, is_est in sorted(quarter_cols, key=lambda x: x[1]):
            val = None
            for row_vals in data_rows:
                v = row_vals[col_idx]
                if v is not None:
                    val = v  # 분기 내내 동일값이 반복되므로 마지막(=가장 최근 갱신) 값을 쓴다
            if val is not None:
                quarters.append({"period": period, "is_estimate": is_est, "op_won": round(val * 1000, 0)})

        if len(quarters) < MIN_QUARTERS_FOR_YOY:
            continue
        has_consensus = any(q["is_estimate"] for q in quarters)
        if not has_consensus:
            continue

        results[code] = {
            "name": name, "latest_mktcap": latest_mktcap, "mc_by_month": mc_by_month, "quarters": quarters,
        }
    return results


def compute_yoy(quarters):
    """quarters: [{period, is_estimate, op_won}] 기간순 정렬됨. 4분기(=1년) 전과 비교해
    YoY% 리스트와 라벨 리스트를 같은 길이로 반환(앞 4개는 None). 둘 다 흑자일 때만 숫자를
    계산하고, 부호가 섞이거나(적자<->흑자 전환) 둘 다 적자(적자 지속)면 숫자 대신 라벨을
    붙인다(2026-09-28 사용자 요청 "흑전이나 적전은 글자로 써주고 ... 적자지속이면 적자로") -
    단순 비율로 계산하면 부호 때문에 실제와 반대로 보일 수 있어서(예: 적자 -100->-50으로
    개선됐는데 계산상 "-50%"로 나와 악화된 것처럼 보임) 숫자 자체를 안 만든다."""
    yoy = [None] * len(quarters)
    label = [None] * len(quarters)
    for i in range(4, len(quarters)):
        cur = quarters[i]["op_won"]
        prev = quarters[i - 4]["op_won"]
        if cur is None or prev is None or prev == 0 or cur == 0:
            continue
        if cur > 0 and prev > 0:
            yoy[i] = round((cur / prev - 1) * 100, 1)
        elif cur > 0 and prev < 0:
            label[i] = "흑전"
        elif cur < 0 and prev > 0:
            label[i] = "적전"
        else:
            label[i] = "적자"
    return yoy, label


def build_row_and_detail(code, data, sector_map, naver_sector_map):
    quarters = data["quarters"]
    yoy, label = compute_yoy(quarters)
    if not any(v is not None for v in yoy) and not any(label):
        return None, None

    # 최신 YoY - 라벨(흑전/적전/적자)만 있고 숫자가 없는 분기도 "그 분기 자체는 값이
    # 있다"고 취급해서 최신 분기로 잡는다.
    idxs_with_entry = [i for i, (v, l) in enumerate(zip(yoy, label)) if v is not None or l is not None]
    latest_i = idxs_with_entry[-1]
    latest_yoy = yoy[latest_i]
    latest_label = label[latest_i]
    latest_q = quarters[latest_i]
    prev_yoy = yoy[latest_i - 1] if latest_i - 1 >= 0 else None

    # 세자리 YoY(100%+) 유지 분기 수 - 최신 분기부터 거꾸로 훑어서 100% 이상이 끊기지 않고
    # 몇 분기째 이어지는지(2026-09-23 사용자 요청 "세자리 YoY가 3분기 이상 유지되는 걸로
    # 필터"). 중간에 YoY가 없거나(부호 전환 등) 100% 밑으로 내려가면 그 자리에서 스트릭이
    # 끊긴다.
    triple_digit_streak = 0
    for i in range(latest_i, -1, -1):
        if yoy[i] is not None and yoy[i] >= 100:
            triple_digit_streak += 1
        else:
            break

    # 가속화(전분기 대비 YoY 상승) 유지 분기 수 - 최신 분기부터 거꾸로, 바로 앞 분기 대비
    # YoY가 계속 더 높아지고 있는 구간이 몇 분기째 이어지는지(2026-09-23 사용자 요청
    # "전분기 대비 가속화가 3분기 이상 유지되고 있는 애들"). 중간에 YoY가 비거나(부호전환 등)
    # 바로 직전 분기가 아니면(연속이 아니면) 비교 자체가 안 되므로 그 자리에서 끊긴다.
    accel_streak = 0
    i = latest_i
    while i - 1 >= 0 and yoy[i] is not None and yoy[i - 1] is not None and yoy[i] > yoy[i - 1]:
        accel_streak += 1
        i -= 1

    # 가속도(%p) = (확정 실적 분기의 2분기 뒤 추정치 YoY%) - (확정 실적 분기의 YoY%)
    # (2026-09-28 사용자 정정 - "2분기 뒤인 26년 4분기 추정치 YoY - 확정된 수치 26년 2분기
    # 수치 YoY = 가속도로 했어??" - 이전엔 latest_i를 "YoY가 있는 가장 마지막 분기"로 잡아서
    # 그냥 바로 전분기와 비교했는데, latest_i가 이미 먼 미래 추정 분기(예: 27Q4)일 수 있어서
    # "확정 실적 대비 2분기 뒤"라는 의도와 안 맞았다. is_estimate=False인 가장 최근 분기를
    # 기준(base)으로 잡고, 그 2분기 뒤(target)의 YoY와 비교한다. 둘 다 실제 숫자가 있어야
    # 계산되고(흑전/적전/적자 라벨만 있으면 None - 지어내지 않는다), 2분기 뒤 데이터 자체가
    # 없으면(시계열이 짧은 종목) 그때도 None.
    latest_actual_i = next((i for i in range(len(quarters) - 1, -1, -1) if not quarters[i]["is_estimate"]), None)
    accel = None
    if latest_actual_i is not None and latest_actual_i + 2 < len(quarters):
        base_yoy = yoy[latest_actual_i]
        target_yoy = yoy[latest_actual_i + 2]
        if base_yoy is not None and target_yoy is not None:
            accel = round(target_yoy - base_yoy, 1)

    # 요약표에 분기별 YoY를 한 줄로 쭉 나열해서 보여주기 위한 맵(2026-09-28 사용자 요청 -
    # "각 분기별 YoY를 넣어주고 그게 양수면 초록색칸, 음수면 빨간색칸으로, 흑전/적전은
    # 글자로, 적자지속이면 적자로 빨강"). 숫자 대신 라벨이 붙은 분기는 label_by_period에
    # 따로 담아서 히트맵에서 텍스트로 보여준다.
    yoy_by_period = {str(q["period"]): yoy[i] for i, q in enumerate(quarters) if yoy[i] is not None}
    label_by_period = {str(q["period"]): label[i] for i, q in enumerate(quarters) if label[i] is not None}
    est_by_period = {str(q["period"]): q["is_estimate"] for q in quarters}

    row = {
        "code": code, "name": data["name"],
        "sector": naver_sector_map.get(code.lstrip("A")) or sector_map.get(data["name"]),
        "latest_mktcap": data["latest_mktcap"],
        "latest_period": latest_q["period"], "latest_is_estimate": latest_q["is_estimate"],
        "latest_yoy": latest_yoy, "latest_label": latest_label, "prev_yoy": prev_yoy,
        "n_quarters": len(quarters), "triple_digit_streak": triple_digit_streak,
        "accel_streak": accel_streak, "accel": accel,
        "yoy_by_period": yoy_by_period, "label_by_period": label_by_period, "est_by_period": est_by_period,
    }

    # 종목 클릭 시 보여줄 상세 - 시기/영업이익/YoY% 표(2026-09-23)에 시가총액 선 + 분기
    # 영업이익 YoY% 막대 차트를 다시 추가(2026-09-28 사용자 요청 - "저번에 만들었던
    # 시가총액이랑 막대그래프랑 같이 나오는 거 다시 해보자"). 분기 막대는 분기말이 아니라
    # "분기말+1개월"(실적 발표 시점 근사)에 둬서 아직 발표도 안 된 시점에 막대가 서는
    # 착시를 피한다. 시가총액 데이터가 끊긴 뒤에도 추정 분기가 남아있으면 그만큼 격자를
    # 늘려서 막대는 계속 보여준다.
    mc_by_month = data["mc_by_month"]
    bars = [(month_end(*add_month(q["period"] // 100, q["period"] % 100)), yoy[i], label[i], q["is_estimate"])
            for i, q in enumerate(quarters) if yoy[i] is not None or label[i] is not None]

    grid_end = max([b[0] for b in bars], default=MKTCAP_CHART_START)
    if mc_by_month:
        grid_end = max(grid_end, month_end(*max(mc_by_month)))
    y, m = MKTCAP_CHART_START.year, MKTCAP_CHART_START.month
    grid = []
    while (y, m) <= (grid_end.year, grid_end.month):
        grid.append(month_end(y, m))
        y, m = add_month(y, m)

    mc_map = {month_end(y2, m2): round(v / 1e8, 1) for (y2, m2), v in mc_by_month.items()}
    bar_yoy_map = {d: v for d, v, _l, _e in bars}
    bar_label_map = {d: l for d, _v, l, _e in bars if l is not None}
    bar_est_map = {d: e for d, _v, _l, e in bars}

    detail = {
        "code": code, "name": data["name"],
        "labels": [str(d) for d in grid],
        "mc_eok": [mc_map.get(d) for d in grid],
        "bar_yoy": [bar_yoy_map.get(d) for d in grid],
        "bar_label": [bar_label_map.get(d) for d in grid],
        "bar_is_estimate": [bool(bar_est_map.get(d, False)) for d in grid],
        "quarters": [
            {"period": q["period"], "op_100mil": round(q["op_won"] / 1e8, 1),
             "yoy": yoy[i], "label": label[i], "is_estimate": q["is_estimate"]}
            for i, q in enumerate(quarters)
        ],
    }
    return row, detail


def main():
    wb_path = find_workbook()
    if not wb_path:
        print("data/manual/ 에 'QoQ 계산' 워크북이 없습니다.")
        return
    print(f"워크북 로드 중: {wb_path}")
    wb = openpyxl.load_workbook(wb_path, read_only=True, data_only=True)

    all_results = {}
    for sn in wb.sheetnames:
        print(f"처리 중: {sn} ...")
        res = process_sheet(wb[sn])
        for code, data in res.items():
            all_results.setdefault(code, data)
        print(f"  {len(res)}개 종목(컨센서스 보유, 5분기 이상)")

    naver_sector_map = load_naver_sector_map()
    sector_map = load_sector_map()

    os.makedirs(DETAIL_OUT_DIR, exist_ok=True)
    rows = []
    for code, data in all_results.items():
        row, detail = build_row_and_detail(code, data, sector_map, naver_sector_map)
        if not row:
            continue
        rows.append(row)
        with open(os.path.join(DETAIL_OUT_DIR, f"{code}.json"), "w", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False)

    print(f"결과 {len(rows)}종목(YoY 계산 가능 + 컨센서스 보유)")

    # 요약표 히트맵 열 기준(분기 축) - 실제로 YoY가 하나라도 계산된 종목이 있는 분기만
    # 모아서 쓴다(2026-09-28 사용자 요청 "분기별 YoY를 넣어주고 ... 쭉 나열").
    all_periods = sorted({p for r in rows for p in r["yoy_by_period"]} | {p for r in rows for p in r["label_by_period"]})

    os.makedirs(SCREEN_DIR, exist_ok=True)
    pd.DataFrame(rows).to_csv(SUMMARY_PATH, index=False, encoding="utf-8-sig")

    html = TEMPLATE.format(
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        n_stocks=len(rows),
        rows_json=json.dumps(rows, ensure_ascii=False),
        periods_json=json.dumps(all_periods),
    )
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(PAGE_OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"저장 완료: {SUMMARY_PATH}")
    print(f"저장 완료: {PAGE_OUT_PATH}")


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>YoY 가속화 트래커</title>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  a.back {{ color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }}
  h1 {{ font-size:20px; margin:8px 0 4px 0; }}
  .updated {{ color:#9aa0a6; font-size:13px; margin-bottom:16px; }}
  .exp {{ background:#1a1d24; border-radius:10px; padding:14px 18px; font-size:12px; color:#9aa0a6; line-height:1.8; max-width:1000px; margin-bottom:18px; }}
  .exp b {{ color:#ffa94d; }}
  .filters {{ display:flex; gap:12px; flex-wrap:wrap; align-items:center; margin-bottom:14px; font-size:13px; color:#9aa0a6; }}
  .filters input, .filters select {{ background:#1a1d24; border:1px solid #2a2e37; color:#e6e6e6; border-radius:6px; padding:6px 10px; font-size:13px; }}
  .filters input[type="number"] {{ width:80px; }}
  .table-wrap {{ overflow-x:auto; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  th, td {{ padding:8px 10px; text-align:right; border-bottom:1px solid #23262e; white-space:nowrap; }}
  th.lbl, td.lbl {{ text-align:left; }}
  th {{ color:#9aa0a6; font-weight:normal; font-size:12px; position:sticky; top:0; background:#0f1115; }}
  .up {{ color:#63e6be; }}
  .down {{ color:#ff8787; }}
  td.hm {{ padding:4px 6px; text-align:center; font-size:11px; }}
  .hm-pos {{ background:hsl(150,45%,28%); color:#c8ecd9; border-radius:4px; }}
  .hm-pos-est {{ background:hsl(150,45%,55%); color:#0b3d24; border-radius:4px; }}
  .hm-neg {{ background:hsl(0,45%,28%); color:#f0c9c9; border-radius:4px; }}
  .hm-neg-est {{ background:hsl(0,45%,55%); color:#4a0f0f; border-radius:4px; }}
  .hm-empty {{ color:#3a3d45; }}
  .est-badge {{ display:inline-block; background:#a9c8ec33; color:#4dabf7; border:1px solid #4dabf7; border-radius:4px; font-size:10px; padding:1px 4px; margin-left:5px; }}
  .sub {{ color:#6b7280; font-size:11px; }}
  .count {{ color:#63e6be; font-size:12px; margin-bottom:8px; }}
  .cmp-bar {{ display:none; align-items:center; gap:12px; background:#1a1d24; border:1px solid #2a2e37; border-radius:8px; padding:8px 14px; margin-bottom:10px; font-size:13px; color:#9aa0a6; }}
  .cmp-bar.show {{ display:flex; }}
  .cmp-bar button {{ background:#1a1d24; border:1px solid #4dabf7; color:#4dabf7; border-radius:6px; padding:5px 12px; cursor:pointer; font-size:12px; font-family:inherit; }}
  .cmp-bar button:disabled {{ opacity:0.4; cursor:not-allowed; }}
  .cmp-chk {{ cursor:pointer; }}
  tbody tr {{ cursor:pointer; }}
  tbody tr:hover {{ background:#1a1d24; }}
  .overlay {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,0.75); z-index:200; overflow:auto; padding:40px 20px; }}
  .overlay.open {{ display:block; }}
  .modal {{ background:#12151b; border:1px solid #23262e; border-radius:14px; max-width:720px; margin:0 auto; padding:22px 26px; }}
  .modal h2 {{ font-size:17px; margin:0 0 12px 0; }}
  .close-btn {{ float:right; background:none; border:none; color:#9aa0a6; font-size:22px; cursor:pointer; line-height:1; }}
  .detail-table {{ width:100%; margin-top:16px; }}
  .detail-table th {{ position:static; }}
  .legend {{ display:flex; gap:16px; flex-wrap:wrap; font-size:12px; color:#9aa0a6; margin-bottom:8px; }}
  .legend span {{ display:flex; align-items:center; gap:4px; }}
  .sw {{ width:12px; height:2px; display:inline-block; }}
  .sq {{ width:10px; height:10px; border-radius:2px; display:inline-block; }}
  .chart-wrap {{ height:320px; position:relative; }}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>YoY 가속화 트래커</h1>
  <div class="updated">최종 갱신: {updated_at} &middot; {n_stocks}종목(컨센서스 보유)</div>

  <div class="exp">
    <b>무엇을 보는 페이지인가</b><br>
    기본 정렬은 <b>가속도(%p) = (확정 실적 분기의 2분기 뒤 컨센서스 분기 YoY%) − (그 확정
    실적 분기의 YoY%)</b> 큰순입니다 - 예를 들어 26년 2분기가 가장 최근 확정 실적이면
    26년 4분기(추정) YoY%에서 26년 2분기 YoY%를 뺀 값입니다(2026-09-28 확정).
    그 외에 분기별 영업이익(실적+애널리스트 컨센서스 추정치)의 <b>전년동기대비(YoY) 증감률</b>도
    표에 바로 색칠된 칸으로 나열됩니다 - <b>밝은 초록=추정 양수, 어두운 초록=실적 양수, 밝은
    빨강=추정 음수, 어두운 빨강=실적 음수</b>(양수는 그 안에서도 값이 클수록 더 진하게).
    종목명을 클릭하면 분기별 시기/영업이익/YoY% 표를
    볼 수 있습니다. 적자/흑자가 뒤바뀌는 구간은 YoY%가 왜곡되므로 계산하지 않습니다(빈 칸).
    FnGuide 컨센서스(추정치)가 하나도 없는 종목은 애초에 포함하지 않습니다.
  </div>

  <div class="filters">
    <label>검색 <input type="text" id="fSearch" placeholder="종목명/코드"></label>
    <label>섹터 <select id="fSector"><option value="">전체</option></select></label>
    <label>최신 YoY% 최소 <input type="number" id="fYoyMin" step="10"></label>
    <label><input type="checkbox" id="fAccelOnly"> 가속 중인 종목만(YoY가 전분기보다 상승)</label>
    <label><input type="checkbox" id="fTripleOnly"> 세자리 YoY(100%+) 3분기 이상 유지</label>
    <label><input type="checkbox" id="fAccelStreakOnly"> 가속화 3분기 이상 유지(전분기 대비 YoY 계속 상승)</label>
    <label>시총 최소(억) <input type="number" id="fMktcapMin" step="100"></label>
    <label>정렬 <select id="fSort">
      <option value="accel2_desc" selected>가속도(%p) 큰순</option>
      <option value="yoy_desc">최신 YoY% 큰순</option>
      <option value="streak_desc">세자리 유지 분기수 큰순</option>
      <option value="accel_streak_desc">가속화 유지 분기수 큰순</option>
      <option value="mktcap_desc">시가총액 큰순</option>
    </select></label>
  </div>
  <div class="count" id="count"></div>

  <div class="cmp-bar" id="cmpBar">
    <span id="cmpLabel"></span>
    <button id="cmpBtn">선택한 종목만 보기</button>
    <button id="cmpClearBtn">선택 초기화</button>
  </div>

  <div class="table-wrap">
  <table>
    <thead><tr id="headRow"></tr></thead>
    <tbody id="tbody"></tbody>
  </table>
  </div>

  <div class="overlay" id="overlay">
    <div class="modal">
      <button class="close-btn" id="closeBtn">&times;</button>
      <h2 id="detailName"></h2>
      <div class="legend">
        <span><span class="sw" style="background:#eb6834"></span>시가총액(조원, 왼쪽 축)</span>
        <span><span class="sq" style="background:#2a78d6"></span>영업이익 YoY%(실적, 오른쪽 축)</span>
        <span><span class="sq" style="background:#a9c8ec"></span>영업이익 YoY%(추정, 오른쪽 축)</span>
      </div>
      <div class="chart-wrap"><canvas id="detailChart"></canvas></div>
      <table class="detail-table">
        <thead><tr><th style="text-align:left;">시기</th><th>영업이익(억원)</th><th>YoY%</th></tr></thead>
        <tbody id="detailBody"></tbody>
      </table>
    </div>
  </div>


<script>
const ROWS = {rows_json};
const PERIODS = {periods_json};

const sectors = [...new Set(ROWS.map(r => r.sector).filter(Boolean))].sort();
const selSector = document.getElementById('fSector');
sectors.forEach(s => {{ const o = document.createElement('option'); o.value = s; o.textContent = s; selSector.appendChild(o); }});

function fmtMktcap(v) {{
  if (v == null) return '-';
  const eok = v / 1e8;
  return eok >= 10000 ? (eok / 10000).toFixed(2) + '조' : Math.round(eok).toLocaleString() + '억';
}}
function fmtPeriod(p) {{
  const y = Math.floor(p / 100), m = p % 100;
  const q = {{3:'Q1',6:'Q2',9:'Q3',12:'Q4'}}[m] || m;
  return y + ' ' + q;
}}
function fmtPeriodShort(p) {{
  const y = Math.floor(p / 100) % 100, m = p % 100;
  const q = {{3:'Q1',6:'Q2',9:'Q3',12:'Q4'}}[m] || m;
  return "'" + y + ' ' + q;
}}
function pctSpan(v) {{
  if (v == null) return '<span class="sub">-</span>';
  const cls = v > 0 ? 'up' : v < 0 ? 'down' : '';
  return `<span class="${{cls}}">${{v > 0 ? '+' : ''}}${{v.toFixed(1)}}%</span>`;
}}
function pctOrLabelSpan(v, lbl) {{
  if (lbl != null) return `<span class="${{lbl === '흑전' ? 'up' : 'down'}}">${{lbl}}</span>`;
  return pctSpan(v);
}}
// 양수 YoY는 값이 클수록 초록이 진하게, 작을수록 연하게(2026-09-28 사용자 요청 - "숫자가
// 크면 진하고, 작아지면 연해지는 느낌으로"). 값 범위가 0%~수만%까지 걸쳐 있어서 로그
// 스케일로 압축하고(CAP% 이상은 전부 가장 진한 색으로 포화), 확정치(실적)는 알파를 낮춰
// 어둡게, 추정치는 알파를 그대로 둬서 밝게(2026-09-28 사용자 요청 - "추정치가 어두워서
// 비교하기 어려우니까 추정치 부분이 밝게 확정치가 어둡게" - 명도 스케일 자체는 그대로).
const POS_CAP = 500;
function posCellStyle(v, isEst) {{
  // 알파블렌딩(어두운 배경에 반투명 겹치기)으로 실적/추정을 구분했더니, 값이 커서
  // 명도가 이미 바닥(25%)까지 떨어진 칸에서는 알파를 아무리 바꿔도 육안으로 거의
  // 구분이 안 됐다(2026-09-28 사용자 지적 - "아닌거 같은데"). 그래서 알파 대신 명도
  // 자체의 시작점을 실적/추정 다르게 잡아서, 값 크기와 무관하게 항상 추정이 실적보다
  // 밝게 나오도록 고침 - 두 범위가 40%p 간격을 유지한 채로 같이 로그스케일로 줄어든다.
  const t = Math.min(1, Math.log10(1 + Math.max(0, v)) / Math.log10(1 + POS_CAP));
  const light = isEst ? Math.round(85 - t * 40) : Math.round(60 - t * 40);
  const textColor = light < 50 ? '#eafff5' : '#0b3d24';
  return `background:hsl(150, 55%, ${{light}}%); color:${{textColor}}; border-radius:4px;`;
}}
function heatCell(r, period) {{
  const v = r.yoy_by_period[period];
  const lbl = r.label_by_period[period];
  const est = r.est_by_period[period];
  if (v == null && lbl == null) return '<td class="hm hm-empty">-</td>';
  if (lbl != null) {{
    // 흑전(적자->흑자)=초록, 적전/적자(흑자->적자, 적자 지속)=빨강 - 숫자 대신 글자로
    // 보여준다(2026-09-28 사용자 요청 - 단순 비율로는 부호 때문에 실제와 반대로 보일 수 있음).
    const cls = lbl === '흑전' ? (est ? 'hm-pos-est' : 'hm-pos') : (est ? 'hm-neg-est' : 'hm-neg');
    return `<td class="hm ${{cls}}">${{lbl}}</td>`;
  }}
  if (v >= 0) {{
    return `<td class="hm" style="${{posCellStyle(v, est)}}">${{Math.round(v)}}%</td>`;
  }}
  const cls = est ? 'hm-neg-est' : 'hm-neg';
  return `<td class="hm ${{cls}}">${{Math.round(v)}}%</td>`;
}}

document.getElementById('headRow').innerHTML = '<th></th><th class="lbl">종목명</th>'
  + PERIODS.map(p => `<th>${{fmtPeriodShort(parseInt(p))}}</th>`).join('')
  + '<th>시가총액</th><th>가속도(%p)</th><th>세자리 유지</th><th>가속화 유지</th><th class="lbl">코드</th><th class="lbl">섹터</th>';

// 종목끼리 비교(2026-09-28 사용자 요청 "5개까지 선택해서 볼 수 있게") - 체크한 코드 집합을
// 필터/정렬이 바뀌어도(재검색 등) 유지한다.
const MAX_COMPARE = 5;
const selectedCodes = new Set();
const ROWS_BY_CODE = Object.fromEntries(ROWS.map(r => [r.code, r]));
let onlySelected = false;

function updateCmpBar() {{
  const bar = document.getElementById('cmpBar');
  const n = selectedCodes.size;
  bar.classList.toggle('show', n > 0);
  document.getElementById('cmpLabel').textContent = `${{n}}/${{MAX_COMPARE}} 선택됨` +
    (n > 0 ? ': ' + [...selectedCodes].map(c => ROWS_BY_CODE[c].name).join(', ') : '');
  document.getElementById('cmpBtn').disabled = n < 1;
  document.getElementById('cmpBtn').textContent = onlySelected ? '전체 다시 보기' : '선택한 종목만 보기';
  if (n === 0) onlySelected = false;
}}

function applyFilters() {{
  const q = document.getElementById('fSearch').value.trim().toLowerCase();
  const sec = selSector.value;
  const yoyMin = parseFloat(document.getElementById('fYoyMin').value);
  const mktcapMin = parseFloat(document.getElementById('fMktcapMin').value);
  const accelOnly = document.getElementById('fAccelOnly').checked;
  const tripleOnly = document.getElementById('fTripleOnly').checked;
  const accelStreakOnly = document.getElementById('fAccelStreakOnly').checked;
  const sort = document.getElementById('fSort').value;

  let rows = ROWS.filter(r => {{
    if (onlySelected) return selectedCodes.has(r.code);
    if (q && !(r.name.toLowerCase().includes(q) || r.code.toLowerCase().includes(q))) return false;
    if (sec && r.sector !== sec) return false;
    if (!isNaN(yoyMin) && (r.latest_yoy == null || r.latest_yoy < yoyMin)) return false;
    if (!isNaN(mktcapMin) && (r.latest_mktcap == null || r.latest_mktcap / 1e8 < mktcapMin)) return false;
    if (accelOnly && (r.accel == null || r.accel <= 0)) return false;
    if (tripleOnly && r.triple_digit_streak < 3) return false;
    if (accelStreakOnly && r.accel_streak < 3) return false;
    return true;
  }});

  if (sort === 'accel2_desc') rows.sort((a, b) => (b.accel ?? -9e9) - (a.accel ?? -9e9));
  else if (sort === 'yoy_desc') rows.sort((a, b) => (b.latest_yoy ?? -9e9) - (a.latest_yoy ?? -9e9));
  else if (sort === 'streak_desc') rows.sort((a, b) => b.triple_digit_streak - a.triple_digit_streak);
  else if (sort === 'accel_streak_desc') rows.sort((a, b) => b.accel_streak - a.accel_streak);
  else rows.sort((a, b) => (b.latest_mktcap ?? 0) - (a.latest_mktcap ?? 0));

  document.getElementById('count').textContent = rows.length + '종목 (행 클릭하면 표, 왼쪽 체크박스로 최대 ' + MAX_COMPARE + '개 비교)';
  document.getElementById('tbody').innerHTML = rows.slice(0, 400).map(r => {{
    const checked = selectedCodes.has(r.code);
    const disabled = !checked && selectedCodes.size >= MAX_COMPARE;
    return `
    <tr data-code="${{r.code}}">
      <td><input type="checkbox" class="cmp-chk" data-code="${{r.code}}" ${{checked ? 'checked' : ''}} ${{disabled ? 'disabled' : ''}}></td>
      <td class="lbl">${{r.name}}</td>
      ${{PERIODS.map(p => heatCell(r, p)).join('')}}
      <td>${{fmtMktcap(r.latest_mktcap)}}</td>
      <td>${{pctSpan(r.accel)}}</td>
      <td class="sub">${{r.triple_digit_streak > 0 ? r.triple_digit_streak + '분기' : '-'}}</td>
      <td class="sub">${{r.accel_streak > 0 ? r.accel_streak + '분기' : '-'}}</td>
      <td class="lbl sub">${{r.code}}</td><td class="lbl sub">${{r.sector || '-'}}</td>
    </tr>`;
  }}).join('');
  document.querySelectorAll('#tbody tr').forEach(tr =>
    tr.addEventListener('click', (e) => {{
      if (e.target.classList.contains('cmp-chk')) return;
      openDetail(tr.dataset.code);
    }}));
  document.querySelectorAll('.cmp-chk').forEach(chk =>
    chk.addEventListener('click', (e) => e.stopPropagation()));
  document.querySelectorAll('.cmp-chk').forEach(chk =>
    chk.addEventListener('change', (e) => {{
      const code = e.target.dataset.code;
      if (e.target.checked) {{
        if (selectedCodes.size >= MAX_COMPARE) {{ e.target.checked = false; return; }}
        selectedCodes.add(code);
      }} else {{
        selectedCodes.delete(code);
      }}
      updateCmpBar();
      applyFilters();
    }}));
  updateCmpBar();
}}

['fSearch','fSector','fYoyMin','fMktcapMin','fAccelOnly','fTripleOnly','fAccelStreakOnly','fSort'].forEach(id => {{
  document.getElementById(id).addEventListener('input', applyFilters);
  document.getElementById(id).addEventListener('change', applyFilters);
}});

let detailChart = null;
function openDetail(code) {{
  fetch(`yoy_accel_tracker_data/${{code}}.json`).then(r => r.json()).then(d => {{
    document.getElementById('detailName').textContent = `${{d.name}} (${{d.code}})`;
    document.getElementById('detailBody').innerHTML = d.quarters.slice().reverse().map(q => `
      <tr>
        <td style="text-align:left;">${{fmtPeriod(q.period)}}${{q.is_estimate ? '<span class="est-badge">추정</span>' : ''}}</td>
        <td>${{q.op_100mil.toLocaleString()}}억</td>
        <td>${{pctOrLabelSpan(q.yoy, q.label)}}</td>
      </tr>`).join('');

    const MC = d.mc_eok.map(v => v == null ? null : v / 10000);
    const OP = d.bar_yoy;
    const COL = d.bar_is_estimate.map(e => e ? '#a9c8ec' : '#2a78d6');
    if (detailChart) detailChart.destroy();
    detailChart = new Chart(document.getElementById('detailChart').getContext('2d'), {{
      data: {{
        labels: d.labels,
        datasets: [
          {{ type: 'bar', label: '영업이익 YoY%', data: OP, backgroundColor: COL, borderRadius: 4, maxBarThickness: 36, order: 2, yAxisID: 'y1' }},
          {{ type: 'line', label: '시가총액', data: MC, borderColor: '#eb6834', backgroundColor: 'rgba(235,104,52,0.08)', borderWidth: 2, pointRadius: 2, fill: true, spanGaps: false, tension: 0.15, order: 1, yAxisID: 'y' }},
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{ mode: 'index', intersect: false, filter: (c) => c.parsed.y != null, callbacks: {{ label: (c) => c.dataset.yAxisID === 'y' ? c.dataset.label + ': ' + c.parsed.y.toLocaleString() + '조원' : c.dataset.label + ': ' + (c.parsed.y >= 0 ? '+' : '') + c.parsed.y.toFixed(1) + '%' }} }}
        }},
        scales: {{
          x: {{ grid: {{ display: false }}, ticks: {{ color: '#9aa0a6', maxTicksLimit: 14, maxRotation: 45 }} }},
          y: {{ position: 'left', grid: {{ color: '#23262e' }}, ticks: {{ color: '#eb6834', callback: (v) => v.toLocaleString() + '조' }} }},
          y1: {{ position: 'right', grid: {{ display: false }}, ticks: {{ color: '#2a78d6', callback: (v) => v + '%' }} }},
        }},
        interaction: {{ mode: 'index', intersect: false }}
      }}
    }});
    document.getElementById('overlay').classList.add('open');
  }});
}}
document.getElementById('closeBtn').addEventListener('click', () => document.getElementById('overlay').classList.remove('open'));
document.getElementById('overlay').addEventListener('click', e => {{
  if (e.target.id === 'overlay') document.getElementById('overlay').classList.remove('open');
}});

// 비교는 별도 차트 팝업 대신 표 자체를 선택한 종목으로만 좁혀서 보여준다(2026-09-28
// 사용자 요청 - "차트로 비교창 안띄어도되고 그냥 선택한 기업만 남게"). 히트맵/가속도 등
// 기존 컬럼을 그대로 나란히 볼 수 있어서 차트보다 오히려 비교가 더 잘 된다.
document.getElementById('cmpBtn').addEventListener('click', () => {{
  onlySelected = !onlySelected;
  updateCmpBar();
  applyFilters();
}});
document.getElementById('cmpClearBtn').addEventListener('click', () => {{
  selectedCodes.clear();
  onlySelected = false;
  updateCmpBar();
  applyFilters();
}});

applyFilters();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
