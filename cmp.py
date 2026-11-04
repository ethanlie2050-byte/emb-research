import json, pandas as pd, numpy as np, os, sys
b=['2022-01-01','2022-07-01','2023-01-01','2023-07-01','2024-01-01','2024-07-01','2025-01-01','2025-07-01','2026-01-01','2026-07-01','2026-10-31']
def curve(m, lo=0, hi=int(__import__('os').environ.get('HI','10'))):
    parts=[]
    for i in range(lo,hi):
        p=f'out/{m}_{b[i]}/logs/equity.jsonl'
        if not os.path.exists(p) or 'DONE' not in open(f'logs/{m}_{b[i]}.log').read(): return None
        e=pd.Series({pd.Timestamp(json.loads(l)['t']):json.loads(l)['equity'] for l in open(p)}); e=e[~e.index.duplicated()].sort_index()
        e=e.reindex(pd.date_range(b[i],b[i+1] if i<9 else '2026-10-31 23:45',freq='15min')[:-1 if i<9 else None]).ffill().bfill()
        parts.append(np.log(e/e.shift(1)).fillna(0))
    return pd.concat(parts)
def turnover(M):
    tot=0
    for i in range(int(__import__('os').environ.get('HI','10'))):
        L=f'out/{M}_{b[i]}/logs/'
        eq=pd.Series({pd.Timestamp(json.loads(l)['t']):json.loads(l)['equity'] for l in open(L+'equity.jsonl')}).sort_index()
        for l in open(L+'fills.jsonl'):
            d=json.loads(l); t=pd.Timestamp(d['t'])
            if pd.Timestamp(b[i])<=t<pd.Timestamp(b[i+1]): tot+=d['execQty']*d['execPrice']/eq.asof(t)
    return tot/((pd.Timestamp(b[int(__import__('os').environ.get('HI','10'))])-pd.Timestamp(b[0])).days/365.25)
DRAG=0.6219
def st(r):
    yrs=(r.index[-1]-r.index[0]).days/365.25; n=len(r)/yrs; out=[]
    for drag in (0,DRAG):
        eq=np.exp((r-drag/n).cumsum()); cg=eq.iloc[-1]**(1/yrs)-1; d=eq.resample('D').last(); dd=(d/d.cummax()-1).min()
        mo=d.resample('ME').last().pct_change().dropna(); out.append((cg,dd,mo.min(),(mo>0).mean()))
    return out
print(f"{'version':8} | {'full CAGR':>9} {'DD':>5} {'Cal':>5} {'wMon':>5} {'green':>5} | {'real':>5} {'rDD':>5} | {'train':>6} {'trDD':>5} | {'test':>5} {'teDD':>5}")
for m in sys.argv[1:]:
    r=curve(m)
    if r is None: print(m,'incomplete'); continue
    DRAG=0.6219*turnover(m)/414; globals()['DRAG']=DRAG
    (cg,dd,wm,gr),(rc,rdd,_,_)=st(r); (tc,tdd,_,_),_=st(curve(m,0,6))
    tr=curve(m,6,10); (ec,edd,_,_),_=st(tr) if tr is not None else ((np.nan,np.nan,0,0),0)
    eqi=np.exp(r.cumsum()); idd=(eqi/eqi.cummax()-1).min(); print(f'   {m}: intraday max drop {idd:.0%}, Calmar(intraday) {cg/-idd:.1f}')
    eq=np.exp(r.cumsum()).resample('D').last(); y=eq.resample('YE').last(); y=y/y.shift(1).fillna(1)-1
    print(f"{m:8} | {cg:9.0%} {dd:5.0%} {cg/-dd:5.1f} {wm:5.0%} {gr:5.0%} | {rc:5.0%} {rdd:5.0%} | {tc:6.0%} {tdd:5.0%} | {ec:5.0%} {edd:5.0%} | " + ' '.join(f'{k.year%100}:{v:+.0%}' for k,v in y.items()))
