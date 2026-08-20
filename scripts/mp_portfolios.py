"""
MP(모델 포트폴리오) 트래커들의 공통 설정. fetch_troy_mp_prices.py / build_troy_mp_page.py가
이 리스트를 순회하면서 포트폴리오별로 파일을 읽고 페이지를 만든다 - 포트폴리오를 새로 하나
추가하고 싶으면 이 파일의 PORTFOLIOS에 항목 하나만 추가하면 된다(2026-08-20, "모멘텀 MP" 추가하며
기존 "트로이 MP" 전용 하드코딩을 여기로 일반화).
"""
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
DOWNLOADS_DIR = os.path.join(DOCS_DIR, "downloads")

# 지수 리베이스 기준값. 원래 100이었는데("편입일=100") 2026-08-20에 사용자 요청으로 10000으로
# 변경 - 두 포트폴리오 모두 이 값을 쓴다.
BASE_INDEX = 10000

TOTAL_CAPITAL = 1_000_000_000  # 총 투입자본(원) - 종목별 매입금액 합계 + 남는 건 현금으로 취급

PORTFOLIOS = [
    {
        "id": "troy_mp",
        "name": "트로이 MP",
        "trades_path": os.path.join(DATA_DIR, "manual", "troy_mp_trades.csv"),
        "prices_path": os.path.join(DATA_DIR, "troy_mp_prices.csv"),
        "out_path": os.path.join(DOCS_DIR, "troy_mp.html"),
        "xlsx_path": os.path.join(DOWNLOADS_DIR, "troy_mp_history.xlsx"),
    },
    {
        "id": "momentum_mp",
        "name": "모멘텀 MP",
        "trades_path": os.path.join(DATA_DIR, "manual", "momentum_mp_trades.csv"),
        "prices_path": os.path.join(DATA_DIR, "momentum_mp_prices.csv"),
        "out_path": os.path.join(DOCS_DIR, "momentum_mp.html"),
        "xlsx_path": os.path.join(DOWNLOADS_DIR, "momentum_mp_history.xlsx"),
    },
]
