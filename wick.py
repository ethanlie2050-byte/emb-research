# worst drawdown including price moves INSIDE each 15m candle (equity log only has candle closes)
import json, sys, numpy as np, pandas as pd, collections
b = ['2022-01-01','2022-07-01','2023-01-01','2023-07-01','2024-01-01','2024-07-01','2025-01-01','2025-07-01','2026-01-01','2026-07-01','2026-10-31']
PX = {c: pd.read_parquet(f'data/{c}.parquet').astype(float) for c in ('BTC', 'ETH')}
for M in sys.argv[1:]:
    worst_all = 0.0; scale = 1.0; pk = 1.0; mdd = 0.0
    for i in range(10):
        L = f'out/{M}_{b[i]}/logs/'
        eq = pd.Series({pd.Timestamp(json.loads(l)['t']): json.loads(l)['equity'] for l in open(L + 'equity.jsonl')}).sort_index()
        eq = eq[~eq.index.duplicated()]
        F = [json.loads(l) for l in open(L + 'fills.jsonl')]
        idx = pd.date_range(b[i], pd.Timestamp(b[i + 1]) - pd.Timedelta('15min'), freq='15min')
        pos = {c: np.zeros(len(idx)) for c in PX}; p = collections.defaultdict(float); k = 0
        Ft = [(pd.Timestamp(d['t']), d) for d in F]
        for j, t in enumerate(idx):
            while k < len(Ft) and Ft[k][0] < t + pd.Timedelta('15min'):   # fills during the bar count from the bar (conservative)
                d = Ft[k][1]; c = d['symbol'][:-4]
                if c in PX: p[c] += (1 if d['side'] == 'Buy' else -1) * d['execQty']
                k += 1
            for c in PX: pos[c][j] = p[c]
        e = eq.reindex(idx + pd.Timedelta('15min'), method='ffill').values
        e = np.where(np.isnan(e), eq.iloc[0], e)
        low = e.copy()
        for c in PX:
            P = PX[c].reindex(idx)
            q = pos[c]
            low = low - np.where(q > 0, q * (P.close.values - P.low.values), -q * (P.high.values - P.close.values))
        e0 = e[0] if i == 0 else e[0]
        # chain chunks: scale so this chunk starts where the previous one ended
        s = scale / eq.asof(pd.Timestamp(b[i])) if eq.asof(pd.Timestamp(b[i])) == eq.asof(pd.Timestamp(b[i])) else scale / e[0]
        for x, y in zip(e * s, low * s):
            mdd = min(mdd, y / pk - 1); pk = max(pk, x)
        scale = e[-1] * s
    print(f'{M}: worst drop including moves inside candles {mdd:.0%}')
