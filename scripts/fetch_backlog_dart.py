"""통신장비 수주잔고: DART 정기보고서 본문의 '수주상황' 구간을 뽑아 data/backlog/*.txt 로 저장."""
import io, os, re, sys, time, zipfile
import xml.etree.ElementTree as ET
import requests
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
KEY = os.environ["DART_API_KEY"]
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "backlog")
CO = {"050890":"쏠리드","032500":"케이엠더블유","088800":"에이스테크","218410":"RFHIC","264450":"유비쿼스",
      "056360":"코위버","039560":"다산네트웍스","178320":"서진시스템","115440":"우리넷","230240":"에치에프알",
      "078070":"유비쿼스홀딩스","100590":"머큐리"}
def corp_map():
    r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": KEY}, timeout=120)
    z = zipfile.ZipFile(io.BytesIO(r.content)); root = ET.fromstring(z.read(z.namelist()[0]))
    return {e.findtext("stock_code"): e.findtext("corp_code") for e in root.iter("list") if (e.findtext("stock_code") or "").strip()}
def reports(cc):
    out = []; pg = 1
    while True:
        j = requests.get("https://opendart.fss.or.kr/api/list.json", params={"crtfc_key":KEY,"corp_code":cc,"bgn_de":"20230101","pblntf_ty":"A","page_count":100,"page_no":pg}, timeout=60).json()
        out += j.get("list", [])
        if pg >= j.get("total_page", 1): break
        pg += 1
    return [x for x in out if re.search(r"(사업|반기|분기)보고서", x["report_nm"]) and "기재정정" not in x["report_nm"]]
def doc_text(rcept):
    r = requests.get("https://opendart.fss.or.kr/api/document.xml", params={"crtfc_key":KEY,"rcept_no":rcept}, timeout=120)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    return z.read(z.namelist()[0]).decode("utf-8", "ignore")
def section(xml):
    t = re.sub(r"<[^>]+>", "|", xml); t = re.sub(r"\|+", "|", t); t = re.sub(r"[ \t\r\n]+", " ", t)
    ms = [m.start() for m in re.finditer(r"수주\s*상황", t)]
    # 본문 표가 있는 구간: '수주' 뒤 3천자 안에 숫자 표가 있는 마지막/가장 긴 후보
    best = ""
    for s in ms:
        seg = t[s:s+2500]
        if re.search(r"수주총액|수주잔고|잔고|기납품액", seg) and len(re.findall(r"\d[\d,]{3,}", seg)) > 4:
            best = seg; break
    return best
if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    cm = corp_map()
    for sc, nm in CO.items():
        cc = cm.get(sc)
        if not cc: print("no corp", nm); continue
        for r in sorted(reports(cc), key=lambda x: x["rcept_dt"]):
            f = os.path.join(OUT, f"{sc}_{r['rcept_dt']}_{r['report_nm'].split()[0]}.txt")
            if os.path.exists(f): continue
            try: s = section(doc_text(r["rcept_no"]))
            except Exception as e: s = f"ERR {e}"
            open(f, "w", encoding="utf-8").write(f"{nm}|{r['report_nm']}|{r['rcept_dt']}\n{s}\n")
            print(nm, r["report_nm"], r["rcept_dt"], len(s)); time.sleep(0.2)
