"""
바이오 텔레그램 다이제스트 봇 - 2026-10-22 사용자 요청.
"임상, 바이오 주요 이슈(일정 계획 등), 바이오 관련 소재·장비 주식들을 올려주는 텔레챗봇. 자료 소스는 46개 전부에서.
 1) 최근 언급 빈도가 높아지는 기업 2) 바이오 주요 일정 이슈 3) 바이오 최근 주가 동향 4) 객관적인 내용으로 채널 출처 남기기"

구성
- 채널 목록: data/manual/bio_tg_channels.csv (분류·채널). '기업 IR'은 자기 회사 얘기라 언급 빈도에서는 빼고 일정·이슈에만 씀.
- collect: 각 채널 공개 미리보기(https://t.me/s/채널)에서 글을 받아 SQLite(data/screening/cache/bio_tg/posts.sqlite)에 쌓음.
  처음엔 최근 BACKFILL_DAYS일, 그 뒤로는 이미 받은 글 번호까지만(이어받기). 로그인 불필요, 공개 채널만.
- 종목 사전: KRX 상장사 중 네이버 업종(제약·생물공학·건강관리장비와용품·생명과학도구및서비스·건강관리업체및서비스·건강관리기술)
  + 섹터 정리(제약바이오·주사제·의료기기). 이름(+짧은 별칭) 문자열 매칭, 퍼온 글 중복은 본문 해시로 한 번만 셈.
- digest: (1) 언급 급증 = 최근 7일 언급 수 vs 직전 28일 주평균(채널 2곳 이상), (2) 일정 = 날짜·학회 + 이벤트 단어(발표·허가·탑라인 등)가
  든 문장(원문 그대로 짧게 + 링크), (3) 주가 = 네이버 차트로 바이오 종목 1일·1주·1달 등락과 거래대금, (4) 모든 줄에 채널 출처 링크.
- send: 텔레그램 봇 API(sendMessage, HTML). TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID는 .env(사용자가 BotFather에서 만든 봇).
사용: python scripts/bio_tg_digest.py collect | digest | send | all
"""
import hashlib
import html
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pandas as pd
import requests
from dotenv import load_dotenv

BASE = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(BASE, ".env"))
sys.path.insert(0, os.path.dirname(__file__))
CH_PATH = os.path.join(BASE, "data", "manual", "bio_tg_channels.csv")
CACHE = os.path.join(BASE, "data", "screening", "cache", "bio_tg")
DB = os.path.join(CACHE, "posts.sqlite")
OUT_TXT = os.path.join(CACHE, "digest_latest.html")
H = {"User-Agent": "Mozilla/5.0"}
BACKFILL_DAYS = 60
BIO_NAVER = {"제약", "생물공학", "건강관리장비와용품", "생명과학도구및서비스", "건강관리업체및서비스", "건강관리기술"}
BIO_SECTORS = {"제약바이오", "주사제", "의료기기"}
ALIAS = {"삼성바이오로직스": ["삼바"], "리가켐바이오": ["리가켐", "레고켐"], "에이비엘바이오": ["ABL바이오", "에이비엘"], "알테오젠": ["알테오젠"],
         "셀트리온": ["셀트리온"], "유한양행": ["유한양행"], "한미약품": ["한미약품"], "HLB": ["에이치엘비"], "SK바이오팜": ["SK바이오팜"],
         "오름테라퓨틱": ["오름"], "디앤디파마텍": ["디앤디"], "에이프릴바이오": ["에이프릴"], "펩트론": ["펩트론"]}
STOP_NAMES = {"HLB", "이엔셀"}   # 너무 짧거나 일반 단어와 겹치면 여기서 조정(HLB는 대문자 단독일 때만 매칭)
CONF_RAW = r"ASCO|ESMO|AACR|ASH|SITC|JPM|BIO USA|바이오 ?USA|BIO-?Europe|바이오유럽|AASLD|EASL|ADA|EASD|ObesityWeek|ACR|AAN|AAD|ISSCR|WCLC|SABCS|ERS|ATS|ECTRIMS"
CONF = rf"(?<![A-Za-z])(?:{CONF_RAW})(?![A-Za-z])"   # MASH 안의 ASH 같은 오인식 막기
EVENT = re.compile(r"발표|공개|예정|탑라인|top-?line|허가|승인|PDUFA|자문위|AdCom|임상\s*개시|첫\s*투약|투약|환자\s*등록|IND|NDA|BLA|MAA|기술이전|L/O|라이선스|계약|상장|IPO|수요예측|데이터|결과|학회|포스터|구두|oral|1상|2상|3상|1/2a상|2a상|2b상", re.I)
DATEPAT = re.compile(r"(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일|(?P<y>20\d\d)[.\-/]\s*(?P<m2>\d{1,2})[.\-/]\s*(?P<d2>\d{1,2})|(?P<m3>1[0-2]|0?[1-9])/(?P<d3>[0-3]?\d)(?!\d)"
                     r"|(?P<q>[1-4])\s*(?:분기|Q)|(?P<h>상반기|하반기|연내|연말|내년|이달|다음\s*달|금주|다음\s*주|" + CONF + ")", re.I)


# ------------------------------------------------------------------ 수집
def db():
    os.makedirs(CACHE, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS posts(ch TEXT, id INTEGER, dt TEXT, text TEXT, h TEXT, PRIMARY KEY(ch, id))")
    if "views" not in [r[1] for r in con.execute("PRAGMA table_info(posts)")]:
        con.execute("ALTER TABLE posts ADD COLUMN views INTEGER")      # 조회수(2026-10-22 추가 - 검색량 대신 '조회수 가중 관심도')
    return con


def views_num(v):
    v = (v or "").strip().upper().replace(",", "")
    try:
        return int(float(v[:-1]) * 1000) if v.endswith("K") else int(float(v[:-1]) * 1e6) if v.endswith("M") else int(float(v))
    except ValueError:
        return None


def fetch_page(ch, before=None):
    u = f"https://t.me/s/{ch}" + (f"?before={before}" if before else "")
    for k in range(3):
        try:
            r = requests.get(u, headers=H, timeout=20)
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
        time.sleep(2 + 2 * k)
    return ""


def parse(t, ch):
    out = []
    for blk in re.split(r'(?=<div class="tgme_widget_message_wrap)', t):
        m = re.search(r'data-post="[^/]+/(\d+)"', blk)
        d = re.search(r'<time datetime="([^"]+)"', blk)
        x = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*(?:<div class="tgme_widget_message_footer|<div class="tgme_widget_message_reactions|</div>)', blk, re.S)
        if not m or not d:
            continue
        txt = x.group(1) if x else ""
        txt = re.sub(r"<br\s*/?>", "\n", txt)
        txt = html.unescape(re.sub(r"<[^>]+>", "", txt)).strip()
        v = re.search(r'<span class="tgme_widget_message_views">([^<]+)</span>', blk)
        out.append((ch, int(m.group(1)), d.group(1)[:19], txt, views_num(v.group(1)) if v else None))
    return out


def collect_channel(ch, refresh_days=None):
    """refresh_days를 주면 그 기간 글을 다시 받아 조회수만 갱신(이미 있는 글)."""
    con = sqlite3.connect(DB)
    last = 0 if refresh_days else (con.execute("SELECT MAX(id) FROM posts WHERE ch=?", (ch,)).fetchone()[0] or 0)
    con.close()
    stop_dt = (datetime.now() - timedelta(days=refresh_days or BACKFILL_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    rows, before = [], None
    for _ in range(60):
        t = fetch_page(ch, before)
        got = parse(t, ch)
        if not got:
            break
        rows += got
        mn = min(g[1] for g in got)
        if mn <= last or min(g[2] for g in got) < stop_dt or mn <= 1:
            break
        before = mn
        time.sleep(0.3)
    con = sqlite3.connect(DB)
    con.executemany("UPDATE posts SET views=? WHERE ch=? AND id=?", [(v, c, i) for c, i, d, x, v in rows if v is not None])   # 받은 글은 조회수 갱신
    rows = [r for r in rows if r[1] > last and r[2] >= stop_dt]
    con.executemany("INSERT OR IGNORE INTO posts(ch, id, dt, text, h, views) VALUES (?,?,?,?,?,?)",
                    [(c, i, d, x, hashlib.md5(re.sub(r"\s+", "", x)[:300].encode()).hexdigest(), v) for c, i, d, x, v in rows])
    con.commit(); con.close()
    return ch, len(rows)


def collect(refresh_days=None):
    db().close()
    chs = pd.read_csv(CH_PATH, encoding="utf-8-sig")["채널"].tolist()
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda c: collect_channel(c, refresh_days), chs))
    n = sum(r[1] for r in res)
    print(f"수집: 채널 {len(chs)}개, 새 글 {n}건", {c: k for c, k in res if k})


# ------------------------------------------------------------------ 종목 사전
def universe():
    sm = pd.read_csv(os.path.join(BASE, "data", "sector_map.csv"), dtype=str)
    sm["code"] = sm["code"].str.zfill(6)
    bio = sm[sm["sector"].isin(BIO_NAVER)][["code", "name", "sector"]]
    try:
        import build_sector_ma_breadth as bsm
        _m, members, *_ = bsm.load_universe()
        extra = [(c, s) for s, cs in members.items() if s in BIO_SECTORS for c in cs]
        names = dict(zip(sm["code"], sm["name"]))
        add = pd.DataFrame([{"code": c, "name": names.get(c, c), "sector": s} for c, s in extra])
        bio = pd.concat([bio, add]).drop_duplicates("code")
    except Exception as e:  # noqa: BLE001
        print("섹터 정리 읽기 실패(네이버 업종만 사용):", e)
    bio = bio[~bio["name"].str.contains("스팩|우$|우B$|리츠", regex=True)]
    pats = []
    for _, r in bio.iterrows():
        nm = r["name"]
        keys = {nm} | set(ALIAS.get(nm, []))
        keys = {k for k in keys if len(k) >= 2}
        if not keys:
            continue
        rx = "|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True))
        if nm in STOP_NAMES:   # 영문 짧은 이름은 앞뒤가 영문·숫자가 아닐 때만
            rx = rf"(?<![A-Za-z0-9])(?:{rx})(?![A-Za-z0-9])"
        pats.append((r["code"], nm, r["sector"], re.compile(rx)))
    return pats


# ------------------------------------------------------------------ 요약 글
def load_posts(days):
    con = sqlite3.connect(DB)
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    df = pd.read_sql("SELECT * FROM posts WHERE dt>=?", con, params=(since,))
    con.close()
    ch = pd.read_csv(CH_PATH, encoding="utf-8-sig")
    df = df.merge(ch[["채널", "분류", "이름"]], left_on="ch", right_on="채널", how="left")
    df["dt"] = pd.to_datetime(df["dt"])
    return df


link = lambda ch, i: f'<a href="https://t.me/{ch}/{i}">{html.escape(str(ch))}</a>'
esc = lambda s: html.escape(str(s))


def mentions(df, pats):
    rows = []
    for _, p in df.iterrows():
        for code, nm, sec, rx in pats:
            if rx.search(p["text"] or ""):
                rows.append({"code": code, "name": nm, "sector": sec, "ch": p["ch"], "id": p["id"], "dt": p["dt"], "h": p["h"], "cat": p["분류"], "views": p.get("views")})
    return pd.DataFrame(rows)


def section_rising(M, now):
    if M.empty:
        return "<b>① 언급 급증 종목</b>\n데이터 없음"
    M = M[~M["cat"].fillna("").str.contains("IR")]
    M = M.drop_duplicates(["code", "h"])        # 같은 글 퍼온 건 한 번만
    recent = M[M["dt"] >= now - timedelta(days=7)]
    prior = M[(M["dt"] < now - timedelta(days=7)) & (M["dt"] >= now - timedelta(days=35))]
    r = recent.groupby(["code", "name"]).agg(n=("id", "size"), chs=("ch", "nunique"), v=("views", "sum")).reset_index()
    p = prior.groupby("code").size().rename("p").reset_index()
    r = r.merge(p, on="code", how="left").fillna({"p": 0})
    r["wk"] = r["p"] / 4
    r["ratio"] = (r["n"] + 1) / (r["wk"] + 1)
    r = r[(r["n"] >= 3) & (r["chs"] >= 2)].sort_values(["ratio", "n"], ascending=False).head(12)
    lines = ["<b>① 언급이 늘어난 바이오 종목</b> <i>(최근 7일 vs 직전 4주 주평균, 채널 2곳 이상, 기업 IR 채널 제외 · 막대 = 최근 14일 하루 언급 수 · 조회 = 언급 글 조회수 합)</i>"]
    days = pd.date_range((now - timedelta(days=13)).date(), now.date())
    bars = "▁▂▃▄▅▆▇█"
    for _, x in r.iterrows():
        src = recent[recent["code"] == x["code"]].sort_values("dt", ascending=False).drop_duplicates("ch").head(3)
        cnt = M[M["code"] == x["code"]].groupby(M["dt"].dt.normalize()).size().reindex(days, fill_value=0)
        mx = max(cnt.max(), 1)
        spark = "".join(bars[min(7, int(c / mx * 7))] if c else "·" for c in cnt)
        vv = f" · 조회 {x['v'] / 1e4:.1f}만" if x["v"] and x["v"] > 0 else ""
        lines.append(f"• <b>{esc(x['name'])}</b> {int(x['n'])}회(평소 주 {x['wk']:.1f}회, {int(x['chs'])}개 채널{vv}) <code>{spark}</code> — " + ", ".join(link(s.ch, s.id) for s in src.itertuples()))
    return "\n".join(lines)


def parse_date(m, now):
    g = m.groupdict()
    try:
        if g.get("y"):
            return datetime(int(g["y"]), int(g["m2"]), int(g["d2"]))
        mo, dd = (g.get("m") or g.get("m3")), (g.get("d") or g.get("d3"))
        if mo and dd:
            d = datetime(now.year, int(mo), int(dd))
            return d if d >= now - timedelta(days=200) else datetime(now.year + 1, int(mo), int(dd))
    except ValueError:
        return None
    return None


def section_schedule(df, pats, now):
    items = []
    for p in df.itertuples():
        for sent in re.split(r"(?<=[.!?。])\s+|\n+", p.text or ""):
            s = sent.strip()
            if len(s) < 12 or len(s) > 220 or not EVENT.search(s):
                continue
            dm = DATEPAT.search(s)
            if not dm:
                continue
            comp = [nm for code, nm, sec, rx in pats if rx.search(s)]
            conf = re.search(CONF, s, re.I)
            if not comp and not conf:
                continue
            d = parse_date(dm, now)
            if d is None and not comp:
                continue                                  # 시기만 있는 문장은 국내 바이오 종목이 나올 때만
            if d is not None and d < now - timedelta(days=1):
                continue                                  # 이미 지난 날짜
            items.append({"d": d, "when": dm.group(0), "s": s, "comp": comp[:2], "ch": p.ch, "id": p.id, "dt": p.dt})
    if not items:
        return "<b>② 다가오는 일정·이슈</b>\n찾은 일정 없음"
    I = pd.DataFrame(items)
    I["k"] = I["s"].str.replace(r"\W", "", regex=True).str[:40]
    I = I.sort_values("dt", ascending=False).drop_duplicates("k")
    I["kr"] = I["comp"].map(bool)
    I["c1"] = I["comp"].map(lambda c: c[0] if c else None)
    I = pd.concat([I[I["c1"].isna()], I[I["c1"].notna()].drop_duplicates(["c1", "d"])])   # 같은 회사·같은 날짜 일정은 한 번만
    dated = I[I["d"].notna()].sort_values(["d", "kr"], ascending=[True, False]).groupby("ch").head(3).sort_values("d").head(15)   # 한 채널이 도배하지 않게 채널당 3개
    undated = I[I["d"].isna()].groupby("ch").head(2).head(10)
    lines = ["<b>② 다가오는 바이오 일정·이슈</b> <i>(채널 글에서 날짜·학회와 발표·허가·임상 등 단어가 함께 나온 문장, 원문 그대로)</i>"]
    for x in dated.itertuples():
        lines.append(f"• <b>{x.d:%m/%d}</b> {esc(x.s[:140])} — {link(x.ch, x.id)}")
    if len(undated):
        lines.append("<i>시기만 나온 것</i>")
        for x in undated.itertuples():
            lines.append(f"• <b>{esc(x.when)}</b> {esc(x.s[:140])} — {link(x.ch, x.id)}")
    return "\n".join(lines)


def section_prices(pats):
    codes = {code: (nm, sec) for code, nm, sec, _ in pats}

    def one(code):
        try:
            t = requests.get("https://fchart.stock.naver.com/sise.nhn", params={"symbol": code, "timeframe": "day", "count": 25, "requestType": 0}, headers=H, timeout=15).text
            rows = [r.split("|") for r in re.findall(r'data="([^"]+)"', t)]
            c = [float(r[4]) for r in rows]; v = [float(r[5]) for r in rows]
            if len(c) < 22:
                return None
            return code, rows[-1][0], c[-1] / c[-2] - 1, c[-1] / c[-6] - 1, c[-1] / c[-21] - 1, c[-1] * v[-1], sum(ci * vi for ci, vi in zip(c[-21:-1], v[-21:-1])) / 20
        except Exception:
            return None
    with ThreadPoolExecutor(8) as ex:
        res = [r for r in ex.map(one, list(codes)) if r]
    P = pd.DataFrame(res, columns=["code", "d", "r1", "r5", "r20", "tv", "tv20"])
    P["name"] = P["code"].map(lambda c: codes[c][0]); P["sec"] = P["code"].map(lambda c: codes[c][1])
    P = P[P["tv20"] >= 5e8]                                # 하루 평균 거래대금 5억 미만은 제외
    asof = P["d"].mode().iloc[0]
    live = asof == datetime.now().strftime("%Y%m%d") and datetime.now().hour < 20
    pc = lambda v: f"{v * 100:+.1f}%"
    lines = [f"<b>③ 바이오 주가 동향</b> <i>({asof[:4]}-{asof[4:6]}-{asof[6:]} {'장중 현재가' if live else '종가'}, 하루 평균 거래대금 5억 이상 {len(P)}종목)</i>",
             f"• 전체 중앙값: 1일 {pc(P.r1.median())} · 1주 {pc(P.r5.median())} · 1달 {pc(P.r20.median())} · 1주 오른 종목 {int((P.r5 > 0).mean() * 100)}%"]
    for lab, g in P.groupby("sec"):
        if len(g) >= 8:
            lines.append(f"  - {esc(lab)}({len(g)}): 1주 {pc(g.r5.median())} · 1달 {pc(g.r20.median())}")
    up = P.sort_values("r5", ascending=False).head(7); dn = P.sort_values("r5").head(5)
    hot = P.assign(x=P.tv / P.tv20).sort_values("x", ascending=False).head(5)
    lines.append("• 1주 상승: " + ", ".join(f"{esc(r.name)} {pc(r.r5)}" for r in up.itertuples()))
    lines.append("• 1주 하락: " + ", ".join(f"{esc(r.name)} {pc(r.r5)}" for r in dn.itertuples()))
    lines.append("• 거래대금 급증(최근일/20일 평균): " + ", ".join(f"{esc(r.name)} {r.x:.1f}배({pc(r.r1)})" for r in hot.itertuples()))
    return "\n".join(lines)


def digest():
    now = datetime.now()
    pats = universe()
    df = load_posts(40)
    M = mentions(df, pats)
    parts = [f"<b>🧬 바이오 텔레그램 다이제스트</b> {now:%Y-%m-%d %H:%M}\n<i>공개 채널 {df['ch'].nunique()}곳 최근 글 기준 · 투자 권유 아님 · 각 줄 끝 링크 = 원문</i>",
             section_rising(M, now), section_schedule(df[df["dt"] >= now - timedelta(days=21)], pats, now), section_prices(pats)]
    msg = "\n\n".join(parts)
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write(msg)
    print(f"다이제스트 저장: {OUT_TXT} ({len(msg)}자)")
    return parts


def send(parts=None):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 .env에 없어 보내지 않음")
        return
    if parts is None:
        parts = open(OUT_TXT, encoding="utf-8").read().split("\n\n")
    chunks, cur = [], ""
    for p in parts:                                        # 텔레그램 한 메시지 4096자 제한
        for line in p.split("\n"):
            if len(cur) + len(line) + 1 > 3900:
                chunks.append(cur); cur = ""
            cur += line + "\n"
        cur += "\n"
    if cur.strip():
        chunks.append(cur)
    for c in chunks:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": c, "parse_mode": "HTML", "disable_web_page_preview": "true"}, timeout=30)
        if not r.ok:
            print("보내기 실패:", r.status_code, r.text[:200])
        time.sleep(1)
    print(f"보냄: {len(chunks)}개 메시지")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "refresh":
        collect(refresh_days=BACKFILL_DAYS); sys.exit()
    if cmd in ("collect", "all"):
        collect()
    parts = digest() if cmd in ("digest", "all") else None
    if cmd in ("send", "all"):
        send(parts)
