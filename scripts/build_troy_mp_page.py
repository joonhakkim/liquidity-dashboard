"""
트로이 MP(모델 포트폴리오) 트래커 페이지(docs/troy_mp.html)를 만든다.

입력: data/manual/troy_mp_trades.csv (팀이 직접 편집하는 매매일지 - 이 파일에 행을 추가/수정하는 것
자체가 "포트폴리오 변경"이다. 컬럼: date, code, name, action(BUY/SELL), price(체결단가,원), amount(매매금액,원))
+ data/troy_mp_prices.csv (fetch_troy_mp_prices.py가 종목별로 받아온 일별 종가)
+ 코스피/코스닥 지수(BM 비교용, 네이버에서 라이브로 받아옴 - data/krx_raw.csv는 당일 아침에만 갱신돼서
당일 종가가 하루 늦게 반영되는 문제가 있어 여기서는 쓰지 않는다)

지수 산출 방법론 - 일별 시간가중수익률(TWR) 연쇄복리:
  하루 수익률 r(t) = [전일 보유수량으로 오늘 종가 평가한 가치] / [전일 보유수량으로 전일 종가 평가한 가치] - 1
  즉 "그날의 매매"는 그날 수익률 계산에 전혀 영향을 주지 않고(매매는 종가에 체결된다고 가정, 다음날
  보유수량에만 반영), 순수하게 "전일 보유 종목바스켓의 가격변동"만 반영한다. 이렇게 하면 신규 편입/비중
  조절(매매금액 유입출)이 지수 레벨을 왜곡하지 않는다(펀드 성과평가의 표준 TWR 방식과 동일 원리) - 그래서
  총 투입자본(초기 원금) 같은 걸 별도로 물어볼 필요가 없다. 미보유 현금은 수익률 0%로 취급(별도 비중 없음).
  MP지수·코스피(BM)지수 둘 다 "MP에 처음 종목이 편입된 날"을 100으로 리베이스한다.

보유종목 테이블 - 이동평균원가법(weighted-average cost)으로 매수단가를 관리한다:
  BUY: shares += amount/price, cost_basis += amount
  SELL: sell_shares = amount/price 만큼 원가도 비례 차감(shares_before 대비 비율만큼 cost_basis 차감)
  평균매수단가 = cost_basis / shares, 수익률 = (현재가-평균매수단가)/평균매수단가
"""
import json
import os
from datetime import datetime

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
DOWNLOADS_DIR = os.path.join(DOCS_DIR, "downloads")
TRADES_PATH = os.path.join(DATA_DIR, "manual", "troy_mp_trades.csv")
PRICES_PATH = os.path.join(DATA_DIR, "troy_mp_prices.csv")
OUT_PATH = os.path.join(DOCS_DIR, "troy_mp.html")
XLSX_HISTORY_PATH = os.path.join(DOWNLOADS_DIR, "troy_mp_history.xlsx")

TOTAL_CAPITAL = 1_000_000_000  # 총 투입자본(원) - 종목별 매입금액 합계 + 남는 건 현금으로 취급


def fetch_index_history(symbol, count=3000):
    """네이버 차트 API로 지수(KOSPI/KOSDAQ) 일별 종가를 받아온다."""
    r = requests.get(
        "https://fchart.stock.naver.com/sise.nhn",
        params={"symbol": symbol, "timeframe": "day", "count": count, "requestType": 0},
        timeout=20,
    )
    rows = []
    for line in r.text.split('<item data="')[1:]:
        raw = line.split('"')[0]
        parts = raw.split("|")
        if len(parts) < 5:
            continue
        try:
            rows.append((pd.to_datetime(parts[0], format="%Y%m%d"), float(parts[4])))
        except ValueError:
            continue
    df = pd.DataFrame(rows, columns=["date", "close"]).sort_values("date")
    return df.set_index("date")["close"]


def load_trades():
    """price를 비워두면(팀이 종목/금액만 적고 단가는 안 적은 경우) 그날 네이버 종가로 자동
    채운다(main()에서 prices_wide로 채움) - 여기서는 price 없는 행도 일단 살려둔다."""
    trades = pd.read_csv(TRADES_PATH, dtype={"code": str}, parse_dates=["date"])
    trades = trades.dropna(subset=["code", "date", "action", "amount"])
    trades["code"] = trades["code"].str.zfill(6)
    trades["action"] = trades["action"].str.upper().str.strip()
    if "sector" not in trades.columns:
        trades["sector"] = None
    return trades.sort_values("date").reset_index(drop=True)


def fill_missing_prices(trades, prices_wide):
    """price가 비어있는 행을 그 날짜의 실제 종가(prices_wide)로 채운다. 그 날짜 종가가 아직
    없으면(당일 장중 등) 가장 최근 종가로 대체한다. 채운 값은 원본 CSV에도 다시 써서 남긴다."""
    missing = trades["price"].isna()
    if not missing.any():
        return trades, False

    changed = False
    for idx in trades[missing].index:
        code, date = trades.at[idx, "code"], trades.at[idx, "date"]
        price = None
        if code in prices_wide.columns:
            s = prices_wide[code]
            if date in s.index and not pd.isna(s.loc[date]):
                price = float(s.loc[date])
            else:
                s_valid = s.dropna()
                if not s_valid.empty:
                    price = float(s_valid.iloc[-1])
        if price is not None:
            trades.at[idx, "price"] = price
            changed = True

    if changed:
        out = trades.copy()
        out["date"] = out["date"].dt.strftime("%Y-%m-%d")
        out.to_csv(TRADES_PATH, index=False, encoding="utf-8-sig")
        print(f"매매일지에 빈 단가 {missing.sum()}건을 네이버 종가로 채워서 저장했습니다.")
    return trades, changed


def compute_holdings_table(trades, latest_prices, name_map, sector_map):
    """이동평균원가법으로 종목별 현재 보유수량/원가/평균매수단가를 계산."""
    pos = {}  # code -> {"shares": x, "cost": y}
    for _, row in trades.iterrows():
        code = row["code"]
        p = pos.setdefault(code, {"shares": 0.0, "cost": 0.0})
        qty = row["amount"] / row["price"]
        if row["action"] == "BUY":
            p["shares"] += qty
            p["cost"] += row["amount"]
        elif row["action"] == "SELL":
            if p["shares"] > 0:
                ratio = min(qty / p["shares"], 1.0)
                p["cost"] *= (1 - ratio)
                p["shares"] -= qty
            else:
                p["shares"] -= qty

    rows = []
    for code, p in pos.items():
        if p["shares"] <= 1e-6:
            continue
        avg_price = p["cost"] / p["shares"]
        cur_price = latest_prices.get(code)
        eval_value = p["shares"] * cur_price if cur_price else None
        ret_pct = (cur_price / avg_price - 1) * 100 if cur_price else None
        rows.append({
            "code": code,
            "name": name_map.get(code, code),
            "sector": sector_map.get(code) or "-",
            "shares": p["shares"],
            "avg_price": avg_price,
            "cost_basis": p["cost"],
            "cur_price": cur_price,
            "eval_value": eval_value,
            "ret_pct": ret_pct,
        })

    stock_eval = sum(r["eval_value"] for r in rows if r["eval_value"])
    cash = max(TOTAL_CAPITAL - sum(p["cost"] for p in pos.values()), 0)
    total_eval = stock_eval + cash

    for r in rows:
        r["weight_pct"] = (r["eval_value"] / total_eval * 100) if r["eval_value"] else None
    rows.sort(key=lambda r: r["eval_value"] or 0, reverse=True)

    if cash > 0:
        rows.append({
            "code": "-", "name": "현금", "sector": "-", "shares": None, "avg_price": None,
            "cost_basis": cash, "cur_price": None, "eval_value": cash, "ret_pct": None,
            "weight_pct": cash / total_eval * 100,
        })

    return rows, total_eval


def build_trade_history(trades, name_map):
    """매매일지를 종목별 누적 보유수량 추적해서 각 매매가 신규 편입/비중 확대/비중 축소/전량 편출
    중 어디에 해당하는지 자동으로 라벨링한 리스트로 변환(최신순). HTML 렌더링과 엑셀 저장이 공유."""
    running_shares = {}
    history = []
    for _, row in trades.sort_values(["date", "code"]).iterrows():
        code = row["code"]
        qty = row["amount"] / row["price"]
        prev_shares = running_shares.get(code, 0.0)
        if row["action"] == "BUY":
            new_shares = prev_shares + qty
            label = "편입" if prev_shares <= 1e-6 else "비중 확대"
            color = "#ffa94d"
        else:
            new_shares = prev_shares - qty
            label = "편출" if new_shares <= 1e-6 else "비중 축소"
            color = "#4dabf7"
        running_shares[code] = new_shares
        history.append({
            "date": row["date"],
            "code": code,
            "name": name_map.get(code, code),
            "label": label,
            "color": color,
            "price": row["price"],
            "qty": qty,
            "amount": row["amount"],
        })
    history.sort(key=lambda h: h["date"], reverse=True)
    return history


def render_trade_history_html(history):
    if not history:
        return '<div style="color:#9aa0a6; font-size:12px;">매매 이력이 없습니다.</div>'

    rows_html = ""
    for h in history:
        rows_html += f"""
        <tr>
          <td>{h['date'].strftime('%m/%d')}</td>
          <td>{h['name']}</td>
          <td style="color:{h['color']}">{h['label']}</td>
          <td>{h['amount']:,.0f}</td>
        </tr>"""
    return f"""<table>
        <thead><tr><th>날짜</th><th>종목</th><th>구분</th><th>금액(원)</th></tr></thead>
        <tbody>{rows_html}
        </tbody>
      </table>"""


def write_trade_history_xlsx(history):
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    df = pd.DataFrame([{
        "날짜": h["date"].strftime("%Y-%m-%d"),
        "종목명": h["name"],
        "코드": h["code"],
        "구분": h["label"],
        "단가": h["price"],
        "수량": round(h["qty"], 4),
        "금액(원)": h["amount"],
    } for h in history])
    with pd.ExcelWriter(XLSX_HISTORY_PATH, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="편입편출 히스토리", index=False)


def compute_twr_index(trades, prices_wide, kospi, kosdaq):
    """일별 TWR 지수(MP)와 코스피/코스닥(BM) 지수를 편입 첫날=100으로 리베이스해서 같이 반환.
    미투자 현금(TOTAL_CAPITAL - 누적 순매수금액)은 수익률 0%로 취급해서 v_start/v_end 양쪽에
    똑같이 더해준다 - 그래야 "몇 %는 현금이라 안 움직인다"는 게 지수에 정확히 희석 반영된다."""
    inception = trades["date"].min()
    common_dates = set(prices_wide.index) & set(kospi.index)
    if kosdaq is not None:
        common_dates &= set(kosdaq.index)
    all_dates = sorted(common_dates)
    all_dates = [d for d in all_dates if d >= inception]
    if not all_dates:
        return [], [], [], []

    codes = sorted(trades["code"].unique())
    shares = {c: 0.0 for c in codes}
    cash = TOTAL_CAPITAL
    trades_by_date = {d: g for d, g in trades.groupby("date")}

    mp_index = [100.0]
    dates_out = [all_dates[0]]

    prev_date = all_dates[0]
    if prev_date in trades_by_date:
        for _, row in trades_by_date[prev_date].iterrows():
            qty = row["amount"] / row["price"]
            if row["action"] == "BUY":
                shares[row["code"]] += qty
                cash -= row["amount"]
            else:
                shares[row["code"]] -= qty
                cash += row["amount"]
    prev_cash = cash

    for d in all_dates[1:]:
        v_start = prev_cash + sum(shares[c] * prices_wide.at[prev_date, c] for c in codes
                       if not pd.isna(prices_wide.at[prev_date, c]) and shares[c] != 0)
        v_end = prev_cash + sum(shares[c] * prices_wide.at[d, c] for c in codes
                     if not pd.isna(prices_wide.at[d, c]) and shares[c] != 0)
        if v_start > 0:
            r = v_end / v_start - 1
        else:
            r = 0.0
        mp_index.append(mp_index[-1] * (1 + r))
        dates_out.append(d)

        if d in trades_by_date:
            for _, row in trades_by_date[d].iterrows():
                qty = row["amount"] / row["price"]
                if row["action"] == "BUY":
                    shares[row["code"]] += qty
                    cash -= row["amount"]
                else:
                    shares[row["code"]] -= qty
                    cash += row["amount"]
        prev_date = d
        prev_cash = cash

    kospi_base = kospi.loc[dates_out[0]]
    bm_kospi = [kospi.loc[d] / kospi_base * 100 for d in dates_out]
    bm_kosdaq = []
    if kosdaq is not None:
        kosdaq_base = kosdaq.loc[dates_out[0]]
        bm_kosdaq = [kosdaq.loc[d] / kosdaq_base * 100 for d in dates_out]

    return dates_out, mp_index, bm_kospi, bm_kosdaq


def compute_period_alpha(dates_out, mp_index, bm_index, days_back=None, prev_trading_day=False):
    """최근 N일(달력 기준) 또는 직전 거래일 대비 MP수익률-BM수익률(초과성과, %p)을 계산.
    dates_out/mp_index/bm_index 둘 다 같은 인덱스에 대응하는 리스트(편입일=100 리베이스 시리즈)라서
    구간 시작점 인덱스만 다르게 잡으면 "그 구간 동안의" 초과성과가 나온다(레벨차 = 수익률차, 둘 다 같은
    시작 base=100이었기 때문). 데이터가 그 기간만큼 아직 안 쌓였으면 None(N/A) 반환."""
    if len(dates_out) < 2:
        return None
    if prev_trading_day:
        start_idx = -2
    else:
        target_date = pd.Timestamp(dates_out[-1]) - pd.Timedelta(days=int(days_back))
        candidates = [i for i, d in enumerate(dates_out) if d <= target_date]
        if not candidates:
            return None
        start_idx = candidates[-1]
    mp_ret = mp_index[-1] / mp_index[start_idx] - 1
    bm_ret = bm_index[-1] / bm_index[start_idx] - 1
    return (mp_ret - bm_ret) * 100


def main():
    if not os.path.exists(TRADES_PATH):
        print("매매일지 파일이 없습니다:", TRADES_PATH)
        return
    trades = load_trades()

    # data/krx_raw.csv(코스피 종가)는 메인 파이프라인이 매일 아침 07:30에만 갱신하는데, 그 시점엔
    # KRX가 아직 전날 종가만 발표한 상태라 당일 종가가 하루 늦게 반영된다(트로이 MP는 종가 확정 후인
    # 17:30에 별도 실행되므로 종목별 현재가는 당일 반영되는데 코스피만 하루 밀리는 불일치가 있었음,
    # 2026-08-19 발견). 코스닥과 동일하게 네이버에서 라이브로 받아와서 날짜를 맞춘다.
    print("코스피 지수 수집 중...")
    kospi = fetch_index_history("KOSPI")
    print("코스닥 지수 수집 중...")
    kosdaq = fetch_index_history("KOSDAQ")

    if trades.empty:
        render_empty_page()
        print("트로이 MP에 아직 편입된 종목이 없습니다. 안내 페이지만 생성했습니다.")
        return

    name_map = dict(zip(trades["code"], trades["name"]))
    sector_map = dict(zip(trades["code"], trades["sector"]))

    if not os.path.exists(PRICES_PATH):
        print("경고: 종목 가격 데이터가 없습니다. fetch_troy_mp_prices.py를 먼저 실행하세요.")
        return
    prices = pd.read_csv(PRICES_PATH, dtype={"code": str}, parse_dates=["date"])
    prices["code"] = prices["code"].str.zfill(6)
    prices_wide = prices.pivot_table(index="date", columns="code", values="close").ffill()

    trades, _ = fill_missing_prices(trades, prices_wide)

    latest_prices = {}
    for code in trades["code"].unique():
        if code in prices_wide.columns:
            s = prices_wide[code].dropna()
            if not s.empty:
                latest_prices[code] = float(s.iloc[-1])

    holdings, total_eval = compute_holdings_table(trades, latest_prices, name_map, sector_map)
    dates_out, mp_index, bm_kospi, bm_kosdaq = compute_twr_index(trades, prices_wide, kospi, kosdaq)

    dates_json = json.dumps([d.strftime("%Y-%m-%d") for d in dates_out])
    mp_json = json.dumps([round(v, 3) for v in mp_index])
    bm_kospi_json = json.dumps([round(v, 3) for v in bm_kospi])
    bm_kosdaq_json = json.dumps([round(v, 3) for v in bm_kosdaq])

    mp_latest = mp_index[-1] if mp_index else 100.0
    bm_kospi_latest = bm_kospi[-1] if bm_kospi else 100.0
    bm_kosdaq_latest = bm_kosdaq[-1] if bm_kosdaq else 100.0

    def fmt_alpha(v):
        return f"{v:+.2f}%p" if v is not None else "N/A"

    alpha_periods = {}
    for bm_name, bm_series in [("kospi", bm_kospi), ("kosdaq", bm_kosdaq)]:
        alpha_periods[f"alpha_{bm_name}_1d"] = fmt_alpha(
            compute_period_alpha(dates_out, mp_index, bm_series, prev_trading_day=True))
        alpha_periods[f"alpha_{bm_name}_1w"] = fmt_alpha(
            compute_period_alpha(dates_out, mp_index, bm_series, days_back=7))
        alpha_periods[f"alpha_{bm_name}_1m"] = fmt_alpha(
            compute_period_alpha(dates_out, mp_index, bm_series, days_back=30))

    # 리베이스(=100)된 BM지수 말고 실제 지수 값(포인트)도 참고용으로 하단에 표시한다. 코스닥은
    # fetch_index_history()를 지금 이 시점에 라이브로 호출해서 장중이면 당일 실시간가가 섞여
    # 들어올 수 있는데, 페이지의 나머지(코스피·MP지수)는 전부 "하루 한 번, 종가 기준"이라
    # 날짜를 맞추기 위해 dates_out(=차트/지수 계산에 실제로 쓰인 마지막 날짜) 기준으로 조회한다.
    ref_date = dates_out[-1] if dates_out else None
    kospi_actual_latest = float(kospi.loc[ref_date]) if ref_date is not None and ref_date in kospi.index else None
    kosdaq_actual_latest = float(kosdaq.loc[ref_date]) if ref_date is not None and kosdaq is not None and ref_date in kosdaq.index else None
    kospi_actual_date = ref_date.strftime("%Y-%m-%d") if ref_date is not None else "N/A"
    kosdaq_actual_date = kospi_actual_date

    # "현금" 다음에 코스피/코스닥 지수를 참고용 행으로 추가한다 - 실제 보유 종목이 아니라서
    # cost_basis/eval_value/weight_pct는 전부 None으로 둔다(총 매입금액·평가금액·BM지수
    # 계산에는 전혀 반영되지 않고, 테이블에 기준값만 나란히 보여주기 위한 표시 전용 행).
    # 누적 수익률 칸에는 지수 자체 수익률이 아니라 "MP수익률 - 지수수익률"(=초과성과, 위 배지의
    # 초과성과와 동일한 값)을 표시한다 - MP가 벤치마크 대비 누적으로 얼마나 앞서/뒤처졌는지를
    # 종목 리스트에서 바로 보기 위함.
    holdings.append({
        "code": "-", "name": "코스피 지수(기준)", "sector": "-", "shares": None, "avg_price": None,
        "cost_basis": None, "cur_price": kospi_actual_latest, "eval_value": None,
        "ret_pct": mp_latest - bm_kospi_latest if bm_kospi_latest is not None else None,
        "weight_pct": None,
    })
    holdings.append({
        "code": "-", "name": "코스닥 지수(기준)", "sector": "-", "shares": None, "avg_price": None,
        "cost_basis": None, "cur_price": kosdaq_actual_latest, "eval_value": None,
        "ret_pct": mp_latest - bm_kosdaq_latest if bm_kosdaq_latest is not None else None,
        "weight_pct": None,
    })

    rows_html = ""
    for r in holdings:
        ret = r["ret_pct"]
        ret_str = "N/A" if ret is None else f"{ret:+.2f}%"
        ret_color = "#adb5bd" if ret is None else ("#ff6b6b" if ret >= 0 else "#4dabf7")
        cur_price_str = f"{r['cur_price']:,.0f}" if r["cur_price"] else "N/A"
        eval_value_str = f"{r['eval_value']:,.0f}" if r["eval_value"] else "N/A"
        weight_str = f"{r['weight_pct']:.1f}%" if r["weight_pct"] is not None else "N/A"
        avg_price_str = f"{r['avg_price']:,.0f}" if r["avg_price"] is not None else "-"
        cost_basis_str = f"{r['cost_basis']:,.0f}" if r["cost_basis"] is not None else "-"
        rows_html += f"""
        <tr>
          <td>{r['name']}</td>
          <td>{r['code']}</td>
          <td>{r['sector']}</td>
          <td>{avg_price_str}</td>
          <td>{cur_price_str}</td>
          <td style="color:{ret_color}">{ret_str}</td>
          <td>{cost_basis_str}</td>
          <td>{eval_value_str}</td>
          <td>{weight_str}</td>
        </tr>"""

    history = build_trade_history(trades, name_map)
    history_html = render_trade_history_html(history)
    write_trade_history_xlsx(history)

    html = TEMPLATE.format(
        history_html=history_html,
        **alpha_periods,
        dates_json=dates_json,
        mp_json=mp_json,
        bm_kospi_json=bm_kospi_json,
        bm_kosdaq_json=bm_kosdaq_json,
        mp_latest=f"{mp_latest:,.2f}",
        bm_kospi_latest=f"{bm_kospi_latest:,.2f}",
        bm_kosdaq_latest=f"{bm_kosdaq_latest:,.2f}",
        alpha_kospi=f"{mp_latest - bm_kospi_latest:+.2f}",
        alpha_kosdaq=f"{mp_latest - bm_kosdaq_latest:+.2f}",
        kospi_actual=f"{kospi_actual_latest:,.2f}" if kospi_actual_latest is not None else "N/A",
        kospi_actual_date=kospi_actual_date,
        kosdaq_actual=f"{kosdaq_actual_latest:,.2f}" if kosdaq_actual_latest is not None else "N/A",
        kosdaq_actual_date=kosdaq_actual_date,
        inception=trades['date'].min().strftime('%Y-%m-%d'),
        n_holdings=len(holdings),
        total_eval=f"{total_eval:,.0f}",
        rows_html=rows_html,
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"저장 완료: {OUT_PATH} (보유 {len(holdings)}종목, MP지수 {mp_latest:.2f} vs 코스피 {bm_kospi_latest:.2f} vs 코스닥 {bm_kosdaq_latest:.2f})")


EMPTY_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>트로이 MP 트래커</title>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  a.back {{ color:#4dabf7; font-size:13px; text-decoration:none; }}
  h1 {{ font-size:20px; margin:16px 0 4px 0; }}
  .note {{ color:#9aa0a6; font-size:13px; line-height:1.8; max-width:640px; background:#1a1d24; border-radius:10px; padding:20px 22px; margin-top:20px; }}
  code {{ background:#23262e; padding:1px 6px; border-radius:4px; }}
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>트로이 MP 트래커</h1>
  <div class="note">
    아직 편입된 종목이 없습니다.<br><br>
    <code>data/manual/troy_mp_trades.csv</code> 파일에 매매 행을 추가하면(date, code, name, action(BUY/SELL),
    price, amount) 다음 파이프라인 실행부터 자동으로 이 페이지에 반영됩니다.
  </div>
</body>
</html>
"""


def render_empty_page():
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(EMPTY_TEMPLATE)


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>트로이 MP 트래커</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  a.back {{ color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }}
  h1 {{ font-size:20px; margin:8px 0 4px 0; }}
  .updated {{ color:#9aa0a6; font-size:13px; margin-bottom:20px; }}
  .badges {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap:12px; margin-bottom:20px; max-width:820px; }}
  table.alpha-table {{ max-width:600px; background:#1a1d24; border-radius:10px; margin-bottom:24px; }}
  table.alpha-table th, table.alpha-table td {{ border-bottom:none; padding:10px 14px; }}
  table.alpha-table td:not(:first-child) {{ font-weight:bold; }}
  .badge {{ background:#1a1d24; border-radius:10px; padding:14px 16px; }}
  .badge .label {{ color:#9aa0a6; font-size:12px; }}
  .badge .value {{ font-size:20px; font-weight:bold; margin-top:4px; }}
  .badge.mp .value {{ color:#ff8787; }}
  .badge.bm .value {{ color:#4dabf7; }}
  .badge.alpha .value {{ color:#63e6be; }}
  .chart-wrap {{ height:420px; position:relative; max-width:1100px; margin-bottom:28px; }}
  table {{ border-collapse: collapse; width:100%; font-size:13px; }}
  th, td {{ padding:8px 12px; text-align:right; border-bottom:1px solid #23262e; }}
  th:first-child, td:first-child {{ text-align:left; }}
  th:nth-child(2), td:nth-child(2) {{ text-align:left; color:#9aa0a6; }}
  th {{ color:#9aa0a6; font-weight:normal; font-size:12px; }}
  .main-row {{ display:flex; align-items:flex-start; gap:20px; flex-wrap:wrap; }}
  .holdings-col {{ flex:1 1 700px; max-width:1000px; }}
  .history-col {{ flex:0 0 340px; background:#1a1d24; border-radius:10px; padding:16px 18px; max-height:640px; overflow-y:auto; }}
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
  <h1>트로이 MP 트래커</h1>
  <div class="updated">최종 갱신: {updated_at} &middot; 편입 시작일 {inception} (=100 기준) &middot; 보유 {n_holdings}종목 &middot; 평가금액 합계 {total_eval}원</div>

  <div class="badges">
    <div class="badge mp"><div class="label">트로이 MP 지수</div><div class="value">{mp_latest}</div></div>
    <div class="badge bm"><div class="label">코스피(BM) 지수</div><div class="value">{bm_kospi_latest}</div></div>
    <div class="badge bm"><div class="label">코스닥(BM) 지수</div><div class="value">{bm_kosdaq_latest}</div></div>
    <div class="badge alpha"><div class="label">초과성과(vs 코스피, %p)</div><div class="value">{alpha_kospi}</div></div>
    <div class="badge alpha"><div class="label">초과성과(vs 코스닥, %p)</div><div class="value">{alpha_kosdaq}</div></div>
  </div>

  <table class="alpha-table">
    <thead><tr><th>구간별 초과성과</th><th>총 누적(편입일~)</th><th>1일</th><th>1주일</th><th>1개월</th></tr></thead>
    <tbody>
      <tr><td>vs 코스피</td><td>{alpha_kospi}%p</td><td>{alpha_kospi_1d}</td><td>{alpha_kospi_1w}</td><td>{alpha_kospi_1m}</td></tr>
      <tr><td>vs 코스닥</td><td>{alpha_kosdaq}%p</td><td>{alpha_kosdaq_1d}</td><td>{alpha_kosdaq_1w}</td><td>{alpha_kosdaq_1m}</td></tr>
    </tbody>
  </table>

  <div class="chart-wrap"><canvas id="navChart"></canvas></div>

  <div class="main-row">
    <div class="holdings-col">
      <table>
        <thead><tr>
          <th>종목명</th><th>코드</th><th>섹터</th><th>평균매수단가</th><th>현재가</th><th>누적 수익률</th><th>매입금액(잔액)</th><th>평가금액</th><th>비중</th>
        </tr></thead>
        <tbody>{rows_html}
        </tbody>
      </table>
    </div>
    <div class="history-col">
      <h3>편입·편출 / 비중 조절 히스토리</h3>
      <a class="dl" href="downloads/troy_mp_history.xlsx">&#128190; 엑셀 다운로드</a>
      {history_html}
    </div>
  </div>

  <div class="note">
    <h3 style="font-size:13px; color:#c7cbd1; margin:0 0 8px 0;">산출 방법론</h3>
    <b>포트폴리오 변경</b> — <code>data/manual/troy_mp_trades.csv</code> 매매일지에 행을 추가/수정하는 방식.
    파이프라인 실행마다 자동으로 반영됩니다. 단가(price)를 비워두면 그날 네이버 종가로 자동 채워집니다.<br><br>
    <b>현금</b> — 총 투입자본(10억원 기준) 중 종목에 배분되지 않은 나머지는 "현금"으로 잡아 수익률 0%로
    취급합니다(비중 계산의 분모에도 포함되어 종목별 비중이 왜곡되지 않습니다).<br><br>
    <b>지수 산출</b> — 일별 시간가중수익률(TWR) 연쇄복리. 그날의 매매는 그날 수익률에 영향을 주지 않고(매매는
    종가 체결 가정, 다음날 비중부터 반영) 순수하게 전일 보유 바스켓(현금 포함)의 가격변동만 반영합니다. 그래서
    신규 편입·비중 조절이 지수 레벨을 왜곡하지 않습니다(펀드 성과평가 TWR 방식과 동일).
    MP·코스피·코스닥(BM) 모두 편입 첫날을 100으로 리베이스합니다.<br><br>
    <b>평균매수단가</b> — 이동평균원가법. 매수 시 원가에 매입금액을 더하고, 매도 시 매도수량 비율만큼 원가를 비례
    차감합니다.
  </div>

  <div class="badges" style="margin-top:16px;">
    <div class="badge bm"><div class="label">코스피 실제 지수({kospi_actual_date})</div><div class="value">{kospi_actual}</div></div>
    <div class="badge bm"><div class="label">코스닥 실제 지수({kosdaq_actual_date})</div><div class="value">{kosdaq_actual}</div></div>
  </div>
  </div>

<script>
const dates = {dates_json};
const mpIndex = {mp_json};
const bmKospiIndex = {bm_kospi_json};
const bmKosdaqIndex = {bm_kosdaq_json};

function initChart() {{
  if (window.__navChartInited) return;
  window.__navChartInited = true;
  new Chart(document.getElementById('navChart').getContext('2d'), {{
    type: 'line',
    data: {{
      labels: dates,
      datasets: [
        {{ label: '트로이 MP', data: mpIndex, borderColor: '#ff8787', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 2 }},
        {{ label: '코스피(BM)', data: bmKospiIndex, borderColor: '#4dabf7', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 2, borderDash: [5,3] }},
        {{ label: '코스닥(BM)', data: bmKosdaqIndex, borderColor: '#63e6be', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 2, borderDash: [2,3] }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ labels: {{ color: '#e6e6e6' }} }} }},
      scales: {{
        x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 12 }}, grid: {{ color: '#23262e' }} }},
        y: {{ title: {{ display: true, text: '지수(편입일=100)', color: '#9aa0a6' }}, ticks: {{ color: '#9aa0a6' }}, grid: {{ color: '#23262e' }} }},
      }}
    }}
  }});
}}

const PW_HASH = "03f1a9ee7721268c34ba420e058dd33d487bec8379c9dea6a997b6968400a60e";
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
    sessionStorage.setItem("troy_mp_unlocked", "1");
    unlockPage();
  }} else {{
    document.getElementById("pw-error").textContent = "비밀번호가 틀렸습니다";
  }}
}}
document.getElementById("pw-submit").addEventListener("click", tryUnlock);
document.getElementById("pw-input").addEventListener("keydown", e => {{ if (e.key === "Enter") tryUnlock(); }});
if (sessionStorage.getItem("troy_mp_unlocked") === "1") {{
  unlockPage();
}}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
