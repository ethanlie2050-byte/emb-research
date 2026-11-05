# crash loss (BTC -15%, ETH -20%) at the worst moment, counting only holdings kept for at least 15 minutes
import json, pandas as pd, sys, collections
b=['2022-01-01','2022-07-01','2023-01-01','2023-07-01','2024-01-01','2024-07-01','2025-01-01','2025-07-01','2026-01-01','2026-07-01','2026-10-31']
CR=lambda k: 0.15 if k=='BTC' else 0.20 if k=='ETH' else 0.60
for M in sys.argv[1:]:
    worst=(0,None,None)
    for i in range(10):
        L=f'out/{M}_{b[i]}/logs/'; pos=collections.defaultdict(float); px={}
        eq=pd.Series({pd.Timestamp(json.loads(l)['t']):json.loads(l)['equity'] for l in open(L+'equity.jsonl')}).sort_index()
        F=[json.loads(l) for l in open(L+'fills.jsonl')]
        for n,d in enumerate(F):
            c=d['symbol'][:-4]; pos[c]+=(1 if d['side']=='Buy' else -1)*d['execQty']; px[c]=d['execPrice']; t=pd.Timestamp(d['t'])
            nxt=pd.Timestamp(F[n+1]['t']) if n+1<len(F) else pd.Timestamp(b[i+1])
            if t<pd.Timestamp(b[i]) or (nxt-t)<pd.Timedelta('15min'): continue
            e=eq.asof(t)
            if not e or e<=0: continue
            st=sum(v*px[k]/e*CR(k) for k,v in pos.items() if v>1e-12)
            if st>worst[0]: worst=(st,str(t),{k:round(v*px[k]/e,2) for k,v in pos.items() if v>1e-12})
    print(f'{M} crash loss (held >=15min): {-worst[0]:.0%} at {worst[1]} {worst[2]}')
