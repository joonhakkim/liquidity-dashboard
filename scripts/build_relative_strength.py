"""
종목/섹터 상대강도 순위 페이지(docs/relative_strength.html)를 만든다 (2026-10-06 사용자 요청 - "저번에 줬던
표처럼 1주,2주,1달,2달,3달 수익률을 코스피대비 수익률로 두고 각 기간별 기업의 순위를 비교할 수 있게",
"섹터는 우리 기업섹터정리를 우선적으로 하고 없는 것들은 네이버에서 따오는 것들로").

2026-10-07 추가 요청 - "기간별로 순위로 기업 나열할 수 있게"(탭2 기간별 순위표), "섹터별 수익률 순위, 섹터 종류는
섹터 정리 파일에 있는 섹터로만, 그안의 기업도"(탭3 섹터별 순위, 섹터를 누르면 소속 종목 펼침). 섹터 수익률은
사용자 엑셀 '섹터 상대강도' 시트와 같은 방식(소속 종목 초과수익률의 단순 평균, 2026-10-07 엑셀과 대조해 확인)을
기본으로 하고 중앙값/이긴 종목 비율도 고를 수 있다. 섹터 '코스피 비중'은 그 엑셀 시트와 같은 정의(섹터 정리에 든
코스피 종목 시가총액 기준, 합 100%)로 OP밴드 요약의 latest_mktcap을 쓴다.

- 가격: data/bollinger_prices.csv(코스피+코스닥 전종목 일별 종가, fetch_bollinger_prices.py 결과).
  수정주가가 아니라서 액면분할/병합 같은 하루 ±31% 초과 변동일은 그날 수익률을 0으로 보고 건너뛴다
  (2026-10-06 사용자 엑셀의 수정주가 기반 표와 대조해서 거의 같은 값이 나옴을 확인).
- 기간: 1주=5, 2주=10, 1달=20, 2달=40, 3달=60, 6달=120 거래일 (사용자 엑셀 '개별종목 상대강도'와 동일).
- 초과수익률 = 종목 N거래일 수익률 - 코스피 N거래일 수익률 (단순 차이). 코스닥 종목도 일단 코스피 대비.
- 순위: 기간마다 초과수익률 내림차순(1위=코스피 대비 가장 강한 종목). 전체 종목 내 순위와 같은 섹터
  안의 순위를 브라우저에서 바로 계산해 토글로 보여준다(필터/정렬/상위 N% 조건도 브라우저 쪽).
- 섹터: data/manual/섹터 정리.xlsx(사용자 분류) 우선, 거기 없는 종목은 data/sector_map.csv(네이버 업종).
- 유니버스: 코스피+코스닥 보통주(스팩/우선주/리츠·인프라펀드 제외), 기준일에 가격이 있는 종목.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from build_bollinger_breakout import fetch_index_history  # noqa: E402
from build_yoy_accel_tracker import load_custom_sector_map  # noqa: E402

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(BASE_DIR, "data")
DOCS_DIR = os.path.join(BASE_DIR, "docs")
PRICES_PATH = os.path.join(DATA_DIR, "bollinger_prices.csv")
NAVER_SECTOR_PATH = os.path.join(DATA_DIR, "sector_map.csv")
KOSPI_CACHE_PATH = os.path.join(DATA_DIR, "kospi_index_cache.csv")
SUMMARY_PATH = os.path.join(DATA_DIR, "screening", "relative_strength_summary.csv")
SECTOR_SUMMARY_PATH = os.path.join(DATA_DIR, "screening", "sector_relative_strength_summary.csv")
OP_BAND_SUMMARY_PATH = os.path.join(DATA_DIR, "screening", "op_band_summary.csv")
PAGE_OUT_PATH = os.path.join(DOCS_DIR, "relative_strength.html")

HORIZONS = [("1주", 5), ("2주", 10), ("1달", 20), ("2달", 40), ("3달", 60), ("6달", 120)]
MAX_DAILY_MOVE = 0.31   # 이보다 크게 움직인 날은 분할/병합 등으로 보고 수익률 0 처리
KEEP_DAYS = 420          # 가격 파일에서 마지막 날짜 기준 이만큼(달력일)만 읽는다(120거래일 + 여유)

# 섹터 정리.xlsx에 옛 사명/오타로 적혀 있어 이름으로는 못 찾는 종목 -> 종목코드 직접 지정(2026-10-06 확인,
# 전부 사명 변경 또는 오타). 새로 이런 종목이 생기면 여기에 추가하면 된다.
NAME_TO_CODE_ALIASES = {
    "에프앤에스테크": "083500", "에스에프에이": "056190", "금호석유": "011780", "케이카": "381970",
    "한국타이어": "161390", "씨아이에스": "222080", "프레스티지바이오로직": "334970",
    "큐리옥스바이오시스템": "445680", "씨어스테크놀로지": "458870", "SPC삼립": "005610",
    "엔씨소프트": "036570", "한글과컴퓨터": "030520", "SGA": "049470", "유비케어": "032620",
    "라이온시큐어": "042510", "티웨이항공": "091810", "씨메스": "475400", "HDC현대산업개발": "294870",
}


def norm(s):
    return re.sub(r"\s+", "", str(s)).upper()


def read_recent_prices(path, keep_days=KEEP_DAYS):
    """가격 파일은 날짜 오름차순으로 쌓이므로 마지막 줄의 날짜를 보고, 그보다 keep_days일 전부터만 남긴다."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 4096))
        tail = f.read().decode("utf-8", errors="ignore").strip().splitlines()
    last_date = tail[-1].split(",")[0]
    cutoff = (datetime.strptime(last_date, "%Y%m%d") - timedelta(days=keep_days)).strftime("%Y%m%d")
    chunks = []
    for ch in pd.read_csv(path, dtype={"date": str, "code": str},
                          usecols=["date", "market", "code", "name", "close"], chunksize=1_000_000):
        ch = ch[ch["date"] >= cutoff]
        if len(ch):
            chunks.append(ch)
    return pd.concat(chunks, ignore_index=True)


def load_kospi_series():
    """네이버 차트 API로 코스피 지수를 받고(성공하면 캐시 갱신), 실패하면 마지막 캐시를 쓴다."""
    try:
        hist = fetch_index_history("KOSPI")
        if len(hist) > 500:
            pd.Series(hist, name="close").rename_axis("date").to_csv(KOSPI_CACHE_PATH, header=True)
            return hist
    except Exception as e:  # noqa: BLE001
        print(f"경고: 코스피 지수 조회 실패({e}) - 캐시 사용")
    if os.path.exists(KOSPI_CACHE_PATH):
        c = pd.read_csv(KOSPI_CACHE_PATH, dtype={"date": str})
        return dict(zip(c["date"], c["close"]))
    raise SystemExit("코스피 지수를 못 구했습니다(조회 실패 + 캐시 없음)")


def exclusion_mask(meta):
    """스팩/우선주/리츠·인프라펀드는 보통주 비교에서 뺀다."""
    spac = meta["name"].str.contains("스팩|SPAC|기업인수목적", regex=True)
    pref = meta.index.str[-1] != "0"   # 우선주는 종목코드 끝자리가 0이 아님
    reit = (meta["market"] == "KOSPI") & meta["name"].str.contains(r"리츠$|이리츠코크렙|맥쿼리인프라|KB발해인프라", regex=True)
    return spac | pref | reit


def assign_sectors(meta):
    """종목코드 -> (섹터, 출처 'U'=내 섹터 정리 / 'N'=네이버 업종 / ''=미분류)."""
    user = load_custom_sector_map()          # 종목명 -> 섹터
    code_by_name = {}
    for code, name in meta["name"].items():
        code_by_name.setdefault(norm(name), []).append(code)
    result = {}
    unmatched = []
    for name, sector in user.items():
        code = NAME_TO_CODE_ALIASES.get(name)
        if code is None:
            cands = code_by_name.get(norm(name), [])
            code = cands[0] if cands else None
        if code is None or code not in meta.index:
            unmatched.append(name)
            continue
        result[code] = (sector, "U")
    naver = {}
    if os.path.exists(NAVER_SECTOR_PATH):
        sm = pd.read_csv(NAVER_SECTOR_PATH, dtype={"code": str})
        naver = dict(zip(sm["code"], sm["sector"]))
    out = {}
    for code in meta.index:
        if code in result:
            out[code] = result[code]
        elif naver.get(code):
            out[code] = (naver[code], "N")
        else:
            out[code] = ("미분류", "")
    return out, unmatched


def compute(asof=None):
    df = read_recent_prices(PRICES_PATH)
    wide = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    if asof:
        wide = wide[wide.index <= asof]
    # 마지막 날짜가 일부 종목만 채워진 불완전한 날이면 한 날 앞으로 물린다
    cnt = wide.notna().sum(axis=1)
    while len(cnt) > 1 and cnt.iloc[-1] < 0.9 * cnt.iloc[-20:-1].median():
        wide = wide.iloc[:-1]
        cnt = cnt.iloc[:-1]
    asof_date = wide.index[-1]
    pos = len(wide) - 1
    meta = df.sort_values("date").groupby("code").agg(name=("name", "last"), market=("market", "last"))
    meta = meta.loc[wide.columns]
    meta = meta[~exclusion_mask(meta)]
    wide = wide[meta.index]
    active = wide.iloc[-1].notna()
    codes = list(wide.columns[active])

    R = wide.pct_change(fill_method=None)
    R[R.abs() > MAX_DAILY_MOVE] = np.nan
    L = np.log1p(R.fillna(0)).cumsum()

    kospi = load_kospi_series()
    if asof_date not in kospi:
        raise SystemExit(f"코스피 지수에 기준일({asof_date})이 없습니다")
    horizons, ex_cols = [], {}
    for label, n in HORIZONS:
        if pos - n < 0:
            raise SystemExit("가격 이력이 부족합니다")
        d0 = wide.index[pos - n]
        k_ret = kospi[asof_date] / kospi[d0] - 1
        ret = np.expm1(L.iloc[-1] - L.iloc[pos - n])
        valid = wide.iloc[-1].notna() & wide.iloc[pos - n].notna()
        ex = ((ret - k_ret) * 100).where(valid)
        ex_cols[label] = ex[codes]
        horizons.append({"label": label, "n": n, "date0": f"{d0[:4]}-{d0[4:6]}-{d0[6:]}",
                         "kospi": round(k_ret * 100, 2), "wins": int((ex[codes] > 0).sum()),
                         "total": int(ex[codes].notna().sum())})
    sectors, unmatched = assign_sectors(meta.loc[codes])
    return asof_date, meta.loc[codes], ex_cols, horizons, sectors, unmatched


def compute_sector_weights(meta, sectors):
    """섹터 정리 섹터별 '코스피 비중' = 그 섹터의 코스피 상장 종목 시가총액 합 / 섹터 정리에 든 코스피 종목 전체 시가총액 합
    (사용자 엑셀 '섹터 상대강도' 시트의 코스피비중과 같은 정의 - 합이 100%). 시가총액은 OP밴드 요약(latest_mktcap)."""
    if not os.path.exists(OP_BAND_SUMMARY_PATH):
        return {}
    op = pd.read_csv(OP_BAND_SUMMARY_PATH, dtype={"code": str}, usecols=["code", "latest_mktcap"])
    op["code"] = op["code"].str.lstrip("A")
    cap = dict(zip(op["code"], op["latest_mktcap"]))
    by, tot = {}, 0.0
    for code in meta.index:
        sec, src = sectors[code]
        if src != "U" or meta.at[code, "market"] != "KOSPI":
            continue
        c = cap.get(code)
        if c is None or pd.isna(c):
            continue
        by[sec] = by.get(sec, 0.0) + float(c)
        tot += float(c)
    return {k: round(v / tot, 6) for k, v in by.items()} if tot > 0 else {}


def save_sector_summary(meta, ex_cols, sectors, sector_w):
    """섹터 정리 섹터별 요약(평균/중앙값 초과수익률, 이긴 종목 비율, 평균 기준 순위) - 페이지 계산 검증/분석용."""
    rows = []
    for sec in sorted({s for s, src in sectors.values() if src == "U"}):
        codes = [c for c in meta.index if sectors[c] == (sec, "U")]
        r = {"sector": sec, "n": len(codes), "kospi_weight": sector_w.get(sec)}
        for label, _ in HORIZONS:
            v = ex_cols[label].reindex(codes).dropna()
            r[f"mean_{label}"] = round(float(v.mean()), 3) if len(v) else None
            r[f"median_{label}"] = round(float(v.median()), 3) if len(v) else None
            r[f"win_{label}"] = round(float((v > 0).mean() * 100), 1) if len(v) else None
        rows.append(r)
    out = pd.DataFrame(rows)
    for label, _ in HORIZONS:
        out[f"rank_mean_{label}"] = out[f"mean_{label}"].rank(ascending=False, method="first")
    os.makedirs(os.path.dirname(SECTOR_SUMMARY_PATH), exist_ok=True)
    out.to_csv(SECTOR_SUMMARY_PATH, index=False, encoding="utf-8-sig")


def to_json_rows(meta, ex_cols, sectors):
    rows = []
    for code in meta.index:
        sec, src = sectors[code]
        vals = []
        for label, _ in HORIZONS:
            v = ex_cols[label].get(code)
            vals.append(None if v is None or pd.isna(v) else round(float(v), 3))
        rows.append([code, meta.at[code, "name"], "P" if meta.at[code, "market"] == "KOSPI" else "Q", sec, src] + vals)
    return rows


def save_summary(meta, ex_cols, sectors):
    out = pd.DataFrame({"code": meta.index, "name": meta["name"].values, "market": meta["market"].values})
    out["sector"] = [sectors[c][0] for c in meta.index]
    out["sector_src"] = [sectors[c][1] for c in meta.index]
    for i, (label, _) in enumerate(HORIZONS, start=1):
        s = ex_cols[label]
        out[f"ex_{label}"] = s.reindex(meta.index).round(3).values
        out[f"rank_{label}"] = s.reindex(meta.index).rank(ascending=False, method="first").values
    os.makedirs(os.path.dirname(SUMMARY_PATH), exist_ok=True)
    out.to_csv(SUMMARY_PATH, index=False, encoding="utf-8-sig")


def main():
    global PAGE_OUT_PATH, SUMMARY_PATH, SECTOR_SUMMARY_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", help="테스트용: 이 날짜(YYYYMMDD)까지의 데이터로 계산")
    ap.add_argument("--out", help="테스트용: HTML 출력 경로")
    ap.add_argument("--summary", help="테스트용: 요약 CSV 출력 경로")
    ap.add_argument("--sector-summary", help="테스트용: 섹터 요약 CSV 출력 경로")
    args = ap.parse_args()
    if args.sector_summary:
        SECTOR_SUMMARY_PATH = args.sector_summary
    if args.out:
        PAGE_OUT_PATH = args.out
    if args.summary:
        SUMMARY_PATH = args.summary

    asof_date, meta, ex_cols, horizons, sectors, unmatched = compute(args.asof)
    rows = to_json_rows(meta, ex_cols, sectors)
    n_user = sum(1 for r in rows if r[4] == "U")
    n_naver = sum(1 for r in rows if r[4] == "N")
    print(f"기준일 {asof_date} | 종목 {len(rows)} (내 섹터 정리 {n_user}, 네이버 {n_naver}, 미분류 {len(rows) - n_user - n_naver})")
    if unmatched:
        print(f"섹터 정리.xlsx 종목 중 가격 데이터와 매칭 안 된 종목 {len(unmatched)}개: {', '.join(unmatched[:20])}")
    for h in horizons:
        print(f"  {h['label']}: 코스피 {h['kospi']:+.2f}% | 이긴 종목 {h['wins']}/{h['total']}")
    save_summary(meta, ex_cols, sectors)
    sector_w = compute_sector_weights(meta, sectors)
    save_sector_summary(meta, ex_cols, sectors, sector_w)

    payload = {"asof": f"{asof_date[:4]}-{asof_date[4:6]}-{asof_date[6:]}", "horizons": horizons, "rows": rows,
               "sectorW": sector_w}
    html = (TEMPLATE
            .replace("__UPDATED__", datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__ASOF__", payload["asof"])
            .replace("__NSTOCKS__", f"{len(rows):,}")
            .replace("__NUSER__", f"{n_user:,}")
            .replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
    os.makedirs(os.path.dirname(PAGE_OUT_PATH), exist_ok=True)
    with open(PAGE_OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"저장 완료: {SUMMARY_PATH}")
    print(f"저장 완료: {SECTOR_SUMMARY_PATH}")
    print(f"저장 완료: {PAGE_OUT_PATH}")


TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>종목·섹터 상대강도 순위</title>
<style>
  body { font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  a.back { color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }
  h1 { font-size:20px; margin:8px 0 4px 0; }
  h1 .sub { font-size:13px; color:#9aa0a6; font-weight:normal; margin-left:6px; }
  .updated { color:#9aa0a6; font-size:13px; margin-bottom:16px; }
  .exp { background:#1a1d24; border-radius:10px; padding:14px 18px; font-size:12px; color:#9aa0a6; line-height:1.8; max-width:1100px; margin-bottom:18px; }
  .exp b { color:#ffa94d; }
  table.sum { border-collapse:collapse; font-size:13px; margin-bottom:14px; }
  table.sum th, table.sum td { padding:6px 14px; text-align:right; border-bottom:1px solid #23262e; white-space:nowrap; }
  table.sum th { color:#9aa0a6; font-weight:normal; font-size:12px; }
  table.sum td.lbl, table.sum th.lbl { text-align:left; color:#9aa0a6; }
  .tabs { display:flex; gap:6px; margin:6px 0 12px 0; flex-wrap:wrap; }
  .tabs button { background:#1a1d24; border:1px solid #2a2e37; color:#9aa0a6; padding:8px 18px; border-radius:8px; cursor:pointer; font-size:14px; font-family:inherit; }
  .tabs button.on { background:#4dabf7; color:#0f1115; border-color:#4dabf7; font-weight:bold; }
  section.tab { display:none; }
  section.tab.on { display:block; }
  .filters { display:flex; gap:12px; flex-wrap:wrap; align-items:center; margin-bottom:10px; font-size:13px; color:#9aa0a6; }
  .filters input, .filters select { background:#1a1d24; border:1px solid #2a2e37; color:#e6e6e6; border-radius:6px; padding:6px 10px; font-size:13px; font-family:inherit; }
  .filters input[type="number"] { width:70px; }
  .filters input[type="text"] { width:150px; }
  .filters button { background:#1a1d24; border:1px solid #4dabf7; color:#4dabf7; border-radius:6px; padding:6px 12px; cursor:pointer; font-size:12px; font-family:inherit; }
  .hz-chk { display:flex; gap:8px; align-items:center; }
  .hz-chk label { color:#c9ccd1; cursor:pointer; }
  .count { color:#63e6be; font-size:12px; margin:8px 0; }
  .note { color:#6b7280; font-size:12px; margin:4px 0 10px 0; line-height:1.7; }
  .table-wrap { overflow:auto; max-height:76vh; border:1px solid #23262e; border-radius:8px; }
  table.main { border-collapse:separate; border-spacing:0; width:100%; font-size:13px; }
  table.main th, table.main td { padding:6px 9px; text-align:center; border-bottom:1px solid #1d2027; white-space:nowrap; }
  table.main thead th { color:#9aa0a6; font-weight:normal; font-size:12px; position:sticky; top:0; background:#141720; z-index:2; cursor:pointer; user-select:none; }
  table.main thead tr.r2 th { top:30px; font-size:11px; }
  table.main thead th.grp { color:#e6e6e6; border-left:1px solid #2a2e37; cursor:default; }
  table.main th.lbl, table.main td.lbl { text-align:left; }
  table.main td.nm { position:sticky; left:0; background:#0f1115; z-index:1; font-weight:600; }
  table.main thead th.nm { position:sticky; left:0; z-index:4; }
  table.main td.nm a { color:#e6e6e6; text-decoration:none; }
  table.main td.nm a:hover { color:#4dabf7; text-decoration:underline; }
  table.main td.first { border-left:1px solid #23262e; }
  table.main tbody tr:hover td { background:#1a1d24; }
  table.main tbody tr:hover td.nm { background:#1a1d24; }
  tr.srow { cursor:pointer; }
  tr.srow td.nm { font-weight:700; }
  tr.mrow td { background:#12151b; color:#c9ccd1; font-size:12px; }
  tr.mrow td.nm { background:#12151b; font-weight:500; padding-left:26px; }
  tr.mrow:hover td, tr.mrow:hover td.nm { background:#1a1d24; }
  .tg { color:#4dabf7; display:inline-block; width:14px; }
  button.lnk { background:none; border:none; color:#4dabf7; cursor:pointer; font-family:inherit; font-size:12px; padding:2px 0; }
  .pos { color:#ff8787; }
  .neg { color:#74a7ff; }
  .na { color:#3a3d45; }
  .rk { font-weight:600; min-width:44px; }
  .mk { font-size:11px; color:#9aa0a6; }
  .sub { color:#6b7280; font-size:11px; }
  .nv { display:inline-block; background:#a9c8ec33; color:#4dabf7; border:1px solid #4dabf7; border-radius:4px; font-size:10px; padding:0 4px; margin-left:5px; }
  .arrow { color:#4dabf7; font-size:10px; margin-left:3px; }
  #moreBtn { display:none; margin:12px auto; background:#1a1d24; border:1px solid #2a2e37; color:#c9ccd1; border-radius:6px; padding:8px 20px; cursor:pointer; font-family:inherit; }
  .legend { display:flex; gap:14px; font-size:12px; color:#9aa0a6; margin:6px 0 10px 0; align-items:center; flex-wrap:wrap; }
  .sq { width:14px; height:10px; border-radius:2px; display:inline-block; }
  table.board { border-collapse:separate; border-spacing:0; width:100%; font-size:13px; }
  table.board th { color:#9aa0a6; font-weight:normal; font-size:12px; position:sticky; top:0; background:#141720; z-index:2; padding:6px 9px; text-align:left; border-bottom:1px solid #23262e; white-space:nowrap; }
  table.board td { padding:5px 9px; border-bottom:1px solid #1d2027; vertical-align:top; white-space:nowrap; }
  table.board td.rn { color:#6b7280; text-align:right; width:30px; }
  table.board td.c a { color:#e6e6e6; text-decoration:none; font-weight:600; }
  table.board td.c a:hover { color:#4dabf7; text-decoration:underline; }
  table.board td.c.hl { background:#27406b; }
  table.board td.c .sub { line-height:1.3; }
  .mc { display:inline-block; font-size:10px; color:#ffa94d; border:1px solid #ffa94d66; border-radius:4px; padding:0 3px; margin-left:5px; }
  .mc3 { background:#ffa94d33; font-weight:bold; }
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>종목·섹터 상대강도 순위<span class="sub">코스피 대비 초과수익률 · 기간별 순위 비교</span></h1>
  <div class="updated">최종 갱신: __UPDATED__ &middot; 데이터 기준일: __ASOF__ &middot; __NSTOCKS__종목(코스피+코스닥 보통주, 그중 내 섹터 정리 __NUSER__종목)</div>

  <div class="exp">
    <b>무엇을 보는 페이지인가</b><br>
    기간별 수익률에서 <b>같은 기간 코스피 수익률을 뺀 초과수익률</b>을 구하고, 기간마다 높은 순서대로
    <b>순위</b>(1위 = 코스피 대비 가장 강한 종목)를 매겼습니다. 기간은 거래일 기준(1주=5, 2주=10, 1달=20,
    2달=40, 3달=60, 6달=120일)이고 코스닥 종목도 일단 코스피 대비로 계산했습니다.<br>
    <b>① 종목별 표</b>: 종목 한 줄에 6개 기간의 순위·초과수익률이 나란히 있습니다. 열 머리글을 누르거나 "정렬" 메뉴로 원하는 기간의
    순위/수익률 순으로 줄 세울 수 있고, "상위 N% 이내" 조건에 기간을 체크하면 체크한 기간 <b>모두</b>에서 상위 N% 안에 든 종목만 남습니다.
    순위 색은 빨강=상위, 파랑=하위(진할수록 극단)입니다.<br>
    <b>② 기간별 순위표</b>: 기간마다 상위(또는 하위) N개 종목을 순위대로 나열합니다. 같은 종목에 마우스를 올리면 다른 기간 열에서도 강조되고,
    <span class="mc" style="margin-left:0">×3</span>처럼 표시된 종목은 표시된 N개 안에 그 개수만큼의 기간에 동시에 올라온 종목입니다.<br>
    <b>③ 섹터별 순위</b>: 내 섹터 정리(<code>섹터 정리.xlsx</code>)의 섹터로만 구분해서, 섹터 소속 종목의 초과수익률 <b>단순 평균</b>(엑셀 '섹터 상대강도'
    시트와 같은 방식)으로 섹터 순위를 매겼고, 섹터를 누르면 소속 종목이 펼쳐집니다. 평균은 소수 종목의 급등락에 흔들릴 수 있어 중앙값·이긴 종목 비율로도 볼 수
    있습니다. 코스피 비중은 섹터 정리에 든 코스피 종목의 시가총액 기준(합 100%)입니다.<br>
    <b>섹터</b>는 내 섹터 정리 분류를 우선 쓰고, 거기 없는 종목은 네이버 업종(<span class="nv" style="margin-left:0">N</span> 표시)으로 채웠습니다.
    가격은 수정주가가 아니라 종가라서, 액면분할 등으로 하루 ±31%를 넘게 움직인 날은 0%로 처리했습니다
    (무상증자처럼 그보다 작은 조정은 반영되지 않아 일부 종목은 수정주가 기준 엑셀 표와 다를 수 있습니다 - 2026-08-07 기준
    엑셀 표와 대조하면 종목의 95~99%가 0.5%p 이내로 일치). 스팩·우선주·리츠는 제외했습니다.
  </div>

  <table class="sum" id="sumTable"></table>

  <div class="tabs" id="tabs">
    <button data-tab="t1" class="on">① 종목별 표</button>
    <button data-tab="t2">② 기간별 순위표</button>
    <button data-tab="t3">③ 섹터별 순위</button>
  </div>

  <div id="stockBar">
    <div class="filters">
      <label class="t1only">검색 <input type="text" id="fSearch" placeholder="종목명/코드"></label>
      <label>시장 <select id="fMarket"><option value="">전체</option><option value="P">코스피</option><option value="Q">코스닥</option></select></label>
      <label>섹터 <select id="fSector"><option value="">전체</option></select></label>
      <label><input type="checkbox" id="fUserOnly"> 내 섹터 정리 종목만</label>
      <label class="t1only">순위 기준 <select id="fScope"><option value="all">전체 종목</option><option value="user">내 섹터 정리 종목 안</option><option value="sec">같은 섹터 안</option></select></label>
      <label class="t1only">표시 <select id="fShow"><option value="both">순위 + 초과수익률</option><option value="rank">순위만</option><option value="ex">초과수익률만</option></select></label>
      <label class="t1only">정렬 <select id="fSort"></select></label>
    </div>
    <div class="filters t1only">
      <label>상위 <input type="number" id="fTop" min="0" max="100" step="1" placeholder="예 10"> % 이내인 기간(체크한 기간 모두):</label>
      <span class="hz-chk" id="hzChk"></span>
      <button id="resetBtn">필터 초기화</button>
      <button id="csvBtn">현재 표 CSV 저장</button>
    </div>
  </div>

  <section class="tab on" id="t1">
    <div class="legend"><span>순위 색</span><span class="sq" style="background:rgba(255,99,99,0.58)"></span><span>상위</span><span class="sq" style="background:rgba(255,255,255,0.06)"></span><span>중간</span><span class="sq" style="background:rgba(77,171,247,0.58)"></span><span>하위</span><span>&nbsp;&middot; 칸에 마우스를 올리면 "N종목 중 몇 위(상위 몇 %)"가 보입니다</span></div>
    <div class="count" id="count"></div>
    <div class="table-wrap">
      <table class="main">
        <thead id="thead"></thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
    <button id="moreBtn">더 보기</button>
  </section>

  <section class="tab" id="t2">
    <div class="filters">
      <label>방향 <select id="bDir"><option value="top">상위(코스피를 가장 크게 이긴 종목)</option><option value="bot">하위(가장 크게 진 종목)</option></select></label>
      <label>표시 개수 <select id="bN"><option>10</option><option>20</option><option selected>30</option><option>50</option><option>100</option><option>200</option></select></label>
      <label><input type="checkbox" id="bSec" checked> 섹터 표시</label>
      <button id="bCsv">CSV 저장</button>
    </div>
    <div class="note">위의 시장·섹터·"내 섹터 정리 종목만" 조건을 적용한 <b>그 안에서의 순위</b>입니다(조건이 없으면 전체 종목 순위). 같은 종목에 마우스를 올리면 다른 기간 열에서도 강조됩니다.</div>
    <div class="table-wrap"><table class="board" id="board"></table></div>
  </section>

  <section class="tab" id="t3">
    <div class="filters">
      <label>순위 기준 <select id="sMetric"><option value="mean">평균 초과수익률 (엑셀 '섹터 상대강도'와 같은 방식)</option><option value="median">중앙값 초과수익률</option><option value="win">코스피를 이긴 종목 비율</option></select></label>
      <label><input type="checkbox" id="sAbs"> 절대수익률로 보기(코스피 수익률 더함)</label>
      <label><input type="checkbox" id="sNoMisc"> 개별주 묶음 제외</label>
      <label>최소 종목수 <input type="number" id="sMinN" min="1" step="1" value="1"></label>
      <button id="sExpandAll">모두 펼치기</button>
      <button id="sCollapseAll">모두 접기</button>
      <button id="sCsv">CSV 저장</button>
    </div>
    <table class="sum" id="secSum"></table>
    <div class="note" id="secNote"></div>
    <div class="table-wrap">
      <table class="main">
        <thead id="sHead"></thead>
        <tbody id="sBody"></tbody>
      </table>
    </div>
  </section>

<script>
const DATA = __DATA__;
const HZ = DATA.horizons, NH = HZ.length;
const ROWS = DATA.rows.map(r => ({code: r[0], name: r[1], mkt: r[2], sec: r[3], src: r[4], ex: r.slice(5, 5 + NH)}));
const SECW = DATA.sectorW || {};
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const sgn = (x, d) => (x >= 0 ? '+' : '') + x.toFixed(d === undefined ? 1 : d);
const nameLink = r => '<a href="https://finance.naver.com/item/main.naver?code=' + r.code + '" target="_blank" rel="noopener">' + esc(r.name) + '</a>';
const mean = a => a.length ? a.reduce((s, x) => s + x, 0) / a.length : null;
const median = a => { const b = a.slice().sort((x, y) => x - y), n = b.length; return n ? (n % 2 ? b[(n - 1) / 2] : (b[n / 2 - 1] + b[n / 2]) / 2) : null; };
const secKey = i => ROWS[i].src + ':' + ROWS[i].sec;
let TAB = 't1';

// ---------- 순위 사전 계산 (전체 종목 / 내 섹터 정리 종목 안 / 같은 섹터 안)
const rkAll = ROWS.map(() => new Array(NH).fill(null));
const rkSec = ROWS.map(() => new Array(NH).fill(null));
const rkUser = ROWS.map(() => new Array(NH).fill(null));
const nAll = new Array(NH).fill(0);
const nUser = new Array(NH).fill(0);
const nSecBy = {};
(function computeRanks() {
  for (let h = 0; h < NH; h++) {
    const idx = [];
    ROWS.forEach((r, i) => { if (r.ex[h] !== null) idx.push(i); });
    idx.sort((a, b) => ROWS[b].ex[h] - ROWS[a].ex[h]);
    nAll[h] = idx.length;
    const bySec = {};
    idx.forEach((i, k) => {
      rkAll[i][h] = k + 1;
      if (ROWS[i].src === 'U') { nUser[h]++; rkUser[i][h] = nUser[h]; }
      const s = secKey(i);
      if (!bySec[s]) bySec[s] = [];
      bySec[s].push(i);
    });
    for (const s in bySec) {
      if (!nSecBy[s]) nSecBy[s] = new Array(NH).fill(0);
      nSecBy[s][h] = bySec[s].length;
      bySec[s].forEach((i, k) => { rkSec[i][h] = k + 1; });
    }
  }
})();

const S = {scope: 'all', show: 'both', sortKey: 'rank', sortH: 0, sortDir: 1, limit: 200};
const rk = (i, h) => S.scope === 'all' ? rkAll[i][h] : (S.scope === 'user' ? rkUser[i][h] : rkSec[i][h]);
const tot = (i, h) => S.scope === 'all' ? nAll[h] : (S.scope === 'user' ? nUser[h] : nSecBy[secKey(i)][h]);
const SCOPE_LABEL = {all: '전체 종목', user: '내 섹터 정리 종목 안', sec: '같은 섹터 안'};

function heat(p) {           // p: 0(최상위) ~ 1(최하위)
  const t = Math.abs(0.5 - p) * 2;
  const a = (0.07 + 0.52 * Math.pow(t, 1.3)).toFixed(2);
  return p <= 0.5 ? 'background:rgba(255,99,99,' + a + ')' : 'background:rgba(77,171,247,' + a + ')';
}

// ---------- 상단 요약표
(function renderSummary() {
  let h = '<thead><tr><th class="lbl"></th>' + HZ.map(x => '<th>' + x.label + '</th>').join('') + '</tr></thead><tbody>';
  h += '<tr><td class="lbl">코스피 수익률</td>' + HZ.map(x => '<td class="' + (x.kospi >= 0 ? 'pos' : 'neg') + '">' + sgn(x.kospi, 2) + '%</td>').join('') + '</tr>';
  h += '<tr><td class="lbl">코스피를 이긴 종목 수</td>' + HZ.map(x => '<td>' + x.wins.toLocaleString() + ' / ' + x.total.toLocaleString() + '</td>').join('') + '</tr>';
  h += '<tr><td class="lbl">이긴 비율</td>' + HZ.map(x => '<td>' + (x.wins / x.total * 100).toFixed(0) + '%</td>').join('') + '</tr></tbody>';
  $('sumTable').innerHTML = h;
})();

// ---------- 공통 필터 컨트롤 초기화
(function initControls() {
  const cnt = {};
  ROWS.forEach(r => { const k = r.src + ':' + r.sec; cnt[k] = (cnt[k] || 0) + 1; });
  const mk = (src, title) => {
    const keys = Object.keys(cnt).filter(k => k[0] === src).sort((a, b) => cnt[b] - cnt[a]);
    if (!keys.length) return '';
    return '<optgroup label="' + title + '">' + keys.map(k => '<option value="' + esc(k) + '">' + esc(k.slice(2)) + ' (' + cnt[k] + ')</option>').join('') + '</optgroup>';
  };
  $('fSector').innerHTML = '<option value="">전체</option>' + mk('U', '내 섹터 정리') + mk('N', '네이버 업종') + mk('', '기타');
  $('hzChk').innerHTML = HZ.map((x, h) => '<label><input type="checkbox" id="tk' + h + '" checked> ' + x.label + '</label>').join('');
  $('fSort').innerHTML = '<option value="">(열 머리글 클릭으로 정렬)</option>' +
    HZ.map((x, h) => '<option value="rank:' + h + '">' + x.label + ' 순위</option>').join('') +
    HZ.map((x, h) => '<option value="ex:' + h + '">' + x.label + ' 초과수익률</option>').join('');
})();

// ---------- ① 종목별 표
function baseOk(i) {
  const r = ROWS[i];
  const mk = $('fMarket').value, sec = $('fSector').value;
  if (mk && r.mkt !== mk) return false;
  if (sec && secKey(i) !== sec) return false;
  if ($('fUserOnly').checked && r.src !== 'U') return false;
  return true;
}
function filtered() {
  const q = $('fSearch').value.trim().toLowerCase();
  const top = parseFloat($('fTop').value);
  const chk = HZ.map((_, h) => $('tk' + h).checked);
  const useTop = !isNaN(top) && top > 0 && chk.some(Boolean);
  const out = [];
  for (let i = 0; i < ROWS.length; i++) {
    const r = ROWS[i];
    if (!baseOk(i)) continue;
    if (q && !(r.name.toLowerCase().includes(q) || r.code.includes(q))) continue;
    if (S.scope === 'user' && r.src !== 'U') continue;
    if (useTop) {
      let ok = true;
      for (let h = 0; h < NH; h++) {
        if (!chk[h]) continue;
        const v = rk(i, h);
        if (v === null || v / tot(i, h) > top / 100 + 1e-9) { ok = false; break; }
      }
      if (!ok) continue;
    }
    out.push(i);
  }
  return out;
}
function sortList(list) {
  const k = S.sortKey, h = S.sortH, d = S.sortDir;
  list.sort((a, b) => {
    if (k === 'name') return d * ROWS[a].name.localeCompare(ROWS[b].name, 'ko');
    if (k === 'sec') return d * ROWS[a].sec.localeCompare(ROWS[b].sec, 'ko') || ROWS[a].name.localeCompare(ROWS[b].name, 'ko');
    if (k === 'mkt') return d * ROWS[a].mkt.localeCompare(ROWS[b].mkt);
    if (k === 'rank') {
      const va = rk(a, h), vb = rk(b, h);
      if (va === null && vb === null) return 0;
      if (va === null) return 1;
      if (vb === null) return -1;
      if (va !== vb) return d * (va - vb);
      return ROWS[b].ex[h] - ROWS[a].ex[h];
    }
    const va = ROWS[a].ex[h], vb = ROWS[b].ex[h];
    if (va === null && vb === null) return 0;
    if (va === null) return 1;
    if (vb === null) return -1;
    return d * (vb - va);
  });
  return list;
}
function renderHead() {
  const cols = S.show === 'both' ? ['rank', 'ex'] : [S.show];
  const txt = k => (k === 'name' || k === 'sec' || k === 'mkt');
  const arrow = (key, h) => (S.sortKey === key && (h === undefined || S.sortH === h)) ? '<span class="arrow">' + ((S.sortDir === 1) === txt(key) ? '▲' : '▼') + '</span>' : '';
  let r1 = '<tr><th class="lbl nm" rowspan="2" data-sort="name:0">종목명' + arrow('name') + '</th><th rowspan="2" data-sort="mkt:0">시장' + arrow('mkt') + '</th><th class="lbl" rowspan="2" data-sort="sec:0">섹터' + arrow('sec') + '</th>';
  let r2 = '<tr class="r2">';
  HZ.forEach((x, h) => {
    r1 += '<th class="grp" colspan="' + cols.length + '">' + x.label + ' <span class="sub">(' + x.n + '일)</span></th>';
    cols.forEach(c => { r2 += '<th data-sort="' + c + ':' + h + '">' + (c === 'rank' ? '순위' : '초과수익률') + arrow(c, h) + '</th>'; });
  });
  $('thead').innerHTML = r1 + '</tr>' + r2 + '</tr>';
}
function cellRank(i, h, first) {
  const v = rk(i, h), cls = 'rk' + (first ? ' first' : '');
  if (v === null) return '<td class="' + cls + ' na">-</td>';
  const t = tot(i, h), p = (v - 0.5) / t;
  return '<td class="' + cls + '" style="' + heat(p) + '" title="' + t.toLocaleString() + '종목 중 ' + v + '위 (상위 ' + (v / t * 100).toFixed(1) + '%)">' + v + '</td>';
}
function cellEx(i, h, first) {
  const x = ROWS[i].ex[h], base = first ? ' first' : '';
  if (x === null) return '<td class="na' + base + '">-</td>';
  return '<td class="' + (x >= 0 ? 'pos' : 'neg') + base + '">' + sgn(x) + '%</td>';
}
let CUR = [];
function renderBody() {
  const n = Math.min(CUR.length, S.limit);
  const parts = [];
  for (let k = 0; k < n; k++) {
    const i = CUR[k], r = ROWS[i];
    let tr = '<tr><td class="lbl nm">' + nameLink(r) + '</td>' +
      '<td class="mk">' + (r.mkt === 'P' ? '코스피' : '코스닥') + '</td>' +
      '<td class="lbl">' + esc(r.sec) + (r.src === 'N' ? '<span class="nv" title="내 섹터 정리에 없어서 네이버 업종으로 채움">N</span>' : '') + '</td>';
    for (let h = 0; h < NH; h++) {
      if (S.show === 'both') tr += cellRank(i, h, true) + cellEx(i, h, false);
      else if (S.show === 'rank') tr += cellRank(i, h, true);
      else tr += cellEx(i, h, true);
    }
    parts.push(tr + '</tr>');
  }
  $('tbody').innerHTML = parts.join('');
  $('moreBtn').style.display = CUR.length > n ? 'block' : 'none';
  $('moreBtn').textContent = '더 보기 (' + n.toLocaleString() + ' / ' + CUR.length.toLocaleString() + ')';
}
function sortLabel() {
  const k = S.sortKey;
  if (k === 'name') return '종목명';
  if (k === 'sec') return '섹터';
  if (k === 'mkt') return '시장';
  return HZ[S.sortH].label + (k === 'rank' ? ' 순위' : ' 초과수익률');
}
function refresh(resetLimit) {
  if (resetLimit) S.limit = 200;
  CUR = sortList(filtered());
  $('count').textContent = CUR.length.toLocaleString() + '종목 (전체 ' + ROWS.length.toLocaleString() + '종목 중) · 순위 기준: ' + SCOPE_LABEL[S.scope] + ' · 정렬: ' + sortLabel();
  $('fSort').value = (S.sortKey === 'rank' || S.sortKey === 'ex') ? S.sortKey + ':' + S.sortH : '';
  renderHead();
  renderBody();
}

// ---------- ② 기간별 순위표
let BOARD_CELLS = {};
let BOARD_LISTS = [];
function renderBoard() {
  const dir = $('bDir').value, N = parseInt($('bN').value, 10), showSec = $('bSec').checked;
  const set = [];
  for (let i = 0; i < ROWS.length; i++) if (baseOk(i)) set.push(i);
  BOARD_LISTS = HZ.map((_, h) => set.filter(i => ROWS[i].ex[h] !== null)
    .sort((a, b) => dir === 'top' ? ROWS[b].ex[h] - ROWS[a].ex[h] : ROWS[a].ex[h] - ROWS[b].ex[h]).slice(0, N));
  const cnt = {};
  BOARD_LISTS.forEach(l => l.forEach(i => { cnt[i] = (cnt[i] || 0) + 1; }));
  const nRows = Math.max(0, ...BOARD_LISTS.map(l => l.length));
  let h = '<thead><tr><th style="text-align:right">#</th>' + HZ.map(x => '<th>' + x.label + ' <span class="sub">코스피 ' + sgn(x.kospi, 2) + '%</span></th>').join('') + '</tr></thead><tbody>';
  for (let k = 0; k < nRows; k++) {
    h += '<tr><td class="rn">' + (k + 1) + '</td>';
    for (let hh = 0; hh < NH; hh++) {
      const i = BOARD_LISTS[hh][k];
      if (i === undefined) { h += '<td class="c"></td>'; continue; }
      const r = ROWS[i], c = cnt[i], x = r.ex[hh];
      h += '<td class="c" data-i="' + i + '">' + nameLink(r) + ' <span class="' + (x >= 0 ? 'pos' : 'neg') + '">' + sgn(x) + '%</span>' +
        (c >= 2 ? '<span class="mc' + (c >= 3 ? ' mc3' : '') + '" title="표시된 ' + c + '개 기간에 동시에 올라온 종목">×' + c + '</span>' : '') +
        (showSec ? '<div class="sub">' + esc(r.sec) + (r.src === 'N' ? ' (N)' : '') + '</div>' : '') + '</td>';
    }
    h += '</tr>';
  }
  $('board').innerHTML = h + '</tbody>';
  BOARD_CELLS = {};
  document.querySelectorAll('#board td[data-i]').forEach(td => { (BOARD_CELLS[td.dataset.i] = BOARD_CELLS[td.dataset.i] || []).push(td); });
}
$('board').addEventListener('mouseover', e => { const td = e.target.closest('td[data-i]'); if (td) (BOARD_CELLS[td.dataset.i] || []).forEach(x => x.classList.add('hl')); });
$('board').addEventListener('mouseout', e => { const td = e.target.closest('td[data-i]'); if (td) (BOARD_CELLS[td.dataset.i] || []).forEach(x => x.classList.remove('hl')); });

// ---------- ③ 섹터별 순위 (내 섹터 정리 섹터만)
const SECMAP = {};
ROWS.forEach((r, i) => { if (r.src === 'U') (SECMAP[r.sec] = SECMAP[r.sec] || {name: r.sec, idx: []}).idx.push(i); });
const SECLIST = Object.values(SECMAP);
SECLIST.forEach((s, id) => {
  s.id = id; s.n = s.idx.length; s.w = (s.name in SECW) ? SECW[s.name] : null;
  s.stat = HZ.map((_, h) => {
    const v = s.idx.map(i => ROWS[i].ex[h]).filter(x => x !== null);
    return {nv: v.length, mean: mean(v), median: median(v), win: v.length ? v.filter(x => x > 0).length / v.length : null};
  });
});
const SM = {metric: 'mean', abs: false, noMisc: false, minN: 1, sortKey: 'rank', sortH: 0, sortDir: 1, open: {}};
let SLIST = [];
function secVal(s, h) {
  const st = s.stat[h];
  if (!st.nv) return null;
  if (SM.metric === 'win') return st.win * 100;
  const b = SM.metric === 'mean' ? st.mean : st.median;
  return SM.abs ? b + HZ[h].kospi : b;
}
function fmtSecVal(v) { return SM.metric === 'win' ? v.toFixed(0) + '%' : sgn(v) + '%'; }
function secValCls(v) { return SM.metric === 'win' ? (v >= 50 ? 'pos' : 'neg') : (v >= 0 ? 'pos' : 'neg'); }
function renderSectors() {
  SM.metric = $('sMetric').value;
  $('sAbs').disabled = SM.metric === 'win';
  SM.abs = $('sAbs').checked && SM.metric !== 'win';
  SM.noMisc = $('sNoMisc').checked;
  SM.minN = Math.max(1, parseInt($('sMinN').value, 10) || 1);
  const list = SECLIST.filter(s => s.n >= SM.minN && !(SM.noMisc && s.name === '개별주'));
  list.forEach(s => { s.v = HZ.map((_, h) => secVal(s, h)); s.rk = new Array(NH).fill(null); });
  const totS = new Array(NH).fill(0);
  for (let h = 0; h < NH; h++) {
    const a = list.filter(s => s.v[h] !== null).sort((x, y) => y.v[h] - x.v[h]);
    totS[h] = a.length;
    a.forEach((s, k) => { s.rk[h] = k + 1; });
  }
  // 요약: 코스피를 이긴 섹터 수 (평균/중앙값 기준 - 이긴 종목 비율 기준일 땐 평균 사용)
  const aggKey = SM.metric === 'median' ? 'median' : 'mean';
  const winSec = HZ.map((_, h) => list.filter(s => s.stat[h].nv && s.stat[h][aggKey] > 0).length);
  $('secSum').innerHTML = '<thead><tr><th class="lbl"></th>' + HZ.map(x => '<th>' + x.label + '</th>').join('') + '</tr></thead><tbody><tr><td class="lbl">코스피를 이긴 섹터 수(' + (aggKey === 'median' ? '중앙값' : '평균') + ' 기준)</td>' +
    HZ.map((_, h) => '<td>' + winSec[h] + ' / ' + totS[h] + '</td>').join('') + '</tr></tbody>';
  $('secNote').textContent = '섹터 ' + list.length + '개(내 섹터 정리 기준) · 기준: ' + {mean: '소속 종목 초과수익률 평균', median: '소속 종목 초과수익률 중앙값', win: '코스피를 이긴 종목 비율'}[SM.metric] + (SM.abs ? ' + 코스피 수익률(절대수익률)' : '') + ' · 섹터 이름을 누르면 소속 종목이 펼쳐지고, 펼친 종목의 순위는 "같은 섹터 안" 순위입니다.';
  // 정렬
  const k = SM.sortKey, hs = SM.sortH, d = SM.sortDir;
  list.sort((a, b) => {
    if (k === 'name') return d * a.name.localeCompare(b.name, 'ko');
    if (k === 'n') return d * (b.n - a.n);
    if (k === 'w') { if (a.w === null && b.w === null) return 0; if (a.w === null) return 1; if (b.w === null) return -1; return d * (b.w - a.w); }
    if (k === 'val') { const va = a.v[hs], vb = b.v[hs]; if (va === null) return 1; if (vb === null) return -1; return d * (vb - va); }
    const ra = a.rk[hs], rb = b.rk[hs];
    if (ra === null) return 1;
    if (rb === null) return -1;
    return d * (ra - rb);
  });
  SLIST = list;
  // 머리글
  const arr = (key, h) => (SM.sortKey === key && (h === undefined || SM.sortH === h)) ? '<span class="arrow">' + ((SM.sortDir === 1) === (key === 'name') ? '▲' : '▼') + '</span>' : '';
  let r1 = '<tr><th class="lbl nm" rowspan="2" data-sort="name:0">섹터' + arr('name') + '</th><th rowspan="2" data-sort="n:0">종목수' + arr('n') + '</th><th rowspan="2" data-sort="w:0" title="섹터 정리에 든 코스피 종목 시가총액 기준, 합 100%">코스피 비중' + arr('w') + '</th>';
  let r2 = '<tr class="r2">';
  HZ.forEach((x, h) => {
    r1 += '<th class="grp" colspan="2">' + x.label + ' <span class="sub">(' + x.n + '일)</span></th>';
    r2 += '<th data-sort="rank:' + h + '">순위' + arr('rank', h) + '</th><th data-sort="val:' + h + '">' + (SM.metric === 'win' ? '이긴 비율' : (SM.abs ? '수익률' : '초과수익률')) + arr('val', h) + '</th>';
  });
  $('sHead').innerHTML = r1 + '</tr>' + r2 + '</tr>';
  // 본문
  const parts = [];
  list.forEach(s => {
    const open = !!SM.open[s.id];
    let tr = '<tr class="srow" data-sid="' + s.id + '"><td class="lbl nm"><span class="tg">' + (open ? '▾' : '▸') + '</span>' + esc(s.name) + '</td><td>' + s.n + '</td><td>' + (s.w === null ? '-' : (s.w * 100).toFixed(1) + '%') + '</td>';
    for (let h = 0; h < NH; h++) {
      const v = s.v[h], r = s.rk[h];
      if (v === null) { tr += '<td class="rk first na">-</td><td class="na">-</td>'; continue; }
      const st = s.stat[h];
      const tip = '평균 ' + sgn(st.mean) + '% · 중앙값 ' + sgn(st.median) + '% · 이긴 종목 ' + Math.round(st.win * st.nv) + '/' + st.nv + ' (' + (st.win * 100).toFixed(0) + '%) · 코스피 대비';
      tr += '<td class="rk first" style="' + heat((r - 0.5) / totS[h]) + '" title="' + totS[h] + '개 섹터 중 ' + r + '위">' + r + '</td><td class="' + secValCls(v) + '" title="' + esc(tip) + '">' + fmtSecVal(v) + '</td>';
    }
    parts.push(tr + '</tr>');
    if (open) {
      const mem = s.idx.slice().sort((a, b) => {
        const ra = rkSec[a][SM.sortH], rb = rkSec[b][SM.sortH];
        if (ra === null && rb === null) return 0;
        if (ra === null) return 1;
        if (rb === null) return -1;
        return ra - rb || ROWS[a].name.localeCompare(ROWS[b].name, 'ko');
      });
      mem.forEach(i => {
        const r = ROWS[i];
        let m = '<tr class="mrow"><td class="lbl nm">' + nameLink(r) + '</td><td class="mk">' + (r.mkt === 'P' ? '코스피' : '코스닥') + '</td><td></td>';
        for (let h = 0; h < NH; h++) {
          const x = r.ex[h], rr = rkSec[i][h];
          if (x === null) { m += '<td class="first na">-</td><td class="na">-</td>'; continue; }
          const val = SM.abs ? x + HZ[h].kospi : x;
          m += '<td class="first" title="섹터 안 ' + nSecBy[secKey(i)][h] + '종목 중 ' + rr + '위">' + rr + '</td><td class="' + (val >= 0 ? 'pos' : 'neg') + '">' + sgn(val) + '%</td>';
        }
        parts.push(m + '</tr>');
      });
      parts.push('<tr class="mrow"><td class="lbl nm"><button class="lnk" data-go="' + s.id + '">이 섹터를 종목별 표에서 보기 &rarr;</button></td><td colspan="' + (2 + 2 * NH) + '"></td></tr>');
    }
  });
  $('sBody').innerHTML = parts.join('');
}

// ---------- 탭 전환 / 이벤트
function refreshCurrent() { if (TAB === 't1') refresh(true); else if (TAB === 't2') renderBoard(); else renderSectors(); }
function setTab(t, keepHash) {
  TAB = t;
  document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === t));
  ['t1', 't2', 't3'].forEach(id => $(id).classList.toggle('on', id === t));
  $('stockBar').style.display = t === 't3' ? 'none' : '';
  document.querySelectorAll('.t1only').forEach(el => { el.style.display = t === 't1' ? '' : 'none'; });
  if (t === 't1') refresh(false); else if (t === 't2') renderBoard(); else renderSectors();
  if (!keepHash) history.replaceState(null, '', location.pathname + location.search + (t === 't2' ? '#board' : (t === 't3' ? '#sector' : '')));
}
$('tabs').addEventListener('click', e => { const b = e.target.closest('button[data-tab]'); if (b) setTab(b.dataset.tab); });
['fSearch', 'fTop'].forEach(id => $(id).addEventListener('input', () => refreshCurrent()));
['fMarket', 'fSector', 'fUserOnly'].forEach(id => $(id).addEventListener('change', () => refreshCurrent()));
$('hzChk').addEventListener('change', () => refresh(true));
$('fScope').addEventListener('change', e => { S.scope = e.target.value; refresh(true); });
$('fShow').addEventListener('change', e => { S.show = e.target.value; refresh(false); });
$('fSort').addEventListener('change', e => {
  if (!e.target.value) return;
  const [key, hs] = e.target.value.split(':');
  S.sortKey = key; S.sortH = parseInt(hs, 10); S.sortDir = 1;
  refresh(true);
});
$('moreBtn').addEventListener('click', () => { S.limit += 300; renderBody(); });
$('thead').addEventListener('click', e => {
  const th = e.target.closest('th[data-sort]');
  if (!th) return;
  const [key, hs] = th.dataset.sort.split(':'), h = parseInt(hs, 10);
  if (S.sortKey === key && (S.sortH === h || key === 'name' || key === 'sec' || key === 'mkt')) S.sortDir *= -1;
  else { S.sortKey = key; S.sortH = h; S.sortDir = 1; }
  refresh(true);
});
$('resetBtn').addEventListener('click', () => {
  $('fSearch').value = ''; $('fMarket').value = ''; $('fSector').value = ''; $('fUserOnly').checked = false; $('fTop').value = '';
  HZ.forEach((_, h) => { $('tk' + h).checked = true; });
  refresh(true);
});
function downloadCsv(name, lines) {
  const blob = new Blob(['﻿' + lines.join('\n')], {type: 'text/csv;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}
const q2 = s => '"' + String(s).replace(/"/g, '""') + '"';
$('csvBtn').addEventListener('click', () => {
  const head = ['종목명', '종목코드', '시장', '섹터', '섹터출처'];
  HZ.forEach(x => { head.push(x.label + ' 순위', x.label + ' 초과수익률(%p)'); });
  const lines = [head.join(',')];
  CUR.forEach(i => {
    const r = ROWS[i];
    const row = [q2(r.name), r.code, r.mkt === 'P' ? '코스피' : '코스닥', q2(r.sec), r.src === 'U' ? '내 섹터 정리' : (r.src === 'N' ? '네이버' : '')];
    HZ.forEach((_, h) => { row.push(rk(i, h) === null ? '' : rk(i, h), r.ex[h] === null ? '' : r.ex[h].toFixed(2)); });
    lines.push(row.join(','));
  });
  downloadCsv('종목_상대강도순위_' + DATA.asof + '.csv', lines);
});
['bDir', 'bN', 'bSec'].forEach(id => $(id).addEventListener('change', renderBoard));
$('bCsv').addEventListener('click', () => {
  const head = ['순위'];
  HZ.forEach(x => { head.push(x.label + ' 종목', x.label + ' 초과수익률(%p)'); });
  const lines = [head.join(',')];
  const nRows = Math.max(0, ...BOARD_LISTS.map(l => l.length));
  for (let k = 0; k < nRows; k++) {
    const row = [k + 1];
    HZ.forEach((_, h) => { const i = BOARD_LISTS[h][k]; row.push(i === undefined ? '' : q2(ROWS[i].name), i === undefined ? '' : ROWS[i].ex[h].toFixed(2)); });
    lines.push(row.join(','));
  }
  downloadCsv('기간별_순위표_' + $('bDir').value + '_' + DATA.asof + '.csv', lines);
});
$('sCsv').addEventListener('click', () => {
  const head = ['섹터', '종목수', '코스피비중(%)'];
  HZ.forEach(x => { head.push(x.label + ' 평균초과(%p)', x.label + ' 중앙값초과(%p)', x.label + ' 이긴비율(%)', x.label + ' 순위(현재기준)'); });
  const lines = [head.join(',')];
  SLIST.forEach(s => {
    const row = [q2(s.name), s.n, s.w === null ? '' : (s.w * 100).toFixed(2)];
    HZ.forEach((_, h) => { const st = s.stat[h]; row.push(st.nv ? st.mean.toFixed(2) : '', st.nv ? st.median.toFixed(2) : '', st.nv ? (st.win * 100).toFixed(1) : '', s.rk[h] === null ? '' : s.rk[h]); });
    lines.push(row.join(','));
  });
  downloadCsv('섹터_상대강도순위_' + SM.metric + '_' + DATA.asof + '.csv', lines);
});
['sMetric', 'sAbs', 'sNoMisc', 'sMinN'].forEach(id => $(id).addEventListener('change', renderSectors));
$('sMinN').addEventListener('input', renderSectors);
$('sExpandAll').addEventListener('click', () => { SECLIST.forEach(s => { SM.open[s.id] = true; }); renderSectors(); });
$('sCollapseAll').addEventListener('click', () => { SM.open = {}; renderSectors(); });
$('sHead').addEventListener('click', e => {
  const th = e.target.closest('th[data-sort]');
  if (!th) return;
  const [key, hs] = th.dataset.sort.split(':'), h = parseInt(hs, 10);
  if (SM.sortKey === key && (SM.sortH === h || key === 'name' || key === 'n' || key === 'w')) SM.sortDir *= -1;
  else { SM.sortKey = key; SM.sortH = h; SM.sortDir = 1; }
  renderSectors();
});
$('sBody').addEventListener('click', e => {
  if (e.target.closest('a')) return;
  const go = e.target.closest('button[data-go]');
  if (go) {
    const s = SECLIST[parseInt(go.dataset.go, 10)];
    $('fMarket').value = ''; $('fUserOnly').checked = false; $('fSector').value = 'U:' + s.name; $('fTop').value = '';
    S.scope = 'sec'; $('fScope').value = 'sec';
    S.sortKey = 'rank'; S.sortH = SM.sortH; S.sortDir = 1;
    setTab('t1');
    return;
  }
  const tr = e.target.closest('tr.srow');
  if (tr) { const id = parseInt(tr.dataset.sid, 10); SM.open[id] = !SM.open[id]; renderSectors(); }
});

setTab(location.hash === '#sector' ? 't3' : (location.hash === '#board' ? 't2' : 't1'), true);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
