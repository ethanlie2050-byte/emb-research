# worst-case flash-crash exposure: largest single-coin and total long exposure (x equity) the bot held at any moment
import json, pandas as pd, sys, collections
b=['2022-01-01','2022-07-01','2023-01-01','2023-07-01','2024-01-01','2024-07-01','2025-01-01','2025-07-01','2026-01-01','2026-07-01','2026-10-31']
M=sys.argv[1]; worst_coin=(0,None); worst_tot=(0,None); worst_st=(0,None)
CR=lambda k: 0.15 if k=='BTC' else 0.20 if k=='ETH' else 0.60   # 10 Oct 2025 observed wicks
for i in range(10):
    L=f'out/{M}_{b[i]}/logs/'; pos=collections.defaultdict(float); px={}
    eq=pd.Series({pd.Timestamp(json.loads(l)['t']):json.loads(l)['equity'] for l in open(L+'equity.jsonl')}).sort_index()
    for l in open(L+'fills.jsonl'):
        d=json.loads(l); c=d['symbol'][:-4]; sg=1 if d['side']=='Buy' else -1
        pos[c]+=sg*d['execQty']; px[c]=d['execPrice']; t=pd.Timestamp(d['t'])
        if t<pd.Timestamp(b[i]): continue
        e=eq.asof(t)
        if not e or e<=0: continue
        longs={k:v*px[k]/e for k,v in pos.items() if v>1e-12}
        if longs:
            k=max(longs,key=longs.get)
            if longs[k]>worst_coin[0]: worst_coin=(longs[k],(str(t),k))
            s=sum(longs.values())
            if s>worst_tot[0]: worst_tot=(s,str(t))
            st=sum(v*CR(k) for k,v in longs.items())
            if st>worst_st[0]: worst_st=(st,str(t),{k:round(v,2) for k,v in longs.items()})

print(M,'max single-coin long exposure',round(worst_coin[0],2),'x equity',worst_coin[1],'| max total long',round(worst_tot[0],2),'x',worst_tot[1])
print('  if that coin wicks -60% with the stop filling at -35%..-60%: loss', f'{-0.35*worst_coin[0]:.0%} .. {-0.6*worst_coin[0]:.0%}')
print('  if everything wicks -40% at once (10 Oct 2025 style, alts avg): loss', f'{-0.4*worst_tot[0]:.0%}')
print('  10-Oct-2025-style crash (BTC -15%, ETH -20%, alts -60%) at the worst moment: loss', f'{-worst_st[0]:.0%}', worst_st[1], worst_st[2])
print('  worst-moment holdings detail:', worst_st[2] if len(worst_st)>2 else '')
