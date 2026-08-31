"""
MP(모델 포트폴리오) 트래커 페이지들(docs/troy_mp.html, docs/momentum_mp.html, ...)을 만든다.
mp_portfolios.PORTFOLIOS를 순회하며 포트폴리오별로 같은 로직을 반복 적용한다(2026-08-20,
"모멘텀 MP" 추가하며 "트로이 MP" 전용 하드코딩을 일반화 - 새 포트폴리오는 mp_portfolios.py에
항목만 추가하면 됨).

입력(포트폴리오별): data/manual/<id>_trades.csv (팀이 직접 편집하는 매매일지 - 이 파일에 행을
추가/수정하는 것 자체가 "포트폴리오 변경". 컬럼: date, code, name, action(BUY/SELL),
price(체결단가,원), amount(매매금액,원), sector)
+ data/<id>_prices.csv (fetch_troy_mp_prices.py가 종목별로 받아온 일별 종가)
+ 코스피/코스닥 지수(BM 비교용, 네이버에서 라이브로 받아옴 - data/krx_raw.csv는 당일 아침에만 갱신돼서
당일 종가가 하루 늦게 반영되는 문제가 있어 여기서는 쓰지 않는다)

지수 산출 방법론 - 일별 시간가중수익률(TWR) 연쇄복리:
  하루 수익률 r(t) = [전일 보유수량으로 오늘 종가 평가한 가치] / [전일 보유수량으로 전일 종가 평가한 가치] - 1
  즉 "그날의 매매"는 그날 수익률 계산에 전혀 영향을 주지 않고(매매는 종가에 체결된다고 가정, 다음날
  보유수량에만 반영), 순수하게 "전일 보유 종목바스켓의 가격변동"만 반영한다. 이렇게 하면 신규 편입/비중
  조절(매매금액 유입출)이 지수 레벨을 왜곡하지 않는다(펀드 성과평가의 표준 TWR 방식과 동일 원리) - 그래서
  총 투입자본(초기 원금) 같은 걸 별도로 물어볼 필요가 없다. 미보유 현금은 수익률 0%로 취급(별도 비중 없음).
  MP지수·코스피(BM)지수 둘 다 "MP에 처음 종목이 편입된 날"을 BASE_INDEX(=10000)로 리베이스한다
  (원래 100이었는데 2026-08-20에 사용자 요청으로 10000으로 변경).

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

from mp_portfolios import PORTFOLIOS, LONG_SHORT_PORTFOLIOS, ALL_PORTFOLIOS, BASE_INDEX, TOTAL_CAPITAL, DOCS_DIR, DOWNLOADS_DIR

# NET EXPOSURE 계산용 종목별 베타 보정치(기본 1.0). 인버스/레버리지 ETF처럼 기초지수와 배율이
# 다르게 움직이는 상품만 여기 등록한다 - 실제 매매/손익 계산에는 영향 없음(compute_twr_index_ls
# 참고). KODEX 코스닥150선물인버스 = 코스닥150 -1배 추종.
EXPOSURE_BETA = {"251340": -1.0}


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


def load_trades(trades_path):
    """price를 비워두면(팀이 종목/금액만 적고 단가는 안 적은 경우) 그날 네이버 종가로 자동
    채운다(main()에서 prices_wide로 채움) - 여기서는 price 없는 행도 일단 살려둔다."""
    trades = pd.read_csv(trades_path, dtype={"code": str}, parse_dates=["date"])
    trades = trades.dropna(subset=["code", "date", "action", "amount"])
    trades["code"] = trades["code"].str.zfill(6)
    trades["action"] = trades["action"].str.upper().str.strip()
    if "sector" not in trades.columns:
        trades["sector"] = None
    return trades.sort_values("date").reset_index(drop=True)


def fill_missing_prices(trades, prices_wide, trades_path):
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
        out.to_csv(trades_path, index=False, encoding="utf-8-sig")
        print(f"매매일지에 빈 단가 {missing.sum()}건을 네이버 종가로 채워서 저장했습니다.")
    return trades, changed


def compute_holdings_table(trades, latest_prices, prev_prices, name_map, sector_map, show_cash_row=True, cash_mode="full"):
    """이동평균원가법으로 종목별 현재 보유수량/원가/평균매수단가를 계산.
    현금은 "TOTAL_CAPITAL - 현재 보유중인 종목들의 원가 합"이 아니라(이 계산은 편출한 종목의
    실현손익을 전혀 반영 못 함 - 예: 손실 보고 전량 매도한 뒤 그 매도대금보다 큰 금액을 다른
    종목에 재투자하면 이 식은 그 차액을 그냥 무시해버린다), 매매일지 전체(편출된 종목 포함)의
    누적 현금흐름으로 계산한다(compute_twr_index의 현금 추적 방식과 동일 - 그래야 리밸런싱 때
    실현손익이 정확히 반영된다).

    SHORT/COVER(공매도) 지원(2026-08-31, "코스닥 롱숏" 포트폴리오 추가하며 일반화) - shares를
    음수로 표현해서 숏 포지션을 나타낸다. SHORT은 BUY의 반대 방향(shares/cost 둘 다 감소, 현금은
    공매도 대금만큼 증가), COVER는 SELL의 반대 방향(shares/cost 둘 다 원위치로 복귀, 현금은 매수
    상환 대금만큼 감소). 기존 BUY/SELL만 쓰는 롱온리 포트폴리오는 이 두 분기를 절대 안 타므로
    동작이 그대로 유지된다.

    cash_mode="full"(기본, 롱온리 포트폴리오용) - BUY/SELL/SHORT/COVER 전부 반영한 현금.
    cash_mode="long_only"(롱숏 포트폴리오용) - 화면에 보여줄 "현금" 행은 롱 매매(BUY/SELL)만
    반영한다. SHORT의 공매도 대금을 그대로 현금에 합치면 100%에 가까운 숫자가 나와서
    "투자 안 하고 노는 돈"처럼 오해를 사는데(실제로는 롱+숏 200% 그로스 익스포저가 걸려있는
    상태), 사용자가 "나중에 롱 쪽에서 리밸런싱하다 남는 현금이 생길 수 있으니 그 자리를
    남겨달라"고 해서(2026-09-01) 롱 매매만의 잔여현금을 보여주기로 함. total_eval(포트폴리오
    전체 평가금액/NAV)은 always cash_mode 상관없이 진짜 전체 현금(cash_full)을 써서 지수와
    어긋나지 않게 한다 - 화면에 보여주는 값(display_cash)만 다르다."""
    pos = {}  # code -> {"shares": x, "cost": y}
    cash_full = TOTAL_CAPITAL
    cash_long = TOTAL_CAPITAL
    for _, row in trades.iterrows():
        code = row["code"]
        p = pos.setdefault(code, {"shares": 0.0, "cost": 0.0})
        qty = row["amount"] / row["price"]
        action = row["action"]
        if action == "BUY":
            p["shares"] += qty
            p["cost"] += row["amount"]
            cash_full -= row["amount"]
            cash_long -= row["amount"]
        elif action == "SELL":
            if p["shares"] > 0:
                ratio = min(qty / p["shares"], 1.0)
                p["cost"] *= (1 - ratio)
                p["shares"] -= qty
            else:
                p["shares"] -= qty
            cash_full += row["amount"]
            cash_long += row["amount"]
        elif action == "SHORT":
            p["shares"] -= qty
            p["cost"] -= row["amount"]
            cash_full += row["amount"]
        elif action == "COVER":
            if p["shares"] < 0:
                ratio = min(qty / abs(p["shares"]), 1.0)
                p["cost"] *= (1 - ratio)
                p["shares"] += qty
            else:
                p["shares"] += qty
            cash_full -= row["amount"]

    cash = cash_full  # total_eval 계산엔 항상 전체 현금을 쓴다(TWR 지수와 일치시키기 위함)
    # "현금(롱 잔여)"은 정수 주식수 반올림 때문에 아주 살짝 마이너스가 나올 수 있는데(예: 목표
    # 금액보다 몇십만원 더 산 경우), 실제로 마이너스 현금(마이너스 잔고)이라는 뜻이 아니라 반올림
    # 오차일 뿐이라 0으로 바닥을 둔다(2026-09-01, 사용자 요청 - "현금이 마이너스면 안 되는거
    # 아니냐"). cash_mode="full"(롱온리 포트폴리오)은 기존처럼 마이너스도 그대로 보여준다 -
    # 그쪽은 실현손익 반영이 핵심이라 마이너스가 실제 의미를 가질 수 있음.
    display_cash = max(0.0, cash_long) if cash_mode == "long_only" else cash_full

    rows = []
    for code, p in pos.items():
        if abs(p["shares"]) <= 1e-6:
            continue
        avg_price = p["cost"] / p["shares"]
        cur_price = latest_prices.get(code)
        prev_price = prev_prices.get(code)
        sign = 1 if p["shares"] > 0 else -1  # 숏 포지션은 가격이 내릴 때 이익이라 손익 부호를 뒤집는다
        eval_value = p["shares"] * cur_price if cur_price else None
        ret_pct = sign * (cur_price / avg_price - 1) * 100 if cur_price else None
        day_ret_pct = sign * (cur_price / prev_price - 1) * 100 if cur_price and prev_price else None
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
            "day_ret_pct": day_ret_pct,
        })

    stock_eval = sum(r["eval_value"] for r in rows if r["eval_value"])
    total_eval = stock_eval + cash

    for r in rows:
        r["weight_pct"] = (r["eval_value"] / total_eval * 100) if r["eval_value"] else None
    rows.sort(key=lambda r: r["eval_value"] or 0, reverse=True)

    # 리밸런싱 반올림으로 생기는 몇백~몇천원 수준의 부동소수점 잔여는 굳이 "현금" 행으로
    # 보여줄 필요가 없어서(총 투입자본의 0.01% 미만) 그보다 큰 경우에만 행을 추가한다 - 단
    # cash_mode="long_only"(롱숏 포트폴리오)는 지금 0이어도 "나중에 롱 쪽 리밸런싱하다 현금이
    # 생길 수 있으니 자리를 남겨달라"는 요청(2026-09-01)이 있어서 금액과 상관없이 항상 행을
    # 보여준다.
    if show_cash_row and (cash_mode == "long_only" or abs(display_cash) > TOTAL_CAPITAL * 0.0001):
        cash_label = "현금(롱 잔여)" if cash_mode == "long_only" else "현금"
        rows.append({
            "code": "-", "name": cash_label, "sector": "-", "shares": None, "avg_price": None,
            "cost_basis": display_cash, "cur_price": None, "eval_value": display_cash, "ret_pct": None,
            "day_ret_pct": None, "weight_pct": display_cash / total_eval * 100,
        })

    return rows, total_eval


def build_trade_history(trades, name_map):
    """매매일지를 종목별 누적 보유수량/원가 추적해서 각 매매가 신규 편입/비중 확대/비중 축소/전량
    편출 중 어디에 해당하는지 자동으로 라벨링한 리스트로 변환(최신순). HTML 렌더링과 엑셀 저장이
    공유. "편출"(전량 매도/전량 상환) 행에는 편입 시점부터 편출 시점까지의 누적 수익률
    (realized_ret_pct, 이동평균원가법 평균매수단가 대비 매도단가)도 같이 계산해서 남긴다.
    SHORT/COVER(공매도)도 지원 - compute_holdings_table과 동일한 부호 규약(shares 음수=숏)."""
    pos = {}  # code -> {"shares": x, "cost": y} - compute_holdings_table과 동일한 방식
    history = []
    for _, row in trades.sort_values(["date", "code"]).iterrows():
        code = row["code"]
        qty = row["amount"] / row["price"]
        p = pos.setdefault(code, {"shares": 0.0, "cost": 0.0})
        prev_shares = p["shares"]
        realized_ret_pct = None
        action = row["action"]
        if action == "BUY":
            p["shares"] += qty
            p["cost"] += row["amount"]
            label = "편입" if prev_shares <= 1e-6 else "비중 확대"
            color = "#ffa94d"
        elif action == "SHORT":
            p["shares"] -= qty
            p["cost"] -= row["amount"]
            label = "편입(숏)" if prev_shares >= -1e-6 else "비중 확대(숏)"
            color = "#ffa94d"
        elif action == "COVER":
            avg_cost_before = p["cost"] / p["shares"] if p["shares"] < -1e-6 else None
            if p["shares"] < 0:
                ratio = min(qty / abs(p["shares"]), 1.0)
                p["cost"] *= (1 - ratio)
                p["shares"] += qty
            else:
                p["shares"] += qty
            if p["shares"] >= -1e-6:
                label = "편출"
                if avg_cost_before:
                    realized_ret_pct = (1 - row["price"] / avg_cost_before) * 100  # 숏은 가격이 내려야 이익
            else:
                label = "비중 축소"
            color = "#4dabf7"
        else:  # SELL
            avg_cost_before = p["cost"] / p["shares"] if p["shares"] > 1e-6 else None
            if p["shares"] > 0:
                ratio = min(qty / p["shares"], 1.0)
                p["cost"] *= (1 - ratio)
                p["shares"] -= qty
            else:
                p["shares"] -= qty
            if p["shares"] <= 1e-6:
                label = "편출"
                if avg_cost_before:
                    realized_ret_pct = (row["price"] / avg_cost_before - 1) * 100
            else:
                label = "비중 축소"
            color = "#4dabf7"
        history.append({
            "date": row["date"],
            "code": code,
            "name": name_map.get(code, code),
            "label": label,
            "color": color,
            "price": row["price"],
            "qty": qty,
            "amount": row["amount"],
            "realized_ret_pct": realized_ret_pct,
        })
    history.sort(key=lambda h: h["date"], reverse=True)
    return history


def render_trade_history_html(history):
    if not history:
        return '<div style="color:#9aa0a6; font-size:12px;">매매 이력이 없습니다.</div>'

    rows_html = ""
    for h in history:
        ret = h.get("realized_ret_pct")
        if ret is None:
            ret_html = "-"
        else:
            ret_color = "#ff6b6b" if ret >= 0 else "#4dabf7"
            ret_html = f'<span style="color:{ret_color}">{ret:+.2f}%</span>'
        rows_html += f"""
        <tr>
          <td>{h['date'].strftime('%m/%d')}</td>
          <td>{h['name']}</td>
          <td style="color:{h['color']}">{h['label']}</td>
          <td>{h['amount']:,.0f}</td>
          <td>{ret_html}</td>
        </tr>"""
    return f"""<table>
        <thead><tr><th>날짜</th><th>종목</th><th>구분</th><th>금액(원)</th><th>편입~편출 누적수익률</th></tr></thead>
        <tbody>{rows_html}
        </tbody>
      </table>"""


def write_trade_history_xlsx(history, xlsx_path):
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    df = pd.DataFrame([{
        "날짜": h["date"].strftime("%Y-%m-%d"),
        "종목명": h["name"],
        "코드": h["code"],
        "구분": h["label"],
        "단가": h["price"],
        "수량": round(h["qty"], 4),
        "금액(원)": h["amount"],
        "편입~편출 누적수익률(%)": round(h["realized_ret_pct"], 2) if h.get("realized_ret_pct") is not None else None,
    } for h in history])
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="편입편출 히스토리", index=False)


def compute_twr_index(trades, prices_wide, kospi, kosdaq):
    """일별 TWR 지수(MP)와 코스피/코스닥(BM) 지수를 편입 첫날=BASE_INDEX로 리베이스해서 같이 반환.
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

    mp_index = [float(BASE_INDEX)]
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
    bm_kospi = [kospi.loc[d] / kospi_base * BASE_INDEX for d in dates_out]
    bm_kosdaq = []
    if kosdaq is not None:
        kosdaq_base = kosdaq.loc[dates_out[0]]
        bm_kosdaq = [kosdaq.loc[d] / kosdaq_base * BASE_INDEX for d in dates_out]

    return dates_out, mp_index, bm_kospi, bm_kosdaq


def compute_twr_index_ls(trades, prices_wide, bm_series):
    """롱숏 포트폴리오 전용(2026-08-31, "코스닥 롱숏" 추가) - compute_twr_index와 원리는 같은
    TWR 연쇄복리인데 두 가지가 다르다: (1) 벤치마크가 코스닥 하나뿐이라 단일 시리즈만 받는다,
    (2) SHORT/COVER를 지원한다 - shares가 음수인 숏 포지션도 v_start/v_end 계산식
    (cash + sum(shares*price))에 자연스럽게 녹아든다(공매도 대금은 SHORT 시점에 현금으로
    잡히고, COVER 시점에 현금에서 빠져나가므로 - 롱 매수/매도의 정확히 반대 방향).
    이 부호 규약 덕분에 v(t) = cash + 롱평가액 + 숏평가액(음수) 이 항상 TOTAL_CAPITAL 근처에서
    움직이는 순수한 시장중립형 펀드의 NAV가 되고, 비율 기반 TWR 수익률 계산이 그대로 유효하다
    (v_start/v_end가 0 근처로 안 가서 나눗셈이 안전함 - 롱 100%+숏 100% 구조라 항상 양수).
    추가로 NET EXPOSURE(그날 종가 기준, 롱비중+숏비중)를 매일 계산해서 함께 반환하는데, 이때
    EXPOSURE_BETA로 종목별 베타를 곱해서 보정한다(인버스ETF처럼 기초지수와 반대로 움직이는
    상품은 "매수했다"는 사실만으로는 실제 시장 방향성을 알 수 없음 - 2026-09-01, KODEX
    코스닥150선물인버스를 SHORT로 담으면 이중반전으로 오히려 코스닥 상승에 베팅하는 꼴이 되는
    버그를 사용자가 지적해서 BUY로 바꾸고, 대신 NET EXPOSURE 계산에서만 베타로 보정함).
    v(t)/실현손익 계산은 항상 실제 shares*price 그대로 쓴다(베타 보정 없음) - 매매/평가금액은
    이미 시장가로 정확히 반영되므로 이중 보정하면 안 됨."""
    inception = trades["date"].min()
    common_dates = set(prices_wide.index) & set(bm_series.index)
    all_dates = sorted(common_dates)
    all_dates = [d for d in all_dates if d >= inception]
    if not all_dates:
        return [], [], [], []

    codes = sorted(trades["code"].unique())
    shares = {c: 0.0 for c in codes}
    cash = TOTAL_CAPITAL
    trades_by_date = {d: g for d, g in trades.groupby("date")}

    def apply_trade(row):
        nonlocal cash
        qty = row["amount"] / row["price"]
        action = row["action"]
        if action == "BUY":
            shares[row["code"]] += qty
            cash -= row["amount"]
        elif action == "SELL":
            shares[row["code"]] -= qty
            cash += row["amount"]
        elif action == "SHORT":
            shares[row["code"]] -= qty
            cash += row["amount"]
        elif action == "COVER":
            shares[row["code"]] += qty
            cash -= row["amount"]

    def exposure_and_value(date, cash_val):
        exposure = 0.0
        exposure_adj = 0.0
        for c in codes:
            px = prices_wide.at[date, c]
            if not pd.isna(px) and shares[c] != 0:
                raw = shares[c] * px
                exposure += raw
                exposure_adj += raw * EXPOSURE_BETA.get(c, 1.0)
        return exposure, exposure_adj, cash_val + exposure

    mp_index = [float(BASE_INDEX)]
    dates_out = [all_dates[0]]
    net_exposure_out = []

    prev_date = all_dates[0]
    if prev_date in trades_by_date:
        for _, row in trades_by_date[prev_date].iterrows():
            apply_trade(row)
    prev_cash = cash
    _, exposure_adj0, v0 = exposure_and_value(prev_date, prev_cash)
    net_exposure_out.append(exposure_adj0 / v0 * 100 if v0 else 0.0)

    for d in all_dates[1:]:
        _, _, v_start = exposure_and_value(prev_date, prev_cash)
        _, _, v_end = exposure_and_value(d, prev_cash)
        r = v_end / v_start - 1 if v_start > 0 else 0.0
        mp_index.append(mp_index[-1] * (1 + r))
        dates_out.append(d)

        if d in trades_by_date:
            for _, row in trades_by_date[d].iterrows():
                apply_trade(row)
        prev_date = d
        prev_cash = cash

        _, exposure_adj_after, v_after = exposure_and_value(d, prev_cash)
        net_exposure_out.append(exposure_adj_after / v_after * 100 if v_after else 0.0)

    bm_base = bm_series.loc[dates_out[0]]
    bm_out = [bm_series.loc[d] / bm_base * BASE_INDEX for d in dates_out]

    return dates_out, mp_index, bm_out, net_exposure_out


def compute_mdd(index_series):
    """지수 시계열의 전체 기간 최대낙폭(MDD, %) - 그동안의 최고점 대비 현재 저점까지 최대
    하락폭. 항상 0 이하 값(하락이 없으면 0.0)."""
    if not index_series:
        return None
    peak = index_series[0]
    mdd = 0.0
    for v in index_series:
        if v > peak:
            peak = v
        dd = (v / peak - 1) * 100
        if dd < mdd:
            mdd = dd
    return mdd


def pct_return(level):
    """리베이스된 지수 레벨(BASE_INDEX 기준)을 편입일 대비 누적 수익률(%)로 변환."""
    return (level / BASE_INDEX - 1) * 100 if level is not None else None


def _period_start_idx(dates_out, days_back=None, prev_trading_day=False):
    """최근 N일(달력 기준) 또는 직전 거래일에 해당하는 dates_out 인덱스를 찾는다.
    데이터가 그 기간만큼 아직 안 쌓였으면 None."""
    if len(dates_out) < 2:
        return None
    if prev_trading_day:
        return -2
    target_date = pd.Timestamp(dates_out[-1]) - pd.Timedelta(days=int(days_back))
    candidates = [i for i, d in enumerate(dates_out) if d <= target_date]
    return candidates[-1] if candidates else None


def compute_period_return(dates_out, index_series, days_back=None, prev_trading_day=False):
    """최근 N일(달력 기준) 또는 직전 거래일 대비 지수 자체의 수익률(%)을 계산. 비율로
    계산해서 BASE_INDEX 값과 무관하게 항상 정확한 %가 나온다."""
    start_idx = _period_start_idx(dates_out, days_back, prev_trading_day)
    if start_idx is None:
        return None
    return (index_series[-1] / index_series[start_idx] - 1) * 100


def compute_period_alpha(dates_out, mp_index, bm_index, days_back=None, prev_trading_day=False):
    """최근 N일(달력 기준) 또는 직전 거래일 대비 MP수익률-BM수익률(초과성과, %p)을 계산."""
    start_idx = _period_start_idx(dates_out, days_back, prev_trading_day)
    if start_idx is None:
        return None
    mp_ret = mp_index[-1] / mp_index[start_idx] - 1
    bm_ret = bm_index[-1] / bm_index[start_idx] - 1
    return (mp_ret - bm_ret) * 100


def main(portfolio, other_portfolios):
    trades_path = portfolio["trades_path"]
    prices_path = portfolio["prices_path"]
    out_path = portfolio["out_path"]
    xlsx_path = portfolio["xlsx_path"]
    name = portfolio["name"]

    if not os.path.exists(trades_path):
        print(f"[{name}] 매매일지 파일이 없습니다:", trades_path)
        return
    trades = load_trades(trades_path)

    # data/krx_raw.csv(코스피 종가)는 메인 파이프라인이 매일 아침 07:30에만 갱신하는데, 그 시점엔
    # KRX가 아직 전날 종가만 발표한 상태라 당일 종가가 하루 늦게 반영된다(MP트래커는 종가 확정 후인
    # 17:30에 별도 실행되므로 종목별 현재가는 당일 반영되는데 코스피만 하루 밀리는 불일치가 있었음,
    # 2026-08-19 발견). 코스닥과 동일하게 네이버에서 라이브로 받아와서 날짜를 맞춘다.
    print(f"[{name}] 코스피 지수 수집 중...")
    kospi = fetch_index_history("KOSPI")
    print(f"[{name}] 코스닥 지수 수집 중...")
    kosdaq = fetch_index_history("KOSDAQ")

    nav_html = render_nav_html(portfolio, other_portfolios)

    if trades.empty:
        render_empty_page(portfolio, nav_html)
        print(f"[{name}] 아직 편입된 종목이 없습니다. 안내 페이지만 생성했습니다.")
        return

    name_map = dict(zip(trades["code"], trades["name"]))
    sector_map = dict(zip(trades["code"], trades["sector"]))

    if not os.path.exists(prices_path):
        print(f"[{name}] 경고: 종목 가격 데이터가 없습니다. fetch_troy_mp_prices.py를 먼저 실행하세요.")
        return
    prices = pd.read_csv(prices_path, dtype={"code": str}, parse_dates=["date"])
    prices["code"] = prices["code"].str.zfill(6)
    prices_wide = prices.pivot_table(index="date", columns="code", values="close").ffill()

    trades, _ = fill_missing_prices(trades, prices_wide, trades_path)

    latest_prices = {}
    prev_prices = {}
    for code in trades["code"].unique():
        if code in prices_wide.columns:
            s = prices_wide[code].dropna()
            if not s.empty:
                latest_prices[code] = float(s.iloc[-1])
            if len(s) >= 2:
                prev_prices[code] = float(s.iloc[-2])

    holdings, total_eval = compute_holdings_table(trades, latest_prices, prev_prices, name_map, sector_map)
    dates_out, mp_index, bm_kospi, bm_kosdaq = compute_twr_index(trades, prices_wide, kospi, kosdaq)

    dates_json = json.dumps([d.strftime("%Y-%m-%d") for d in dates_out])
    mp_json = json.dumps([round(v, 3) for v in mp_index])
    bm_kospi_json = json.dumps([round(v, 3) for v in bm_kospi])
    bm_kosdaq_json = json.dumps([round(v, 3) for v in bm_kosdaq])

    mp_latest = mp_index[-1] if mp_index else float(BASE_INDEX)
    bm_kospi_latest = bm_kospi[-1] if bm_kospi else float(BASE_INDEX)
    bm_kosdaq_latest = bm_kosdaq[-1] if bm_kosdaq else float(BASE_INDEX)
    mdd = compute_mdd(mp_index)

    def fmt_neon(v, suffix):
        """초과성과/자체 수익률 표 칸에 야광(neon) 색으로 강조해서 표시 - 양수는 네온 그린, 음수는 네온 핑크."""
        if v is None:
            return '<span style="color:#6b7280">N/A</span>'
        color = "#39ff14" if v >= 0 else "#ff2ec4"
        return f'<span style="color:{color}; text-shadow:0 0 6px {color}88;">{v:+.2f}{suffix}</span>'

    def fmt_alpha(v):
        return fmt_neon(v, "%p")

    def fmt_return(v):
        return fmt_neon(v, "%")

    alpha_periods = {}
    for bm_name, bm_series, bm_latest in [("kospi", bm_kospi, bm_kospi_latest), ("kosdaq", bm_kosdaq, bm_kosdaq_latest)]:
        alpha_periods[f"alpha_{bm_name}_total"] = fmt_alpha(pct_return(mp_latest) - pct_return(bm_latest))
        alpha_periods[f"alpha_{bm_name}_1d"] = fmt_alpha(
            compute_period_alpha(dates_out, mp_index, bm_series, prev_trading_day=True))
        alpha_periods[f"alpha_{bm_name}_1w"] = fmt_alpha(
            compute_period_alpha(dates_out, mp_index, bm_series, days_back=7))
        alpha_periods[f"alpha_{bm_name}_1m"] = fmt_alpha(
            compute_period_alpha(dates_out, mp_index, bm_series, days_back=30))

    # 포트폴리오 자체의 구간별 수익률(벤치마크 대비 초과성과가 아니라 순수 자체 수익률) -
    # 초과성과 표 옆에 나란히 보여주기 위함.
    own_periods = {
        "own_total": fmt_return(pct_return(mp_latest)),
        "own_1d": fmt_return(compute_period_return(dates_out, mp_index, prev_trading_day=True)),
        "own_1w": fmt_return(compute_period_return(dates_out, mp_index, days_back=7)),
        "own_1m": fmt_return(compute_period_return(dates_out, mp_index, days_back=30)),
    }

    # 리베이스된 BM지수 말고 실제 지수 값(포인트)도 참고용으로 하단에 표시한다. 코스닥은
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
    # 1일/누적 수익률 칸 둘 다 "지수 자체의" 수익률로 통일한다(한때 누적 칸에 MP초과성과를 넣었었는데,
    # 1일 칸은 지수 자체 등락률이라 같은 행 안에서 기준이 달라 헷갈린다는 피드백으로 되돌림- MP
    # 초과성과는 위쪽 "구간별 초과성과" 표에 이미 따로 있어서 여기서 중복 표시할 필요가 없음).
    kospi_day_ret = (bm_kospi[-1] / bm_kospi[-2] - 1) * 100 if len(bm_kospi) >= 2 else None
    kosdaq_day_ret = (bm_kosdaq[-1] / bm_kosdaq[-2] - 1) * 100 if len(bm_kosdaq) >= 2 else None

    holdings.append({
        "code": "-", "name": "코스피 지수(기준)", "sector": "-", "shares": None, "avg_price": None,
        "cost_basis": None, "cur_price": kospi_actual_latest, "eval_value": None,
        "ret_pct": pct_return(bm_kospi_latest),
        "day_ret_pct": kospi_day_ret, "weight_pct": None,
    })
    holdings.append({
        "code": "-", "name": "코스닥 지수(기준)", "sector": "-", "shares": None, "avg_price": None,
        "cost_basis": None, "cur_price": kosdaq_actual_latest, "eval_value": None,
        "ret_pct": pct_return(bm_kosdaq_latest),
        "day_ret_pct": kosdaq_day_ret, "weight_pct": None,
    })

    rows_html = ""
    for r in holdings:
        ret = r["ret_pct"]
        ret_str = "N/A" if ret is None else f"{ret:+.2f}%"
        ret_color = "#adb5bd" if ret is None else ("#ff6b6b" if ret >= 0 else "#4dabf7")
        day_ret = r.get("day_ret_pct")
        day_ret_str = "N/A" if day_ret is None else f"{day_ret:+.2f}%"
        day_ret_color = "#adb5bd" if day_ret is None else ("#ff6b6b" if day_ret >= 0 else "#4dabf7")
        cur_price_str = f"{r['cur_price']:,.0f}" if r["cur_price"] else "N/A"
        eval_value_str = f"{r['eval_value']:,.0f}" if r["eval_value"] is not None else "N/A"
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

    html = TEMPLATE.format(
        page_name=name,
        nav_html=nav_html,
        history_html=history_html,
        xlsx_name=xlsx_name,
        base_index=f"{BASE_INDEX:,}",
        **alpha_periods,
        **own_periods,
        dates_json=dates_json,
        mp_json=mp_json,
        bm_kospi_json=bm_kospi_json,
        bm_kosdaq_json=bm_kosdaq_json,
        mp_latest=f"{mp_latest:,.2f}",
        bm_kospi_latest=f"{bm_kospi_latest:,.2f}",
        bm_kosdaq_latest=f"{bm_kosdaq_latest:,.2f}",
        mdd=f"{mdd:.2f}%" if mdd is not None else "N/A",
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
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[{name}] 저장 완료: {out_path} (보유 {len(holdings)}종목, MP지수 {mp_latest:.2f} vs 코스피 {bm_kospi_latest:.2f} vs 코스닥 {bm_kosdaq_latest:.2f}, MDD {mdd:.2f}%)")


def render_nav_html(portfolio, other_portfolios):
    """여러 MP 포트폴리오 페이지 사이를 전환할 수 있는 탭 - 현재 페이지는 굵게, 나머지는 링크.
    탭 순서는 항상 ALL_PORTFOLIOS 등록 순서 그대로 고정한다(2026-09-01, 사용자 지적 - 예전엔
    "현재 페이지" 탭을 맨 앞으로 보내서 어느 페이지에 있느냐에 따라 탭 순서가 바뀌었음. 항상
    같은 자리에 같은 탭이 있어야 헷갈리지 않음). other_portfolios 인자는 하위호환을 위해
    시그니처만 남겨두고(호출부 수정 안 해도 되게) 실제로는 ALL_PORTFOLIOS 전체를 고정 순서로 쓴다."""
    items = []
    for p in ALL_PORTFOLIOS:
        label = p["name"]
        href = os.path.basename(p["out_path"])
        if p["id"] == portfolio["id"]:
            items.append(f'<span class="mp-tab active">{label}</span>')
        else:
            items.append(f'<a class="mp-tab" href="{href}">{label}</a>')
    return '<div class="mp-tabs">' + "".join(items) + '</div>'


EMPTY_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{page_name} 트래커</title>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  a.back {{ color:#4dabf7; font-size:13px; text-decoration:none; }}
  h1 {{ font-size:20px; margin:16px 0 4px 0; }}
  .note {{ color:#9aa0a6; font-size:13px; line-height:1.8; max-width:640px; background:#1a1d24; border-radius:10px; padding:20px 22px; margin-top:20px; }}
  code {{ background:#23262e; padding:1px 6px; border-radius:4px; }}
  .mp-tabs {{ margin:14px 0; }}
  .mp-tabs a, .mp-tabs span {{ display:inline-block; margin-right:8px; padding:6px 14px; border-radius:8px; font-size:13px; text-decoration:none; }}
  .mp-tabs a {{ color:#9aa0a6; background:#1a1d24; }}
  .mp-tabs span.active {{ color:#0f1115; background:#4dabf7; font-weight:bold; }}
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>{page_name} 트래커</h1>
  {nav_html}
  <div class="note">
    아직 편입된 종목이 없습니다.<br><br>
    매매일지 파일에 매매 행을 추가하면(date, code, name, action(BUY/SELL),
    price, amount) 다음 파이프라인 실행부터 자동으로 이 페이지에 반영됩니다.
  </div>
</body>
</html>
"""


def render_empty_page(portfolio, nav_html):
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(portfolio["out_path"], "w", encoding="utf-8") as f:
        f.write(EMPTY_TEMPLATE.format(page_name=portfolio["name"], nav_html=nav_html))


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
  .mp-tabs {{ position:sticky; top:0; z-index:100; margin:0 -24px 18px -24px; padding:14px 24px; background:#0f1115; border-bottom:1px solid #23262e; }}
  .mp-tabs a, .mp-tabs span {{ display:inline-block; margin-right:8px; padding:6px 14px; border-radius:8px; font-size:13px; text-decoration:none; }}
  .mp-tabs a {{ color:#9aa0a6; background:#1a1d24; }}
  .mp-tabs span.active {{ color:#0f1115; background:#4dabf7; font-weight:bold; }}
  .badges {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap:12px; margin-bottom:20px; max-width:820px; }}
  .table-row {{ display:flex; gap:16px; flex-wrap:wrap; margin-bottom:24px; }}
  table.alpha-table {{ max-width:600px; background:#1a1d24; border-radius:10px; margin-bottom:0; }}
  table.alpha-table th, table.alpha-table td {{ border-bottom:none; padding:10px 14px; }}
  table.alpha-table td:not(:first-child) {{ font-weight:bold; }}
  .badge {{ background:#1a1d24; border-radius:10px; padding:14px 16px; }}
  .badge .label {{ color:#9aa0a6; font-size:12px; }}
  .badge .value {{ font-size:20px; font-weight:bold; margin-top:4px; }}
  .badge.mp .value {{ color:#ff8787; }}
  .badge.bm .value {{ color:#4dabf7; }}
  .badge.alpha .value {{ color:#63e6be; }}
  .badge.mdd .value {{ color:#ff2ec4; }}
  .chart-wrap {{ height:420px; position:relative; max-width:1100px; margin-bottom:28px; }}
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
  {nav_html}
  <div class="updated">최종 갱신: {updated_at} &middot; 편입 시작일 {inception} (={base_index} 기준) &middot; 보유 {n_holdings}종목 &middot; 평가금액 합계 {total_eval}원</div>

  <div class="badges">
    <div class="badge mp"><div class="label">{page_name} 지수</div><div class="value">{mp_latest}</div></div>
    <div class="badge bm"><div class="label">코스피(BM) 지수</div><div class="value">{bm_kospi_latest}</div></div>
    <div class="badge bm"><div class="label">코스닥(BM) 지수</div><div class="value">{bm_kosdaq_latest}</div></div>
    <div class="badge mdd"><div class="label">MDD(전체기간 최대낙폭)</div><div class="value">{mdd}</div></div>
  </div>

  <div class="table-row">
    <table class="alpha-table">
      <thead><tr><th>포트폴리오 자체 수익률</th><th>총 누적(시작일~)</th><th>1일</th><th>1주일</th><th>1개월</th></tr></thead>
      <tbody>
        <tr><td>{page_name}</td><td>{own_total}</td><td>{own_1d}</td><td>{own_1w}</td><td>{own_1m}</td></tr>
      </tbody>
    </table>
    <table class="alpha-table">
      <thead><tr><th>구간별 초과성과</th><th>총 누적(시작일~)</th><th>1일</th><th>1주일</th><th>1개월</th></tr></thead>
      <tbody>
        <tr><td>vs 코스피</td><td>{alpha_kospi_total}</td><td>{alpha_kospi_1d}</td><td>{alpha_kospi_1w}</td><td>{alpha_kospi_1m}</td></tr>
        <tr><td>vs 코스닥</td><td>{alpha_kosdaq_total}</td><td>{alpha_kosdaq_1d}</td><td>{alpha_kosdaq_1w}</td><td>{alpha_kosdaq_1m}</td></tr>
      </tbody>
    </table>
  </div>

  <div class="chart-wrap"><canvas id="navChart"></canvas></div>

  <div class="main-row">
    <div class="holdings-col">
      <table>
        <thead><tr>
          <th>종목명</th><th>코드</th><th>섹터</th><th>평균매수단가</th><th>현재가</th><th>1일 수익률</th><th>누적 수익률</th><th>매입금액(잔액)</th><th>평가금액</th><th>비중</th>
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
    <b>포트폴리오 변경</b> — 매매일지에 행을 추가/수정하는 방식.
    파이프라인 실행마다 자동으로 반영됩니다. 단가(price)를 비워두면 그날 네이버 종가로 자동 채워집니다.<br><br>
    <b>현금</b> — 총 투입자본(10억원 기준) 중 종목에 배분되지 않은 나머지는 "현금"으로 잡아 수익률 0%로
    취급합니다(비중 계산의 분모에도 포함되어 종목별 비중이 왜곡되지 않습니다).<br><br>
    <b>지수 산출</b> — 일별 시간가중수익률(TWR) 연쇄복리. 그날의 매매는 그날 수익률에 영향을 주지 않고(매매는
    종가 체결 가정, 다음날 비중부터 반영) 순수하게 전일 보유 바스켓(현금 포함)의 가격변동만 반영합니다. 그래서
    신규 편입·비중 조절이 지수 레벨을 왜곡하지 않습니다(펀드 성과평가 TWR 방식과 동일).
    MP·코스피·코스닥(BM) 모두 편입 첫날을 {base_index}로 리베이스합니다.<br><br>
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
        {{ label: '{page_name}', data: mpIndex, borderColor: '#ff8787', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 2 }},
        {{ label: '코스피(BM)', data: bmKospiIndex, borderColor: '#4dabf7', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 2, borderDash: [5,3] }},
        {{ label: '코스닥(BM)', data: bmKosdaqIndex, borderColor: '#63e6be', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 2, borderDash: [2,3] }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ labels: {{ color: '#e6e6e6' }} }} }},
      scales: {{
        x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 12 }}, grid: {{ color: '#23262e' }} }},
        y: {{ title: {{ display: true, text: '지수(편입일={base_index})', color: '#9aa0a6' }}, ticks: {{ color: '#9aa0a6' }}, grid: {{ color: '#23262e' }} }},
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
    sessionStorage.setItem("mp_unlocked", "1");
    unlockPage();
  }} else {{
    document.getElementById("pw-error").textContent = "비밀번호가 틀렸습니다";
  }}
}}
document.getElementById("pw-submit").addEventListener("click", tryUnlock);
document.getElementById("pw-input").addEventListener("keydown", e => {{ if (e.key === "Enter") tryUnlock(); }});
if (sessionStorage.getItem("mp_unlocked") === "1") {{
  unlockPage();
}}
</script>
</body>
</html>
"""


def main_long_short(portfolio, other_portfolios):
    """롱숏 포트폴리오 전용 빌드 함수(2026-08-31, "코스닥 롱숏" 2종 추가하며 신설) - main()과
    구조는 같은데 벤치마크가 코스닥 하나뿐이고, compute_twr_index_ls(SHORT/COVER 지원 +
    NET EXPOSURE 계산)를 쓰고, MDD 배지가 추가된다는 점이 다르다. 기존 main()을 건드리면
    트로이 MP/모멘텀 MP(듀얼 벤치마크, 롱온리)에 영향이 갈 수 있어서 별도 함수로 분리했다."""
    trades_path = portfolio["trades_path"]
    prices_path = portfolio["prices_path"]
    out_path = portfolio["out_path"]
    xlsx_path = portfolio["xlsx_path"]
    name = portfolio["name"]

    if not os.path.exists(trades_path):
        print(f"[{name}] 매매일지 파일이 없습니다:", trades_path)
        return
    trades = load_trades(trades_path)

    print(f"[{name}] 코스닥 지수 수집 중...")
    kosdaq = fetch_index_history("KOSDAQ")

    nav_html = render_nav_html(portfolio, other_portfolios)

    if trades.empty:
        render_empty_page(portfolio, nav_html)
        print(f"[{name}] 아직 편입된 종목이 없습니다. 안내 페이지만 생성했습니다.")
        return

    name_map = dict(zip(trades["code"], trades["name"]))
    sector_map = dict(zip(trades["code"], trades["sector"]))

    if not os.path.exists(prices_path):
        print(f"[{name}] 경고: 종목 가격 데이터가 없습니다. fetch_troy_mp_prices.py를 먼저 실행하세요.")
        return
    prices = pd.read_csv(prices_path, dtype={"code": str}, parse_dates=["date"])
    prices["code"] = prices["code"].str.zfill(6)
    prices_wide = prices.pivot_table(index="date", columns="code", values="close").ffill()

    trades, _ = fill_missing_prices(trades, prices_wide, trades_path)

    latest_prices = {}
    prev_prices = {}
    for code in trades["code"].unique():
        if code in prices_wide.columns:
            s = prices_wide[code].dropna()
            if not s.empty:
                latest_prices[code] = float(s.iloc[-1])
            if len(s) >= 2:
                prev_prices[code] = float(s.iloc[-2])

    holdings, total_eval = compute_holdings_table(trades, latest_prices, prev_prices, name_map, sector_map, show_cash_row=True, cash_mode="long_only")
    dates_out, mp_index, bm_kosdaq, net_exposure = compute_twr_index_ls(trades, prices_wide, kosdaq)

    dates_json = json.dumps([d.strftime("%Y-%m-%d") for d in dates_out])
    mp_json = json.dumps([round(v, 3) for v in mp_index])
    bm_kosdaq_json = json.dumps([round(v, 3) for v in bm_kosdaq])
    net_exposure_json = json.dumps([round(v, 2) for v in net_exposure])

    mp_latest = mp_index[-1] if mp_index else float(BASE_INDEX)
    bm_kosdaq_latest = bm_kosdaq[-1] if bm_kosdaq else float(BASE_INDEX)
    net_exposure_latest = net_exposure[-1] if net_exposure else 0.0
    mdd = compute_mdd(mp_index)

    def fmt_neon(v, suffix):
        if v is None:
            return '<span style="color:#6b7280">N/A</span>'
        color = "#39ff14" if v >= 0 else "#ff2ec4"
        return f'<span style="color:{color}; text-shadow:0 0 6px {color}88;">{v:+.2f}{suffix}</span>'

    def fmt_alpha(v):
        return fmt_neon(v, "%p")

    def fmt_return(v):
        return fmt_neon(v, "%")

    alpha_periods = {
        "alpha_kosdaq_total": fmt_alpha(pct_return(mp_latest) - pct_return(bm_kosdaq_latest)),
        "alpha_kosdaq_1d": fmt_alpha(compute_period_alpha(dates_out, mp_index, bm_kosdaq, prev_trading_day=True)),
        "alpha_kosdaq_1w": fmt_alpha(compute_period_alpha(dates_out, mp_index, bm_kosdaq, days_back=7)),
        "alpha_kosdaq_1m": fmt_alpha(compute_period_alpha(dates_out, mp_index, bm_kosdaq, days_back=30)),
    }
    own_periods = {
        "own_total": fmt_return(pct_return(mp_latest)),
        "own_1d": fmt_return(compute_period_return(dates_out, mp_index, prev_trading_day=True)),
        "own_1w": fmt_return(compute_period_return(dates_out, mp_index, days_back=7)),
        "own_1m": fmt_return(compute_period_return(dates_out, mp_index, days_back=30)),
    }

    ref_date = dates_out[-1] if dates_out else None
    kosdaq_actual_latest = float(kosdaq.loc[ref_date]) if ref_date is not None and ref_date in kosdaq.index else None
    kosdaq_actual_date = ref_date.strftime("%Y-%m-%d") if ref_date is not None else "N/A"
    kosdaq_day_ret = (bm_kosdaq[-1] / bm_kosdaq[-2] - 1) * 100 if len(bm_kosdaq) >= 2 else None

    # 롱/숏을 표 자체를 나눠서 보여준다(2026-08-31, 사용자 요청 - 종목명 옆 [롱]/[숏] 태그보다
    # 롱 칸/숏 칸을 아예 분리하는 게 구분이 더 잘 됨). 코스닥 지수(기준) 참고행은 shares=None이라
    # 롱/숏 어느 쪽도 아니고, 별도로 작게 표시한다.
    def render_rows(rows):
        out = ""
        for r in rows:
            ret = r["ret_pct"]
            ret_str = "N/A" if ret is None else f"{ret:+.2f}%"
            ret_color = "#adb5bd" if ret is None else ("#ff6b6b" if ret >= 0 else "#4dabf7")
            day_ret = r.get("day_ret_pct")
            day_ret_str = "N/A" if day_ret is None else f"{day_ret:+.2f}%"
            day_ret_color = "#adb5bd" if day_ret is None else ("#ff6b6b" if day_ret >= 0 else "#4dabf7")
            cur_price_str = f"{r['cur_price']:,.0f}" if r["cur_price"] else "N/A"
            eval_value_str = f"{r['eval_value']:,.0f}" if r["eval_value"] is not None else "N/A"
            weight_str = f"{r['weight_pct']:.1f}%" if r["weight_pct"] is not None else "N/A"
            avg_price_str = f"{r['avg_price']:,.0f}" if r["avg_price"] is not None else "-"
            cost_basis_str = f"{r['cost_basis']:,.0f}" if r["cost_basis"] is not None else "-"
            out += f"""
        <tr>
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
        return out

    holdings.append({
        "code": "-", "name": "코스닥 지수(기준)", "sector": "-", "shares": None, "avg_price": None,
        "cost_basis": None, "cur_price": kosdaq_actual_latest, "eval_value": None,
        "ret_pct": pct_return(bm_kosdaq_latest),
        "day_ret_pct": kosdaq_day_ret, "weight_pct": None,
    })
    # 롱/숏 표 분류는 실제 매매 방향(shares 부호)이 아니라 "경제적 방향"(EXPOSURE_BETA 반영) 기준
    # - 인버스ETF는 매수(shares>0)해도 실질은 코스닥 하락 베팅(숏)이라 숏 표에 넣어달라는 요청
    # (2026-09-01). 매매/평가금액 자체는 그대로 BUY로 남아있고 표시 위치만 바뀐다.
    def effective_side(r):
        return (1 if r["shares"] > 0 else -1) * EXPOSURE_BETA.get(r["code"], 1.0)

    long_rows = [r for r in holdings if r["shares"] is not None and effective_side(r) > 0]
    short_rows = [r for r in holdings if r["shares"] is not None and effective_side(r) < 0]
    ref_rows = [r for r in holdings if r["shares"] is None]

    long_rows_html = render_rows(long_rows)
    short_rows_html = render_rows(short_rows)
    ref_rows_html = render_rows(ref_rows)

    history = build_trade_history(trades, name_map)
    history_html = render_trade_history_html(history)
    write_trade_history_xlsx(history, xlsx_path)
    xlsx_name = os.path.basename(xlsx_path)

    html = TEMPLATE_LS.format(
        page_name=name,
        nav_html=nav_html,
        history_html=history_html,
        xlsx_name=xlsx_name,
        base_index=f"{BASE_INDEX:,}",
        **alpha_periods,
        **own_periods,
        dates_json=dates_json,
        mp_json=mp_json,
        bm_kosdaq_json=bm_kosdaq_json,
        net_exposure_json=net_exposure_json,
        mp_latest=f"{mp_latest:,.2f}",
        bm_kosdaq_latest=f"{bm_kosdaq_latest:,.2f}",
        net_exposure_latest=f"{net_exposure_latest:+.1f}%",
        mdd=f"{mdd:.2f}%" if mdd is not None else "N/A",
        kosdaq_actual=f"{kosdaq_actual_latest:,.2f}" if kosdaq_actual_latest is not None else "N/A",
        kosdaq_actual_date=kosdaq_actual_date,
        inception=trades['date'].min().strftime('%Y-%m-%d'),
        n_holdings=len(holdings),
        n_long=len(long_rows),
        n_short=len(short_rows),
        total_eval=f"{total_eval:,.0f}",
        long_rows_html=long_rows_html,
        short_rows_html=short_rows_html,
        ref_rows_html=ref_rows_html,
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[{name}] 저장 완료: {out_path} (롱 {len(long_rows)}종목/숏 {len(short_rows)}종목, MP지수 {mp_latest:.2f} vs 코스닥 {bm_kosdaq_latest:.2f}, NET EXPOSURE {net_exposure_latest:+.1f}%, MDD {mdd:.2f}%)")


TEMPLATE_LS = """<!doctype html>
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
  .mp-tabs {{ position:sticky; top:0; z-index:100; margin:0 -24px 18px -24px; padding:14px 24px; background:#0f1115; border-bottom:1px solid #23262e; }}
  .mp-tabs a, .mp-tabs span {{ display:inline-block; margin-right:8px; padding:6px 14px; border-radius:8px; font-size:13px; text-decoration:none; }}
  .mp-tabs a {{ color:#9aa0a6; background:#1a1d24; }}
  .mp-tabs span.active {{ color:#0f1115; background:#4dabf7; font-weight:bold; }}
  .badges {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap:12px; margin-bottom:20px; max-width:820px; }}
  .table-row {{ display:flex; gap:16px; flex-wrap:wrap; margin-bottom:24px; }}
  table.alpha-table {{ max-width:600px; background:#1a1d24; border-radius:10px; margin-bottom:0; }}
  table.alpha-table th, table.alpha-table td {{ border-bottom:none; padding:10px 14px; }}
  table.alpha-table td:not(:first-child) {{ font-weight:bold; }}
  .badge {{ background:#1a1d24; border-radius:10px; padding:14px 16px; }}
  .badge .label {{ color:#9aa0a6; font-size:12px; }}
  .badge .value {{ font-size:20px; font-weight:bold; margin-top:4px; }}
  .badge.mp .value {{ color:#ff8787; }}
  .badge.bm .value {{ color:#4dabf7; }}
  .badge.exposure .value {{ color:#ffd43b; }}
  .badge.mdd .value {{ color:#ff2ec4; }}
  .chart-wrap {{ height:420px; position:relative; max-width:1100px; margin-bottom:28px; }}
  table {{ border-collapse: collapse; width:100%; font-size:13px; }}
  th, td {{ padding:8px 12px; text-align:right; border-bottom:1px solid #23262e; }}
  th:first-child, td:first-child {{ text-align:left; }}
  th:nth-child(2), td:nth-child(2) {{ text-align:left; color:#9aa0a6; }}
  th {{ color:#9aa0a6; font-weight:normal; font-size:12px; }}
  .main-row {{ display:flex; align-items:flex-start; gap:20px; flex-wrap:wrap; }}
  .holdings-col {{ flex:1 1 700px; max-width:1000px; }}
  .side-h {{ font-size:13px; font-weight:bold; margin:0 0 8px 0; padding:6px 10px; border-radius:6px; display:inline-block; }}
  .side-h.long {{ color:#39ff14; background:#39ff1414; }}
  .side-h.short {{ color:#ff2ec4; background:#ff2ec414; margin-top:24px; }}
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
  {nav_html}
  <div class="updated">최종 갱신: {updated_at} &middot; 편입 시작일 {inception} (={base_index} 기준) &middot; 보유 {n_holdings}종목 &middot; 평가금액 합계 {total_eval}원</div>

  <div class="badges">
    <div class="badge mp"><div class="label">{page_name} 지수</div><div class="value">{mp_latest}</div></div>
    <div class="badge bm"><div class="label">코스닥(BM) 지수</div><div class="value">{bm_kosdaq_latest}</div></div>
    <div class="badge exposure"><div class="label">NET EXPOSURE(롱비중+숏비중)</div><div class="value">{net_exposure_latest}</div></div>
    <div class="badge mdd"><div class="label">MDD(전체기간 최대낙폭)</div><div class="value">{mdd}</div></div>
  </div>

  <div class="table-row">
    <table class="alpha-table">
      <thead><tr><th>포트폴리오 자체 수익률</th><th>총 누적(시작일~)</th><th>1일</th><th>1주일</th><th>1개월</th></tr></thead>
      <tbody>
        <tr><td>{page_name}</td><td>{own_total}</td><td>{own_1d}</td><td>{own_1w}</td><td>{own_1m}</td></tr>
      </tbody>
    </table>
    <table class="alpha-table">
      <thead><tr><th>구간별 초과성과</th><th>총 누적(시작일~)</th><th>1일</th><th>1주일</th><th>1개월</th></tr></thead>
      <tbody>
        <tr><td>vs 코스닥</td><td>{alpha_kosdaq_total}</td><td>{alpha_kosdaq_1d}</td><td>{alpha_kosdaq_1w}</td><td>{alpha_kosdaq_1m}</td></tr>
      </tbody>
    </table>
  </div>

  <div class="chart-wrap"><canvas id="navChart"></canvas></div>

  <div class="main-row">
    <div class="holdings-col">
      <h3 class="side-h long">롱 ({n_long}종목)</h3>
      <table>
        <thead><tr>
          <th>종목명</th><th>코드</th><th>섹터</th><th>평균매수단가</th><th>현재가</th><th>1일 수익률</th><th>누적 수익률</th><th>매입금액(잔액)</th><th>평가금액</th><th>비중</th>
        </tr></thead>
        <tbody>{long_rows_html}
        </tbody>
      </table>

      <h3 class="side-h short">숏 ({n_short}종목)</h3>
      <table>
        <thead><tr>
          <th>종목명</th><th>코드</th><th>섹터</th><th>평균진입단가</th><th>현재가</th><th>1일 수익률</th><th>누적 수익률</th><th>상환금액(잔액)</th><th>평가금액</th><th>비중</th>
        </tr></thead>
        <tbody>{short_rows_html}
        </tbody>
      </table>

      <table style="margin-top:16px;">
        <tbody>{ref_rows_html}
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
    <b>포트폴리오 변경</b> — 매매일지에 행을 추가/수정하는 방식(action: BUY/SELL=롱 진입·청산,
    SHORT/COVER=숏 진입·상환). 단가(price)를 비워두면 그날 네이버 종가로 자동 채워집니다.<br><br>
    <b>비중·평가금액</b> — 종목을 롱 표/숏 표로 나눠서 보여줍니다. 숏 표의 비중/평가금액은
    음수로 표시됩니다(공매도 익스포저). 평균진입단가·수익률은 숏의 경우 부호를 뒤집어 계산합니다
    (가격이 내려야 이익).<br><br>
    <b>NET EXPOSURE</b> — 롱 비중 합 + 숏 비중 합(둘 다 이미 부호가 반영돼 있어 그냥 더하면 됨).
    0%면 롱숏 익스포저가 정확히 상쇄된 시장중립 상태, +면 순매수(롱 과다), -면 순매도(숏 과다)
    포지션임을 뜻합니다. 차트에 매일 값이 오른쪽 축(%)으로 같이 표시됩니다. 단, 인버스ETF처럼
    기초지수와 반대로 움직이는 상품은 "매수(BUY)"로 담겨 있어도(공매도로 담으면 이중반전되어
    버그가 됨) NET EXPOSURE 계산에서는 -1배로 보정해서 실제 시장 방향성(코스닥 하락 베팅)이
    정확히 반영되도록 합니다 - 매입금액/평가금액/수익률 자체는 보정 없이 실제 매수 그대로입니다.
    <br><br>
    <b>MDD</b> — 편입 시작일 이후 지금까지 지수가 직전 최고점 대비 가장 많이 빠졌던 낙폭(%).<br><br>
    <b>지수 산출</b> — 일별 시간가중수익률(TWR) 연쇄복리(v = 현금 + 롱평가액 + 숏평가액). 그날의
    매매는 그날 수익률에 영향을 주지 않고(매매는 종가 체결 가정, 다음날 비중부터 반영) 순수하게
    전일 보유 바스켓(롱+숏+현금)의 가격변동만 반영합니다. MP·코스닥(BM) 모두 편입 첫날을
    {base_index}로 리베이스합니다.
  </div>

  <div class="badges" style="margin-top:16px;">
    <div class="badge bm"><div class="label">코스닥 실제 지수({kosdaq_actual_date})</div><div class="value">{kosdaq_actual}</div></div>
  </div>
  </div>

<script>
const dates = {dates_json};
const mpIndex = {mp_json};
const bmKosdaqIndex = {bm_kosdaq_json};
const netExposure = {net_exposure_json};

function initChart() {{
  if (window.__navChartInited) return;
  window.__navChartInited = true;
  new Chart(document.getElementById('navChart').getContext('2d'), {{
    type: 'line',
    data: {{
      labels: dates,
      datasets: [
        {{ label: '{page_name}', data: mpIndex, borderColor: '#ff8787', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 2, yAxisID: 'y' }},
        {{ label: '코스닥(BM)', data: bmKosdaqIndex, borderColor: '#4dabf7', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 2, borderDash: [5,3], yAxisID: 'y' }},
        {{ label: 'NET EXPOSURE(%, 우측축)', data: netExposure, borderColor: '#ffd43b', backgroundColor: 'transparent', tension: 0.1, pointRadius: 0, borderWidth: 1.5, borderDash: [2,2], yAxisID: 'y1' }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ labels: {{ color: '#e6e6e6' }} }} }},
      scales: {{
        x: {{ ticks: {{ color: '#9aa0a6', maxTicksLimit: 12 }}, grid: {{ color: '#23262e' }} }},
        y: {{ position: 'left', title: {{ display: true, text: '지수(편입일={base_index})', color: '#9aa0a6' }}, ticks: {{ color: '#9aa0a6' }}, grid: {{ color: '#23262e' }} }},
        y1: {{ position: 'right', title: {{ display: true, text: 'NET EXPOSURE(%)', color: '#9aa0a6' }}, ticks: {{ color: '#9aa0a6' }}, grid: {{ display: false }} }},
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
    sessionStorage.setItem("mp_unlocked", "1");
    unlockPage();
  }} else {{
    document.getElementById("pw-error").textContent = "비밀번호가 틀렸습니다";
  }}
}}
document.getElementById("pw-submit").addEventListener("click", tryUnlock);
document.getElementById("pw-input").addEventListener("keydown", e => {{ if (e.key === "Enter") tryUnlock(); }});
if (sessionStorage.getItem("mp_unlocked") === "1") {{
  unlockPage();
}}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    # 탭 네비게이션은 종류(롱온리/롱숏) 상관없이 등록된 MP 포트폴리오 전부를 보여준다 - 사용자가
    # "왔다갔다 볼 수 있게" 요청(2026-08-31)해서 그룹을 나누지 않고 ALL_PORTFOLIOS 전체를 씀.
    for p in PORTFOLIOS:
        others = [o for o in ALL_PORTFOLIOS if o is not p]
        main(p, others)
    for p in LONG_SHORT_PORTFOLIOS:
        others = [o for o in ALL_PORTFOLIOS if o is not p]
        main_long_short(p, others)
