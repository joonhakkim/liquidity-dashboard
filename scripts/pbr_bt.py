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
above=P>q90; cross=above&~above.shift(1,fill_value=False)
res={}
for h,lab in((4,"20일"),(12,"60일"),(24,"120일")):
    fw=np.log(M.shift(-h)/M); fw=fw.where(fw.abs()<1.5)
    ex=fw.sub(fw.mean(axis=1),axis=0)
    def stat(mask):
        v=ex.where(mask).stack(); 
        yrs=ex.where(mask).stack().groupby(level=0).mean().groupby(lambda d:d.year).mean()
        return f"n={len(v):6d} 평균{v.mean()*100:+5.2f}%p 중앙{v.median()*100:+5.2f} 승률{(v>0).mean()*100:4.1f}% 연도+{(yrs>0).sum()}/{len(yrs)}"
    allm=ex.notna()
    print(f"== {lab}")
    print(" 상위10% 구간(상태)   ",stat(above))
    print(" 상위10% 첫 돌파      ",stat(cross))
    print(" 중간(10~90%)        ",stat((P<=q90)&(P>=q10)))
    print(" 하위10% 이하        ",stat(P<q10))
    hiR=above&(R>R.rolling(260,min_periods=150).median())
    print(" 상위10% & ROE 상승중   ",stat(above&(R>R.shift(12))))
    print(" 상위10% & ROE 하락/정체",stat(above&(R<=R.shift(12))))
    print(" 상위10% 시총<3000억  ",stat(above&(M<3e11)))
    print(" 상위10% 시총>=3000억 ",stat(above&(M>=3e11)))
