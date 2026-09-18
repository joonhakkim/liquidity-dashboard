"""
해외MP(미국주식) 트래커 페이지(docs/overseas_mp.html)를 만든다. 국내 MP 트래커들
(build_troy_mp_page.py)과 인터페이스(다크테마, 배지, 차트+기간버튼, 보유종목표, 매매히스토리,
리스크지표, 비밀번호 잠금)는 동일하게 가져오되, 통화(달러)와 벤치마크(S&P500 하나만 - 사용자
요청, 2026-09-18)가 달라서 별도 스크립트로 둔다.

- 종목코드가 6자리 숫자가 아니라 영문 티커라서 build_troy_mp_page.load_trades()의 zfill(6)을
  그대로 못 쓴다 -> 이 파일에 zfill 없는 버전을 따로 둠.
- 계산 로직(TWR 지수/평균매수단가/MDD/표준편차/정보비율/소르티노/구간수익률)은
  build_troy_mp_page.py의 함수를 그대로 재사용한다(total_capital 파라미터로 달러 총자본
  주입 - 두 스크립트가 계산 방식이 어긋나지 않도록).
- 시가총액 기준 미국 섹터 벤치마크 데이터가 없어서 섹터 OW/UW 비교 테이블은 일단 뺀다(보유
  종목의 섹터별 비중 합계만 보여줌).
"""
import json
import os
from datetime import datetime

import pandas as pd

from mp_portfolios import OVERSEAS_PORTFOLIOS, BASE_INDEX, TOTAL_CAPITAL_USD, DOCS_DIR, DOWNLOADS_DIR
from build_troy_mp_page import (
    fill_missing_prices, compute_holdings_table, compute_twr_index, compute_mdd, pct_return,
    compute_annualized_stdev, compute_information_ratio, compute_sortino_ratio,
    compute_period_return, compute_period_alpha, build_trade_history, render_trade_history_html,
    write_trade_history_xlsx,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def load_trades_overseas(trades_path):
    """build_troy_mp_page.load_trades()와 동일하지만 code에 zfill(6)을 안 건다(영문 티커라
    앞에 0을 채우면 코드가 깨짐)."""
    trades = pd.read_csv(trades_path, dtype={"code": str}, parse_dates=["date"])
    trades = trades.dropna(subset=["code", "date", "action", "amount"])
    trades["code"] = trades["code"].str.upper().str.strip()
    trades["action"] = trades["action"].str.upper().str.strip()
    if "sector" not in trades.columns:
        trades["sector"] = None
    return trades.sort_values("date", kind="stable").reset_index(drop=True)


def render_sector_table(holdings):
    """미국 시장 전체 섹터 비중 벤치마크가 없어서 OW/UW 비교 없이 보유 비중 합계만 보여준다."""
    totals = {}
    for r in holdings:
        if r["shares"] is None or not r["eval_value"]:
            continue
        totals[r["sector"]] = totals.get(r["sector"], 0.0) + r["eval_value"]
    total_sum = sum(totals.values())
    if not total_sum:
        return ""
    rows = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    trs = "".join(
        f'<tr><td>{sector}</td><td>{amt / total_sum * 100:.1f}%</td></tr>'
        for sector, amt in rows
    )
    return f"""
  <div class="note" style="margin-top:16px;">
    <h3 style="font-size:13px; color:#c7cbd1; margin:0 0 8px 0;">섹터별 비중</h3>
    <table style="max-width:400px;"><thead><tr><th>섹터</th><th>비중</th></tr></thead>
    <tbody>{trs}</tbody></table>
  </div>"""


def main():
    portfolio = OVERSEAS_PORTFOLIOS[0]
    trades_path = portfolio["trades_path"]
    prices_path = portfolio["prices_path"]
    out_path = portfolio["out_path"]
    xlsx_path = portfolio["xlsx_path"]
    name = portfolio["name"]

    if not os.path.exists(trades_path):
        print(f"[{name}] 매매일지 파일이 없습니다:", trades_path)
        return
    trades = load_trades_overseas(trades_path)
    if trades.empty:
        print(f"[{name}] 아직 편입된 종목이 없습니다.")
        return

    name_map = dict(zip(trades["code"], trades["name"]))
    sector_map = dict(zip(trades["code"], trades["sector"]))

    if not os.path.exists(prices_path):
        print(f"[{name}] 경고: 가격 데이터가 없습니다. fetch_overseas_mp_prices.py를 먼저 실행하세요.")
        return
    prices = pd.read_csv(prices_path, dtype={"code": str}, parse_dates=["date"])
    prices_wide = prices.pivot_table(index="date", columns="code", values="close").ffill()

    trades, _ = fill_missing_prices(trades, prices_wide, trades_path)

    latest_prices, prev_prices = {}, {}
    for code in trades["code"].unique():
        if code in prices_wide.columns:
            s = prices_wide[code].dropna()
            if not s.empty:
                latest_prices[code] = float(s.iloc[-1])
            if len(s) >= 2:
                prev_prices[code] = float(s.iloc[-2])

    holdings, total_eval = compute_holdings_table(
        trades, latest_prices, prev_prices, name_map, sector_map, total_capital=TOTAL_CAPITAL_USD)

    if "^GSPC" not in prices_wide.columns:
        print(f"[{name}] 경고: S&P500(^GSPC) 가격이 없습니다.")
        return
    sp500 = prices_wide["^GSPC"].dropna()

    dates_out, mp_index, bm_sp500, _ = compute_twr_index(
        trades, prices_wide, sp500, None, total_capital=TOTAL_CAPITAL_USD)

    dates_json = json.dumps([d.strftime("%Y-%m-%d") for d in dates_out])
    mp_json = json.dumps([round(v, 3) for v in mp_index])
    bm_json = json.dumps([round(v, 3) for v in bm_sp500])

    mp_latest = mp_index[-1] if mp_index else float(BASE_INDEX)
    bm_latest = bm_sp500[-1] if bm_sp500 else float(BASE_INDEX)
    mdd = compute_mdd(mp_index)
    stdev_annualized = compute_annualized_stdev(mp_index)
    ir_sp500 = compute_information_ratio(mp_index, bm_sp500)
    sortino = compute_sortino_ratio(mp_index)

    def fmt_neon(v, suffix):
        if v is None:
            return '<span style="color:#6b7280">N/A</span>'
        color = "#39ff14" if v >= 0 else "#ff2ec4"
        return f'<span style="color:{color}; text-shadow:0 0 6px {color}88;">{v:+.2f}{suffix}</span>'

    fmt_alpha = lambda v: fmt_neon(v, "%p")
    fmt_return = lambda v: fmt_neon(v, "%")

    alpha_periods = {
        "alpha_total": fmt_alpha(pct_return(mp_latest) - pct_return(bm_latest)),
        "alpha_1d": fmt_alpha(compute_period_alpha(dates_out, mp_index, bm_sp500, prev_trading_day=True)),
        "alpha_1w": fmt_alpha(compute_period_alpha(dates_out, mp_index, bm_sp500, days_back=7)),
        "alpha_1m": fmt_alpha(compute_period_alpha(dates_out, mp_index, bm_sp500, days_back=30)),
        "alpha_6m": fmt_alpha(compute_period_alpha(dates_out, mp_index, bm_sp500, days_back=180)),
        "alpha_1y": fmt_alpha(compute_period_alpha(dates_out, mp_index, bm_sp500, days_back=365)),
    }
    own_periods = {
        "own_total": fmt_return(pct_return(mp_latest)),
        "own_1d": fmt_return(compute_period_return(dates_out, mp_index, prev_trading_day=True)),
        "own_1w": fmt_return(compute_period_return(dates_out, mp_index, days_back=7)),
        "own_1m": fmt_return(compute_period_return(dates_out, mp_index, days_back=30)),
        "own_6m": fmt_return(compute_period_return(dates_out, mp_index, days_back=180)),
        "own_1y": fmt_return(compute_period_return(dates_out, mp_index, days_back=365)),
    }

    ref_date = dates_out[-1] if dates_out else None
    sp500_actual = float(sp500.loc[ref_date]) if ref_date is not None and ref_date in sp500.index else None
    sp500_actual_date = ref_date.strftime("%Y-%m-%d") if ref_date is not None else "N/A"
    sp500_day_ret = (bm_sp500[-1] / bm_sp500[-2] - 1) * 100 if len(bm_sp500) >= 2 else None

    holdings.append({
        "code": "-", "name": "S&P500 지수(기준)", "sector": "-", "shares": None, "avg_price": None,
        "cost_basis": None, "cur_price": sp500_actual, "eval_value": None,
        "ret_pct": pct_return(bm_latest), "day_ret_pct": sp500_day_ret, "weight_pct": None,
    })

    rows_html = ""
    stock_no = 0
    for r in holdings:
        if r["shares"] is not None:
            stock_no += 1
        no_str = str(stock_no) if r["shares"] is not None else "-"
        ret = r["ret_pct"]
        ret_str = "N/A" if ret is None else f"{ret:+.2f}%"
        ret_color = "#adb5bd" if ret is None else ("#ff6b6b" if ret >= 0 else "#4dabf7")
        day_ret = r.get("day_ret_pct")
        day_ret_str = "N/A" if day_ret is None else f"{day_ret:+.2f}%"
        day_ret_color = "#adb5bd" if day_ret is None else ("#ff6b6b" if day_ret >= 0 else "#4dabf7")
        cur_price_str = f"${r['cur_price']:,.2f}" if r["cur_price"] else "N/A"
        eval_value_str = f"${r['eval_value']:,.0f}" if r["eval_value"] is not None else "N/A"
        weight_str = f"{r['weight_pct']:.1f}%" if r["weight_pct"] is not None else "N/A"
        avg_price_str = f"${r['avg_price']:,.2f}" if r["avg_price"] is not None else "-"
        cost_basis_str = f"${r['cost_basis']:,.0f}" if r["cost_basis"] is not None else "-"
        rows_html += f"""
        <tr>
          <td>{no_str}</td>
          <td>{r['name']}</td>
          <td>{r['code']}</td>
          <td>{r['sector']}</td>
          <td>{avg_price_str}</td>
          <td>{cur_price_str}</td>
          <td style="color:{day_ret_color}">{day_ret_str}</td>
          <td style="color:{ret_color}">{ret_str}</td>
          <td>{cost_basis_str}</td>
          <td>{eval_value_str}</td>
          <td>{weight_str}</td>
        </tr>"""

    history = build_trade_history(trades, name_map)
    history_html = render_trade_history_html(history)
    write_trade_history_xlsx(history, xlsx_path)
    xlsx_name = os.path.basename(xlsx_path)
    sector_table_html = render_sector_table(holdings)

    html = TEMPLATE.format(
        page_name=name,
        history_html=history_html,
        xlsx_name=xlsx_name,
        pw_hash=portfolio.get("pw_hash"),
        base_index=f"{BASE_INDEX:,}",
        sector_table_html=sector_table_html,
        **alpha_periods,
        **own_periods,
        dates_json=dates_json,
        mp_json=mp_json,
        bm_json=bm_json,
        mp_latest=f"{mp_latest:,.2f}",
        bm_latest=f"{bm_latest:,.2f}",
        mdd=f"{mdd:.2f}%" if mdd is not None else "N/A",
        stdev_annualized=f"{stdev_annualized:.2f}%" if stdev_annualized is not None else "N/A",
        ir_sp500=f"{ir_sp500:+.2f}" if ir_sp500 is not None else "N/A",
        sortino=f"{sortino:+.2f}" if sortino is not None else "N/A",
        sp500_actual=f"{sp500_actual:,.2f}" if sp500_actual is not None else "N/A",
        sp500_actual_date=sp500_actual_date,
        inception=trades["date"].min().strftime("%Y-%m-%d"),
        n_holdings=sum(1 for r in holdings if r["shares"] is not None),
        total_eval=f"${total_eval:,.0f}",
        rows_html=rows_html,
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[{name}] 저장 완료: {out_path} (보유 {sum(1 for r in holdings if r['shares'] is not None)}종목, "
          f"MP지수 {mp_latest:.2f} vs S&P500 {bm_latest:.2f}, MDD {mdd:.2f}%)")


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{page_name} 트래커</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  a.back {{ color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }}
  h1 {{ font-size:20px; margin:8px 0 4px 0; }}
  .updated {{ color:#9aa0a6; font-size:13px; margin-bottom:20px; }}
  .badges {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap:12px; margin-bottom:20px; max-width:820px; }}
  .table-row {{ display:flex; gap:16px; flex-wrap:wrap; margin-bottom:24px; }}
  table.alpha-table {{ max-width:760px; background:#1a1d24; border-radius:10px; margin-bottom:0; }}
  table.alpha-table th, table.alpha-table td {{ border-bottom:none; padding:10px 14px; }}
  table.alpha-table td:not(:first-child) {{ font-weight:bold; }}
  .badge {{ background:#1a1d24; border-radius:10px; padding:14px 16px; }}
  .badge .label {{ color:#9aa0a6; font-size:12px; }}
  .badge .value {{ font-size:20px; font-weight:bold; margin-top:4px; }}
  .badge.mp .value {{ color:#ff8787; }}
  .badge.bm .value {{ color:#4dabf7; }}
  .badge.alpha .value {{ color:#63e6be; }}
  .badge.mdd .value {{ color:#ff2ec4; }}
  .badge.risk .value {{ color:#9775fa; }}
  .chart-wrap {{ height:420px; position:relative; max-width:1100px; margin-bottom:28px; }}
  .chart-range-buttons {{ display:flex; gap:6px; margin-bottom:10px; }}
  .chart-range-buttons button {{ background:#1a1d24; border:1px solid #23262e; color:#9aa0a6;
    font-size:12px; padding:5px 12px; border-radius:6px; cursor:pointer; }}
  .chart-range-buttons button:hover {{ border-color:#4dabf7; color:#e6e6e6; }}
  .chart-range-buttons button.active {{ background:#2a3f5f; border-color:#4dabf7; color:#e6e6e6; }}
  table {{ border-collapse: collapse; width:100%; font-size:13px; }}
  th, td {{ padding:8px 12px; text-align:right; border-bottom:1px solid #23262e; }}
  th:first-child, td:first-child {{ text-align:left; }}
  th:nth-child(2), td:nth-child(2) {{ text-align:left; color:#9aa0a6; }}
  th {{ color:#9aa0a6; font-weight:normal; font-size:12px; }}
  .main-row {{ display:flex; align-items:flex-start; gap:20px; flex-wrap:wrap; }}
  .holdings-col {{ flex:1 1 700px; max-width:1000px; }}
  .history-col {{ flex:0 0 420px; background:#1a1d24; border-radius:10px; padding:16px 18px; max-height:640px; overflow-y:auto; }}
  .history-col h3 {{ font-size:13px; color:#c7cbd1; margin:0 0 10px 0; }}
  .history-col a.dl {{ display:block; color:#4dabf7; font-size:12px; text-decoration:none; margin-bottom:12px; }}
  .note {{ color:#9aa0a6; font-size:12px; line-height:1.7; max-width:900px; background:#1a1d24; border-radius:10px; padding:16px 18px; margin-top:24px; }}
  .note b {{ color:#ffa94d; }}
</style>
</head>
<body>
  <div id="lock-screen" style="position:fixed;inset:0;background:#0f1115;display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:1000;">
    <div style="background:#1a1d24;border-radius:12px;padding:32px 36px;max-width:320px;width:90%;text-align:center;">
      <div style="font-size:15px;color:#e6e6e6;margin-bottom:14px;">비밀번호를 입력하세요</div>
      <input id="pw-input" type="password" style="width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;border:1px solid #333;background:#0f1115;color:#e6e6e6;font-size:14px;" autofocus>
      <div id="pw-error" style="color:#ff6b6b;font-size:12px;margin-top:8px;height:14px;"></div>
      <button id="pw-submit" style="margin-top:12px;width:100%;padding:10px;border-radius:8px;border:none;background:#4dabf7;color:#0f1115;font-weight:bold;cursor:pointer;">확인</button>
    </div>
  </div>
  <div id="page-content" style="display:none">
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>{page_name} 트래커</h1>
  <div class="updated">최종 갱신: {updated_at} &middot; 편입 시작일 {inception} (={base_index} 기준) &middot; 보유 {n_holdings}종목 &middot; 평가금액 합계 {total_eval}</div>

  <div class="badges">
    <div class="badge mp"><div class="label">{page_name} 지수</div><div class="value">{mp_latest}</div></div>
    <div class="badge bm"><div class="label">S&P500(BM) 지수</div><div class="value">{bm_latest}</div></div>
    <div class="badge mdd"><div class="label">MDD(전체기간 최대낙폭)</div><div class="value">{mdd}</div></div>
  </div>
  <div class="badges" style="margin-top:10px;">
    <div class="badge risk"><div class="label">연율화 표준편차</div><div class="value">{stdev_annualized}</div></div>
    <div class="badge risk"><div class="label">정보비율(IR) vs S&P500</div><div class="value">{ir_sp500}</div></div>
    <div class="badge risk"><div class="label">소르티노비율</div><div class="value">{sortino}</div></div>
  </div>

  <div class="table-row">
    <table class="alpha-table">
      <thead><tr><th>포트폴리오 자체 수익률</th><th>총 누적(시작일~)</th><th>1일</th><th>1주일</th><th>1개월</th><th>6개월</th><th>1년</th></tr></thead>
      <tbody>
        <tr><td>{page_name}</td><td>{own_total}</td><td>{own_1d}</td><td>{own_1w}</td><td>{own_1m}</td><td>{own_6m}</td><td>{own_1y}</td></tr>
      </tbody>
    </table>
    <table class="alpha-table">
      <thead><tr><th>구간별 초과성과</th><th>총 누적(시작일~)</th><th>1일</th><th>1주일</th><th>1개월</th><th>6개월</th><th>1년</th></tr></thead>
      <tbody>
        <tr><td>vs S&P500</td><td>{alpha_total}</td><td>{alpha_1d}</td><td>{alpha_1w}</td><td>{alpha_1m}</td><td>{alpha_6m}</td><td>{alpha_1y}</td></tr>
      </tbody>
    </table>
  </div>

  <div class="chart-range-buttons" id="chartRangeButtons"></div>
  <div class="chart-wrap"><canvas id="navChart"></canvas></div>

  <div class="main-row">
    <div class="holdings-col">
      <table>
        <thead><tr>
          <th>#</th><th>종목명</th><th>티커</th><th>섹터</th><th>평균매수단가</th><th>현재가</th><th>1일 수익률</th><th>누적 수익률</th><th>매입금액(잔액)</th><th>평가금액</th><th>비중</th>
        </tr></thead>
        <tbody>{rows_html}
        </tbody>
      </table>
    </div>
    <div class="history-col">
      <h3>편입·편출 / 비중 조절 히스토리</h3>
      <a class="dl" href="downloads/{xlsx_name}">&#128190; 엑셀 다운로드</a>
      {history_html}
    </div>
  </div>

  <div class="note">
    <h3 style="font-size:13px; color:#c7cbd1; margin:0 0 8px 0;">산출 방법론</h3>
    국내 MP 트래커와 계산방식은 동일합니다(TWR 연쇄복리 지수, 이동평균원가법, 연율화
    표준편차/정보비율/소르티노비율) — 통화만 달러, 벤치마크만 S&P500 하나로 바뀌었습니다.
    총 투입자본은 $1,000,000 기준이며, 가격은 Yahoo Finance에서 매일 수집합니다.
    미국 시장 전체 섹터 비중 데이터가 없어 섹터 OW/UW 비교 대신 보유 비중만 보여줍니다.
  </div>

  <div class="badges" style="margin-top:16px;">
    <div class="badge bm"><div class="label">S&P500 실제 지수({sp500_actual_date})</div><div class="value">{sp500_actual}</div></div>
  </div>
  {sector_table_html}
  </div>

<script>
const dates = {dates_json};
const mpIndex = {mp_json};
const bmIndex = {bm_json};

const RANGE_LABELS = [['1w','1주'],['1m','1개월'],['3m','3개월'],['6m','6개월'],['1y','1년'],['all','시작이후']];
const RANGE_DAYS = {{ '1w':7, '1m':30, '3m':90, '6m':180, '1y':365, 'all':null }};
let navChart = null;

function sliceStart(range) {{
  if (range === 'all' || dates.length === 0) return 0;
  const days = RANGE_DAYS[range];
  const cutoffMs = new Date(dates[dates.length - 1]).getTime() - days * 86400000;
  const idx = dates.findIndex(d => new Date(d).getTime() >= cutoffMs);
  return idx < 0 ? 0 : idx;
}}

function rebase(series, idx0, doRebase) {{
  if (!doRebase) return series.slice(idx0);
  const base = series[idx0];
  if (!base) return series.slice(idx0);
  return series.slice(idx0).map(v => v == null ? null : (v / base) * 100);
}}

function renderChart(range) {{
  const idx0 = sliceStart(range);
  const doRebase = range !== 'all';
  const yTitle = doRebase ? '구간 시작=100' : '지수(편입일={base_index})';
  if (navChart) navChart.destroy();
  navChart = new Chart(document.getElementById('navChart').getContext('2d'), {{
    type: 'line',
    data: {{
      labels: dates.slice(idx0),
      datasets: [
        {{ label: '{page_name}', data: rebase(mpIndex, idx0, doRebase), borderColor: '#ff8787', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 2 }},
        {{ label: 'S&P500(BM)', data: rebase(bmIndex, idx0, doRebase), borderColor: '#4dabf7', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 2, borderDash: [5,3] }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ labels: {{ color: '#e6e6e6' }} }} }},
      scales: {{
        x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 12 }}, grid: {{ color: '#23262e' }} }},
        y: {{ title: {{ display: true, text: yTitle, color: '#9aa0a6' }}, ticks: {{ color: '#9aa0a6' }}, grid: {{ color: '#23262e' }} }},
      }}
    }}
  }});
  document.querySelectorAll('#chartRangeButtons button').forEach(btn => {{
    btn.classList.toggle('active', btn.dataset.range === range);
  }});
}}

function initChart() {{
  if (window.__navChartInited) return;
  window.__navChartInited = true;
  const btnWrap = document.getElementById('chartRangeButtons');
  RANGE_LABELS.forEach(([key, label]) => {{
    const btn = document.createElement('button');
    btn.textContent = label;
    btn.dataset.range = key;
    btn.addEventListener('click', () => renderChart(key));
    btnWrap.appendChild(btn);
  }});
  renderChart('all');
}}

const PW_HASH = "{pw_hash}";
async function sha256Hex(str) {{
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(str));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("");
}}
function unlockPage() {{
  document.getElementById("lock-screen").style.display = "none";
  document.getElementById("page-content").style.display = "block";
  initChart();
}}
async function tryUnlock() {{
  const val = document.getElementById("pw-input").value;
  const hash = await sha256Hex(val);
  if (hash === PW_HASH) {{
    sessionStorage.setItem("mp_unlocked_shared", "1");
    unlockPage();
  }} else {{
    document.getElementById("pw-error").textContent = "비밀번호가 틀렸습니다";
  }}
}}
document.getElementById("pw-submit").addEventListener("click", tryUnlock);
document.getElementById("pw-input").addEventListener("keydown", e => {{ if (e.key === "Enter") tryUnlock(); }});
if (sessionStorage.getItem("mp_unlocked_shared") === "1") {{
  unlockPage();
}}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
