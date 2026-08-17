"""
"조정 경고 지표" 발동 여부만 한눈에 보여주는 페이지(docs/risk_signals.html).

2026-08-17 대화에서 유동성 지표 + 볼린저밴드 돌파종목수를 조합해 과거 -15%+ 조정(2010년~)을
잡아낼 수 있는지 백테스트한 결과: 여러 지표가 "동시에" 켜져야 한다는 식(AND)으로 설계하면
오히려 놓치고, 지표마다 잡아내는 조정의 성격이 달랐다(현금성자산 지표 vs 시장폭 지표).
그래서 복합 점수 하나로 합치지 않고, 지표별로 "지금 발동 중인지"만 개별 표시한다(OR 방식 -
아무거나 하나라도 지속적으로 발동되면 눈에 띄게).

발동 기준: 최근 500거래일 기준 하위/상위 20%(방향은 지표마다 다름) 백분위 진입.
"""
import json
import os
from datetime import datetime

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
MERGED_PATH = os.path.join(DATA_DIR, "merged.csv")
BOLLINGER_PATH = os.path.join(DATA_DIR, "bollinger_prices.csv")
OUT_PATH = os.path.join(DOCS_DIR, "risk_signals.html")

Z_WINDOW = 500
FLAG_PCTL = 0.20

# (컬럼, 방향('low'=낮을수록 위험/'high'=높을수록 위험), 표시 라벨, 설명, 카테고리)
# 유동성 대시보드 CURATED_ONLY_LABELS(build_dashboard.py)에서 고른 지표 전부 - 처음엔 6개만
# 테스트해봤는데(2026-08-17), 나머지가 별로여서 뺀 게 아니라 그냥 아직 안 넣어본 거였어서 전부 추가.
#
# 카테고리 구분(사용자 요청, 2026-08-17): 이 15개는 전부 "유동성/시장내부" 계열이다 - 자금
# 흐름(자금), 시장 참여폭(시장폭), 심리·안전자산(심리) 3그룹으로 나눴다. 코로나 같은 진짜
# 외부충격(팬데믹/전쟁/지정학 등)은 이 지표들이 성격상 원래 못 잡는다(2020-01 백테스트에서
# 실증 확인) - 이건 "네 번째 카테고리"가 아니라 이 지표 세트 전체의 구조적 한계라서 카테고리로
# 안 나누고 페이지 하단에 별도로 명시한다.
CAT_FUNDING = "자금·유동성"
CAT_BREADTH = "시장폭"
CAT_SENTIMENT = "심리·안전자산"

INDICATORS = [
    ("breakout_ma20", "low", "볼린저밴드 돌파종목수(20일선)", "코스피 전종목 중 20일 상단밴드를 돌파한 종목 수 - 시장 내부 폭", CAT_BREADTH),
    ("kospi_adr", "low", "코스피 ADR", "최근 20거래일 상승/하락 종목수 비율 - 시장 폭", CAT_BREADTH),
    ("kosdaq_adr", "low", "코스닥 ADR", "코스닥판 ADR - 중소형주 시장 폭", CAT_BREADTH),
    ("dry_powder_qoq", "low", "실탄합계(예탁금+CMA) QoQ", "투자자예탁금+CMA잔고 합계의 분기 증가율", CAT_FUNDING),
    ("cma_balance_qoq", "low", "CMA잔고 QoQ", "CMA(수시입출금형 단기자금) 잔고 분기 증가율", CAT_FUNDING),
    ("cma_balance_5w", "low", "CMA잔고 5주선", "CMA잔고 원값의 5주(35일) 이동평균", CAT_FUNDING),
    ("broker_rp_balance_yoy", "low", "대고객RP매도잔고 YoY", "증권사가 고객에게 판 RP 잔고 - 딜러 유동성 프록시", CAT_FUNDING),
    ("rp_sale_balance_60w", "low", "RP매각잔고 60주선", "RP매각잔고 원값의 60주(420일) 이동평균", CAT_FUNDING),
    ("fed_total_assets_qoq", "low", "연준총자산 QoQ", "연준 대차대조표 축소(QT)=유동성 감소", CAT_FUNDING),
    ("us_reverse_repo_qoq", "high", "ON RRP QoQ", "익일역레포 급증=자금이 시장 대신 연준에 묶임(유동성 이탈)", CAT_FUNDING),
    ("us_reverse_repo_yoy", "high", "ON RRP YoY", "ON RRP QoQ와 같은 논리, 연율 기준", CAT_FUNDING),
    ("japan_ust_holdings", "low", "일본 미국채 보유액", "일본이 미국채를 팔면(보유액 감소) 글로벌 유동성 압박 신호로 흔히 해석", CAT_FUNDING),
    ("gdx_close", "low", "GDX(금광기업ETF)", "하락 시작 = 유동성 축소 신호로 검증됨(2008년 등)", CAT_SENTIMENT),
    ("gold_usd", "low", "금가격", "GDX와 같은 논리 - 하락 시작이 위험 신호", CAT_SENTIMENT),
    ("news_sentiment_12w", "low", "뉴스심리지수 12주선", "한국은행 뉴스심리지수의 12주(84일) 이동평균", CAT_SENTIMENT),
]


def main():
    if not os.path.exists(MERGED_PATH):
        print("merged.csv가 없습니다. build_dashboard.py를 먼저 실행하세요.")
        return
    merged = pd.read_csv(MERGED_PATH, parse_dates=["date"])

    if os.path.exists(BOLLINGER_PATH):
        long_df = pd.read_csv(BOLLINGER_PATH, dtype={"date": str, "code": str})
        sub = long_df[long_df["market"] == "KOSPI"]
        wide = sub.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
        bb_mean = wide.rolling(20, min_periods=20).mean()
        bb_std = wide.rolling(20, min_periods=20).std()
        breakout = (wide > bb_mean + 2 * bb_std).sum(axis=1)
        breakout = breakout[bb_mean.notna().any(axis=1)]
        ma20 = breakout.rolling(20, min_periods=1).mean()
        bb_df = pd.DataFrame({"date": pd.to_datetime(ma20.index), "breakout_ma20": ma20.values})
        merged = merged.merge(bb_df, on="date", how="left")
    else:
        merged["breakout_ma20"] = None

    merged["dry_powder_qoq"] = merged["dry_powder"].pct_change(63) * 100
    merged["cma_balance_qoq"] = merged["cma_balance"].pct_change(63) * 100
    merged["broker_rp_balance_yoy"] = merged["broker_rp_balance"].pct_change(252) * 100
    merged["cma_balance_5w"] = merged["cma_balance"].rolling(35, min_periods=20).mean()
    merged["rp_sale_balance_60w"] = merged["rp_sale_balance"].rolling(420, min_periods=100).mean()
    merged["fed_total_assets_qoq"] = merged["fed_total_assets_bil"].pct_change(63) * 100
    merged["us_reverse_repo_qoq"] = merged["us_reverse_repo"].pct_change(63) * 100
    merged["us_reverse_repo_yoy"] = merged["us_reverse_repo"].pct_change(252) * 100
    merged["news_sentiment_12w"] = merged["news_sentiment_index"].rolling(84, min_periods=40).mean()

    merged = merged.sort_values("date").reset_index(drop=True)

    rows = []
    for col, direction, label, desc, category in INDICATORS:
        if col not in merged.columns:
            continue
        s = merged[col]
        pctl = s.rolling(Z_WINDOW, min_periods=100).apply(lambda x: x.rank(pct=True).iloc[-1], raw=False)
        flagged = (pctl <= FLAG_PCTL) if direction == "low" else (pctl >= (1 - FLAG_PCTL))

        valid = flagged.dropna()
        if valid.empty:
            continue
        # 연속 발동일수(최신 상태가 발동중이면 며칠째인지)
        consecutive = 0
        for v in valid.values[::-1]:
            if v:
                consecutive += 1
            else:
                break

        latest_idx = s.last_valid_index()
        latest_val = s.loc[latest_idx] if latest_idx is not None else None
        latest_date = merged.loc[latest_idx, "date"].strftime("%Y-%m-%d") if latest_idx is not None else None
        is_flagged = bool(valid.iloc[-1])

        rows.append({
            "label": label, "desc": desc, "category": category, "flagged": is_flagged,
            "consecutive_days": consecutive if is_flagged else 0,
            "latest_value": round(float(latest_val), 2) if latest_val is not None else None,
            "latest_date": latest_date,
            "percentile": round(float(pctl.dropna().iloc[-1]) * 100, 1) if pctl.dropna().shape[0] else None,
        })

    n_flagged = sum(1 for r in rows if r["flagged"])
    html = TEMPLATE.format(
        rows_json=json.dumps(rows, ensure_ascii=False),
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        n_flagged=n_flagged,
        n_total=len(rows),
    )
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"저장 완료: {OUT_PATH} ({n_flagged}/{len(rows)}개 발동 중)")


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>조정 경고 신호</title>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  a.back {{ color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }}
  h1 {{ font-size:20px; margin:8px 0 4px 0; }}
  .updated {{ color:#9aa0a6; font-size:13px; margin-bottom:24px; }}
  .summary {{ font-size:15px; margin-bottom:20px; }}
  .summary b {{ font-size:22px; }}
  .cards {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:14px; max-width:1100px; }}
  .card {{ background:#1a1d24; border-radius:12px; padding:18px 20px; border:1px solid #23262e; }}
  .card.on {{ border-color:#ff6b6b; background:#241a1a; }}
  .card .top {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }}
  .card .name {{ font-size:15px; font-weight:bold; }}
  .badge {{ font-size:12px; padding:3px 10px; border-radius:999px; font-weight:bold; }}
  .badge.on {{ background:#ff6b6b; color:#0f1115; }}
  .badge.off {{ background:#2a2e37; color:#9aa0a6; }}
  .card .desc {{ color:#9aa0a6; font-size:12px; line-height:1.5; margin-bottom:10px; }}
  .card .meta {{ font-size:12px; color:#c7cbd1; display:flex; justify-content:space-between; }}
  .category-title {{ font-size:14px; color:#9aa0a6; margin:24px 0 10px 0; }}
  .category-title:first-of-type {{ margin-top:8px; }}
  .note {{ color:#9aa0a6; font-size:12px; line-height:1.7; max-width:900px; background:#1a1d24; border-radius:10px; padding:16px 18px; margin-top:24px; }}
  .note.warn {{ border:1px solid #ffa94d; }}
  .note b {{ color:#ffa94d; }}
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>조정 경고 신호</h1>
  <div class="updated">최종 갱신: {updated_at}</div>
  <div class="summary">현재 <b id="nFlagged">{n_flagged}</b> / {n_total}개 지표 발동 중</div>

  <div id="grouped"></div>

  <div class="note warn">
    <b>이 지표들이 못 잡는 것</b> — 여기 15개는 전부 "유동성/시장내부" 계열입니다(자금 흐름,
    시장 참여폭, 심리·안전자산). 코로나(2020-01) 같은 <b>진짜 외부충격형 조정(팬데믹, 전쟁,
    지정학 리스크 등)은 실제 백테스트에서 15개 중 단 하나도 사전 경고를 주지 못했습니다</b> -
    이건 지표 설계의 결함이 아니라, 유동성 지표가 애초에 다루는 영역이 아니기 때문입니다.
    이 페이지가 전부 정상이어도 외부충격형 조정은 언제든 올 수 있습니다.
  </div>

  <div class="note">
    <b>발동 기준</b> — 최근 500거래일 기준 하위(또는 상위) 20% 백분위 진입. 지표마다 잡아내는
    조정의 성격이 달라서(자금·유동성 지표 vs 시장폭 지표 vs 심리 지표) 여러 개를 합쳐 하나의
    점수로 안 만들고, 각각 개별적으로 표시합니다 - 아무거나 하나라도 여러 날 지속되면 눈여겨볼
    필요가 있습니다.
  </div>

<script>
const ROWS = {rows_json};
const CATEGORY_ORDER = ['자금·유동성', '시장폭', '심리·안전자산'];
const grouped = document.getElementById('grouped');

function cardHtml(r) {{
  return `
  <div class="card ${{r.flagged ? 'on' : ''}}">
    <div class="top">
      <span class="name">${{r.label}}</span>
      <span class="badge ${{r.flagged ? 'on' : 'off'}}">${{r.flagged ? '발동중' : '정상'}}</span>
    </div>
    <div class="desc">${{r.desc}}</div>
    <div class="meta">
      <span>최신값 ${{r.latest_value ?? 'N/A'}} (${{r.latest_date ?? 'N/A'}})</span>
      <span>${{r.flagged ? r.consecutive_days + '일째' : '백분위 ' + r.percentile + '%'}}</span>
    </div>
  </div>`;
}}

grouped.innerHTML = CATEGORY_ORDER.map(cat => {{
  const items = ROWS.filter(r => r.category === cat);
  if (!items.length) return '';
  const nOn = items.filter(r => r.flagged).length;
  return `<div class="category-title">${{cat}} (${{nOn}}/${{items.length}} 발동)</div>
  <div class="cards">${{items.map(cardHtml).join('')}}</div>`;
}}).join('');
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
