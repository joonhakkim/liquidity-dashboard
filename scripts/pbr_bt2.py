"""PBR 상위 구간 돌파 검증(연구용) - 2026-11-01. 주 단위 표본, 직전 5년 롤링 분위, 전종목 평균 대비 초과수익."""
import numpy as np, pandas as pd, build_pbr_band as b
st=pd.read_pickle(b.EQ_PATH); eq,ni=st["eq"],st["ni"]
mc=b.load_mktcap(); mc=mc[[c for c in mc.columns if c.endswith("0")]]
dates=mc.index; E={}
for code in sorted({c for (c,p) in eq}):
    if code not in mc.columns: continue
    recs=b.build_fund(code,eq,ni)
    if not recs: continue
    recs.sort(key=lambda r:r[0])
    f=pd.DataFrame(recs,columns=["d","eq","ttm","avg"]).drop_duplicates("d",keep="last").set_index("d")
    f=f.reindex(dates.union(f.index)).ffill().reindex(dates); E[code]=f
EQ=pd.DataFrame({c:f["eq"] for c,f in E.items()}); TTM=pd.DataFrame({c:f["ttm"] for c,f in E.items()}); AVG=pd.DataFrame({c:f["avg"] for c,f in E.items()})
mcs=mc[EQ.columns]; PBR=(mcs/EQ.where(EQ>0)).where(mcs>0); ROE=TTM/AVG.where(AVG>0)*100
wk=PBR.index[::5]; P=PBR.loc[wk]; M=mcs.loc[wk]; R=ROE.loc[wk]
q90=P.rolling(260,min_periods=150).quantile(0.9); q10=P.rolling(260,min_periods=150).quantile(0.1); med=P.rolling(260,min_periods=150).median()

import os
TV=os.path.join(b.CACHE,"pbr_tv.pkl")
old=pd.read_pickle(TV) if os.path.exists(TV) else pd.DataFrame(); have=set(old.index); rows={}
for f in sorted(os.listdir(b.KRX_DIR)):
    d=pd.Timestamp(f[:8])
    if d<b.START or d in have: continue
    x=pd.read_pickle(os.path.join(b.KRX_DIR,f))
    if not len(x) or "ACC_TRDVAL" not in x.columns: continue
    sr=pd.Series(pd.to_numeric(x["ACC_TRDVAL"],errors="coerce").values,index=x["ISU_CD"].astype(str).str.zfill(6)); sr=sr[~sr.index.duplicated()]
    rows[d]=pd.concat([rows[d],sr]) if d in rows else sr
if rows:
    new=pd.DataFrame(rows).T; old=pd.concat([old,new]).sort_index(); old=old.groupby(level=0).first(); pd.to_pickle(old,TV)
TVd=old.reindex(columns=mcs.columns)
ratio=(TVd.rolling(5,min_periods=3).mean()/TVd.shift(5).rolling(60,min_periods=40).mean()).reindex(wk)   # 최근 5일 평균 / 그 전 60일 평균
below=(P<=med).rolling(4).max().shift(1).astype(bool)      # 직전 4주 안에 중앙값 이하였음
crossmed=(P>med)&below&(P.shift(1)<=med.shift(1))
cap_ok=M>=1e11
for h,lab in((4,"20일"),(12,"60일"),(24,"120일")):
    fw=np.log(M.shift(-h)/M); fw=fw.where(fw.abs()<1.5); ex=fw.sub(fw.mean(axis=1),axis=0)
    def stat(mask):
        m=mask&cap_ok; v=ex.where(m).stack()
        yrs=v.groupby(level=0).mean().groupby(lambda d:d.year).mean()
        return f"n={len(v):6d} 평균{v.mean()*100:+5.2f}%p 중앙{v.median()*100:+5.2f} 승률{(v>0).mean()*100:4.1f}% 연도+{(yrs>0).sum()}/{len(yrs)}"
    print(f"== {lab} (시총 1000억 이상, 5년 롤링 중앙값)")
    print(" 중앙값 상향 돌파(전체)        ",stat(crossmed))
    for k in (1.5,2,3):
        print(f" 돌파 & 거래대금 {k}배 이상     ",stat(crossmed&(ratio>=k)))
    print(" 돌파 & 거래대금 1.5배 미만     ",stat(crossmed&(ratio<1.5)))
    print(" 돌파 & 거래대금 2배 & ROE 상승 ",stat(crossmed&(ratio>=2)&(R>R.shift(12))))
    print(" 돌파 & 거래대금 2배 & PBR<적정 ",stat(crossmed&(ratio>=2)&(P<(R/100-0.025)/0.075)))
    print(" 비교: 거래대금 2배만(돌파 무관) ",stat((ratio>=2)))
