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
    {
        "id": "mingu_mp",
        "name": "민구MP",
        "trades_path": os.path.join(DATA_DIR, "manual", "mingu_mp_trades.csv"),
        "prices_path": os.path.join(DATA_DIR, "mingu_mp_prices.csv"),
        "out_path": os.path.join(DOCS_DIR, "mingu_mp.html"),
        "xlsx_path": os.path.join(DOWNLOADS_DIR, "mingu_mp_history.xlsx"),
    },
]

# 롱숏(공매도 포함) 포트폴리오 - 롱온리(PORTFOLIOS)와 계산 방식(TWR + SHORT/COVER + NET
# EXPOSURE + MDD, 벤치마크는 코스닥 하나뿐)이 달라서 build_troy_mp_page.py의 main_long_short()가
# 따로 처리한다(2026-08-31, "코스닥 롱숏" 2종 추가 -> 2026-09-10, "코스닥 롱숏(개별종목)"은
# 삭제하고 "코스닥 롱숏(지수)"만 남김. 개별종목의 과거 이력은 data/manual/archive_20260910/에
# 백업 보존).
# 주의(2026-09-01): 롱/숏 목표비중 합이 정확히 100%인 경우, 종목별 정수 주식수를 "반올림"으로
# 잡으면 몇몇 종목이 위로 반올림되면서 총합이 10억원(TOTAL_CAPITAL)을 살짝 넘어갈 수 있다
# (사용자가 "롱 다 더하니 10억 넘는다"고 지적해서 발견). 그래서 이 포트폴리오는 반올림이 아니라
# "내림"(shares = floor(target/price))으로 잡아서 각 종목 매입금액이 목표치를 절대 넘지 않게
# 하고, 합계도 항상 10억원 이하가 되도록 한다. 목표비중 합이 100% 미만인 트로이/모멘텀/민구
# MP는 애초에 여유(현금)가 있어서 이 문제가 없다.
LONG_SHORT_PORTFOLIOS = [
    {
        "id": "kosdaq_long_short_index",
        "name": "코스닥 롱숏(지수)",
        "trades_path": os.path.join(DATA_DIR, "manual", "kosdaq_long_short_index_trades.csv"),
        "prices_path": os.path.join(DATA_DIR, "kosdaq_long_short_index_prices.csv"),
        "out_path": os.path.join(DOCS_DIR, "kosdaq_long_short_index.html"),
        "xlsx_path": os.path.join(DOWNLOADS_DIR, "kosdaq_long_short_index_history.xlsx"),
    },
]

# fetch_troy_mp_prices.py처럼 "포트폴리오 종류 상관없이 전부 순회"하고 싶을 때 쓰는 합친 리스트.
# 이 순서가 그대로 모든 MP 페이지 상단 탭 순서로 쓰인다(render_nav_html) - PORTFOLIOS/
# LONG_SHORT_PORTFOLIOS 등록 순서와 무관하게 여기서 최종 표시 순서를 정한다. 민구MP를 맨
# 마지막에 두고 싶다는 요청(2026-09-01)이 있어서 PORTFOLIOS 뒤에 그냥 이어붙이지 않고 직접 나열.
_BY_ID = {p["id"]: p for p in PORTFOLIOS + LONG_SHORT_PORTFOLIOS}
ALL_PORTFOLIOS = [_BY_ID[i] for i in [
    "troy_mp", "momentum_mp", "kosdaq_long_short_index", "mingu_mp",
]]

# 페이지 비밀번호(클라이언트 사이드 SHA-256 해시 - build_troy_mp_page.py 참고). 위 4개 MP는
# 전부 이 공통 해시를 쓴다(portfolio dict에 "pw_hash"가 없으면 이 값으로 폴백).
DEFAULT_PW_HASH = "03f1a9ee7721268c34ba420e058dd33d487bec8379c9dea6a997b6968400a60e"

# 비공개 개인 MP(2026-09-15 사용자 요청) - 의도적으로 ALL_PORTFOLIOS에 안 넣는다. render_nav_html이
# 항상 ALL_PORTFOLIOS만 순회해서 탭을 그리기 때문에, 여기 안 넣으면 트로이/모멘텀/코스닥롱숏/민구
# 어느 페이지의 nav에도 이 포트폴리오로 가는 링크가 안 생긴다(URL을 직접 아는 사람만 접근).
# 다른 4개와 다른 비밀번호(pw_hash)를 써서 공용 비밀번호로는 못 열어보게 한다.
# 주의: 이 레포는 public GitHub repo라 페이지 소스(및 트레이딩 로그 csv)는 URL/레포를 아는
# 누구나 볼 수 있다 - 비밀번호는 클라이언트 사이드 확인일 뿐 실제 접근 제어가 아니다.
PRIVATE_PORTFOLIOS = [
    {
        "id": "my_mp",
        "name": "마이 MP",
        "trades_path": os.path.join(DATA_DIR, "manual", "my_mp_trades.csv"),
        "prices_path": os.path.join(DATA_DIR, "my_mp_prices.csv"),
        "out_path": os.path.join(DOCS_DIR, "my_mp.html"),
        "xlsx_path": os.path.join(DOWNLOADS_DIR, "my_mp_history.xlsx"),
        "pw_hash": "f7c80e84aca1584a8596bbdc541ecb3c64758b7f567e4d2e1bf41433da34c7e9",
    },
]
