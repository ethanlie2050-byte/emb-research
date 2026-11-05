# quick screen: per-chunk backtest return and worst drop on 5 key half-years, vs a baseline
import json, sys, pandas as pd
K = ['2022-01-01', '2022-07-01', '2024-01-01', '2025-01-01', '2026-01-01']
def ch(m, s):
    p = f'out/{m}_{s}/logs/equity.jsonl'
    try: e = pd.Series({pd.Timestamp(json.loads(l)['t']): json.loads(l)['equity'] for l in open(p)}).sort_index()
    except Exception: return None
    e = e[e.index >= s]
    return e.iloc[-1] / e.iloc[0] - 1, (e / e.cummax() - 1).min()
base = sys.argv[1]
for m in sys.argv[1:]:
    r = [ch(m, s) for s in K]
    if None in r: print(m, 'incomplete'); continue
    tot = 1
    for x in r: tot *= 1 + x[0]
    print(f"{m:12} " + ' '.join(f"{s[2:7]}:{x[0]:+.0%}/{x[1]:.0%}" for s, x in zip(K, r)) + f" | product {tot-1:+.0%} worst {min(x[1] for x in r):.0%}")
