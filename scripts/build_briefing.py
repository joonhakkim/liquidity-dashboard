"""
아침 회의용 시장 브리핑(A4 2장) - 2026-10-16 사용자 요청("지금 상황 자체를 아침 회의에 쓸 수 있도록 요약해서 2페이지짜리 자동으로").
매일 아침 정규 갱신(run_pipeline)에서 시장 국면·섹터 이평선 페이지가 만들어진 뒤 실행:
  바탕화면 클로드\브리핑\시장브리핑_YYYYMMDD.pdf(크롬 헤드리스로 인쇄용 HTML을 PDF로)만 만든다 - 홈페이지엔 올리지 않음
  (2026-10-16 사용자 "홈페이지로 보지 말고 그냥 생성해서 나한테 주라"). HTML은 PDF 만들 때만 임시로 쓰고 지움.
1장 시장 판단: 국면·목표 비중·꺾임 주의, 지수, 국면 재료, 시장 폭(이평선 위 비율·8/15 교차), 밸류 바닥, 차트 2개.
2장 섹터: 4사분면(8일/15일 - 2026-10-20 사용자 "섹터 4사분면은 8일 15일인데 왜 브리핑은 이렇냐", 페이지와 같게), 섹터 1주 등락, 20주선 위 강·약 섹터와 1주 변화, 실적 가속 섹터, 자동 요약.
쓰는 데이터는 모두 이미 만들어진 산출물(같은 숫자가 각 페이지와 일치): market_phase_daily.csv, docs/sector_ma_breadth.html의 데이터,
op_band_summary.csv, sector_yoy_accel_summary_trimmed.csv, phase_warning_inputs.csv(코스닥). MP 보유 내역(비밀번호 페이지)은 넣지 않음.
"""
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
DOCS = BASE / "docs"
DESKTOP_DIR = Path(r"C:\Users\joonh\OneDrive\Desktop\클로드\브리핑")
CHROME = [r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
WD = "월화수목금토일"
PH3 = {1: "상승", 2: "상승", 3: "상승", 4: "고점권 조정", 5: "하락", 6: "하락"}
PH3C = {"상승": "#2f9e44", "고점권 조정": "#e8590c", "하락": "#c92a2a"}
QNAME = {1: "강세 지속", 2: "개선 중", 3: "약세", 4: "꺾이는 중"}
QCOL = {1: "#c92a2a", 2: "#e67700", 3: "#1971c2", 4: "#e8590c"}


def pct(a, b):
    return (a / b - 1) * 100 if a and b else np.nan


def sg(v, d=1, unit="%"):
    return "-" if v is None or v != v else f"{v:+.{d}f}{unit}"


def f0(v, unit="%"):
    return "-" if v is None or v != v else f"{v:.0f}{unit}"


def load_sector_page():
    s = (DOCS / "sector_ma_breadth.html").read_text(encoding="utf-8")
    i = s.index("const D = ") + len("const D = ")
    return json.loads(s[i:s.index(";\n", i)])


def cut_to_date(D, asof):
    """섹터 데이터를 국면 기준일까지로 자름(2026-10-20 사용자 "4사분면 느낌상 19일인 거 같은데?" - 아침엔 섹터 시총 캐시에 전날이 먼저 들어와
    머리글(국면 10/16)과 4사분면(섹터 10/19)의 날짜가 섞였음). dates와 길이가 같은 리스트는 전부 같은 길이로 자른다."""
    dates = D["dates"]
    k = sum(1 for x in dates if x <= asof.strftime("%Y-%m-%d"))
    if k >= len(dates):
        return D
    L = len(dates)
    def cut(o):
        if isinstance(o, list) and len(o) == L:
            return o[:k]
        if isinstance(o, dict):
            return {kk: cut(v) for kk, v in o.items()}
        if isinstance(o, list):
            return [cut(v) for v in o]
        return o
    return cut(D)


def sma(a, m):
    s = pd.Series(a, dtype="float64")
    return s.rolling(m, min_periods=m).mean().values if m > 1 else s.values


def cross_state(f, s):
    """빠른-느린 차의 부호와 마지막 교차 위치."""
    d = f - s
    n = len(d)
    last = None
    for i in range(1, n):
        if np.isnan(d[i]) or np.isnan(d[i - 1]):
            continue
        if (d[i - 1] <= 0 < d[i]) or (d[i - 1] >= 0 > d[i]):
            last = i
    return d, last


def svg_lines(dates, series, height=150, width=720, ymin=None, ymax=None, bands=None, marks=None, hline=None, right=None):
    """간단한 인쇄용 선 차트(SVG). series: [(값 배열, 색, 굵기, 이름, 점선?)], bands: [(i0, i1, 색)], marks: [(i, 값, '▲'/'▼', 색)]."""
    L, R, T, B = 34, 40 if right else 8, 8, 18
    W, H = width - L - R, height - T - B
    vals = np.concatenate([np.asarray(v, float) for v, *_ in series])
    lo = np.nanmin(vals) if ymin is None else ymin
    hi = np.nanmax(vals) if ymax is None else ymax
    if hi - lo < 1e-9:
        hi = lo + 1
    n = len(dates)
    X = lambda i: L + W * i / max(1, n - 1)
    Y = lambda v: T + H * (1 - (v - lo) / (hi - lo))
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="display:block">']
    for i0, i1, col in bands or []:
        out.append(f'<rect x="{X(i0):.1f}" y="{T}" width="{max(1, X(i1) - X(i0)):.1f}" height="{H}" fill="{col}" opacity="0.13"/>')
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        out.append(f'<line x1="{L}" x2="{L + W}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#e9ecef"/><text x="{L - 4}" y="{Y(v) + 3:.1f}" font-size="9" text-anchor="end" fill="#868e96">{v:.0f}</text>')
    if hline is not None:
        out.append(f'<line x1="{L}" x2="{L + W}" y1="{Y(hline):.1f}" y2="{Y(hline):.1f}" stroke="#868e96" stroke-dasharray="3 3"/>')
    prev_m = None
    for i, d in enumerate(dates):
        if d[5:7] != prev_m:
            prev_m = d[5:7]
            if i > 0:
                out.append(f'<line x1="{X(i):.1f}" x2="{X(i):.1f}" y1="{T}" y2="{T + H}" stroke="#f1f3f5"/>')
            out.append(f'<text x="{X(i):.1f}" y="{height - 5}" font-size="9" fill="#868e96">{d[2:4]}.{d[5:7]}</text>')
    for v, col, w, _nm, dash in series:
        pts = [f"{X(i):.1f},{Y(x):.1f}" for i, x in enumerate(v) if x == x]
        segs, cur = [], []
        for i, x in enumerate(v):
            if x == x:
                cur.append(f"{X(i):.1f},{Y(x):.1f}")
            elif cur:
                segs.append(cur); cur = []
        if cur:
            segs.append(cur)
        for sgm in segs:
            out.append(f'<polyline points="{" ".join(sgm)}" fill="none" stroke="{col}" stroke-width="{w}"{" stroke-dasharray=\"4 3\"" if dash else ""}/>')
    for i, v, ch, col in marks or []:
        out.append(f'<text x="{X(i):.1f}" y="{Y(v) + (12 if ch == "▼" else -4):.1f}" font-size="10" text-anchor="middle" fill="{col}">{ch}</text>')
    if right:
        rv, rcol, rnm = right
        rv = np.asarray(rv, float); rlo, rhi = np.nanmin(rv), np.nanmax(rv)
        YR = lambda x: T + H * (1 - (x - rlo) / (rhi - rlo if rhi > rlo else 1))
        segs, cur = [], []
        for i, x in enumerate(rv):
            if x == x:
                cur.append(f"{X(i):.1f},{YR(x):.1f}")
            elif cur:
                segs.append(cur); cur = []
        if cur:
            segs.append(cur)
        for sgm in segs:
            out.append(f'<polyline points="{" ".join(sgm)}" fill="none" stroke="{rcol}" stroke-width="1"/>')
        for k in range(3):
            x = rlo + (rhi - rlo) * k / 2
            out.append(f'<text x="{L + W + 3}" y="{YR(x) + 3:.1f}" font-size="9" fill="{rcol}">{x:,.0f}</text>')
    lx = L + 4
    for v, col, w, nm, dash in series:
        out.append(f'<line x1="{lx}" x2="{lx + 14}" y1="{T + 6}" y2="{T + 6}" stroke="{col}" stroke-width="{max(1.5, w)}"{" stroke-dasharray=\"4 3\"" if dash else ""}/><text x="{lx + 17}" y="{T + 9}" font-size="9" fill="#495057">{nm}</text>')
        lx += 22 + 7 * len(nm)
    if right:
        out.append(f'<line x1="{lx}" x2="{lx + 14}" y1="{T + 6}" y2="{T + 6}" stroke="{right[1]}"/><text x="{lx + 17}" y="{T + 9}" font-size="9" fill="#495057">{right[2]}(오른쪽)</text>')
    out.append("</svg>")
    return "".join(out)


def svg_quad(pts, width=720, height=385):
    """섹터 4사분면 산점도(SVG). pts: [(이름, x, y, x1주전, y1주전, 종목수)].
    축은 지금 점들이 보이게 확대(50%·0 기준선은 항상 포함), 1주 전 꼬리는 그림 밖이면 잘림, 이름표는 겹치지 않게 배치."""
    L, R, T, B = 46, 10, 10, 26
    W, H = width - L - R, height - T - B
    # 축은 모든 섹터가 들어오게 잡는다(가장자리 여유). outl은 혹시 범위를 벗어나는 경우의 안전장치
    xs = sorted(p[1] for p in pts); ys = sorted(abs(p[2]) for p in pts)
    qx = lambda a: xs[min(len(xs) - 1, max(0, int(round(a * (len(xs) - 1)))))]
    xlo = max(0, min(45, (int(qx(0)) - 6) // 10 * 10)); xhi = min(100, max(55, (int(qx(1)) + 16) // 10 * 10))
    ym = max(10, int(ys[-1] * 1.15 / 5 + 1) * 5)   # 모든 섹터가 실제 위치에 보이게(2026-10-16 사용자 "전부 다 나올 수 있게")
    outl = {p[0]: (p[1], p[2]) for p in pts if not (xlo <= p[1] <= xhi and -ym <= p[2] <= ym)}
    pts = [(nm, min(max(x, xlo), xhi), min(max(y, -ym), ym), x5, y5, n_) for nm, x, y, x5, y5, n_ in pts]
    X = lambda v: L + W * (v - xlo) / (xhi - xlo)
    Y = lambda v: T + H * (1 - (v + ym) / (2 * ym))
    q = lambda x, y: (1 if y >= 0 else 4) if x >= 50 else (2 if y >= 0 else 3)
    o = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="display:block;font-family:Malgun Gothic,sans-serif">',
         f'<defs><clipPath id="qc"><rect x="{L}" y="{T}" width="{W}" height="{H}"/></clipPath></defs>']
    for qq, (x0, y0, x1, y1) in {1: (50, 0, xhi, ym), 2: (xlo, 0, 50, ym), 3: (xlo, -ym, 50, 0), 4: (50, -ym, xhi, 0)}.items():
        o.append(f'<rect x="{X(x0):.1f}" y="{Y(y1):.1f}" width="{X(x1) - X(x0):.1f}" height="{Y(y0) - Y(y1):.1f}" fill="{QCOL[qq]}" opacity="0.06"/>')
    step = 10 if xhi - xlo <= 60 else 25
    for v in range(xlo, xhi + 1, step):
        o.append(f'<line x1="{X(v):.1f}" x2="{X(v):.1f}" y1="{T}" y2="{T + H}" stroke="#f1f3f5"/><text x="{X(v):.1f}" y="{T + H + 11}" font-size="9" text-anchor="middle" fill="#868e96">{v}%</text>')
    ystep = 5 if ym <= 20 else (10 if ym <= 50 else 20)
    for v in range(-ym, ym + 1, ystep):
        o.append(f'<line x1="{L}" x2="{L + W}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#f8f9fa"/><text x="{L - 4}" y="{Y(v) + 3:.1f}" font-size="9" text-anchor="end" fill="#868e96">{v:+d}</text>')
    o.append(f'<line x1="{X(50):.1f}" x2="{X(50):.1f}" y1="{T}" y2="{T + H}" stroke="#868e96" stroke-dasharray="4 3"/><line x1="{L}" x2="{L + W}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="#868e96" stroke-dasharray="4 3"/>')
    for qq, (x, y, anc) in {1: (L + W - 4, T + 12, "end"), 2: (L + 4, T + 12, "start"), 3: (L + 4, T + H - 5, "start"), 4: (L + W - 4, T + H - 5, "end")}.items():
        o.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="11" font-weight="700" text-anchor="{anc}" fill="{QCOL[qq]}">{QNAME[qq]}</text>')
    mx = max([p[5] for p in pts] or [1])
    rad = {p[0]: 3 + 6 * (p[5] / mx) ** 0.5 for p in pts}
    o.append('<g clip-path="url(#qc)">')
    for nm, x, y, x5, y5, _n in pts:
        if x5 == x5 and y5 == y5:
            o.append(f'<line x1="{X(x5):.1f}" y1="{Y(y5):.1f}" x2="{X(x):.1f}" y2="{Y(y):.1f}" stroke="{QCOL[q(x, y)]}" stroke-opacity="0.18" stroke-width="1"/><circle cx="{X(x5):.1f}" cy="{Y(y5):.1f}" r="1.6" fill="#adb5bd"/>')
    o.append('</g>')
    for nm, x, y, x5, y5, _n in sorted(pts, key=lambda p: -p[5]):
        o.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="{rad[nm]:.1f}" fill="{QCOL[q(x, y)]}" fill-opacity="0.85" stroke="#fff" stroke-width="0.8"/>')
    # 이름표: 오른쪽·왼쪽·위·아래·대각 후보 중 다른 이름표·점과 안 겹치는 자리(없으면 가장 덜 겹치는 자리)
    boxes = [(X(p[1]) - rad[p[0]], Y(p[2]) - rad[p[0]], X(p[1]) + rad[p[0]], Y(p[2]) + rad[p[0]]) for p in pts]
    def ov(a, b):
        return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    placed = []
    for nm, x, y, *_ in sorted(pts, key=lambda p: (p[1], p[2])):
        cx, cy, r = X(x), Y(y), rad[nm]
        lab = nm + (f" ({outl[nm][0]:.0f}%, {outl[nm][1]:+.0f})" if nm in outl else "")
        w, h = 8.6 * len(nm) * 0.95 + (6 * (len(lab) - len(nm)) if nm in outl else 0) + 2, 10
        cands = [(cx + r + 2, cy - h / 2), (cx - r - 2 - w, cy - h / 2), (cx - w / 2, cy - r - h - 1), (cx - w / 2, cy + r + 1),
                 (cx + r, cy - r - h), (cx + r, cy + r), (cx - r - w, cy - r - h), (cx - r - w, cy + r)]
        best = None
        for bx, by in cands:
            bb = (bx, by, bx + w, by + h)
            if bb[0] < L or bb[2] > L + W or bb[1] < T or bb[3] > T + H:
                continue
            cost = sum(ov(bb, z) for z in placed) * 3 + sum(ov(bb, z) for z in boxes)
            if best is None or cost < best[0]:
                best = (cost, bb)
            if cost == 0:
                break
        if best is None:
            best = (0, (cx + r + 2, cy - h / 2, cx + r + 2 + w, cy + h / 2))
        bb = best[1]; placed.append(bb)
        o.append(f'<text x="{bb[0]:.1f}" y="{bb[1] + 8.5:.1f}" font-size="8.5" fill="#212529" paint-order="stroke" stroke="#fff" stroke-width="2.2">{lab}</text>')
    o.append(f'<text x="{L + W / 2:.1f}" y="{height - 2}" font-size="9" text-anchor="middle" fill="#868e96">15일선 위 비율(추세 수준) →</text>')
    o.append(f'<text x="11" y="{T + H / 2:.1f}" font-size="9" text-anchor="middle" fill="#868e96" transform="rotate(-90 11 {T + H / 2:.1f})">8일 - 15일선 위 비율(%p, 모멘텀) →</text>')
    o.append("</svg>")
    return "".join(o)


def main():
    d = pd.read_csv(DATA / "screening" / "market_phase_daily.csv", parse_dates=["date"], index_col="date")
    d = d[d["phase"].notna()]
    asof = d.index[-1]
    # 2026-10-21 사용자 "시가총액은 내가 매일 올리니까 홈페이지엔 최신 수치가 있는데" - 섹터·시장 폭은 시총 파일 기준 최신일까지 쓰고
    # (국면 점수·지수는 KRX 종가가 들어와야 갱신되므로 국면 기준일 그대로), 머리글에 두 날짜를 따로 표시한다.
    D = load_sector_page()
    sec_asof = D["dates"][-1]
    dates = D["dates"]
    n = len(dates)
    now = datetime.now()

    # ---------- 국면
    ph = d["phase_shown"].astype(int)
    p3 = ph.map(PH3)
    cur3 = p3.iloc[-1]
    k = 1
    while k < len(p3) and p3.iloc[-1 - k] == cur3:
        k += 1
    since = p3.index[-k]
    target = 60 if int(d["stage"].iloc[-1]) == 1 else 100
    cau = int(d["caution"].iloc[-1]) == 1
    ck = 0
    while cau and ck < len(d) and d["caution"].iloc[-1 - ck] == 1:
        ck += 1
    sc, sc5 = d["score"].iloc[-1], d["score"].iloc[-6]
    raw, line = d["cau_raw"].iloc[-1], d["caution_line"].iloc[-1]
    # 지수
    kq = pd.read_csv(DATA / "phase_warning_inputs.csv", parse_dates=["date"]).set_index("date")["kosdaq_close"].dropna()
    kq = kq.loc[:asof]
    smi, ksp = d["sm_idx"].dropna(), d["kospi"].dropna()
    def rets(s):
        return [pct(s.iloc[-1], s.iloc[-1 - h]) if len(s) > h else np.nan for h in (1, 5, 20)]
    ma120 = smi.rolling(120).mean()
    sm_below = smi.iloc[-1] < ma120.iloc[-1]
    kb = 0
    while kb < len(smi) and (smi.iloc[-1 - kb] < ma120.iloc[-1 - kb]) == sm_below:
        kb += 1
    # 시장 폭(섹터 정리 전체, 종목 수 기준)
    A = D["all"]
    def at(key, back=0):
        a = A.get(key); i = n - 1 - back
        return a[i] if a and i >= 0 and a[i] is not None else np.nan
    breadth = [(lbl, at("n" + key), at("n" + key, 5), at("n" + key, 21)) for lbl, key in (("20일선", "20"), ("20주선", "20w"), ("120일선", "120"), ("200일선", "200"))]
    # 브리핑 교차는 8일/15일선 위 비율(평활 없음) - 2026-10-19 사용자 "20/40 말고 10일 15일로" -> 같은 날 "8일 15일로"
    f2040 = np.array(A["n8"], float); s2040 = np.array(A["n15"], float)
    d2040, lc2040 = cross_state(f2040, s2040)
    # 밸류
    ob = pd.read_csv(DATA / "screening" / "op_band_summary.csv", usecols=["latest_mult", "b_3y_p10", "gap_3y_p10"])
    below = int(((ob["latest_mult"] > 0) & (ob["gap_3y_p10"] < 0)).sum())
    nb = int(((ob["latest_mult"] > 0) & ob["gap_3y_p10"].notna()).sum())
    C, C5, C20 = d["C"].iloc[-1] * 100, d["C"].iloc[-6] * 100, d["C"].iloc[-21] * 100

    # ---------- 섹터
    secs = D["sectors"]
    def sec_at(sx, key, back=0):
        a = sx.get(key); i = n - 1 - back
        return a[i] if a and i >= 0 and a[i] is not None else np.nan
    quad = {1: [], 2: [], 3: [], 4: []}
    for sx in secs:
        x, y = sec_at(sx, "n15"), sec_at(sx, "n8") - sec_at(sx, "n15")
        x5, y5 = sec_at(sx, "n15", 5), sec_at(sx, "n8", 5) - sec_at(sx, "n15", 5)
        if x != x or y != y:
            continue
        q = (1 if y >= 0 else 4) if x >= 50 else (2 if y >= 0 else 3)
        q5 = None if (x5 != x5 or y5 != y5) else ((1 if y5 >= 0 else 4) if x5 >= 50 else (2 if y5 >= 0 else 3))
        quad[q].append((sx["name"], y, q5))
    qpts = []
    for sx in secs:
        x, y = sec_at(sx, "n15"), sec_at(sx, "n8") - sec_at(sx, "n15")
        if x == x and y == y:
            qpts.append((sx["name"], x, y, sec_at(sx, "n15", 5), sec_at(sx, "n8", 5) - sec_at(sx, "n15", 5), sx["n"]))
    # 이익추정치 상향(이익추정치 상향 트래커와 같은 데이터) - 섹터는 섹터 정리 기준
    code2sec = {c: sx["name"] for sx in secs for c in sx["codes"]}
    try:
        es = (DOCS / "estimate_revision.html").read_text(encoding="utf-8")
        i = es.index("const ROWS = ") + len("const ROWS = ")
        ER = pd.DataFrame(json.loads(es[i:es.index(";\n", i)]))
        ER["c6"] = ER["code"].str[-6:]
        ER["sec"] = ER["c6"].map(code2sec)
        er_date = ER["latest_date"].max()
    except Exception:  # noqa: BLE001
        ER, er_date = pd.DataFrame(), "-"
    # 코스피를 이긴 섹터(종목 상대강도 페이지와 같은 계산 - 섹터 소속 종목의 코스피 대비 초과수익 평균)
    try:
        RS = pd.read_csv(DATA / "screening" / "sector_relative_strength_summary.csv")
    except Exception:  # noqa: BLE001
        RS = pd.DataFrame()
    # 2026-10-21: 시총 파일 기준 최신일까지의 종목 수익률(D["ret"], 1/5/20일)로 다시 계산 - 파일(종가 기준)은 KRX가 늦으면 하루 뒤처짐.
    # 코스피 대비 초과수익(%p) 섹터 평균·이긴 종목 비율, 1주=5거래일·1달=20거래일. 실패하면 위 csv를 그대로 쓴다.
    try:
        rt, ik = D.get("ret"), D.get("idx", {}).get("코스피")
        if rt and ik and len(ik) == n:
            kr = {h: (ik[-1] / ik[-1 - h] - 1) * 100 for h in (5, 20)}
            rows_rs = []
            for sx in secs:
                e5, e20 = [], []
                for c in sx["codes"]:
                    v = rt["stk"].get(c)
                    if v and v[1] is not None and v[2] is not None:
                        e5.append(v[1] - kr[5]); e20.append(v[2] - kr[20])
                if e5:
                    rows_rs.append(dict(sector=sx["name"], mean_1주=float(np.mean(e5)), win_1주=100 * float(np.mean([x > 0 for x in e5])),
                                        mean_1달=float(np.mean(e20)), win_1달=100 * float(np.mean([x > 0 for x in e20]))))
            if len(rows_rs) >= 30:
                RS = pd.DataFrame(rows_rs)
    except Exception:  # noqa: BLE001
        pass
    # 강·약 섹터는 15일선 위 비율(2026-10-16 사용자 "20주선 말고 15일선으로") - 4사분면 가로축과 같은 기준
    w20 = sorted([(sx["name"], sec_at(sx, "n15"), sec_at(sx, "n15") - sec_at(sx, "n15", 5), sx["n"]) for sx in secs if sec_at(sx, "n15") == sec_at(sx, "n15")], key=lambda r: -r[1])
    movers = sorted([r for r in w20 if r[2] == r[2]], key=lambda r: r[2])
    # 섹터 8일/15일선 위 비율 골드·데드크로스 신규 진입(최근 5거래일 안에 교차) - 2026-10-22 사용자 "15일선 위 비율 높은/낮은 섹터 빼고 골드·데드 신규 진입 섹터로"
    new_gold, new_dead = [], []
    for sx in secs:
        f8, s15 = np.array(sx.get("n8") or [], float)[:n], np.array(sx.get("n15") or [], float)[:n]
        if len(f8) < 10 or len(s15) < 10:
            continue
        dd, lc = cross_state(f8, s15)
        if lc is None or n - 1 - lc >= 5 or dd[-1] != dd[-1] or dd[-1] == 0:   # 차이 0(동률)은 제외
            continue
        rec = (sx["name"], n - lc, dd[-1], f8[-1], s15[-1], dates[lc][5:])
        (new_gold if dd[-1] > 0 else new_dead).append(rec)
    new_gold.sort(key=lambda r: (r[1], -r[2])); new_dead.sort(key=lambda r: (r[1], r[2]))   # 최근 교차 먼저, 같은 날이면 차이 큰 순
    ret5 = (D.get("ret") or {}).get("5", {})
    rrows = ret5.get("rows", [])
    try:
        yoy = pd.read_csv(DATA / "screening" / "sector_yoy_accel_summary_trimmed.csv")
        # 기저효과 극단치(±200%p 초과)와 5종목 미만 섹터는 빼고(철강 +999%p 같은 값이 상위를 차지해서)
        yoy = yoy.dropna(subset=["accel"])
        yoy = yoy[(yoy["accel"].abs() <= 200) & (yoy["n_stocks"] >= 5)].sort_values("accel", ascending=False)
    except Exception:  # noqa: BLE001
        yoy = pd.DataFrame()

    # ---------- 자동 요약(규칙 기반)
    bullets = []
    bullets.append(f"국면 <b style='color:{PH3C[cur3]}'>{cur3}</b> · 목표 비중 <b>{target}%</b> ({since:%m/%d}부터 {k}거래일째)" +
                   (f". 꺾임 주의 {ck}일째 - 꺾임 판단 점수 {raw:.0f}점이 주의선 {line:.0f}점 아래" if cau else ". 꺾임 주의 꺼짐"))
    r_sm = rets(smi)
    bullets.append(f"중소형 지수 1일 {sg(r_sm[0])}, 1주 {sg(r_sm[1])}, 1달 {sg(r_sm[2])} · 120일선 {'아래' if sm_below else '위'} {kb}거래일째({sg(pct(smi.iloc[-1], ma120.iloc[-1]))})")
    st2040 = "골드(빠른 선 위)" if d2040[-1] > 0 else "데드(빠른 선 아래)"
    bullets.append(f"시장 폭 교차(8일/15일선 위 비율) <b>{st2040}</b>" + (f" - {dates[lc2040]} 교차, {n - lc2040}거래일째" if lc2040 else "") +
                   f", 차이 {d2040[-1]:+.0f}%p(1주 전 {d2040[-6]:+.0f}%p).")
    b20 = breadth[0]
    bullets.append(f"20일선 위 종목 {f0(b20[1])}(1주 전 {f0(b20[2])}, 1달 전 {f0(b20[3])}) · 200일선 위 {f0(breadth[3][1])}")
    if len(RS):
        wins = RS[RS["mean_1주"] > 0].sort_values("mean_1주", ascending=False)["sector"].tolist()
        bullets.append(f"1주 코스피를 이긴 섹터 {len(wins)}/{len(RS)}개" + (": " + ", ".join(wins[:8]) + (" 등" if len(wins) > 8 else "") if wins else ""))
    if len(ER):
        up1m = ER[ER["fy2_1m"] > 0].shape[0]; dn1m = ER[ER["fy2_1m"] < 0].shape[0]
        bullets.append(f"이익추정치(내년 FY2) 1달 상향 {up1m}종목 / 하향 {dn1m}종목")
    new_strong = [nm for nm, _y, q5 in quad[1] if q5 and q5 != 1]
    new_turn = [nm for nm, _y, q5 in quad[4] if q5 == 1]
    new_weak = [nm for nm, _y, q5 in quad[3] if q5 in (1, 4)]
    if new_strong:
        bullets.append("새로 강세 지속에 들어온 섹터: " + ", ".join(new_strong))
    if new_turn or new_weak:
        bullets.append("강세에서 내려온 섹터: " + ", ".join(new_turn + new_weak))

    # ---------- 차트(최근 6달)
    cut = max(0, n - 126)
    dd = dates[cut:]
    marks = []
    for i in range(max(cut, 1), n):
        a, b = d2040[i - 1], d2040[i]
        if a == a and b == b and ((a <= 0 < b) or (a >= 0 > b)):
            marks.append((i - cut, f2040[i], "▲" if b > 0 else "▼", "#c92a2a" if b > 0 else "#1971c2"))
    # 2026-10-21: 코스닥 선도 섹터 이평선 페이지와 같은 시총 파일 기준 지수(D["idx"], 최신일까지) 사용 - 국면 파일은 KRX 종가가 늦어 하루 뒤처짐
    kqw = np.array(D["idx"]["코스닥"][cut:], dtype=float) if D.get("idx", {}).get("코스닥") and len(D["idx"]["코스닥"]) == n \
        else kq.reindex(pd.to_datetime(dd), method="ffill").values
    chart2 = svg_lines(dd, [(f2040[cut:], "#e8590c", 1.8, "8일선 위", False), (s2040[cut:], "#1971c2", 1.8, "15일선 위", False)],
                       ymin=0, ymax=100, marks=marks, hline=50, right=(kqw, "#adb5bd", "코스닥"))

    # ---------- HTML
    title_date = f"{now:%Y-%m-%d}({WD[now.weekday()]})"
    def tr(cells, cls=""):
        return f"<tr{cls}>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    try:
        if D.get("idx", {}).get("코스피") and len(D["idx"]["코스피"]) == n:
            ksp = pd.Series(D["idx"]["코스피"], index=pd.to_datetime(D["dates"]), dtype=float)
            kq = pd.Series(D["idx"]["코스닥"], index=pd.to_datetime(D["dates"]), dtype=float)
    except Exception:  # noqa: BLE001
        pass
    idx_tbl = "".join(tr([nm] + [f"{v:,.2f}" if nm != "중소형 지수" else f"{v:,.1f}"] + [f"<span class='{'up' if x >= 0 else 'dn'}'>{sg(x)}</span>" for x in rets(s)])
                      for nm, s, v in (("코스피", ksp, ksp.iloc[-1]), ("코스닥", kq, kq.iloc[-1]), ("중소형 지수", smi, smi.iloc[-1])))
    quad_svg = svg_quad(qpts)
    def rs_rows(col, top=True):
        if not len(RS):
            return ""
        t = RS.dropna(subset=[col]).sort_values(col, ascending=not top).head(7)
        return "".join(tr([r["sector"], f"<b class='{'up' if r[col] >= 0 else 'dn'}'>{r[col]:+.1f}%p</b>", f"{r[col.replace('mean', 'win')]:.0f}%"]) for _, r in t.iterrows())
    nwin1w = int((RS["mean_1주"] > 0).sum()) if len(RS) else 0
    nwin1m = int((RS["mean_1달"] > 0).sum()) if len(RS) else 0
    ER_TOP_N = 15   # 2026-10-21 사용자 "이익추정치 상향 종목 15개까지" (6 -> 15, 두 표로 나눠 8+7)

    def er_rows(lo=0, hi=ER_TOP_N):
        if not len(ER):
            return ""
        t = ER[(ER["mktcap"] >= 1e11) & ER["fy2_1w"].between(0.5, 100)].sort_values("fy2_1w", ascending=False).head(ER_TOP_N).iloc[lo:hi]
        return "".join(tr([r["name"], r["sec"] if isinstance(r["sec"], str) else (r["sector"] or "-"), f"{r['mktcap'] / 1e8:,.0f}억",
                           f"<b class='up'>{r['fy2_1w']:+.1f}%</b>", sg(r["fy2_1m"]), sg(r["fy1_1w"])]) for _, r in t.iterrows())
    def er_sec():
        if not len(ER):
            return ""
        g = ER.dropna(subset=["sec", "fy2_1m"]).groupby("sec")
        t = pd.DataFrame({"n": g.size(), "up": g["fy2_1m"].apply(lambda v: (v > 0).sum()), "dn": g["fy2_1m"].apply(lambda v: (v < 0).sum()), "med": g["fy2_1m"].median()})
        t = t[t["n"] >= 5]
        t["net"] = (t["up"] - t["dn"]) / t["n"] * 100
        t = t.sort_values("net", ascending=False).head(6)
        return "".join(tr([nm, f"<span class='up'>{int(r['up'])}</span> / <span class='dn'>{int(r['dn'])}</span> ({int(r['n'])})", f"<b>{r['net']:+.0f}%p</b>"]) for nm, r in t.iterrows())
    br_tbl = "".join(tr([lbl, f"<b>{f0(a)}</b>", f0(b), f0(c), f"<span class='{'up' if a - b >= 0 else 'dn'}'>{sg(a - b, 0, '%p')}</span>"]) for lbl, a, b, c in breadth)
    mat = "".join(tr([nm, f"{v1:.1f}", f"{v0:.1f}", f"{s1:.0f}점", f"{s0:.0f}점"]) for nm, v1, v0, s1, s0 in (
        ("밸류 하단 종목 비율(%)", d["C"].iloc[-1] * 100, d["C"].iloc[-6] * 100, d["sC"].iloc[-1], d["sC"].iloc[-6]),
        ("공격-방어 1달(%p)", d["D"].iloc[-1] * 100, d["D"].iloc[-6] * 100, d["sD"].iloc[-1], d["sD"].iloc[-6]),
        ("120일선 위 종목(%)", d["X"].iloc[-1] * 100, d["X"].iloc[-6] * 100, d["sX"].iloc[-1], d["sX"].iloc[-6])))
    def qcell(q):
        items = sorted(quad[q], key=lambda r: -r[1])
        return "".join(f"<span class='chip'>{nm}{f' <i style=\"color:{QCOL[q5]}\">←{QNAME[q5]}</i>' if q5 and q5 != q else ''}</span>" for nm, _y, q5 in items) or "-"
    quad_tbl = "".join(f"<tr><td class='qn' style='color:{QCOL[q]}'>{QNAME[q]} ({len(quad[q])})</td><td class='ql'>{qcell(q)}</td></tr>" for q in (1, 2, 4, 3))
    def rbar(r):
        w = min(100, abs(r["r"]) / max(0.1, max(abs(x["r"]) for x in rrows)) * 100)
        return f"<tr><td>{r['name']}</td><td class='{'up' if r['r'] >= 0 else 'dn'}'><b>{r['r']:+.1f}%</b></td><td style='width:45%'><div class='bar' style='width:{w:.0f}%;background:{'#ffa8a8' if r['r'] >= 0 else '#a5d8ff'}'></div></td><td class='sub'>{r['up']}/{r['n']}↑</td></tr>"
    ret_top = "".join(rbar(r) for r in rrows[:6])
    ret_bot = "".join(rbar(r) for r in rrows[-6:][::-1])
    xrow = lambda r: tr([r[0], f"{r[5]} ({r[1]}일째)", f"<b class='{'up' if r[2] >= 0 else 'dn'}'>{sg(r[2], 0, '%p')}</b>", f"{r[3]:.0f}% / {r[4]:.0f}%"])
    none_row = "<tr><td colspan='4' class='sub'>최근 5거래일 새로 교차한 섹터 없음</td></tr>"
    strong = "".join(xrow(r) for r in new_gold[:10]) or none_row
    weak = "".join(xrow(r) for r in new_dead[:10]) or none_row
    mv_up = ", ".join(f"{nm} {c:+.0f}" for nm, _v, c, _n in movers[::-1][:5])
    mv_dn = ", ".join(f"{nm} {c:+.0f}" for nm, _v, c, _n in movers[:5])
    yoy_tbl = "".join(tr([r["name"], f"{int(r['n_stocks'])}", f"<b>{r['accel']:+.1f}%p</b>", r.get("valuation_grade") if isinstance(r.get("valuation_grade"), str) else "-"])
                      for _, r in yoy.head(5).iterrows()) if len(yoy) else ""
    html = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>시장 브리핑 {title_date}</title>
<style>
  @page {{ size: A4; margin: 9mm 10mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: "Malgun Gothic", -apple-system, sans-serif; color:#212529; background:#f1f3f5; margin:0; font-size:11px; line-height:1.45; }}
  .page {{ width:210mm; min-height:277mm; margin:12px auto; background:#fff; padding:9mm 10mm; box-shadow:0 1px 4px #0002; }}
  @media print {{ body {{ background:#fff; }} .page {{ margin:0; box-shadow:none; padding:0; width:auto; min-height:auto; page-break-after:always; }} .noprint {{ display:none; }} }}
  h1 {{ font-size:17px; margin:0; }} h2 {{ font-size:12.5px; margin:10px 0 4px; padding-bottom:2px; border-bottom:2px solid #343a40; }}
  .hdr {{ display:flex; justify-content:space-between; align-items:flex-end; border-bottom:3px solid #212529; padding-bottom:4px; margin-bottom:6px; }}
  .hdr .sub, .sub {{ color:#868e96; font-size:10px; }}
  .hero {{ display:flex; gap:8px; margin:6px 0; }}
  .hero .box {{ flex:1; border:1px solid #dee2e6; border-radius:6px; padding:6px 8px; }}
  .hero .lbl {{ font-size:10px; color:#868e96; }} .hero .val {{ font-size:18px; font-weight:700; }}
  ul {{ margin:4px 0; padding-left:16px; }} li {{ margin:2px 0; }}
  table {{ border-collapse:collapse; width:100%; }} td, th {{ padding:2px 5px; border-bottom:1px solid #f1f3f5; text-align:right; white-space:nowrap; }}
  td:first-child, th:first-child {{ text-align:left; }} th {{ color:#868e96; font-weight:normal; font-size:10px; }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }} .grid3 {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; }}
  .up {{ color:#e03131; }} .dn {{ color:#1971c2; }}
  .bar {{ height:8px; border-radius:2px; }}
  .chip {{ display:inline-block; margin:1px 8px 1px 0; white-space:nowrap; }} .chip i {{ font-style:normal; font-size:9.5px; }}
  td.qn {{ font-weight:700; width:78px; vertical-align:top; }} td.ql {{ text-align:left; white-space:normal; }}
  .qguide {{ font-size:10px; color:#495057; line-height:1.55; background:#f8f9fa; border-radius:5px; padding:5px 8px; margin:4px 0; }}
  .qtbl td {{ padding:1px 4px; font-size:9.5px; line-height:1.35; }} .qtbl .chip {{ margin-right:6px; }}
  .note {{ color:#868e96; font-size:9.5px; margin-top:4px; }}
  .btn {{ position:fixed; right:16px; top:12px; background:#212529; color:#fff; border:0; border-radius:6px; padding:6px 12px; cursor:pointer; }}
</style></head><body>
<button class="btn noprint" onclick="window.print()">인쇄 / PDF</button>
<div class="page">
  <div class="hdr"><div><h1>시장 브리핑 · {title_date}</h1><div class="sub">국면 {asof:%Y-%m-%d}({WD[asof.weekday()]}) 종가 · 지수·시장 폭·섹터 {sec_asof}(시가총액 파일 기준, 중소형 지수는 국면 기준일) · 섹터 정리 종목 기준 · 자동 생성 {now:%m/%d %H:%M}</div></div>
    <div class="sub">1/2 시장 판단</div></div>
  <div class="hero">
    <div class="box" style="border-color:{PH3C[cur3]}"><div class="lbl">국면</div><div class="val" style="color:{PH3C[cur3]}">{cur3}</div><div class="sub">{since:%m/%d}부터 {k}거래일째</div></div>
    <div class="box"><div class="lbl">목표 비중</div><div class="val">{target}%</div><div class="sub">{'꺾임 주의 ' + str(ck) + '일째' if cau else '꺾임 주의 없음'}</div></div>
    <div class="box"><div class="lbl">국면 점수(65일 EMA)</div><div class="val">{sc:.1f}</div><div class="sub">1주 전 {sc5:.1f} · 낮음&lt;{d['lo'].iloc[-1]:.0f} 높음&gt;{d['hi'].iloc[-1]:.0f}</div></div>
    <div class="box"><div class="lbl">시장 폭 교차 8/15</div><div class="val" style="color:{'#c92a2a' if d2040[-1] > 0 else '#1971c2'}">{'골드' if d2040[-1] > 0 else '데드'}</div><div class="sub">{(dates[lc2040][5:] + ' 교차 · ' + str(n - lc2040) + '일째') if lc2040 else ''}</div></div>
  </div>
  <h2>요약</h2><ul>{''.join(f'<li>{b}</li>' for b in bullets)}</ul>
  <div class="grid2">
    <div><h2>지수</h2><table><tr><th></th><th>종가</th><th>1일</th><th>1주</th><th>1달</th></tr>{idx_tbl}</table></div>
    <div><h2>시장 폭 (이평선 위 종목 비율)</h2><table><tr><th></th><th>지금</th><th>1주 전</th><th>1달 전</th><th>1주 변화</th></tr>{br_tbl}</table></div>
  </div>
  <h2>코스피를 이긴 섹터 <span class="sub">섹터 소속 종목의 코스피 대비 초과수익 평균 · 오른쪽 = 코스피를 이긴 종목 비율 · 1주 {nwin1w}/{len(RS)}개, 1달 {nwin1m}/{len(RS)}개 섹터가 이김</span></h2>
  <div class="grid2">
    <div><table><tr><th>1주 상위</th><th>초과수익</th><th>이긴 종목</th></tr>{rs_rows("mean_1주")}</table></div>
    <div><table><tr><th>1달 상위</th><th>초과수익</th><th>이긴 종목</th></tr>{rs_rows("mean_1달")}</table></div>
  </div>
  <h2>시장 폭 교차: 8일선 위 vs 15일선 위 비율 <span class="sub">▲ 골드 · ▼ 데드 · 회색 = 코스닥</span></h2>{chart2}
  <div class="note">섹터 정리 전체 종목 기준. 짧은 조합이라 교차가 잦음(단기 흐름용) - 손익 비교(2016~)에서 가장 견고했던 조합은 20일/40일·5일 평활(섹터 이평선 페이지 기본값).</div>
  <h2>실적 가속 섹터 (섹터별 YoY 가속화, 절사평균) <span class="sub">가속도 = 2분기 뒤 추정 YoY - 최근 확정 YoY</span></h2>
  <table><tr><th>섹터</th><th>종목</th><th>가속도</th><th>밸류 등급</th></tr>{yoy_tbl}</table>
  <div class="note">기저효과 극단치(±200%p 초과)·5종목 미만 섹터 제외. 밸류 등급 = OP밴드 3년 하위 10% 바텀 대비 %의 5분위(상 = 바텀에 가까움).</div>
</div>
<div class="page">
  <div class="hdr"><div><h1>섹터 흐름</h1><div class="sub">데이터 기준 {sec_asof} · 섹터 정리 {len(secs)}개 섹터 · 종목 수 기준 비율</div></div><div class="sub">2/2 섹터 · joonhakkim.github.io/liquidity-dashboard</div></div>
  <h2>섹터 4사분면 (가로 15일선 위 비율 50% · 세로 8일-15일선 위 비율 0) <span class="sub">← = 1주 전 사분면</span></h2>
  {quad_svg}
  <div class="qguide"><b>읽는 법</b> 가로 = 섹터 종목 중 15일선 위에 있는 비율(오른쪽일수록 단기 추세 강함, 50%가 기준) · 세로 = 8일선 위 비율 − 15일선 위 비율(위쪽일수록 최근 며칠 더 좋아지는 중, 0이 기준).
    점 크기 = 종목 수, 회색 점에서 이어진 옅은 선 = 1주 전 위치. 섹터는 보통 <span style="color:#e67700">개선 중</span>(왼쪽 위) → <span style="color:#c92a2a">강세 지속</span>(오른쪽 위) → <span style="color:#e8590c">꺾이는 중</span>(오른쪽 아래) → <span style="color:#1971c2">약세</span>(왼쪽 아래)로 시계 방향으로 돕니다.
    지금 위치를 보는 지도이고, 다음 성과를 맞히는 힘은 약합니다(2016~ 사분면별 이후 20일 차이 ±0.5%p 안).</div>
  <table class="qtbl">{quad_tbl}</table>
  <h2>이익추정치 상향 <span class="sub">내년(FY2) 영업이익 컨센서스 변화율 · {er_date} 기준 · 시총 1천억 이상</span></h2>
  <div class="grid2">
    <div><table><tr><th>1주 상향 상위 종목</th><th>섹터</th><th>시총</th><th>FY2 1주</th><th>1달</th><th>FY1 1주</th></tr>{er_rows(0, 8)}</table></div>
    <div><table><tr><th>(이어서)</th><th>섹터</th><th>시총</th><th>FY2 1주</th><th>1달</th><th>FY1 1주</th></tr>{er_rows(8, ER_TOP_N)}</table></div>
  </div>
  <div class="grid3">
    <div><h2>상향 우세 섹터 <span class="sub">(1달 · 괄호=종목수)</span></h2><table>{er_sec()}</table></div>
    <div><h2>골드크로스 신규 진입 섹터 {len(new_gold)}개 <span class="sub">(8일선 위 비율이 15일선 위 비율을 위로 · 교차일·차이·8일/15일)</span></h2><table>{strong}</table></div>
    <div><h2>데드크로스 신규 진입 섹터 {len(new_dead)}개 <span class="sub">(8일선 위 비율이 15일선 위 비율 아래로 · 최근 5거래일)</span></h2><table>{weak}</table></div>
  </div>
</div>
</body></html>"""
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    OUT_HTML = DESKTOP_DIR / f"_briefing_{now:%Y%m%d_%H%M%S}.html"   # PDF 변환용 임시 파일
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"브리핑 생성(데이터 기준 {asof.date()}, 국면 {cur3} {target}%)")
    # PDF(크롬 헤드리스) - 실패해도 페이지는 남김
    exe = next((c for c in CHROME if os.path.exists(c)), None)
    if not exe:
        print("크롬/엣지 없음 - PDF 생략"); OUT_HTML.unlink(missing_ok=True); return
    try:
        DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
        dst = DESKTOP_DIR / f"시장브리핑_{now:%Y%m%d}.pdf"
        subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={dst}", OUT_HTML.as_uri()],
                       check=True, timeout=120, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(f"PDF 저장 완료: {dst}")
    except Exception as e:  # noqa: BLE001
        print(f"PDF 실패: {e}")
    finally:
        OUT_HTML.unlink(missing_ok=True)

if __name__ == "__main__":
    main()
