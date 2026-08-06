"""
data/ecos_raw.csv, data/krx_raw.csv, data/kofia_raw.csv 를 날짜 기준으로 병합해
data/merged.csv 를 만들고, Chart.js 기반 정적 HTML 대시보드를 docs/index.html 로 생성한다
(GitHub Pages가 /docs 폴더를 소스로 쓰는 관례에 맞춤).

패널 구성:
  1. 수급주체            - 투자자별(개인/외국인/기관/기타법인) 순매매대금
  2. 유동성지표          - 한국은행 총자산 / 통안증권잔액 / RP매각잔고 / M2 (각각 별도 차트)
  3. 실탄게이지           - 투자자예탁금 + CMA잔고 (KOFIA) [계산식 표시]
  4. MMF                 - M2 구성항목 중 MMF
  5. 예수금·신용거래 현황 - 파생상품거래예수금/RP매도잔고/미수금/반대매매 (KOFIA)
  6. M2 통화공급(한국·미국) - 한국 ECOS M2 vs 미국 FRED M2SL
  7. 코스피 주가추이      - 코스피 종가/거래대금 (네이버 금융)

ECOS는 월별이라 일별 그래프에서 자연스럽게 이어지도록 forward-fill해서
merged.csv에 daily 인덱스로 저장한다 (원본 raw csv들은 그대로 둠).
대시보드에는 최근 구간(RECENT_WINDOW_DAYS)만 기본으로 보여준다 - 전체 기간을
다 그리면 최근 몇 달이 26년치 축에 눌려서 안 보이기 때문.
"""
import json
import os
from datetime import datetime

import pandas as pd

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(BASE_DIR, "data")
DIST_DIR = os.path.join(BASE_DIR, "docs")  # GitHub Pages 관례상 /docs 폴더를 소스로 씀

ECOS_PATH = os.path.join(DATA_DIR, "ecos_raw.csv")
KRX_PATH = os.path.join(DATA_DIR, "krx_raw.csv")
KOFIA_PATH = os.path.join(DATA_DIR, "kofia_raw.csv")
FRED_PATH = os.path.join(DATA_DIR, "fred_raw.csv")
BITCOIN_PATH = os.path.join(DATA_DIR, "bitcoin_raw.csv")
MERGED_PATH = os.path.join(DATA_DIR, "merged.csv")
DASHBOARD_PATH = os.path.join(DIST_DIR, "index.html")

RECENT_WINDOW_DAYS = 1825  # 대시보드 기본 표시 구간 (~5년)


def load_csv(path):
    if os.path.exists(path):
        return pd.read_csv(path, parse_dates=["date"])
    return pd.DataFrame(columns=["date"])


def build_merged():
    ecos = load_csv(ECOS_PATH)
    krx = load_csv(KRX_PATH)
    kofia = load_csv(KOFIA_PATH)
    fred = load_csv(FRED_PATH)
    bitcoin = load_csv(BITCOIN_PATH)

    # M2 YoY(전년동월대비 %)는 각자 월별 원본에서 12개월 shift로 계산한다
    # (일별로 ffill된 시계열에서 12"일" shift하면 틀리므로 ffill 전에 계산).
    if not ecos.empty and "m2" in ecos.columns:
        ecos = ecos.sort_values("date").reset_index(drop=True)
        ecos["korea_m2_yoy"] = ecos["m2"] / ecos["m2"].shift(12) * 100 - 100
    if not fred.empty and "us_m2" in fred.columns:
        fred = fred.sort_values("date").reset_index(drop=True)
        fred["us_m2_yoy"] = fred["us_m2"] / fred["us_m2"].shift(12) * 100 - 100

    all_dates = pd.concat([ecos["date"], krx["date"], kofia["date"], fred["date"], bitcoin["date"]], ignore_index=True).dropna()
    if all_dates.empty:
        raise SystemExit("병합할 데이터가 없습니다. fetch_*.py 스크립트를 먼저 실행하세요.")

    # ffill 전, 컬럼별 실제 마지막 관측일을 따로 기록해둔다 (대시보드의 "최신" 배지가
    # ffill로 늘어난 날짜가 아니라 진짜 관측된 마지막 날짜를 보여주도록).
    raw_latest = {}
    for df in (ecos, krx, kofia, fred, bitcoin):
        if df.empty:
            continue
        for col in df.columns:
            if col == "date":
                continue
            sub = df.dropna(subset=[col])
            if not sub.empty:
                raw_latest[col] = sub["date"].max()

    full_range = pd.date_range(all_dates.min(), all_dates.max(), freq="D")
    merged = pd.DataFrame({"date": full_range})

    for df in (ecos, krx, kofia, fred, bitcoin):
        if df.empty or len(df.columns) <= 1:
            continue
        df = df.drop_duplicates(subset="date").sort_values("date")
        df = df.set_index("date").reindex(full_range).ffill().reset_index()
        df = df.rename(columns={"index": "date"})
        merged = merged.merge(df, on="date", how="left")

    if {"investor_deposit", "cma_balance"}.issubset(merged.columns):
        merged["dry_powder"] = merged["investor_deposit"] + merged["cma_balance"]
        raw_latest["dry_powder"] = min(raw_latest.get("investor_deposit", pd.Timestamp.min),
                                        raw_latest.get("cma_balance", pd.Timestamp.min))

    if {"investor_deposit", "broker_rp_balance"}.issubset(merged.columns):
        merged["deposit_minus_rp"] = merged["investor_deposit"] - merged["broker_rp_balance"]
        if "investor_deposit" in raw_latest and "broker_rp_balance" in raw_latest:
            raw_latest["deposit_minus_rp"] = min(raw_latest["investor_deposit"], raw_latest["broker_rp_balance"])

    if {"kospi_trading_value", "kospi_market_cap"}.issubset(merged.columns):
        # kospi_trading_value 단위는 백만원(네이버), kospi_market_cap 단위는 원(KRX Open API) - 단위 맞춰서 계산
        merged["kospi_turnover_ratio"] = (merged["kospi_trading_value"] * 1_000_000) / merged["kospi_market_cap"] * 100
        if "kospi_trading_value" in raw_latest and "kospi_market_cap" in raw_latest:
            raw_latest["kospi_turnover_ratio"] = min(raw_latest["kospi_trading_value"], raw_latest["kospi_market_cap"])

    if {"m2", "kospi_market_cap"}.issubset(merged.columns):
        # m2 단위는 십억원(ECOS), kospi_market_cap 단위는 원(KRX Open API)
        merged["m2_to_marketcap_ratio"] = (merged["m2"] * 1_000_000_000) / merged["kospi_market_cap"] * 100
        if "m2" in raw_latest and "kospi_market_cap" in raw_latest:
            raw_latest["m2_to_marketcap_ratio"] = min(raw_latest["m2"], raw_latest["kospi_market_cap"])

    os.makedirs(DATA_DIR, exist_ok=True)
    merged.to_csv(MERGED_PATH, index=False, encoding="utf-8-sig")
    print(f"병합 완료: {MERGED_PATH} ({len(merged)}행, {merged['date'].min().date()} ~ {merged['date'].max().date()})")
    return merged, raw_latest


def col_or_none(df, col):
    return df[col].where(pd.notna(df[col]), None).tolist() if col in df.columns else [None] * len(df)


def has_data(df, col):
    return bool(col in df.columns and df[col].notna().any())


def latest_date_str(raw_latest, cols):
    dates = [raw_latest[c] for c in cols if c in raw_latest]
    if not dates:
        return None
    return max(dates).strftime("%Y-%m-%d")


PANEL_SOURCES = {
    "수급주체": "KOFIA FreeSIS 증시자금추이 (투자자별 순매매대금, 수동 다운로드 병합)",
    "유동성지표": "한국은행 ECOS Open API - 한국은행 주요계정(103Y002), M2(161Y008)",
    "실탄게이지": "KOFIA FreeSIS Open API(자동 수집) 기반 계산",
    "MMF": "한국은행 ECOS Open API - M2 구성항목(161Y008/BBGA04)",
    "예수금·신용거래 현황": "KOFIA FreeSIS (meta/getMetaDataList.do, 자동 수집)",
    "신용거래융자 · MMF 현황": "KOFIA FreeSIS (meta/getMetaDataList.do, 자동 수집)",
    "M2 통화공급 (한국·미국)": "한국: ECOS Open API(161Y008) / 미국: FRED M2SL (둘 다 월별, 단위 다름 - 각각 자체 축)",
    "코스피 주가추이": "네이버 금융 일별시세 (finance.naver.com)",
    "시가총액 회전율 · M2 비율": "코스피 거래대금: 네이버 금융 / 코스피 시가총액: KRX Open API(stk_bydd_trd, MKTCAP 합산) / M2: ECOS",
    "신용카드 대출수요 vs 코스피": "코스피: 네이버 금융 / 대출수요: 한국은행 ECOS 대출행태서베이(514Y003, 신용카드회사, 분기)",
    "비트코인 시가총액 vs 코스피": "비트코인: CoinGecko (무료 API, 최근 365일만 제공) / 코스피: 네이버 금융",
    "투자자예탁금 - RP매도잔고": "KOFIA FreeSIS Open API(자동 수집) 기반 계산",
}

PANEL_FORMULAS = {
    "실탄게이지": "계산식: 실탄(dry powder) = 투자자예탁금 + CMA잔고",
    "투자자예탁금 - RP매도잔고": "계산식: 투자자예탁금 - 대고객 RP매도잔고",
    "시가총액 회전율 · M2 비율": "계산식: 회전율(%) = 코스피 거래대금 / 코스피 상장시가총액 × 100  |  M2/시가총액(%) = 한국 M2 / 코스피 상장시가총액 × 100",
    "M2 통화공급 (한국·미국)": "계산식: M2 YoY(%) = (M2(t) / M2(t-12개월) - 1) × 100",
}


def trim_to_data(df, cols):
    """cols 중 하나라도 값이 있는 첫 날짜 ~ 마지막 날짜로 잘라낸다 (앞뒤 빈 구간 제거)."""
    cols_present = [c for c in cols if c in df.columns]
    if not cols_present:
        return df.iloc[0:0]
    mask = df[cols_present].notna().any(axis=1)
    if not mask.any():
        return df.iloc[0:0]
    idx = mask[mask].index
    return df.loc[idx.min():idx.max()]


def trim_to_common_data(df, cols):
    """cols가 전부(교집합) 값을 갖는 구간으로 잘라낸다 - 두 지표를 겹쳐볼 때
    한쪽 데이터가 짧으면(예: 비트코인 1년치) 그 시작 시점부터만 비교하도록."""
    cols_present = [c for c in cols if c in df.columns]
    if len(cols_present) < len(cols):
        return df.iloc[0:0]
    mask = df[cols_present].notna().all(axis=1)
    if not mask.any():
        return df.iloc[0:0]
    idx = mask[mask].index
    return df.loc[idx.min():idx.max()]


def build_panel(merged, window_df, panel_type, series_map):
    cols = list(series_map.values())
    trimmed = trim_to_data(window_df, cols)
    dates = trimmed["date"].dt.strftime("%Y-%m-%d").tolist()
    return {
        "type": panel_type,
        "labels": dates,
        "series": {name: col_or_none(trimmed, col) for name, col in series_map.items()},
        "has_data": any(has_data(trimmed, c) for c in cols),
    }


def build_dual_panel(merged, window_df, left_map, right_map):
    """왼쪽/오른쪽 축이 다른 두 지표를 한 차트에 겹쳐 그린다 (예: 코스피 vs 분기 지수).
    두 지표가 모두 존재하는 교집합 구간만 보여준다 (한쪽이 짧으면 그 시작점부터)."""
    cols = list(left_map.values()) + list(right_map.values())
    trimmed = trim_to_common_data(window_df, cols)
    dates = trimmed["date"].dt.strftime("%Y-%m-%d").tolist()
    return {
        "type": "dual",
        "labels": dates,
        "left": {name: col_or_none(trimmed, col) for name, col in left_map.items()},
        "right": {name: col_or_none(trimmed, col) for name, col in right_map.items()},
        "has_data": any(has_data(trimmed, c) for c in cols),
    }


MASTER_SERIES = [
    ("코스피 종가", "kospi_close", True),
    ("코스피 거래대금(백만원)", "kospi_trading_value", False),
    ("코스피 회전율(%)", "kospi_turnover_ratio", False),
    ("M2/코스피 시가총액(%)", "m2_to_marketcap_ratio", False),
    ("한국 M2(십억원)", "m2", False),
    ("한국 M2 YoY(%)", "korea_m2_yoy", False),
    ("미국 M2(십억달러)", "us_m2", False),
    ("미국 M2 YoY(%)", "us_m2_yoy", False),
    ("한국은행 총자산(십억원)", "bok_total_assets", False),
    ("통안증권잔액(십억원)", "msb_balance", False),
    ("RP매각잔고(십억원)", "rp_sale_balance", False),
    ("MMF(십억원)", "mmf", False),
    ("실탄 합계(예탁금+CMA)", "dry_powder", False),
    ("투자자예탁금", "investor_deposit", False),
    ("CMA잔고", "cma_balance", False),
    ("장내파생상품 거래예수금", "deriv_deposit", False),
    ("대고객 RP매도잔고", "broker_rp_balance", False),
    ("위탁매매 미수금", "margin_call_unpaid", False),
    ("반대매매금액", "margin_call_liquidation", False),
    ("반대매매비중(%)", "margin_liquidation_ratio", False),
    ("개인 순매매대금", "indiv_net_value", False),
    ("외국인 순매매대금", "foreign_net_value", False),
    ("기관 순매매대금", "inst_net_value", False),
    ("기타법인 순매매대금", "other_corp_net_value", False),
    ("신용카드 대출수요(BSI)", "credit_card_loan_demand", False),
    ("비트코인 시가총액(USD)", "btc_market_cap_usd", False),
    ("투자자예탁금 - RP매도잔고", "deposit_minus_rp", False),
    ("신용거래융자(코스피)", "credit_loan_kospi", False),
    ("신용거래융자(코스닥)", "credit_loan_kosdaq", False),
    ("MMF 개인", "mmf_indiv", False),
    ("MMF 법인", "mmf_corp", False),
]


def build_combined(merged, window_df):
    items = []
    for label, col, default_visible in MASTER_SERIES:
        if col not in window_df.columns or not has_data(window_df, col):
            continue
        items.append({
            "label": label,
            "column": col,
            "data": col_or_none(window_df, col),
            "default_visible": default_visible,
        })
    dates = window_df["date"].dt.strftime("%Y-%m-%d").tolist()
    return {"labels": dates, "items": items}


def build_dashboard(merged, raw_latest):
    cutoff = merged["date"].max() - pd.Timedelta(RECENT_WINDOW_DAYS, unit="D")
    recent = merged[merged["date"] >= cutoff].reset_index(drop=True)

    combined = build_combined(merged, recent)

    panels = {
        "수급주체": build_panel(merged, recent, "multi", {
            "개인": "indiv_net_value", "외국인": "foreign_net_value",
            "기관": "inst_net_value", "기타법인": "other_corp_net_value",
        }),
        "유동성지표": build_panel(merged, recent, "split", {
            "한국은행 총자산": "bok_total_assets", "통안증권잔액": "msb_balance",
            "RP매각잔고": "rp_sale_balance", "M2": "m2",
        }),
        "실탄게이지": build_panel(merged, recent, "split", {
            "실탄 합계(예탁금+CMA)": "dry_powder", "투자자예탁금": "investor_deposit", "CMA잔고": "cma_balance",
        }),
        "MMF": build_panel(merged, recent, "multi", {"MMF": "mmf"}),
        "예수금·신용거래 현황": build_panel(merged, recent, "split", {
            "장내파생상품 거래예수금": "deriv_deposit",
            "대고객 RP매도잔고": "broker_rp_balance",
            "위탁매매 미수금": "margin_call_unpaid",
            "반대매매금액": "margin_call_liquidation",
            "반대매매비중(%)": "margin_liquidation_ratio",
        }),
        "M2 통화공급 (한국·미국)": build_panel(merged, recent, "split", {
            "한국 M2 (십억원)": "m2", "미국 M2 (십억달러, M2SL)": "us_m2",
            "한국 M2 YoY(%)": "korea_m2_yoy", "미국 M2 YoY(%)": "us_m2_yoy",
        }),
        "신용거래융자 · MMF 현황": build_panel(merged, recent, "split", {
            "신용거래융자(코스피)": "credit_loan_kospi", "신용거래융자(코스닥)": "credit_loan_kosdaq",
            "MMF 개인": "mmf_indiv", "MMF 법인": "mmf_corp",
        }),
        "코스피 주가추이": build_panel(merged, recent, "split", {
            "코스피 종가": "kospi_close", "코스피 거래대금(백만원)": "kospi_trading_value",
        }),
        "시가총액 회전율 · M2 비율": build_panel(merged, recent, "split", {
            "코스피 회전율(%)": "kospi_turnover_ratio", "M2/코스피 시가총액(%)": "m2_to_marketcap_ratio",
        }),
        "신용카드 대출수요 vs 코스피": build_dual_panel(
            merged, recent,
            {"코스피 종가": "kospi_close"},
            {"신용카드 대출수요(BSI)": "credit_card_loan_demand"},
        ),
        "비트코인 시가총액 vs 코스피": build_dual_panel(
            merged, recent,
            {"코스피 종가": "kospi_close"},
            {"비트코인 시가총액(USD)": "btc_market_cap_usd"},
        ),
        "투자자예탁금 - RP매도잔고": build_panel(merged, recent, "multi", {
            "투자자예탁금 - RP매도잔고": "deposit_minus_rp",
        }),
    }

    panels["수급주체"]["latest"] = latest_date_str(raw_latest, ["indiv_net_value", "foreign_net_value", "inst_net_value", "other_corp_net_value"])
    panels["유동성지표"]["latest"] = latest_date_str(raw_latest, ["bok_total_assets", "m2"])
    panels["실탄게이지"]["latest"] = latest_date_str(raw_latest, ["investor_deposit", "cma_balance"])
    panels["M2 통화공급 (한국·미국)"]["latest"] = latest_date_str(raw_latest, ["m2", "us_m2"])
    panels["코스피 주가추이"]["latest"] = latest_date_str(raw_latest, ["kospi_close", "kospi_trading_value"])
    panels["MMF"]["latest"] = latest_date_str(raw_latest, ["mmf"])
    panels["예수금·신용거래 현황"]["latest"] = latest_date_str(raw_latest, [
        "deriv_deposit", "broker_rp_balance", "margin_call_unpaid", "margin_call_liquidation", "margin_liquidation_ratio",
    ])
    panels["시가총액 회전율 · M2 비율"]["latest"] = latest_date_str(raw_latest, ["kospi_turnover_ratio", "m2_to_marketcap_ratio"])
    panels["신용거래융자 · MMF 현황"]["latest"] = latest_date_str(raw_latest, ["credit_loan_kospi", "credit_loan_kosdaq", "mmf_indiv", "mmf_corp"])
    panels["신용카드 대출수요 vs 코스피"]["latest"] = latest_date_str(raw_latest, ["kospi_close", "credit_card_loan_demand"])
    panels["비트코인 시가총액 vs 코스피"]["latest"] = latest_date_str(raw_latest, ["kospi_close", "btc_market_cap_usd"])
    panels["투자자예탁금 - RP매도잔고"]["latest"] = latest_date_str(raw_latest, ["deposit_minus_rp"])

    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    panels_json = json.dumps(panels, ensure_ascii=False)
    sources_json = json.dumps(PANEL_SOURCES, ensure_ascii=False)
    formulas_json = json.dumps(PANEL_FORMULAS, ensure_ascii=False)
    combined_json = json.dumps(combined, ensure_ascii=False)

    html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>한국 증시 유동성 스크리닝 대시보드</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  h1 {{ font-size:20px; margin-bottom:4px; }}
  .updated {{ color:#9aa0a6; font-size:13px; margin-bottom:20px; }}
  .grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(440px, 1fr)); gap:20px; }}
  .card {{ background:#1a1d24; border-radius:12px; padding:16px; }}
  .card.wide {{ grid-column: 1 / -1; }}
  .card h2 {{ font-size:15px; margin:0 0 4px 0; display:inline-block; }}
  .latest {{ color:#63e6be; font-size:11px; float:right; }}
  .source {{ color:#7a8290; font-size:11px; margin-bottom:4px; clear:both; }}
  .formula {{ color:#ffa94d; font-size:11px; margin-bottom:10px; font-family:monospace; }}
  .nodata {{ color:#7a8290; font-size:13px; padding:40px 0; text-align:center; }}
  canvas {{ max-height:420px; }}
  .split-grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap:20px; }}
  .split-item h3 {{ font-size:13px; color:#c7cbd1; margin:6px 0 6px 0; font-weight:normal; }}
  .split-item canvas {{ max-height:320px; }}
  .combined-canvas {{ max-height:560px; }}
  .combined-hint {{ color:#7a8290; font-size:11px; margin-bottom:10px; }}
  .layout {{ display:flex; align-items:flex-start; gap:20px; }}
  .sidebar {{ width:220px; flex-shrink:0; position:sticky; top:24px; max-height:calc(100vh - 48px); overflow-y:auto; }}
  .nav-btn {{ display:block; width:100%; text-align:left; background:none; border:none; color:#9aa0a6;
    padding:10px 12px; border-radius:8px; cursor:pointer; font-size:13px; margin-bottom:2px; font-family:inherit; }}
  .nav-btn:hover {{ background:#1a1d24; color:#c7cbd1; }}
  .nav-btn.active {{ background:#1a1d24; color:#4dabf7; font-weight:bold; }}
  .content {{ flex:1; min-width:0; }}
  .section {{ display:none; }}
  .section.active {{ display:block; }}
  .range-bar {{ display:flex; gap:6px; margin-bottom:16px; flex-wrap:wrap; }}
  .range-btn {{ background:#1a1d24; border:1px solid #2a2e37; color:#9aa0a6; padding:6px 14px;
    border-radius:999px; cursor:pointer; font-size:12px; font-family:inherit; }}
  .range-btn:hover {{ color:#c7cbd1; border-color:#4dabf7; }}
  .range-btn.active {{ background:#4dabf7; color:#0f1115; border-color:#4dabf7; font-weight:bold; }}
</style>
</head>
<body>
  <h1>한국 증시 유동성 스크리닝 대시보드</h1>
  <div class="updated">최종 갱신일시: {updated_at} (화면은 최근 약 {RECENT_WINDOW_DAYS}일만 표시, 아래 버튼으로 구간 조절 가능)</div>

  <div class="layout">
    <nav class="sidebar" id="sidebar"></nav>
    <main class="content">
      <div class="range-bar" id="rangeBar"></div>
      <div id="sectionContainer"></div>
    </main>
  </div>

<script>
const PANELS = {panels_json};
const SOURCES = {sources_json};
const FORMULAS = {formulas_json};
const COMBINED = {combined_json};
const COLORS = ["#4dabf7","#f783ac","#69db7c","#ffa94d","#b197fc","#63e6be","#ff8787","#66d9e8","#eebefa","#a9e34b","#ffd43b","#e599f7"];

const content = document.getElementById('sectionContainer');
const sidebar = document.getElementById('sidebar');
const rangeBar = document.getElementById('rangeBar');
const sections = [];
const activeCharts = [];
let currentRangeDays = null;

const RANGE_OPTIONS = [
  {{ label: '1개월', days: 30 }},
  {{ label: '3개월', days: 90 }},
  {{ label: '6개월', days: 180 }},
  {{ label: '1년', days: 365 }},
  {{ label: '3년', days: 1095 }},
  {{ label: '전체', days: null }},
];

function registerChart(chart) {{
  const fullLabels = chart.data.labels.slice();
  chart.data.datasets.forEach(ds => {{ ds._fullData = ds.data.slice(); }});
  const entry = {{ chart, fullLabels }};
  activeCharts.push(entry);
  applyRangeToEntry(entry, currentRangeDays);
}}

function lastNonNull(arr) {{
  for (let i = arr.length - 1; i >= 0; i--) {{
    if (arr[i] !== null && arr[i] !== undefined) return arr[i];
  }}
  return null;
}}

function formatLatestSuffix(v) {{
  if (v === null || v === undefined) return '';
  const formatted = Number(v).toLocaleString(undefined, {{ maximumFractionDigits: 1 }});
  return ' : ' + formatted;
}}

function applyRangeToEntry(entry, days) {{
  const {{ chart, fullLabels }} = entry;
  let startIdx = 0;
  if (days !== null && fullLabels.length) {{
    const cutoff = new Date(fullLabels[fullLabels.length - 1]);
    cutoff.setDate(cutoff.getDate() - days);
    const found = fullLabels.findIndex(d => new Date(d) >= cutoff);
    startIdx = found < 0 ? 0 : found;
  }}
  chart.data.labels = fullLabels.slice(startIdx);
  chart.data.datasets.forEach(ds => {{ ds.data = ds._fullData.slice(startIdx); }});
  chart.update();
}}

function applyRange(days) {{
  currentRangeDays = days;
  activeCharts.forEach(entry => applyRangeToEntry(entry, days));
  document.querySelectorAll('.range-btn').forEach(btn => {{
    btn.classList.toggle('active', btn.dataset.days === String(days));
  }});
}}

RANGE_OPTIONS.forEach(opt => {{
  const btn = document.createElement('button');
  btn.className = 'range-btn';
  btn.textContent = opt.label;
  btn.dataset.days = String(opt.days);
  btn.onclick = () => applyRange(opt.days);
  rangeBar.appendChild(btn);
}});
applyRange(null);

function addSection(id, title, buildFn) {{
  const section = document.createElement('div');
  section.className = 'section';
  section.id = 'section-' + id;
  content.appendChild(section);
  // 차트는 탭이 열릴 때 처음 한 번만 그린다 (숨겨진 상태에서 그리면 Chart.js가
  // 캔버스 크기를 0으로 잡아버려서 나중에 탭을 열어도 안 보이는 문제가 있음).
  sections.push({{ id, title, section, buildFn, built: false }});
}}

function activateSection(id) {{
  const target = sections.find(s => s.id === id);
  if (target && !target.built) {{
    target.buildFn(target.section);
    target.built = true;
  }}
  document.querySelectorAll('.section').forEach(el => el.classList.toggle('active', el.id === 'section-' + id));
  document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.id === id));
}}

function renderCombined(section) {{
  section.innerHTML = `<div class="card wide">
    <h2>통합 차트 (전체 지표 겹쳐보기)</h2>
    <div class="source">모든 지표를 한 차트에 모았습니다. 아래 범례를 클릭하면 해당 지표를 켜고 끌 수 있습니다.</div>
    <div class="combined-hint">지표마다 단위가 달라 각자 숨겨진 축(스케일)을 따로 씁니다 - 절대값보다는 시점/추세 비교용입니다.</div>
    <canvas id="combinedChart" class="combined-canvas"></canvas>
  </div>`;
  if (!COMBINED.items.length) return;
  const datasets = COMBINED.items.map((item, i) => ({{
    label: item.label,
    data: item.data,
    yAxisID: 'axis_' + i,
    borderColor: COLORS[i % COLORS.length],
    backgroundColor: COLORS[i % COLORS.length] + '33',
    spanGaps: true,
    pointRadius: 0,
    borderWidth: 1.5,
    hidden: !item.default_visible,
  }}));
  const scales = {{
    x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 10 }}, grid: {{ color:'#2a2e37' }} }},
  }};
  COMBINED.items.forEach((item, i) => {{
    scales['axis_' + i] = {{ display: false }};
  }});
  const chart = new Chart(document.getElementById('combinedChart'), {{
    type: 'line',
    data: {{ labels: COMBINED.labels, datasets }},
    options: {{
      responsive: true,
      interaction: {{ mode: 'index', intersect: false }},
      scales,
      plugins: {{
        legend: {{
          position: 'bottom',
          labels: {{
            color:'#e6e6e6', boxWidth: 12, font: {{ size: 11 }},
            generateLabels: (c) => c.data.datasets.map((ds, i) => {{
              const v = lastNonNull(ds.data);
              return {{
                text: ds.label + formatLatestSuffix(v),
                fillStyle: v !== null && v < 0 ? '#ff6b6b' : ds.borderColor,
                strokeStyle: ds.borderColor,
                hidden: !c.isDatasetVisible(i),
                datasetIndex: i,
                lineWidth: 0,
              }};
            }}),
          }},
        }},
      }},
    }},
  }});
  registerChart(chart);
}}

function makeLineChart(canvas, labels, datasets) {{
  const chart = new Chart(canvas, {{
    type: 'line',
    data: {{ labels, datasets }},
    options: {{
      responsive: true,
      interaction: {{ mode: 'index', intersect: false }},
      scales: {{
        x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 6 }}, grid: {{ color:'#2a2e37' }} }},
        y: {{ ticks: {{ color: '#9aa0a6' }}, grid: {{ color:'#2a2e37' }} }},
      }},
      plugins: {{ legend: {{ labels: {{ color:'#e6e6e6', boxWidth: 12, font: {{ size: 11 }} }} }} }},
    }},
  }});
  registerChart(chart);
}}

function makeDualAxisChart(canvas, labels, leftSeries, rightSeries) {{
  const datasets = [
    ...Object.entries(leftSeries).map(([name, data], i) => ({{
      label: name, data, yAxisID: 'y', borderColor: COLORS[i % COLORS.length],
      backgroundColor: COLORS[i % COLORS.length] + '33',
      spanGaps: true, pointRadius: 0, borderWidth: 1.5,
    }})),
    ...Object.entries(rightSeries).map(([name, data], i) => ({{
      label: name, data, yAxisID: 'y1', borderColor: COLORS[(i + 3) % COLORS.length],
      backgroundColor: COLORS[(i + 3) % COLORS.length] + '33',
      spanGaps: true, pointRadius: 0, borderWidth: 1.5, borderDash: [5, 3],
    }})),
  ];
  const chart = new Chart(canvas, {{
    type: 'line',
    data: {{ labels, datasets }},
    options: {{
      responsive: true,
      interaction: {{ mode: 'index', intersect: false }},
      scales: {{
        x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 6 }}, grid: {{ color:'#2a2e37' }} }},
        y: {{ position: 'left', ticks: {{ color: '#9aa0a6' }}, grid: {{ color:'#2a2e37' }} }},
        y1: {{ position: 'right', ticks: {{ color: '#9aa0a6' }}, grid: {{ drawOnChartArea: false }} }},
      }},
      plugins: {{ legend: {{ labels: {{ color:'#e6e6e6', boxWidth: 12, font: {{ size: 11 }} }} }} }},
    }},
  }});
  registerChart(chart);
}}

function renderPanel(title, panel, section) {{
  const card = document.createElement('div');
  card.className = 'card wide';
  const source = SOURCES[title] || '';
  const formula = FORMULAS[title];
  const latest = panel.latest ? `<span class="latest">최신: ${{panel.latest}}</span>` : '';
  card.innerHTML = `<h2>${{title}}</h2>${{latest}}<div class="source">데이터 출처: ${{source}}</div>` +
                    (formula ? `<div class="formula">${{formula}}</div>` : '');
  section.appendChild(card);

  if (!panel.has_data) {{
    const div = document.createElement('div');
    div.className = 'nodata';
    div.textContent = '아직 수집된 데이터가 없습니다.';
    card.appendChild(div);
    return;
  }}

  if (panel.type === 'dual') {{
    const canvas = document.createElement('canvas');
    card.appendChild(canvas);
    makeDualAxisChart(canvas, panel.labels, panel.left, panel.right);
    return;
  }}

  if (panel.type === 'split') {{
    const splitGrid = document.createElement('div');
    splitGrid.className = 'split-grid';
    Object.entries(panel.series).forEach(([name, data], i) => {{
      const item = document.createElement('div');
      item.className = 'split-item';
      item.innerHTML = `<h3>${{name}}</h3>`;
      const canvas = document.createElement('canvas');
      item.appendChild(canvas);
      splitGrid.appendChild(item);
      makeLineChart(canvas, panel.labels, [{{
        label: name, data, borderColor: COLORS[i % COLORS.length],
        backgroundColor: COLORS[i % COLORS.length] + '33',
        spanGaps: true, pointRadius: 0, borderWidth: 1.5,
      }}]);
    }});
    card.appendChild(splitGrid);
    return;
  }}

  const canvas = document.createElement('canvas');
  card.appendChild(canvas);
  const datasets = Object.entries(panel.series).map(([name, data], i) => ({{
    label: name, data, borderColor: COLORS[i % COLORS.length],
    backgroundColor: COLORS[i % COLORS.length] + '33',
    spanGaps: true, pointRadius: 0, borderWidth: 1.5,
  }}));
  makeLineChart(canvas, panel.labels, datasets);
}}

addSection('combined', '통합 차트', renderCombined);
Object.entries(PANELS).forEach(([title, panel], i) => {{
  addSection('panel' + i, title, (section) => renderPanel(title, panel, section));
}});

sections.forEach(({{ id, title }}) => {{
  const btn = document.createElement('button');
  btn.className = 'nav-btn';
  btn.textContent = title;
  btn.dataset.id = id;
  btn.onclick = () => activateSection(id);
  sidebar.appendChild(btn);
}});
if (sections.length) activateSection(sections[0].id);
</script>
</body>
</html>
"""
    os.makedirs(DIST_DIR, exist_ok=True)
    with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"대시보드 생성 완료: {DASHBOARD_PATH}")


def main():
    merged, raw_latest = build_merged()
    build_dashboard(merged, raw_latest)


if __name__ == "__main__":
    main()
