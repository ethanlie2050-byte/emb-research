import json, pandas as pd, numpy as np, collections, sys
b=['2022-01-01','2022-07-01','2023-01-01','2023-07-01','2024-01-01','2024-07-01','2025-01-01','2025-07-01','2026-01-01','2026-07-01','2026-10-31']
M=sys.argv[1]; R=[]
for i in range(10):
    L=f'out/{M}_{b[i]}/logs/'; s,t=pd.Timestamp(b[i]),pd.Timestamp(b[i+1]); eq={}
    for l in open(L+'decisions.jsonl'):
        d=json.loads(l)
        if d['kind']=='orders': eq[d['trade']]=d['eq']
        if d['kind'] in ('exit','cluster_exit') and s<=pd.Timestamp(d['time'])<t and d.get('trade') in eq:
            R.append((d['eng'],d['coin'],np.log1p(d['pnl']/eq[d['trade']]),d['time'][:4]))
T=pd.DataFrame(R,columns=['eng','coin','lr','yr']); tot=T.lr.sum()
print(M,'trades',len(T),'total log-return from engine trades',round(tot,2))
print(' by engine:', T.groupby('eng').lr.sum().round(2).to_dict())
print(' by coin  :', T.groupby('coin').lr.sum().sort_values(ascending=False).round(2).to_dict())
top=T.lr.sort_values(ascending=False)
print(f' best 10 trades = {top.head(10).sum()/tot:.0%} of profit; best 20 = {top.head(20).sum()/tot:.0%}; without best 20 total log-return {tot-top.head(20).sum():.2f}')
print(' win rate', f'{(T.lr>0).mean():.0%}', 'worst trade', f'{np.expm1(T.lr.min()):.1%}')
