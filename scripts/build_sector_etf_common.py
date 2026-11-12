"""
섹터 ETF 공통 편입종목 페이지(docs/sector_etf_common.html) - 2026-11-12 사용자 요청
("내가 잘하는 ETF에서 공통된 종목들을 뽑아서 섹터단으로 보고, 변화를 매일 확인").

방식
- 네이버 금융 ETF 목록(etfItemList)에서 '국내 업종/테마'(etfTabCode=2) ETF만 쓴다. 레버리지/인버스/커버드콜/선물형,
  시총 100억 미만은 뺀다. 이름의 키워드로 섹터를 나눈다(SECTORS - 이름 기반이라 일부 어긋날 수 있음).
- 섹터마다 1개월 수익률 상위 TOP_N(5)개 ETF를 '잘하는 ETF'로 본다.
- 그 ETF들의 상위 10개 구성종목(네이버 etfAnalysis)을 모아, 몇 개 ETF에 같이 들어 있는지(공통 편입)와 평균 비중을 센다.
  IDM(삼성전자·SK하이닉스·SK스퀘어)은 모든 섹터에서 센 결과에서 뺀다(IDM).
- 매일 스냅샷을 data/etf_sector_common/YYYY-MM-DD.json 으로 쌓고, 직전 스냅샷과 비교해 신규/이탈/ETF 수 변화를 보여준다.
- 기준일 = 네이버 수익률 기준일(returnPerformanceReferenceDate). 휴장일엔 같은 날짜로 덮어쓴다.
스케줄: Windows 작업 'KRX-Sector-ETF'(평일 16:10) -> run_sector_etf.py
"""
import glob
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
SNAP_DIR = os.path.join(BASE_DIR, "data", "etf_sector_common")
OUT_PATH = os.path.join(BASE_DIR, "docs", "sector_etf_common.html")
TOP_N = 8  # 그룹당 '잘하는 ETF' 개수(2026-11-12 5->8)
MIN_MCAP = 100  # 억원
HEADERS = {"User-Agent": "Mozilla/5.0"}
DROP_NAME = re.compile(r"레버리지|인버스|커버드콜|위클리|옵션|2X|선물|채\(")
# 순서가 중요: 먼저 맞는 섹터가 된다('필수소비재'의 '수소', 'K엔터&여행레저'의 '여행' 같은 겹침 때문)
SECTORS = [
    ("게임·미디어·콘텐츠", r"엔터|게임|콘텐츠|컨텐츠|KPOP|K-POP|웹툰|미디어|컬처"),
    ("화장품·소비", r"필수소비재|경기소비재|생활소비재|화장품|뷰티|푸드|소비|여행|골프|농업"),
    ("반도체", r"반도체|HBM|MLCC|비메모리|SK하이닉스밸류|삼성전기"),
    ("2차전지", r"2차전지|전고체|배터리|양극재"),
    ("바이오·헬스", r"바이오|헬스|의료|제약|신약"),
    ("조선", r"조선"),
    ("방산·우주", r"방산|우주|위성"),
    ("원전·전력·에너지", r"원자력|SMR|전력|신재생|태양광|수소|친환경에너지|에너지화학|기후변화|그린뉴딜|AI전력|ESS"),
    ("로봇·모빌리티", r"로봇|휴머노이드|자율주행|스마트카|모빌리티|자동차|전기&수소차"),
    ("금융", r"은행|증권|보험|금융"),
    ("건설·인프라", r"건설|리츠"),
    ("철강·소재", r"철강|소재"),
    ("기계·중공업", r"기계장비|중공업|운송|산업재|CAPEX"),
    ("IT·소프트웨어·AI", r"소프트웨어|인터넷|AI소프트|소버린|IT|플랫폼|메타버스|네트워크|AI플랫폼|온디바이스|AI인프라|광통신|데이터센터|생성형AI|e커머스|AI테크"),
]
W_CAP = 10.0  # ETF 안에서 한 종목 비중이 이보다 크면 10%로 깎아서 평균·최대 비중을 계산(2026-11-12 사용자 요청)
IDM = {"삼성전자", "SK하이닉스", "SK스퀘어"}  # 모든 섹터에서 공통 편입 집계에서 제외(2026-11-12 사용자 요청)


def sector_of(name):
    for s, k in SECTORS:
        if re.search(k, name):
            return s
    return None


def get_json(url):
    for _ in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                return r
        except Exception:
            pass
        time.sleep(1)
    return None


def num(s):
    try:
        return float(str(s).replace("%", "").replace(",", ""))
    except ValueError:
        return None


def pick_etfs():
    r = get_json("https://finance.naver.com/api/sise/etfItemList.nhn")
    r.encoding = "euc-kr"
    items = r.json()["result"]["etfItemList"]
    by_sec = {}
    for it in items:
        if it.get("etfTabCode") != 2 or DROP_NAME.search(it["itemname"]):
            continue
        if (it.get("marketSum") or 0) < MIN_MCAP or it.get("threeMonthEarnRate") is None:
            continue
        s = sector_of(it["itemname"])
        if s:  # 2026-11-12: 13개 섹터 대신 반도체 / 비반도체 두 그룹으로만 나눈다(분류 못 한 스타일·그룹주 ETF는 계속 제외)
            by_sec.setdefault("반도체" if s == "반도체" else "비반도체", []).append(it)
    return by_sec


def fetch_detail(code):
    r = get_json(f"https://m.stock.naver.com/api/stock/{code}/etfAnalysis")
    if r is None:
        return code, None
    try:
        return code, r.json()
    except Exception:
        return code, None


def perf1m(d):
    for p in (d.get("returnPerformanceList") or []):
        if p.get("periodTypeCode") == "M1":
            return p.get("value")
    return None


def build_snapshot():
    by_sec = pick_etfs()
    codes = sorted({e["itemcode"] for v in by_sec.values() for e in v})
    with ThreadPoolExecutor(6) as ex:
        det = dict(ex.map(fetch_detail, codes))
    failed = [c for c in codes if det.get(c) is None]
    if len(failed) > len(codes) * 0.3:
        raise RuntimeError(f"ETF 상세 조회 실패 과다: {len(failed)}/{len(codes)}")
    chosen = {}
    for s, lst in by_sec.items():
        ok = [e for e in lst if det.get(e["itemcode"]) and perf1m(det[e["itemcode"]]) is not None]
        ok.sort(key=lambda e: -perf1m(det[e["itemcode"]]))
        chosen[s] = {"n_all": len(ok), "etfs": ok[:TOP_N]}
    asof = None
    for d in det.values():
        if d and d.get("returnPerformanceReferenceDate"):
            asof = str(d["returnPerformanceReferenceDate"]).replace(".", "-")[:10]
            break
    asof = asof or datetime.now().strftime("%Y-%m-%d")

    secs = {}
    for s, v in chosen.items():
        etfs, hold = [], {}
        for e in v["etfs"]:
            d = det.get(e["itemcode"])
            if not d:
                continue
            perf = {p["periodTypeCode"]: p["value"] for p in (d.get("returnPerformanceList") or [])}
            etfs.append({"code": e["itemcode"], "name": e["itemname"], "r3": e["threeMonthEarnRate"],
                         "r1": perf1m(d), "r6": perf.get("M6"), "ytd": perf.get("YTD"), "mcap": e["marketSum"]})
            for x in d.get("etfTop10MajorConstituentAssets") or []:
                if x["itemName"] in IDM:
                    continue
                h = hold.setdefault(x["itemCode"], {"c": x["itemCode"], "name": x["itemName"], "etfs": [], "ws": [], "wl": {}, "sc": {}})
                h["etfs"].append(e["itemname"])
                w = num(x.get("etfWeight"))
                if w:
                    w = min(w, W_CAP)
                if w:
                    h["ws"].append(w)
                    h["wl"][e["itemname"]] = w
                sc = num(x.get("stockCount"))  # CU(설정단위)당 보유 주식수 - 주가와 무관해서 ETF가 실제로 더 담았는지 보여줌
                if sc:
                    h["sc"][e["itemname"]] = sc
        stocks = []
        for h in hold.values():
            stocks.append({"c": h["c"], "name": h["name"], "k": len(h["etfs"]),
                           "w": round(sum(h["ws"]) / len(h["ws"]), 1) if h["ws"] else None, "etfs": h["etfs"],
                           "wl": h["wl"], "sc": h["sc"], "wm": max(h["ws"]) if h["ws"] else None})
        stocks.sort(key=lambda x: (-x["k"], -(x["w"] or 0)))
        secs[s] = {"n_all": v["n_all"], "n": len(etfs), "etfs": etfs, "stocks": stocks}
    return {"asof": asof, "sectors": secs}


def load_history():
    snaps = []
    for p in sorted(glob.glob(os.path.join(SNAP_DIR, "*.json"))):
        with open(p, encoding="utf-8") as f:
            snaps.append(json.load(f))
    return snaps


def compare(cur, prev):
    """섹터별 변화: 신규/이탈 공통종목, ETF 수 변화, 상위 ETF 교체, ETF별 신규 편입/제외 종목(비중 포함)."""
    out = {}
    for s, c in cur["sectors"].items():
        p = (prev or {}).get("sectors", {}).get(s)
        if not p:
            out[s] = None
            continue
        pk = {x["c"]: x["k"] for x in p["stocks"]}
        pmap = {x["c"]: set(x["etfs"]) for x in p["stocks"]}
        ck = {x["c"]: x for x in c["stocks"]}
        pe = {e["code"]: e["name"] for e in p["etfs"]}
        ce = {e["code"]: e["name"] for e in c["etfs"]}
        both = set(pe.values()) & set(ce.values())  # 어제와 오늘 둘 다 상위 ETF인 것만 비교(ETF 교체로 생기는 잡음 제외)
        new = [x["name"] for x in c["stocks"] if x["k"] >= 2 and pk.get(x["c"], 0) < 2]
        gone = [x["name"] for x in p["stocks"] if x["k"] >= 2 and (ck.get(x["c"]) or {"k": 0})["k"] < 2]
        delta = {x["c"]: x["k"] - pk.get(x["c"], 0) for x in c["stocks"]}
        adds, drops = [], []
        for x in c["stocks"]:
            was = pmap.get(x["c"], set())
            add_e = [e for e in x["etfs"] if e in both and e not in was]
            if add_e:
                ws = [(x.get("wl") or {}).get(e) for e in add_e if (x.get("wl") or {}).get(e)]
                adds.append({"name": x["name"], "etfs": add_e, "w": max(ws) if ws else None, "brand": x["c"] not in pmap})
        for x in p["stocks"]:
            now = set((ck.get(x["c"]) or {"etfs": []})["etfs"])
            drop_e = [e for e in x["etfs"] if e in both and e not in now]
            if drop_e:
                drops.append({"name": x["name"], "etfs": drop_e})
        adds.sort(key=lambda a: -(a["w"] or 0))
        out[s] = {"new": new, "gone": gone, "delta": delta, "adds": adds, "drops": drops,
                  "etf_in": [n for k, n in ce.items() if k not in pe], "etf_out": [n for k, n in pe.items() if k not in ce]}
    return out


HORIZONS = (1, 5, 20)


def trend(cur, ref):
    """ref(과거 스냅샷) 대비 섹터별 '최근 많이 담기 시작한 종목'. 수량 = ETF의 CU당 보유 주식수(주가와 무관)."""
    out = {}
    for s, c in cur["sectors"].items():
        p = ref["sectors"].get(s)
        if not p:
            continue
        pm = {x["c"]: x for x in p["stocks"]}
        both = {e["name"] for e in p["etfs"]} & {e["name"] for e in c["etfs"]}
        rows = []
        for x in c["stocks"]:
            o = pm.get(x["c"])
            cmp_n = up = 0
            chs = []
            if o:
                for e, v in (x.get("sc") or {}).items():
                    ov = (o.get("sc") or {}).get(e)
                    if e in both and ov:
                        cmp_n += 1
                        ch = (v / ov - 1) * 100
                        chs.append(ch)
                        if ch > 0.5:
                            up += 1
            dk = x["k"] - (o["k"] if o else 0)
            dw = round((x["w"] or 0) - (o["w"] or 0), 1) if o and x["w"] is not None and o.get("w") is not None else None
            new = o is None and any(e in both for e in x["etfs"])
            if up > 0 or dk > 0 or new:
                rows.append({"name": x["name"], "k": x["k"], "dk": dk, "dw": dw, "up": up, "cmp": cmp_n,
                             "mean": round(sum(chs) / len(chs), 1) if chs else None, "new": new, "w": x["w"]})
        rows.sort(key=lambda r: (-r["up"], -(r["mean"] or 0), -r["dk"], -(r["w"] or 0)))
        out[s] = rows[:10]
    return out


def build_page(snaps):
    cur = snaps[-1]
    prev = snaps[-2] if len(snaps) > 1 else None
    chg = compare(cur, prev)
    dates = [s["asof"] for s in snaps]
    tr = {}
    for h in HORIZONS:
        if len(snaps) > h:
            tr[h] = {"ref": snaps[-1 - h]["asof"], "rows": trend(cur, snaps[-1 - h])}
    sectors = []
    for s, v in sorted(cur["sectors"].items(), key=lambda kv: -kv[1]["n"]):
        if v["n"] < 2:
            continue
        ch = chg.get(s)
        stocks = []
        for x in v["stocks"][:25]:
            hist = []
            for sn in snaps:
                sv = sn["sectors"].get(s)
                m = next((y for y in sv["stocks"] if y["c"] == x["c"]), None) if sv else None
                hist.append(m["k"] if m else (0 if sv else None))
            stocks.append({"c": x["c"], "name": x["name"], "k": x["k"], "w": x["w"], "wm": x.get("wm"), "etfs": x["etfs"],
                           "d": (ch["delta"].get(x["c"], x["k"]) if ch else None), "h": hist})
        sectors.append({"name": s, "n": v["n"], "n_all": v["n_all"], "etfs": v["etfs"], "stocks": stocks,
                        "ex": sorted(IDM), "tr": {str(h): t["rows"].get(s, []) for h, t in tr.items()}, "chg": ({k: ch[k] for k in ("new", "gone", "etf_in", "etf_out", "adds", "drops")} if ch else None)})
    data = {"asof": cur["asof"], "prev": prev["asof"] if prev else None, "dates": dates, "topN": TOP_N, "trRef": {str(h): t["ref"] for h, t in tr.items()}, "sectors": sectors}
    html = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("__ASOF__", cur["asof"]).replace("__UPDATED__", datetime.now().strftime("%m/%d %H:%M"))
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"저장 완료: {OUT_PATH} (섹터 {len(sectors)}개, 스냅샷 {len(snaps)}일)")


def main():
    snap = build_snapshot()
    os.makedirs(SNAP_DIR, exist_ok=True)
    with open(os.path.join(SNAP_DIR, snap["asof"] + ".json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, separators=(",", ":"))
    build_page(load_history())


TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>섹터 ETF 공통 편입종목</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body { font-family: -apple-system, "Malgun Gothic", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  a.back { color:#4dabf7; font-size:13px; text-decoration:none; margin-right:12px; }
  h1 { font-size:20px; margin:8px 0 4px 0; } h1 .sub { font-size:13px; color:#9aa0a6; font-weight:normal; margin-left:6px; }
  .updated { color:#9aa0a6; font-size:13px; margin-bottom:14px; }
  .ctl { display:flex; gap:14px; flex-wrap:wrap; align-items:center; font-size:12px; color:#c9ccd1; margin-bottom:14px; }
  .ctl select { background:#0f1115; color:#e6e6e6; border:1px solid #2a2e37; border-radius:6px; padding:4px 6px; font-family:inherit; font-size:12px; }
  .seg button { background:#1a1d24; border:1px solid #2a2e37; color:#c9ccd1; padding:5px 11px; border-radius:999px; cursor:pointer; font-size:12px; font-family:inherit; margin-right:4px; }
  .seg button.active { background:#4dabf7; color:#0f1115; border-color:#4dabf7; font-weight:bold; }
  .box { background:#12151b; border:1px solid #23262e; border-radius:12px; padding:14px 16px; margin-bottom:16px; }
  .box h2 { font-size:15px; margin:0 0 6px 0; } .box .desc { font-size:12px; color:#9aa0a6; margin-bottom:8px; line-height:1.6; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  th, td { padding:6px 8px; border-bottom:1px solid #23262e; text-align:right; white-space:nowrap; }
  th:first-child, td:first-child { text-align:left; }
  td.etfs { white-space:normal; text-align:left; color:#9aa0a6; font-size:11px; min-width:220px; }
  th { color:#9aa0a6; font-weight:normal; }
  tr.stk:hover { background:#1a1f28; cursor:pointer; } tr.sel { background:#1c2433; }
  .pos { color:#ff8787; } .neg { color:#74a7ff; }
  .badge { display:inline-block; font-size:10px; padding:1px 6px; border-radius:999px; background:#2b3a1f; color:#a9e34b; margin-left:4px; }
  .badge.out { background:#3a1f1f; color:#ff8787; }
  .tblwrap { overflow-x:auto; }
  .chg { font-size:12px; line-height:1.9; color:#c9ccd1; }
  .chg b { color:#ffa94d; }
  .chart-wrap { position:relative; height:240px; }
  .note { font-size:12px; color:#9aa0a6; line-height:1.7; background:#1a1d24; border-radius:10px; padding:12px 16px; }
  .note b { color:#ffa94d; }
  @media (max-width: 640px) { body { padding:16px; } }
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; 홈</a>
  <h1>섹터 ETF 공통 편입종목<span class="sub">섹터별 1개월 수익률 상위 ETF들이 같이 담은 종목</span></h1>
  <div class="updated">최종 갱신: __UPDATED__ &middot; 데이터 기준일: __ASOF__ &middot; 매일 자동 갱신</div>

  <div class="ctl">
    <span>섹터</span><select id="sel"></select>
    <span>표시 종목</span><span class="seg" id="segN"><button data-n="10" class="active">10개</button><button data-n="20">20개</button><button data-n="25">전체</button></span>
    <span>정렬</span><span class="seg" id="segS"><button data-s="k" class="active">편입 ETF 수</button><button data-s="w">평균 비중</button><button data-s="wm">최대 비중</button></span>
    <span>최소 편입 ETF 수</span><span class="seg" id="segK"><button data-k="1">1+</button><button data-k="2" class="active">2+</button><button data-k="3">3+</button><button data-k="4">4+</button></span>
  </div>

  <div class="box" id="todayBox">
    <h2>오늘의 변화 <span style="font-size:12px;font-weight:normal;color:#9aa0a6" id="todaySub"></span></h2>
    <div class="chg" id="today"></div>
  </div>

  <div class="box">
    <h2>최근 많이 담기 시작한 종목 <span style="font-size:12px;font-weight:normal;color:#9aa0a6" id="trSub"></span></h2>
    <div class="desc">같은 ETF가 그 종목을 <b>실제로 더 사 담았는지</b>를 봅니다(ETF의 CU당 보유 주식수 변화 - 주가가 올라 비중이 커진 것과 구분됩니다). 수량↑ ETF = 보유 주식수를 0.5% 넘게 늘린 ETF 수 / 비교 가능한 ETF 수. 어제와 오늘 둘 다 상위 ETF인 것만 비교합니다.</div>
    <div class="ctl"><span>비교 기간</span><span class="seg" id="segT"><button data-t="1" class="active">1일 전</button><button data-t="5">5일 전</button><button data-t="20">20일 전</button></span></div>
    <div class="tblwrap"><table id="trTbl"></table></div>
  </div>

  <div class="box">
    <h2 id="etfTitle">상위 ETF</h2>
    <div class="desc">섹터 안에서 1개월 수익률 상위 ETF(수익률은 %). 순위는 매일 바뀔 수 있습니다.</div>
    <div class="tblwrap"><table id="etfTbl"></table></div>
  </div>

  <div class="box">
    <h2 id="stkTitle">공통 편입종목</h2>
    <div class="desc">상위 ETF들의 상위 10개 구성종목 기준(네이버가 하루 한 번 갱신하는 값으로, 장중에는 바뀌지 않음). 편입 ETF 수 = 이 종목을 담은 상위 ETF 개수, 평균 비중 = 비중이 공개된 ETF의 평균, 최대 비중 = 그중 가장 큰 ETF의 비중(ETF 안에서 10%를 넘는 비중은 10%로 깎아서 계산), 전일 = 직전 기준일 대비 편입 ETF 수 변화(없던 종목이 새로 들어오면 '신규'). 정렬 버튼으로 비중 높은 순도 볼 수 있습니다. 행을 누르면 아래에 추이가 나옵니다.</div>
    <div class="tblwrap"><table id="stkTbl"></table></div>
  </div>

  <div class="box">
    <h2 id="hTitle">편입 ETF 수 추이</h2>
    <div class="chart-wrap"><canvas id="cHist"></canvas></div>
    <div class="desc" id="hNote" style="margin-top:6px"></div>
  </div>

  <div class="note">
    <b>읽는 법</b> &middot; 네이버 금융의 '국내 업종/테마' ETF 중 레버리지·인버스·커버드콜·선물형과 시총 100억 미만을 뺀 뒤, ETF 이름의 키워드로 섹터를 나눴습니다(이름 기준이라 일부는 어긋날 수 있음).
    반도체와 비반도체(나머지 모든 섹터 ETF)로 나눠 각각 1개월 수익률 상위 __TOPN__개를 '잘하는 ETF'로 보고, 이들의 상위 10개 구성종목을 모아 몇 개 ETF에 같이 들어 있는지 셉니다. 비중이 비공개인 액티브 ETF는 평균 비중에서 빠집니다.
    IDM(삼성전자·SK하이닉스·SK스퀘어)은 모든 섹터에서 센 결과에서 뺐습니다. ETF가 2개 미만인 섹터는 비교가 의미 없어 숨겼고, 5개 미만인 섹터는 비율이 크게 흔들립니다. 추이는 이 페이지가 매일 쌓은 기록부터 보입니다.
  </div>

<script>
const D = __DATA__;
let curSec = 0, showN = 10, minK = 2, sortBy = 'k', trH = '1', selStock = null, chart = null;
const $ = id => document.getElementById(id);
const cls = v => v > 0 ? 'pos' : (v < 0 ? 'neg' : '');
const fp = v => v == null ? '-' : (v > 0 ? '+' : '') + v.toFixed(1) + '%';

D.sectors.forEach((s, i) => { const o = document.createElement('option'); o.value = i; o.textContent = s.name + ' (' + s.n + ')'; $('sel').appendChild(o); });
$('sel').onchange = e => { curSec = +e.target.value; selStock = null; render(); };
document.querySelectorAll('#segK button').forEach(b => b.onclick = () => {
  document.querySelectorAll('#segK button').forEach(x => x.classList.remove('active')); b.classList.add('active');
  minK = +b.dataset.k; selStock = null; render();
});
document.querySelectorAll('#segN button').forEach(b => b.onclick = () => {
  document.querySelectorAll('#segN button').forEach(x => x.classList.remove('active')); b.classList.add('active');
  showN = +b.dataset.n; selStock = null; render();
});
document.querySelectorAll('#segT button').forEach(b => b.onclick = () => {
  document.querySelectorAll('#segT button').forEach(x => x.classList.remove('active')); b.classList.add('active');
  trH = b.dataset.t; render();
});
document.querySelectorAll('#segS button').forEach(b => b.onclick = () => {
  document.querySelectorAll('#segS button').forEach(x => x.classList.remove('active')); b.classList.add('active');
  sortBy = b.dataset.s; render();
});

function renderToday() {
  if (!D.prev) { $('todaySub').textContent = ''; $('today').innerHTML = '첫 기록일입니다. 내일부터 전일 대비 변화가 표시됩니다.'; return; }
  $('todaySub').textContent = '(' + D.prev + ' 대비)';
  const rows = [];
  D.sectors.forEach(s => {
    const c = s.chg; if (!c) return;
    const p = [];
    if (c.adds && c.adds.length) p.push('<span class="pos">신규 편입</span> ' + c.adds.slice(0, 8).map(a => a.name + (a.brand ? '(새 종목)' : '') + ' ' + (a.w ? a.w.toFixed(1) + '%' : '') + ' [' + a.etfs.map(n => n.replace(/^(KODEX|TIGER|SOL|ACE|RISE|PLUS|HANARO|KoAct|WON|TIME|1Q|BNK|DAISHIN|KIWOOM|마이티|UNICORN|IBK)\s*/, '')).join(', ') + ']').join(' · '));
    if (c.drops && c.drops.length) p.push('<span class="neg">제외</span> ' + c.drops.slice(0, 8).map(a => a.name + ' [' + a.etfs.map(n => n.replace(/^(KODEX|TIGER|SOL|ACE|RISE|PLUS|HANARO|KoAct|WON|TIME|1Q|BNK|DAISHIN|KIWOOM|마이티|UNICORN|IBK)\s*/, '')).join(', ') + ']').join(' · '));
    if (c.new.length) p.push('<span class="pos">신규 공통</span> ' + c.new.join(', '));
    if (c.gone.length) p.push('<span class="neg">공통 이탈</span> ' + c.gone.join(', '));
    if (c.etf_in.length) p.push('상위 ETF 진입 ' + c.etf_in.join(', '));
    if (c.etf_out.length) p.push('상위 ETF 탈락 ' + c.etf_out.join(', '));
    if (p.length) rows.push('<b>' + s.name + '</b> &middot; ' + p.join(' &middot; '));
  });
  $('today').innerHTML = rows.length ? rows.join('<br>') : '변화가 없습니다.';
}

function renderTrend(s) {
  const ref = D.trRef[trH];
  if (!ref) {
    $('trSub').textContent = '';
    $('trTbl').innerHTML = '<tr><td style="text-align:left;color:#9aa0a6">아직 ' + trH + '일 전 기록이 없습니다. 기록이 ' + (+trH + 1) + '일 이상 쌓이면 표시됩니다(현재 ' + D.dates.length + '일치).</td></tr>';
    return;
  }
  $('trSub').textContent = '(' + ref + ' 대비)';
  const rows = (s.tr || {})[trH] || [];
  let t = '<tr><th>종목</th><th>수량↑ ETF</th><th>수량 변화(평균)</th><th>편입 ETF 수</th><th>평균 비중 변화</th></tr>';
  rows.forEach(r => {
    const dk = r.dk > 0 ? '+' + r.dk : (r.dk < 0 ? r.dk : '-');
    t += '<tr><td>' + r.name + (r.new ? '<span class="badge">신규</span>' : '') + '</td><td>' + (r.cmp ? r.up + ' / ' + r.cmp : '-') + '</td><td class="' + cls(r.mean) + '">' +
      (r.mean == null ? '-' : (r.mean > 0 ? '+' : '') + r.mean.toFixed(1) + '%') + '</td><td>' + r.k + (r.dk ? ' (' + dk + ')' : '') + '</td><td class="' + cls(r.dw) + '">' +
      (r.dw == null ? '-' : (r.dw > 0 ? '+' : '') + r.dw.toFixed(1) + '%p') + '</td></tr>';
  });
  if (!rows.length) t += '<tr><td colspan="5" style="text-align:left;color:#9aa0a6">이 기간에 새로 담기 시작한 종목이 없습니다.</td></tr>';
  $('trTbl').innerHTML = t;
}

function render() {
  const s = D.sectors[curSec];
  renderTrend(s);
  $('etfTitle').textContent = s.name + ' 상위 ' + s.n + '개 ETF (섹터 내 ' + s.n_all + '개 중)';
  let h = '<tr><th>ETF</th><th>1개월</th><th>3개월</th><th>6개월</th><th>연초 이후</th><th>시총(억)</th></tr>';
  s.etfs.forEach(e => { h += '<tr><td>' + e.name + '</td><td class="' + cls(e.r1) + '">' + fp(e.r1) + '</td><td class="' + cls(e.r3) + '">' + fp(e.r3) +
    '</td><td class="' + cls(e.r6) + '">' + fp(e.r6) + '</td><td class="' + cls(e.ytd) + '">' + fp(e.ytd) + '</td><td>' + (e.mcap || 0).toLocaleString() + '</td></tr>'; });
  $('etfTbl').innerHTML = h;

  const rows = s.stocks.filter(x => x.k >= minK).slice();
  const key = { k: x => x.k * 1000 + (x.w || 0), w: x => x.w || 0, wm: x => x.wm || 0 }[sortBy];
  rows.sort((a, b) => key(b) - key(a));
  rows.splice(showN);
  $('stkTitle').textContent = s.name + ' 공통 편입종목' + (s.ex.length ? ' (IDM 제외)' : '');
  let t = '<tr><th>종목</th><th>편입 ETF</th><th>평균 비중</th><th>최대 비중</th><th>전일</th><th style="text-align:left">편입 ETF 목록</th></tr>';
  rows.forEach(x => {
    const dl = x.d == null ? '-' : (x.d > 0 ? '+' + x.d : x.d === 0 ? '-' : x.d);
    const nw = (x.d != null && x.d === x.k) ? '<span class="badge">신규</span>' : '';
    t += '<tr class="stk' + (selStock === x.c ? ' sel' : '') + '" data-c="' + x.c + '"><td>' + x.name + nw + '</td><td>' + x.k + ' / ' + s.n + '</td><td>' +
      (x.w == null ? '-' : x.w.toFixed(1) + '%') + '</td><td>' + (x.wm == null ? '-' : x.wm.toFixed(1) + '%') + '</td><td class="' + (x.d > 0 ? 'pos' : x.d < 0 ? 'neg' : '') + '">' + dl + '</td><td class="etfs">' + x.etfs.join(' · ') + '</td></tr>';
  });
  if (!rows.length) t += '<tr><td colspan="6" style="text-align:left;color:#9aa0a6">조건에 맞는 종목이 없습니다.</td></tr>';
  $('stkTbl').innerHTML = t;
  document.querySelectorAll('tr.stk').forEach(r => r.onclick = () => { selStock = r.dataset.c; render(); });
  drawHist(s, rows);
}

function drawHist(s, rows) {
  const x = s.stocks.find(y => y.c === selStock) || rows[0];
  if (chart) { chart.destroy(); chart = null; }
  if (!x) { $('hTitle').textContent = '편입 ETF 수 추이'; $('hNote').textContent = ''; return; }
  $('hTitle').textContent = x.name + ' - 편입 ETF 수 추이 (' + s.name + ')';
  $('hNote').textContent = D.dates.length < 2 ? '아직 기록이 하루뿐입니다. 매일 쌓이면 추이가 그려집니다.' : D.dates.length + '일치 기록';
  chart = new Chart($('cHist'), { type: 'line',
    data: { labels: D.dates, datasets: [{ label: '편입 ETF 수', data: x.h, borderColor: '#4dabf7', backgroundColor: '#4dabf7', stepped: true, pointRadius: 3, spanGaps: true }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { y: { min: 0, max: s.n, ticks: { stepSize: 1, color: '#9aa0a6' }, grid: { color: '#23262e' } }, x: { ticks: { color: '#9aa0a6', maxTicksLimit: 8 }, grid: { color: '#23262e' } } } } });
}
renderToday(); render();
</script>
</body>
</html>
"""
TEMPLATE = TEMPLATE.replace("__TOPN__", str(TOP_N))

if __name__ == "__main__":
    main()
