"""
docs/index.html(홈/랜딩 페이지)을 만든다. GitHub Pages는 docs/index.html을 루트로 서빙하므로
이 파일이 실제 홈페이지가 되고, 유동성 대시보드(liquidity.html)와 주식 스크리닝(screening.html)은
여기서 링크로 들어간다.
"""
import os
from datetime import datetime

DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
OUT_PATH = os.path.join(DOCS_DIR, "index.html")

TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>국내 리서치팀 데이터 확인</title>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0;
    padding:60px 24px; display:flex; flex-direction:column; align-items:center; }}
  h1 {{ font-size:26px; margin:0 0 8px 0; }}
  .updated {{ color:#9aa0a6; font-size:13px; margin-bottom:48px; }}
  .cards {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap:24px; width:100%; max-width:760px; }}
  a.card {{ display:block; background:#1a1d24; border:1px solid #23262e; border-radius:14px; padding:28px 24px;
    text-decoration:none; color:inherit; transition:border-color 0.15s, transform 0.15s; }}
  a.card:hover {{ border-color:#4dabf7; transform:translateY(-2px); }}
  .card .icon {{ font-size:28px; margin-bottom:12px; }}
  .card h2 {{ font-size:18px; margin:0 0 8px 0; }}
  .card p {{ font-size:13px; color:#9aa0a6; line-height:1.6; margin:0; }}
  .card .arrow {{ color:#4dabf7; font-size:13px; margin-top:16px; }}
</style>
</head>
<body>
  <h1>국내 리서치팀 데이터 확인</h1>
  <div class="updated">최종 갱신일시: {updated_at}</div>
  <div class="cards">
    <a class="card" href="liquidity.html">
      <div class="icon">&#128200;</div>
      <h2>유동성 지표 보기</h2>
      <p>한국은행/KOFIA/KRX 기반 국내 증시 유동성 스크리닝 대시보드 - M2, 신용융자, 투자자예탁금, 수급주체 등.</p>
      <div class="arrow">바로가기 &rarr;</div>
    </a>
    <a class="card" href="screening.html">
      <div class="icon">&#128202;</div>
      <h2>주식 스크리닝</h2>
      <p>상장사 전체 대상 실적 트래킹 - 영업이익 증가율/PER 밴드 위치로 직접 필터링, 분기 실적·PER/PBR 밴드 확인.</p>
      <div class="arrow">바로가기 &rarr;</div>
    </a>
    <a class="card" href="per_tracker.html">
      <div class="icon">&#128201;</div>
      <h2>코스피 선행 PER 트래커</h2>
      <p>코스피 시총 상위 50종목의 후행/당해선행(2026E)/차년선행(2027E) PER을 시가총액 가중으로 집계 - 코스피 지수와 함께 추적.</p>
      <div class="arrow">바로가기 &rarr;</div>
    </a>
    <a class="card" href="kosdaq_per_tracker.html">
      <div class="icon">&#128201;</div>
      <h2>코스닥 선행 PER 트래커</h2>
      <p>동일한 방식으로 코스닥 시총 상위 50종목 기준 선행/후행 PER을 집계 - 코스닥 지수와 함께 추적.</p>
      <div class="arrow">바로가기 &rarr;</div>
    </a>
    <a class="card" href="troy_mp.html">
      <div class="icon">&#128181;</div>
      <h2>트로이 MP 트래커</h2>
      <p>리서치팀 모델 포트폴리오 - 종목별 매수단가/수익률과 코스피 대비 초과성과(TWR 지수, 편입일=100)를 추적.</p>
      <div class="arrow">바로가기 &rarr;</div>
    </a>
    <a class="card" href="crack_spread.html">
      <div class="icon">&#9981;</div>
      <h2>정유화학 정제마진 트래커</h2>
      <p>EIA 공식 데이터 기반 3:2:1 크랙 스프레드(WTI/휘발유/경유) - 2006년~ 일별 추이, 엑셀 다운로드 지원.</p>
      <div class="arrow">바로가기 &rarr;</div>
    </a>
    <a class="card" href="op_band.html">
      <div class="icon">&#128200;</div>
      <h2>OP밴드 트래커</h2>
      <p>전종목 시가총액 대비 영업이익 추정치 배수 밴드 - 최신 배수/과거 대비 백분위로 저평가 종목 필터링.</p>
      <div class="arrow">바로가기 &rarr;</div>
    </a>
    <a class="card" href="bollinger_breakout.html">
      <div class="icon">&#128300;</div>
      <h2>볼린저밴드 돌파 종목수</h2>
      <p>코스피·코스닥 전종목 20일 볼린저밴드 상단 돌파 종목수(막대) + 5일선, 지수와 비교.</p>
      <div class="arrow">바로가기 &rarr;</div>
    </a>
  </div>
</body>
</html>
"""


def main():
    html = TEMPLATE.format(updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"))
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"저장 완료: {OUT_PATH}")


if __name__ == "__main__":
    main()
