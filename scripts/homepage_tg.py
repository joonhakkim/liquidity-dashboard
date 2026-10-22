"""
홈페이지 요약을 텔레그램(@Troyinvestbot)으로 보냄 - 2026-10-22 사용자 "이 챗봇에 현재 홈페이지들의 정보들을 2시간 간격으로
올려줄 수 있나? 1) 섹터별 수익률 2) 섹터 4사분면 3) 19시 이후에는 시장 국면".

- 섹터 등락률·4사분면: docs/sector_ma_breadth.html에 박힌 확정 데이터(D)에 docs/live/intraday.json 오늘 잠정값을 페이지 JS(applyLive)와
  같은 방식으로 붙여서 계산. 4사분면은 페이지 기본값 그대로(가로 = 15일선 위 비율, 세로 = 8일선 위 - 15일선 위, 1주 전 위치와 비교).
- 시장 국면: docs/live/intraday.json의 오늘 잠정 판정(live_intraday.phase_live, 페이지와 같은 규칙) + data/screening/market_phase_daily.csv 확정값.
- 실행: scripts/run_homepage_tg.py(작업 스케줄러 'Homepage-TG', 평일 11·13·15·17·19시). 19시 실행 때만 국면을 같이 보냄.
- 명령: python scripts/homepage_tg.py [sector|phase|all] [--dry] (--dry = 보내지 않고 미리보기 파일만 만듦)
"""
import json
import os
import sys
import time
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import requests
from dotenv import load_dotenv

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
load_dotenv(os.path.join(BASE, ".env"))
SECTOR_HTML = os.path.join(BASE, "docs", "sector_ma_breadth.html")
LIVE_JSON = os.path.join(BASE, "docs", "live", "intraday.json")
PHASE_CSV = os.path.join(BASE, "data", "screening", "market_phase_daily.csv")
OUT_DIR = os.path.join(BASE, "data", "screening", "cache", "homepage_tg")

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

QNAME = {1: "강세 지속", 2: "개선 중", 3: "약세", 4: "꺾이는 중"}
QCOL = {1: "#ff6b6b", 2: "#ffd43b", 3: "#4dabf7", 4: "#ffa94d"}
QF, QS = "8", "15"          # 페이지 4사분면 기본값(2026-10-19 사용자 "8일 15일")
PH3 = {1: "상승", 2: "고점권 조정", 3: "하락"}
PH6 = {1: "① 바닥권 반등", 2: "② 중간 상승", 3: "③ 고점권 상승", 4: "④ 고점권 꺾임", 5: "⑤ 중간 하락", 6: "⑥ 바닥권 꺾임"}
PHDESC = {1: "국면 점수가 오르는 중. 비중 100%",
          2: "고점권에서 일별 점수 급락(꺾임 주의). 강한 시장의 일시 조정인 경우가 많아 비중 100% 유지",
          3: "꺾임 주의 또는 느린 점수 하락 중. 비중 60%"}


def to3(q):
    return 1 if q <= 3 else (2 if q == 4 else 3)


def load_live():
    try:
        return json.load(open(LIVE_JSON, encoding="utf-8"))
    except Exception:
        return None


def load_sector(j):
    """페이지 데이터 + 오늘 잠정값(페이지 applyLive와 같은 규칙: 잠정 날짜 > 확정 기준일일 때만 붙임)."""
    h = open(SECTOR_HTML, encoding="utf-8").read()
    i = h.index("const D = ") + len("const D = ")
    D, _ = json.JSONDecoder().raw_decode(h[i:])
    D["live"] = False
    L = (j or {}).get("sector")
    if L and L.get("keys") and j["date"] > D["asof"]:
        at = {k: n for n, k in enumerate(L["keys"])}
        D["dates"].append(j["date"])
        for s in D["sectors"]:
            v = L["sec"].get(s["name"])
            for k in list(s.keys()):
                if k[:1] in "nc" and k[1:] and isinstance(s[k], list):
                    s[k].append(v[at[k]] if v and k in at else None)
        for c, v in (L.get("stocks") or {}).items():   # 오늘 잠정 시총(억)
            if c in D["stocks"]:
                D["stocks"][c]["cap"] = v[0]
        if L.get("ret"):
            D["ret"] = L["ret"]
        D["live"] = True
    return D


def stamp(j, D):
    if D["live"]:
        t = j.get("built", j.get("ts", ""))[-5:]
        return f"{j['date']} {t} 잠정" + (" (장 마감 기준)" if t >= "15:30" else "")
    return f"{D['ret']['date']} 종가"


# ---------------- 섹터 등락률 ----------------
def sector_returns(D, j):
    R = D["ret"]
    lines = [f"<b>📊 섹터 등락률</b> · {stamp(j, D)}"]
    hdr = R["1"].get("hdr") or []
    if hdr:
        lines.append(" · ".join(f"{h['name']} {h['v']:,.1f} ({h['ch']:+.1f}%)" for h in hdr))
    lines.append("구성종목 단순평균 · 섹터 정리 기준")
    for key, lbl in (("1", "1일"), ("5", "1주"), ("20", "1달")):
        rows = sorted(R[key]["rows"], key=lambda x: -x["r"])
        sub = "" if key == "1" else f" ({R[key]['from']}~)"
        lines.append(f"\n<b>{lbl}{sub}</b>")
        top = [f"{x['name']} {x['r']:+.1f}%" for x in rows[:5]]
        bot = [f"{x['name']} {x['r']:+.1f}%" for x in rows[-5:][::-1]]
        lines.append("🔺 " + " · ".join(top))
        lines.append("🔻 " + " · ".join(bot))
        up = sum(1 for x in rows if x["r"] > 0)
        lines.append(f"오른 섹터 {up}/{len(rows)}")
    lines.append("\n" + sector_leaders(D))
    return "\n".join(lines)


def fmt_cap(c):
    return f"{c / 10000:.1f}조" if c >= 10000 else f"{c:,.0f}억"


def sector_leaders(D, top=10, per=3, min_cap=1000):
    """2026-10-22 사용자 "섹터내 상승 주요종목 보여주고" - 1일 등락률 상위 섹터마다 오른 종목 중 시총 1,000억 이상을
    등락률 순으로 3개(1,000억 이상이 없으면 전체에서). 등락률은 페이지 구성종목 펼침과 같은 D.ret.stk 값."""
    stk = D["ret"].get("stk") or {}
    secs = {s["name"]: s for s in D["sectors"]}
    rows = sorted(D["ret"]["1"]["rows"], key=lambda x: -x["r"])[:top]
    out = [f"<b>상승 섹터 주요 종목</b> <i>(1일 상위 {top}개 섹터, 오른 종목 중 시총 {min_cap:,}억 이상 등락률 순)</i>"]
    for x in rows:
        s = secs.get(x["name"])
        if not s:
            continue
        m = [(c, stk[c][0], (D["stocks"].get(c) or {}).get("cap") or 0) for c in s["codes"] if c in stk and stk[c][0] is not None and stk[c][0] > 0]
        big = [t for t in m if t[2] >= min_cap] or m
        big.sort(key=lambda t: -t[1])
        names = " · ".join(f"{(D['stocks'].get(c) or {}).get('name', c)} {r:+.1f}%({fmt_cap(cap)})" for c, r, cap in big[:per]) or "오른 종목 없음"
        out.append(f"• <b>{x['name']} {x['r']:+.1f}%</b> ({x['up']}/{x['n']}↑): {names}")
    return "\n".join(out)


def sector_returns_chart(D, j, path):
    rows = sorted(D["ret"]["1"]["rows"], key=lambda x: x["r"])
    r5 = {x["name"]: x["r"] for x in D["ret"]["5"]["rows"]}
    fig, ax = plt.subplots(figsize=(8, 0.26 * len(rows) + 1.2), facecolor="#0f1115")
    ax.set_facecolor("#0f1115")
    vals = [x["r"] for x in rows]
    ax.barh(range(len(rows)), vals, color=["#ff6b6b" if v >= 0 else "#4dabf7" for v in vals], height=0.7)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([f"{x['name']} ({x['up']}/{x['n']}↑)" for x in rows], color="#e6e6e6", fontsize=8)
    mx = max(abs(v) for v in vals) or 1
    for i, x in enumerate(rows):
        v = x["r"]
        ax.text(v + (mx * 0.02 if v >= 0 else -mx * 0.02), i, f"{v:+.1f}%  (1주 {r5.get(x['name'], 0):+.1f}%)" if v >= 0 else f"(1주 {r5.get(x['name'], 0):+.1f}%)  {v:+.1f}%",
                va="center", ha="left" if v >= 0 else "right", color="#e6e6e6", fontsize=7)
    ax.set_xlim(-mx * 1.7, mx * 1.7)
    ax.axvline(0, color="#6b7280", lw=0.8)
    ax.tick_params(axis="x", colors="#9aa0a6")
    for s in ax.spines.values():
        s.set_color("#23262e")
    ax.set_title(f"섹터 1일 등락률 · {stamp(j, D)}", color="#e6e6e6", fontsize=11, fontweight="bold")
    plt.tight_layout()
    plt.savefig(path, dpi=130, facecolor=fig.get_facecolor())
    plt.close()


# ---------------- 4사분면 ----------------
def quad_of(x, y):
    return (1 if y >= 0 else 4) if x >= 50 else (2 if y >= 0 else 3)


def quad_points(D):
    N = len(D["dates"])
    pts = []
    for s in D["sectors"]:
        f, sl = s.get("n" + QF) or [], s.get("n" + QS) or []

        def at(k):
            i = N - 1 - k
            if i < 0 or i >= len(f) or i >= len(sl) or f[i] is None or sl[i] is None:
                return None
            return (sl[i], f[i] - sl[i])
        cur = at(0)
        if cur:
            pts.append({"name": s["name"], "n": s["n"], "cur": cur, "wk": at(5)})
    return pts


def quad_text(D, j):
    pts = quad_points(D)
    by = {1: [], 2: [], 3: [], 4: []}
    for p in pts:
        by[quad_of(*p["cur"])].append(p)
    lines = [f"<b>🧭 섹터 4사분면</b> · {stamp(j, D)}",
             f"가로 = {QS}일선 위 비율(50% 기준, 추세) · 세로 = {QF}일선 위 − {QS}일선 위(0 기준, 모멘텀)",
             "보통 개선 중 → 강세 지속 → 꺾이는 중 → 약세 순서로 돕니다. ← = 1주 전 사분면"]
    for q in (2, 1, 4, 3):
        items = []
        for p in sorted(by[q], key=lambda p: -p["cur"][1]):
            w = quad_of(*p["wk"]) if p["wk"] else q
            items.append(p["name"] + (f"(←{QNAME[w]})" if w != q else ""))
        lines.append(f"\n<b>{QNAME[q]} ({len(by[q])})</b>\n" + (" · ".join(items) if items else "-"))
    moved = [p for p in pts if p["wk"] and quad_of(*p["wk"]) != quad_of(*p["cur"])]
    lines.append(f"\n이번 주 사분면이 바뀐 섹터 {len(moved)}개")
    return "\n".join(lines)


def quad_chart(D, j, path):
    pts = quad_points(D)
    maxn = max(p["n"] for p in pts)
    fig, ax = plt.subplots(figsize=(9, 7.5), facecolor="#0f1115")
    ax.set_facecolor("#0f1115")
    ys = [p["cur"][1] for p in pts] + [p["wk"][1] for p in pts if p["wk"]]
    ym = max(10, (int(max(abs(y) for y in ys)) // 5 + 2) * 5)
    for q, (x0, x1, y0, y1) in {1: (50, 100, 0, ym), 2: (0, 50, 0, ym), 3: (0, 50, -ym, 0), 4: (50, 100, -ym, 0)}.items():
        ax.fill_between([x0, x1], y0, y1, color=QCOL[q], alpha=0.07, lw=0)
        ax.text(x1 - 1 if q in (1, 4) else x0 + 1, y1 - ym * 0.03 if q <= 2 else y0 + ym * 0.03, QNAME[q], color=QCOL[q], alpha=0.8,
                ha="right" if q in (1, 4) else "left", va="top" if q <= 2 else "bottom", fontsize=11, fontweight="bold")
    ax.axvline(50, color="#6b7280", ls="--", lw=0.8)
    ax.axhline(0, color="#6b7280", ls="--", lw=0.8)
    for p in pts:
        q = quad_of(*p["cur"])
        if p["wk"]:
            ax.annotate("", xy=p["cur"], xytext=p["wk"], arrowprops=dict(arrowstyle="-|>", color=QCOL[q], alpha=0.45, lw=0.9))
        ax.scatter(*p["cur"], s=25 + 160 * (p["n"] / maxn) ** 0.5, color=QCOL[q], alpha=0.85, edgecolors="#0f1115", zorder=3)
        ax.text(p["cur"][0] + 1, p["cur"][1], p["name"], color="#e6e6e6", fontsize=7.5, va="center", zorder=4)
    ax.set_xlim(0, 100)
    ax.set_ylim(-ym, ym)
    ax.set_xlabel(f"{QS}일선 위 비율(%) - 추세 수준", color="#9aa0a6")
    ax.set_ylabel(f"{QF}일선 위 - {QS}일선 위(%p) - 모멘텀", color="#9aa0a6")
    ax.tick_params(colors="#9aa0a6")
    for s in ax.spines.values():
        s.set_color("#23262e")
    ax.grid(color="#23262e")
    ax.set_title(f"섹터 4사분면 · {stamp(j, D)} · 화살표 = 1주 전 → 지금", color="#e6e6e6", fontsize=11, fontweight="bold")
    plt.tight_layout()
    plt.savefig(path, dpi=130, facecolor=fig.get_facecolor())
    plt.close()


# ---------------- 시장 국면 ----------------
def phase_text(j):
    d = pd.read_csv(PHASE_CSV)
    d = d[d["phase_shown"].notna()].reset_index(drop=True)
    last = d.iloc[-1]
    ph3 = d["phase_shown"].astype(int).map(to3)
    k = len(ph3) - 1
    while k > 0 and ph3.iloc[k - 1] == ph3.iloc[-1]:
        k -= 1
    lines = ["<b>🌡 시장 국면</b>"]
    P = (j or {}).get("phase")
    if P and j["date"] > str(last["date"]):
        sh = int(P["shown"])
        cau_days = 0
        if P.get("caution"):
            cau_days = 1
            for v in d["caution"].iloc[::-1]:
                if int(v) != 1:
                    break
                cau_days += 1
        lines.append(f"<b>오늘 잠정 ({j['date']} {j.get('built', '')[-5:]})</b>: {PH3[to3(sh)]} · 비중 {P['target']}%"
                     + (f" · ⚠ 꺾임 주의 {cau_days}일째" if P.get("caution") else ""))
        lines.append(f"세부 {PH6[sh]} · 느린 점수 기준 {PH6[int(P['slow'])]}")
        lines.append(f"국면 점수 {P['score']:.1f} (8주 전 {d['score'].iloc[-40]:.1f} → {P['score'] - d['score'].iloc[-40]:+.1f})")
        lines.append(f"일별 점수 {P['raw']:.1f} (어제 {P['raw_y']:.1f}, 20일 고점 {P['max20']:.1f}, 꺾임 주의선 {P['line']:.1f})")
        if (j or {}).get("sm"):
            s = j["sm"]
            hist = pd.read_csv(PHASE_CSV, usecols=["sm_idx"])["sm_idx"].dropna()
            below = (hist < hist.rolling(120).mean()).tolist()
            run = 0
            for b in below[::-1]:
                if b != s["below"]:
                    break
                run += 1
            lines.append(f"중소형 지수 {s['idx']:.1f} ({s['chg']:+.2f}%) · 120일선 {s['ma120']:.1f} ({(s['idx'] / s['ma120'] - 1) * 100:+.1f}%) "
                         + ("🔻 하락 추세" if s["below"] else "🔺 상승 추세") + f" {run + 1}거래일째")
        lines.append("")
    sh = int(last["phase_shown"])
    lines.append(f"<b>확정 ({last['date']} 종가)</b>: {PH3[to3(sh)]} · 비중 {60 if int(last['stage']) == 1 else 100}%"
                 + (" · ⚠ 꺾임 주의" if int(last["caution"]) == 1 else ""))
    lines.append(f"세부 {PH6[sh]} · 느린 점수 기준 {PH6[int(last['phase'])]}")
    lines.append(f"국면 점수 {last['score']:.1f} (낮음 &lt; {last['lo']:.1f} · 높음 &gt; {last['hi']:.1f}) · 40거래일 전 {d['score'].iloc[-41]:.1f}")
    lines.append(f"밸류 하단 비율 {last['C'] * 100:.1f}% · 공격-방어 1달 {last['D'] * 100:+.1f}%p · 120일선 위 {last['X'] * 100:.1f}%")
    lines.append(f"대형주만 버팀 {last['capn60']:+.1f}%p · 취약 신호 {int(last['vuln_n'])}/9")
    if k > 0:
        lines.append(f"마지막 국면 변경: {d['date'].iloc[k]} ({PH3[ph3.iloc[k - 1]]} → {PH3[ph3.iloc[-1]]}, {len(ph3) - k}거래일째)")
    lines.append(f"뜻: {PHDESC[to3(sh)]}")
    lines.append("확정값은 다음 날 아침에 나옵니다(KRX 종가·시총 데이터가 아침에 들어옴).")
    return "\n".join(lines)


# ---------------- 보내기 ----------------
def tg(method, data, files=None):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 .env에 없어 보내지 않음")
        return
    data = dict(data, chat_id=chat)
    r = requests.post(f"https://api.telegram.org/bot{tok}/{method}", data=data, files=files, timeout=60)
    if not r.ok:
        print("보내기 실패:", method, r.status_code, r.text[:200])
    time.sleep(1)


def send_text(t):
    while t:
        cut = t if len(t) <= 3900 else t[:t.rfind("\n", 0, 3900)]
        tg("sendMessage", {"text": cut, "parse_mode": "HTML", "disable_web_page_preview": "true"})
        t = t[len(cut):].lstrip("\n")


def send_photo(path, cap):
    with open(path, "rb") as f:
        tg("sendPhoto", {"caption": cap, "parse_mode": "HTML"}, files={"photo": f})


def run(what="sector", dry=False, force=False):
    os.makedirs(OUT_DIR, exist_ok=True)
    j = load_live()
    today = datetime.now().strftime("%Y-%m-%d")
    if not force and (not j or j.get("date") != today):
        print(f"오늘({today}) 장중 데이터가 없음(휴장일이거나 장중 갱신이 안 됨) - 건너뜀. live date = {j and j.get('date')}")
        return
    msgs, photos = [], []
    if what in ("sector", "all"):
        D = load_sector(j)
        p1, p2 = os.path.join(OUT_DIR, "sector_ret.png"), os.path.join(OUT_DIR, "sector_quad.png")
        sector_returns_chart(D, j, p1)
        quad_chart(D, j, p2)
        msgs += [sector_returns(D, j), quad_text(D, j)]
        photos += [(p1, f"섹터 1일 등락률 · {stamp(j, D)}"), (p2, f"섹터 4사분면 · {stamp(j, D)}")]
    if what in ("phase", "all"):
        msgs.append(phase_text(j))
    preview = "\n\n────────\n\n".join(msgs)
    open(os.path.join(OUT_DIR, "preview.txt"), "w", encoding="utf-8").write(preview)
    print(preview)
    if dry:
        print("(--dry: 보내지 않음)", photos)
        return
    if what in ("sector", "all"):
        send_text(msgs[0]); send_photo(*photos[0])
        send_text(msgs[1]); send_photo(*photos[1])
    if what in ("phase", "all"):
        send_text(msgs[-1])
    print("보냄")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    run(args[0] if args else "sector", dry="--dry" in sys.argv, force="--force" in sys.argv)
