"""
OP밴드 관련 스크립트(build_op_band_v2.py/build_estimate_revision.py)가 공유하는 라이브러리.

원래는 이 파일 자체가 "OP밴드(v1)" 페이지(docs/op_band.html)를 빌드했었는데, v2(12개월
선행 보간 + 최근N년 하위퍼센타일 바텀)가 v1의 "6월 스위칭 절벽" 문제를 해결한 사실상의
상위호환으로 자리잡아서 v1은 삭제했다(2026-09-28 사용자 판단 - "OP밴드 V2가 나쁘지 않은
거 같다 그러니까 기존 OP밴드 트래커는 삭제하도록 하자"). v2가 op_band.html/op_band_data/
경로와 op_band_summary.csv 파일명까지 그대로 이어받아서, 이 파일에 남아있는 공용 함수
(워크북 파싱/섹터 매핑/FnGuide 로드/연도 override)만 그대로 재사용하면 되고 다른 스크립트나
fetch_op_band_consensus.py 쪽 경로는 손댈 필요가 없었다.

data/manual/*기업*밴드*.xlsx(데이터터미널 시가총액/영업이익 컨센서스 export) 워크북 구조
(사용자 제공 스펙) - 신형/구형 두 포맷이 있다:
- 구형("기업 밴드 찾기.xlsx", 시트 "구성 1/2/3" - 엑셀 컬럼 한계 16,384열 때문에 종목을 나눠
  이어붙인 것): 종목 1개당 +0 시가총액(S102100) / +1 TTM(M121505.M) / +2~ 분기별 영업이익
  추정치(E121500.M, 4개씩 묶여 회계연도 1개, Base Date=YYYYMM)
- 신형("기업 밴드 찾기 2.xlsx"부터, 시트 "기업밴드" 단일 - 2026-09-03 도입, 시계열이
  2021-12-30~로 더 길고 분기합산 없이 FnGuide가 이미 집계한 연간 직접추정치를 줌 - 분기합
  대비 오차 ±1~3% 수준(2026-09-03 확인, 삼성전자 등 대형주도 이상치 아님으로 판단하고 그대로
  사용): 종목 1개당 +0 시가총액(S102100) / +1 TTM(M121505.M) / +2 당해연도 추정(E121500.M,
  Base Date="NFY1") / +3 차년도 추정(E121500.M, Base Date="NFY2")
- 공통: 8행=종목코드, 9행=종목명, 10행=item code, 11행=단위, 12행=Base Date, 15행부터
  날짜별 데이터(A열=날짜)
"""
import glob
import math
import os
from datetime import datetime

import openpyxl
import pandas as pd


DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MANUAL_DIR = os.path.join(DATA_DIR, "manual")
FNGUIDE_PATH = os.path.join(DATA_DIR, "op_band_fnguide.csv")

MKTCAP_ITEM = "S102100"
TTM_ITEM = "M121505.M"
QUARTER_ITEM = "E121500.M"
HEADER_CODE_ROW = 8
HEADER_NAME_ROW = 9
HEADER_ITEM_ROW = 10
HEADER_BASEDATE_ROW = 12
DATA_START_ROW = 15

NICE_STEPS = [1, 2, 5, 10, 15, 20, 25, 30, 50, 100, 200, 250, 500, 1000]
TARGET_BAND_LINES = 6


def find_sector_curation_workbook():
    """"섹터별 구성 종목" 큐레이션이 담긴 "*데이터 모음*.xlsm" 파일 경로.
    2026-09-07, 주식 스크리닝 페이지를 삭제하면서 build_screening_page.py에 있던
    find_workbook()/load_sector_map()을 여기로 그대로 옮겨왔다(OP밴드가 섹터 폴백으로 계속
    씀) - 아래 find_workbook()(기업 밴드 찾기 엑셀 찾는 함수)과 이름이 겹쳐서 구분함."""
    candidates = glob.glob(os.path.join(MANUAL_DIR, "*데이터 모음*.xls*"))
    candidates = [c for c in candidates if not os.path.basename(c).startswith("~$")]
    return max(candidates, key=os.path.getmtime) if candidates else None


def load_sector_map():
    """"*데이터 모음*.xlsm"의 "섹터별 구성 종목" 시트 -> {종목명: 섹터}. 팀이 관리하는
    테마성 큐레이션이라 913개 종목만 커버 - load_naver_sector_map()(코드 기준, 거의 전종목
    커버)이 1순위고 여기는 그걸로 못 찾은 종목만 보충하는 폴백."""
    path = find_sector_curation_workbook()
    if not path:
        return {}
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_vba=False)
    if "섹터별 구성 종목" not in wb.sheetnames:
        return {}
    ws = wb["섹터별 구성 종목"]
    rows = list(ws.iter_rows(values_only=True))
    header_idx = next(i for i, r in enumerate(rows) if r[0] == "섹터")
    sectors = rows[header_idx]
    mapping = {}
    for r in rows[header_idx + 1:]:
        for col, sector in enumerate(sectors):
            if col == 0 or not sector:
                continue
            name = r[col] if col < len(r) else None
            if name and name not in mapping:
                mapping[name] = sector
    return mapping


def find_workbook():
    candidates = glob.glob(os.path.join(MANUAL_DIR, "*기업*밴드*.xls*"))
    candidates = [c for c in candidates if not os.path.basename(c).startswith("~$") and "갱신" not in os.path.basename(c)]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


# ---------------------------------------------------------------------------------------------
# 갱신용 파일 합치기(2026-10-23 사용자 "기업 밴드 찾기 2 갱신용 엑셀파일을 만들었거든 ... 이미 쌓여있는
# 데이터가 있으니까 갱신용을 이용해서 최신 데이터를 추가하기만 해줘").
# 본 파일(기업 밴드 찾기 2.xlsx, 2015~ 전체)은 한 번만 읽어 BAND_STORE_PATH에 쌓아두고, 갱신용 파일
# (같은 양식, 최근 며칠만)의 날짜 행을 (종목코드, 항목, 기준일) 열 단위로 맞춰 덮어쓰거나 뒤에 붙인다.
# 본 파일이 새로 저장되면(수정 시각이 바뀌면) 그걸로 다시 쌓는다. load_band_workbook()은 openpyxl
# 워크북처럼 쓸 수 있는 객체를 돌려줘서 기존 process_sheet 함수들을 그대로 쓴다.
# ---------------------------------------------------------------------------------------------
BAND_STORE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "screening", "cache", "band_store.pkl")


def find_update_workbook():
    candidates = glob.glob(os.path.join(MANUAL_DIR, "*기업*밴드*갱신*.xls*"))
    candidates = [c for c in candidates if not os.path.basename(c).startswith("~$")]
    return max(candidates, key=os.path.getmtime) if candidates else None


def _read_sheet_arrays(ws):
    """워크시트 -> (머리 행들, 날짜 목록, 값 행렬 float64(NaN=빈칸))."""
    import numpy as np
    max_col = ws.max_column
    head = [tuple(r) for r in ws.iter_rows(min_row=1, max_row=DATA_START_ROW - 1, max_col=max_col, values_only=True)]
    dates, rows = [], []
    for row in ws.iter_rows(min_row=DATA_START_ROW, max_col=max_col, values_only=True):
        d = row[0]
        if not isinstance(d, datetime):
            continue
        dates.append(d)
        rows.append([v if isinstance(v, (int, float)) else np.nan for v in row[1:max_col]] + [np.nan] * (max_col - len(row)))
    mat = np.array(rows, dtype="float64").reshape(len(rows), max_col - 1)
    return head, dates, mat


def _col_keys(head):
    codes, items, bases = head[HEADER_CODE_ROW - 1], head[HEADER_ITEM_ROW - 1], head[HEADER_BASEDATE_ROW - 1]
    return [(codes[k], items[k], bases[k] if k < len(bases) else None) for k in range(1, len(codes))]


def _merge_update(store, upd_path):
    """갱신용 파일 날짜 행을 store에 덮어쓰기/추가. 빈칸은 기존 값을 지우지 않는다."""
    import numpy as np
    wb = openpyxl.load_workbook(upd_path, read_only=True, data_only=True)
    added = replaced = 0
    for sn in wb.sheetnames:
        head, dates, mat = _read_sheet_arrays(wb[sn])
        ukeys = _col_keys(head)
        for sh in store["sheets"].values():
            pos = {k: i for i, k in enumerate(_col_keys(sh["head"]))}
            pairs = [(ui, pos[k]) for ui, k in enumerate(ukeys) if k[0] and k in pos]
            if not pairs:
                continue
            ui_idx = np.array([p[0] for p in pairs]); si_idx = np.array([p[1] for p in pairs])
            for r, d in enumerate(dates):
                vals = mat[r, ui_idx]
                if np.isnan(vals).all():
                    continue
                if d in sh["dates"]:
                    i = sh["dates"].index(d); replaced += 1
                else:
                    sh["dates"].append(d); sh["mat"] = np.vstack([sh["mat"], np.full((1, sh["mat"].shape[1]), np.nan)])
                    i = len(sh["dates"]) - 1; added += 1
                ok = ~np.isnan(vals)
                sh["mat"][i, si_idx[ok]] = vals[ok]
            order = sorted(range(len(sh["dates"])), key=lambda i: sh["dates"][i])
            sh["dates"] = [sh["dates"][i] for i in order]; sh["mat"] = sh["mat"][order]
    wb.close()
    return added, replaced


class _BandSheet:
    """openpyxl read_only 워크시트 흉내(iter_rows(values_only=True), max_row, max_column만)."""
    def __init__(self, sh):
        self.head, self.dates, self.mat = sh["head"], sh["dates"], sh["mat"]
        self.max_column = self.mat.shape[1] + 1
        self.max_row = DATA_START_ROW - 1 + len(self.dates)

    def iter_rows(self, min_row=1, max_row=None, max_col=None, values_only=True):
        import math as _m
        max_row = self.max_row if max_row is None else min(max_row, self.max_row)
        ncol = self.max_column if max_col is None else max_col
        for r in range(min_row, max_row + 1):
            if r < DATA_START_ROW:
                h = self.head[r - 1]
                yield tuple(h[:ncol]) + (None,) * max(0, ncol - len(h))
            else:
                i = r - DATA_START_ROW
                yield (self.dates[i],) + tuple(None if _m.isnan(v) else v for v in self.mat[i, :ncol - 1].tolist())


class _BandBook:
    def __init__(self, store):
        self.sheetnames = list(store["sheets"])
        self._s = {sn: _BandSheet(sh) for sn, sh in store["sheets"].items()}

    def __getitem__(self, sn):
        return self._s[sn]


def load_band_workbook(verbose=True):
    """본 파일 + 갱신용 파일을 합친 워크북 객체(없으면 None)."""
    import pickle
    main = find_workbook()
    if not main:
        return None
    store = None
    if os.path.exists(BAND_STORE_PATH):
        try:
            with open(BAND_STORE_PATH, "rb") as f:
                store = pickle.load(f)
        except Exception:
            store = None
    main_mtime = os.path.getmtime(main)
    changed = False
    if store is None or store.get("main") != os.path.basename(main) or store.get("main_mtime") != main_mtime:
        if verbose:
            print(f"본 워크북 읽어서 쌓기: {main}")
        wb = openpyxl.load_workbook(main, read_only=True, data_only=True)
        store = {"main": os.path.basename(main), "main_mtime": main_mtime, "upd_mtime": None, "sheets": {}}
        for sn in wb.sheetnames:
            head, dates, mat = _read_sheet_arrays(wb[sn])
            store["sheets"][sn] = {"head": head, "dates": dates, "mat": mat}
        wb.close()
        changed = True
    upd = find_update_workbook()
    if upd and store.get("upd_mtime") != os.path.getmtime(upd):
        added, replaced = _merge_update(store, upd)
        store["upd_mtime"] = os.path.getmtime(upd)
        changed = True
        if verbose:
            print(f"갱신용 반영: {os.path.basename(upd)} - 새 날짜 {added}행, 덮어쓴 날짜 {replaced}행")
    if changed:
        os.makedirs(os.path.dirname(BAND_STORE_PATH), exist_ok=True)
        with open(BAND_STORE_PATH, "wb") as f:
            pickle.dump(store, f, protocol=4)
    if verbose:
        last = max((sh["dates"][-1] for sh in store["sheets"].values() if sh["dates"]), default=None)
        print(f"밴드 데이터 마지막 날짜: {last:%Y-%m-%d}" if last else "밴드 데이터 날짜 없음")
    return _BandBook(store)


def detect_blocks(row_codes):
    """1번(A열=날짜/라벨열) 다음부터 코드가 연속되는 구간을 회사 블록으로 묶는다."""
    blocks = []
    n = len(row_codes)
    i = 1
    while i < n:
        code = row_codes[i]
        if code is None:
            i += 1
            continue
        j = i
        while j < n and row_codes[j] == code:
            j += 1
        blocks.append((code, i, j))
        i = j
    return blocks


def pick_band_multiples(mults):
    positive = [m for m in mults if m and m > 0]
    if not positive:
        return []
    end = math.ceil(max(positive)) + 1
    threshold = end / TARGET_BAND_LINES
    step = next((s for s in NICE_STEPS if s >= threshold), NICE_STEPS[-1])
    # 배수가 비정상적으로 큰 종목(영업이익 추정치가 0 근처를 지나가면 배수가 수억 배까지
    # 튄다)에서 while 루프가 폭주해 MemoryError가 나는 걸 막는다(2026-09-10, OP밴드 v2
    # 보간 분모에서 FY1 적자 + FY2 흑자가 섞여 0을 통과하며 발견). 정상 종목은 밴드선이
    # 수십 개를 넘지 않으므로 이 상한이 기존 결과를 바꾸지 않는다.
    MAX_LINES = 200
    n = min(int(end // step), MAX_LINES)
    multiples = [step * (i + 1) for i in range(n)]
    return multiples or [step]


def load_naver_sector_map():
    """data/sector_map.csv(fetch_naver_sector.py 결과) -> {종목코드(6자리): 섹터}.
    네이버 자체 업종분류라 상장사 대부분(2500여종목 중 2519개, 99%)을 커버한다 - 반면
    load_sector_map()(엑셀 "섹터별 구성 종목" 시트)는 팀이 관리하는 테마성 큐레이션이라 913개
    종목만 커버. 그래서 이쪽을 1순위로 쓰고, 여기 없는 종목만 엑셀 매핑으로 보충한다."""
    path = os.path.join(DATA_DIR, "sector_map.csv")
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path, dtype={"code": str})
    return dict(zip(df["code"], df["sector"]))


def load_fnguide_map():
    """data/op_band_fnguide.csv(fetch_op_band_consensus.py 결과) -> {code: {year: op_won}}."""
    if not os.path.exists(FNGUIDE_PATH):
        return {}
    df = pd.read_csv(FNGUIDE_PATH, dtype={"code": str})
    m = {}
    for _, row in df.iterrows():
        m.setdefault(row["code"], {})[int(row["year"])] = row["op_100mil"] * 1e8  # 억원 -> 원
    return m


def apply_year_override(code, data, override_map, current_use_year):
    """엑셀/FnGuide 값을 현재 회계연도(use_year) 구간에 한해 override_map 값으로 덮어쓴다.
    과거 구간(이미 지나간 회계연도)은 되돌릴 수 없으니 건드리지 않고, 지금 활성 회계연도에
    해당하는 날짜들(시계열 맨 끝의 연속 구간)만 보정한다. FnGuide 교차검증과 사용자 수동
    지정(data/manual/op_band_overrides.csv) 둘 다 이 함수를 공용으로 쓴다."""
    op_by_year = override_map.get(code)
    if not op_by_year or current_use_year not in op_by_year:
        return data

    new_op_won = op_by_year[current_use_year]
    if new_op_won == 0:
        return data

    dates = data["dates"]
    mktcap = data["mktcap"]
    op = data["op"][:]
    mult = data["mult"][:]

    for i in range(len(dates) - 1, -1, -1):
        d = datetime.strptime(dates[i], "%Y-%m-%d")
        use_year = d.year if d.month <= 6 else d.year + 1
        if use_year != current_use_year:
            break
        op[i] = round(new_op_won, 0)
        if mktcap[i] is not None:
            mult[i] = round(mktcap[i] / new_op_won, 4)

    data["op"] = op
    data["mult"] = mult
    return data

