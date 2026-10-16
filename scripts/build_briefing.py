"""
아침 회의용 시장 브리핑(A4 2장) - 2026-10-16 사용자 요청("지금 상황 자체를 아침 회의에 쓸 수 있도록 요약해서 2페이지짜리 자동으로").
매일 아침 정규 갱신(run_pipeline)에서 시장 국면·섹터 이평선 페이지가 만들어진 뒤 실행:
  docs/briefing.html(인쇄용 A4 2장, 사이트엔 인쇄/PDF 버튼) + 크롬 헤드리스 PDF는 바탕화면 클로드\브리핑\시장브리핑_YYYYMMDD.pdf에만
  (매일 PDF를 저장소에 올리면 1년 50MB씩 쌓여서).
1장 시장 판단: 국면·목표 비중·꺾임 주의, 지수, 국면 재료, 시장 폭(이평선 위 비율·20/40 교차·5/15), 밸류 바닥, 차트 2개.
2장 섹터: 4사분면(5일/15일), 섹터 1주 등락, 20주선 위 강·약 섹터와 1주 변화, 실적 가속 섹터, 자동 요약.
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
OUT_HTML = DOCS / "briefing.html"
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


def main():
    d = pd.read_csv(DATA / "screening" / "market_phase_daily.csv", parse_dates=["date"], index_col="date")
    d = d[d["phase"].notna()]
    asof = d.index[-1]
    D = load_sector_page()
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
    f2040 = sma(np.array(A["n20"], float), 5); s2040 = sma(np.array(A["n40"], float), 5)
    d2040, lc2040 = cross_state(f2040, s2040)
    f515, s515 = np.array(A["n5"], float), np.array(A["n15"], float)
    d515, lc515 = cross_state(f515, s515)
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
        x, y = sec_at(sx, "n15"), sec_at(sx, "n5") - sec_at(sx, "n15")
        x5, y5 = sec_at(sx, "n15", 5), sec_at(sx, "n5", 5) - sec_at(sx, "n15", 5)
        if x != x or y != y:
            continue
        q = (1 if y >= 0 else 4) if x >= 50 else (2 if y >= 0 else 3)
        q5 = None if (x5 != x5 or y5 != y5) else ((1 if y5 >= 0 else 4) if x5 >= 50 else (2 if y5 >= 0 else 3))
        quad[q].append((sx["name"], y, q5))
    w20 = sorted([(sx["name"], sec_at(sx, "n20w"), sec_at(sx, "n20w") - sec_at(sx, "n20w", 5), sx["n"]) for sx in secs if sec_at(sx, "n20w") == sec_at(sx, "n20w")], key=lambda r: -r[1])
    movers = sorted([r for r in w20 if r[2] == r[2]], key=lambda r: r[2])
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
    bullets.append(f"시장 폭 교차(20일/40일선 위 비율, 5일 평활) <b>{st2040}</b>" + (f" - {dates[lc2040]} 교차 뒤 {n - 1 - lc2040}거래일째" if lc2040 else "") +
                   f", 차이 {d2040[-1]:+.0f}%p(1주 전 {d2040[-6]:+.0f}%p). 단기(5/15)는 {'골드' if d515[-1] > 0 else '데드'}" + (f"({dates[lc515][5:]}부터)" if lc515 else ""))
    b20 = breadth[0]
    bullets.append(f"20일선 위 종목 {f0(b20[1])}(1주 전 {f0(b20[2])}, 1달 전 {f0(b20[3])}) · 200일선 위 {f0(breadth[3][1])} · 밸류 하단(3년 하위 10% 아래) 비율 {C:.0f}%(1주 전 {C5:.0f}%)")
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
    smw = smi.reindex(pd.to_datetime(dd)).values
    maw = ma120.reindex(pd.to_datetime(dd)).values
    p3w = p3.reindex(pd.to_datetime(dd))
    bands, i0 = [], 0
    for i in range(1, len(dd) + 1):
        if i == len(dd) or p3w.iloc[i] != p3w.iloc[i0]:
            if isinstance(p3w.iloc[i0], str):
                bands.append((i0, i, PH3C[p3w.iloc[i0]]))
            i0 = i
    chart1 = svg_lines(dd, [(smw, "#f08c00", 1.8, "중소형 지수", False), (maw, "#495057", 1.2, "120일선", True)], bands=bands)
    marks = []
    for i in range(max(cut, 1), n):
        a, b = d2040[i - 1], d2040[i]
        if a == a and b == b and ((a <= 0 < b) or (a >= 0 > b)):
            marks.append((i - cut, f2040[i], "▲" if b > 0 else "▼", "#c92a2a" if b > 0 else "#1971c2"))
    kqw = kq.reindex(pd.to_datetime(dd), method="ffill").values
    chart2 = svg_lines(dd, [(f2040[cut:], "#e8590c", 1.8, "20일선 위", False), (s2040[cut:], "#1971c2", 1.8, "40일선 위", False)],
                       ymin=0, ymax=100, marks=marks, hline=50, right=(kqw, "#adb5bd", "코스닥"))

    # ---------- HTML
    title_date = f"{now:%Y-%m-%d}({WD[now.weekday()]})"
    def tr(cells, cls=""):
        return f"<tr{cls}>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    idx_tbl = "".join(tr([nm] + [f"{v:,.2f}" if nm != "중소형 지수" else f"{v:,.1f}"] + [f"<span class='{'up' if x >= 0 else 'dn'}'>{sg(x)}</span>" for x in rets(s)])
                      for nm, s, v in (("코스피", ksp, ksp.iloc[-1]), ("코스닥", kq, kq.iloc[-1]), ("중소형 지수", smi, smi.iloc[-1])))
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
    strong = "".join(tr([nm, f"<b>{v:.0f}%</b>", f"<span class='{'up' if c >= 0 else 'dn'}'>{sg(c, 0, '%p')}</span>"]) for nm, v, c, _n in w20[:6])
    weak = "".join(tr([nm, f"<b>{v:.0f}%</b>", f"<span class='{'up' if c >= 0 else 'dn'}'>{sg(c, 0, '%p')}</span>"]) for nm, v, c, _n in w20[-6:][::-1])
    mv_up = ", ".join(f"{nm} {c:+.0f}" for nm, _v, c, _n in movers[::-1][:5])
    mv_dn = ", ".join(f"{nm} {c:+.0f}" for nm, _v, c, _n in movers[:5])
    yoy_tbl = "".join(tr([r["name"], f"{int(r['n_stocks'])}", f"<b>{r['accel']:+.1f}%p</b>", r.get("valuation_grade") if isinstance(r.get("valuation_grade"), str) else "-"])
                      for _, r in yoy.head(6).iterrows()) if len(yoy) else ""
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
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
  .up {{ color:#e03131; }} .dn {{ color:#1971c2; }}
  .bar {{ height:8px; border-radius:2px; }}
  .chip {{ display:inline-block; margin:1px 8px 1px 0; white-space:nowrap; }} .chip i {{ font-style:normal; font-size:9.5px; }}
  td.qn {{ font-weight:700; width:78px; vertical-align:top; }} td.ql {{ text-align:left; white-space:normal; }}
  .note {{ color:#868e96; font-size:9.5px; margin-top:4px; }}
  .btn {{ position:fixed; right:16px; top:12px; background:#212529; color:#fff; border:0; border-radius:6px; padding:6px 12px; cursor:pointer; }}
</style></head><body>
<button class="btn noprint" onclick="window.print()">인쇄 / PDF</button>
<div class="page">
  <div class="hdr"><div><h1>시장 브리핑 · {title_date}</h1><div class="sub">데이터 기준 {asof:%Y-%m-%d}({WD[asof.weekday()]}) 종가 · 섹터 정리 종목 기준 · 자동 생성 {now:%m/%d %H:%M}</div></div>
    <div class="sub">1/2 시장 판단</div></div>
  <div class="hero">
    <div class="box" style="border-color:{PH3C[cur3]}"><div class="lbl">국면</div><div class="val" style="color:{PH3C[cur3]}">{cur3}</div><div class="sub">{since:%m/%d}부터 {k}거래일째</div></div>
    <div class="box"><div class="lbl">목표 비중</div><div class="val">{target}%</div><div class="sub">{'꺾임 주의 ' + str(ck) + '일째' if cau else '꺾임 주의 없음'}</div></div>
    <div class="box"><div class="lbl">국면 점수(65일 EMA)</div><div class="val">{sc:.1f}</div><div class="sub">1주 전 {sc5:.1f} · 낮음&lt;{d['lo'].iloc[-1]:.0f} 높음&gt;{d['hi'].iloc[-1]:.0f}</div></div>
    <div class="box"><div class="lbl">시장 폭 교차 20/40</div><div class="val" style="color:{'#c92a2a' if d2040[-1] > 0 else '#1971c2'}">{'골드' if d2040[-1] > 0 else '데드'}</div><div class="sub">{(dates[lc2040][5:] + ' 교차 · ' + str(n - 1 - lc2040) + '일째') if lc2040 else ''}</div></div>
  </div>
  <h2>요약</h2><ul>{''.join(f'<li>{b}</li>' for b in bullets)}</ul>
  <div class="grid2">
    <div><h2>지수</h2><table><tr><th></th><th>종가</th><th>1일</th><th>1주</th><th>1달</th></tr>{idx_tbl}</table></div>
    <div><h2>시장 폭 (이평선 위 종목 비율)</h2><table><tr><th></th><th>지금</th><th>1주 전</th><th>1달 전</th><th>1주 변화</th></tr>{br_tbl}</table></div>
  </div>
  <div class="grid2">
    <div><h2>국면 재료 (지금 / 1주 전)</h2><table><tr><th></th><th>지금</th><th>1주 전</th><th>점수</th><th>1주 전</th></tr>{mat}</table>
      <div class="note">꺾임 판단 점수 {raw:.0f}점 · 주의선(20일 고점-20) {line:.0f}점 · 국면 점수 = 밸류3·공격방어2·120일선2</div></div>
    <div><h2>밸류 바닥</h2><table>
      <tr><td>OP밴드 3년 하위 10% 바텀 아래 종목</td><td><b>{below:,}개</b> / {nb:,}</td></tr>
      <tr><td>밸류 하단 비율(국면 재료)</td><td><b>{C:.1f}%</b></td></tr><tr><td>1주 전 / 1달 전</td><td>{C5:.1f}% / {C20:.1f}%</td></tr></table>
      <div class="note">많을수록 시장이 밸류 바닥권(국면 점수엔 거꾸로 반영)</div></div>
  </div>
  <h2>중소형 지수와 120일선 <span class="sub">배경 = 국면(초록 상승 · 주황 고점권 조정 · 빨강 하락), 최근 6달</span></h2>{chart1}
  <h2>시장 폭 교차: 20일선 위 vs 40일선 위 비율(5일 평균) <span class="sub">▲ 골드 · ▼ 데드 · 회색 = 코스닥</span></h2>{chart2}
  <div class="note">교차 기준(20/40·5일 평활)은 2016~ 조합 288개 손익 비교(골드 100%/데드 60%)에서 네 칸 모두 보유보다 나았던 유일한 조합. 효과가 작아 국면 판단의 보조 신호.</div>
</div>
<div class="page">
  <div class="hdr"><div><h1>섹터 흐름</h1><div class="sub">데이터 기준 {asof:%Y-%m-%d} · 섹터 정리 {len(secs)}개 섹터 · 종목 수 기준 비율</div></div><div class="sub">2/2 섹터</div></div>
  <h2>섹터 4사분면 (가로 15일선 위 비율 50% · 세로 5일-15일선 위 비율 0) <span class="sub">← = 1주 전 사분면</span></h2>
  <table>{quad_tbl}</table>
  <div class="note">보통 개선 중 → 강세 지속 → 꺾이는 중 → 약세로 돕니다. 지금 위치를 보는 지도이지 다음 성과 예측은 약함(사분면별 이후 20일 차이 ±0.5%p 안).</div>
  <div class="grid2">
    <div><h2>1주 섹터 등락 상위</h2><table>{ret_top}</table></div>
    <div><h2>1주 섹터 등락 하위</h2><table>{ret_bot}</table></div>
  </div>
  <div class="grid2">
    <div><h2>20주선 위 비율 높은 섹터 <span class="sub">(1주 변화)</span></h2><table>{strong}</table></div>
    <div><h2>20주선 위 비율 낮은 섹터 <span class="sub">(1주 변화)</span></h2><table>{weak}</table></div>
  </div>
  <div class="note">20주선 위 비율 1주 변화 - 많이 오른 섹터: {mv_up} / 많이 내린 섹터: {mv_dn} (%p). 40% 아래에 오래 머문 섹터는 이후 6달 평균 3~6%p 약했음.</div>
  <h2>실적 가속 섹터 (섹터별 YoY 가속화, 절사평균) <span class="sub">가속도 = 2분기 뒤 추정 YoY - 최근 확정 YoY</span></h2>
  <table><tr><th>섹터</th><th>종목</th><th>가속도</th><th>밸류 등급</th></tr>{yoy_tbl}</table>
  <div class="note">기저효과 극단치(±200%p 초과)·5종목 미만 섹터 제외. 밸류 등급 = OP밴드 3년 하위 10% 바텀 대비 %의 5분위(상 = 바텀에 가까움).</div>
  <h2>자세히 보기</h2>
  <div class="sub">시장 국면 market_phase.html · 섹터 이평선(교차·4사분면) sector_ma_breadth.html · OP밴드 op_band.html · 섹터 YoY sector_yoy_accel_tracker.html — https://joonhakkim.github.io/liquidity-dashboard/</div>
</div>
</body></html>"""
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"저장 완료: {OUT_HTML} (데이터 기준 {asof.date()}, 국면 {cur3} {target}%)")
    # PDF(크롬 헤드리스) - 실패해도 페이지는 남김
    exe = next((c for c in CHROME if os.path.exists(c)), None)
    if not exe:
        print("크롬/엣지 없음 - PDF 생략"); return
    try:
        DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
        dst = DESKTOP_DIR / f"시장브리핑_{now:%Y%m%d}.pdf"
        subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={dst}", OUT_HTML.as_uri()],
                       check=True, timeout=120, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(f"PDF 저장 완료: {dst}")
    except Exception as e:  # noqa: BLE001
        print(f"PDF 실패(페이지는 저장됨): {e}")

if __name__ == "__main__":
    main()
