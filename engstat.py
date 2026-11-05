import json,sys,collections,glob,pandas as pd
M=sys.argv[1]; R=[];S=collections.Counter()
b=['2022-01-01','2022-07-01','2023-01-01','2023-07-01','2024-01-01','2024-07-01','2025-01-01','2025-07-01','2026-01-01','2026-07-01','2026-10-31']
for i in range(10):
    eq={}; ES=pd.Series({pd.Timestamp(json.loads(l)['t']):json.loads(l)['equity'] for l in open(f'out/{M}_{b[i]}/logs/equity.jsonl')}).sort_index()
    for l in open(f'out/{M}_{b[i]}/logs/decisions.jsonl'):
        d=json.loads(l); t=pd.Timestamp(d['time'])
        if t<pd.Timestamp(b[i]): continue
        if d['kind']=='orders': eq[d['trade']]=d['eq']
        if d['kind']=='exit': R.append(dict(t=t,E=d['eng'],c=d['coin'],r=d['pnl']/eq.get(d['trade'],ES.asof(t)),why=d['reason']))
        if d['kind']=='skip': S[(d['eng'],d['coin'],d['why'])]+=1
R=pd.DataFrame(R); R['y']=R.t.dt.year
g=R.groupby(['E','c']).r
print(pd.DataFrame(dict(n=g.size(),tot=g.sum().round(2),win=g.apply(lambda x:(x>0).mean()).round(2),avg=g.mean().round(4),worst=g.min().round(3))))
print(R.pivot_table(index=['E','c'],columns='y',values='r',aggfunc='sum').round(2))
print(R.groupby(['E','why']).r.agg(['size','sum']).round(2))
print(sorted(S.items(),key=lambda x:-x[1])[:15])
