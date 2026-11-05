"""random search for one engine: python3 search.py E1|E3 N SEED OUT.csv"""
import sys, json, random, numpy as np, pandas as pd, multiprocessing as mp
from fast import load, feats, trades, stats, qok, BPD

ENG, N, SEED, OUT = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
FRS = [(.25, .25, .5), (.33, .33, .34), (.5, .25, .25), (.2, .3, .5), (.1, .3, .6), (.4, .3, .3)]


def draw(r):
    tf = r.choice(['15m', '15m', '30m', '1h'] if ENG == 'E1' else ['15m', '30m', '1h', '1h', '4h'])
    P = dict(tf=tf, zw=r.choice([10, 14, 20, 30, 48]), atrn=r.choice([7, 14, 28]), aregn=r.choice([250, 500, 1000]))
    k = r.choice([1, 2, 3, 3, 3])
    l1 = r.uniform(0, 2.5); lv = [l1]
    for _ in range(k - 1): lv.append(lv[-1] + r.uniform(0.5, 3.5))
    fr = [1.0] if k == 1 else ([.5, .5] if k == 2 and r.random() < .5 else [.3, .7]) if k == 2 else list(r.choice(FRS))
    hold_h = r.choice([6, 12, 24, 48, 96] if ENG == 'E1' else [24, 48, 96, 192, 384])
    P.update(lv=[round(x, 2) for x in lv], fr=fr, Wn=r.choice([1, 2, 4]), tp=round(r.uniform(1, 6), 2),
             H=max(2, int(hold_h * BPD[tf] / 24)), hold_h=hold_h)
    P['st'] = 0.0 if r.random() < 0.35 else round(lv[-1] + r.uniform(1, 8), 2)
    if r.random() < 0.3:
        P['tpf'] = 0.5; P['tp2'] = round(P['tp'] + r.uniform(1, 4), 2); P['be'] = r.choice([0, 1])
    if ENG == 'E1':
        P.update(s=1, z=round(r.uniform(1.0, 3.0), 2), ar=round(r.uniform(0.8, 2.6), 2),
                 q=r.choice([0, 0, 1.3, 1.5, 1.7, 1.9, 2.1, 2.3]))
    else:
        P.update(s=-1, z=round(r.uniform(0.8, 2.5), 2), ar=round(r.uniform(0.8, 2.2), 2), r7=round(r.uniform(-0.15, -0.03), 3), q=0)
    return P


_D, _F, _G = {}, {}, {}


def run(args):
    i, P = args
    tf = P['tf']
    if tf not in _D: _D[tf] = load(tf)
    fk = (tf, P['zw'], P['atrn'], P['aregn'])
    if fk not in _F: _F[fk] = {c: feats(_D[tf][c], tf, P['zw'], P['atrn'], P['aregn']) for c in ('BTC', 'ETH')}
    G = None
    if P['q'] > 0:
        gk = fk + (P['q'],)
        if gk not in _G:
            _G[gk] = {}
            for c in ('BTC', 'ETH'):
                q = qok(_F[fk][c], 'E1', P['q']).values; _G[gk][c] = np.r_[False, q[:-1]]
        G = _G[gk]
    try:
        T = trades(P, _D[tf], _F[fk], tf, G=G)
        S = 2.5 if ENG == 'E1' else 1.5
        o = stats(T, S)
        if o is None: return None
        T2 = trades(P, _D[tf], _F[fk], tf, cost=2.0, G=G); o2 = stats(T2, S)
        o['cal2'] = o2['cal'] if o2 else 0; o['cagr2'] = o2['cagr'] if o2 else 0
        o['P'] = json.dumps(P); o['i'] = i
        return o
    except Exception as e:
        return None


if __name__ == '__main__':
    r = random.Random(SEED)
    jobs = [(i, draw(r)) for i in range(N)]
    # sort by feature key so each worker reuses its caches
    jobs.sort(key=lambda x: (x[1]['tf'], x[1]['zw'], x[1]['atrn'], x[1]['aregn']))
    rows = []
    with mp.Pool(2) as pool:
        for n, o in enumerate(pool.imap_unordered(run, jobs, chunksize=8)):
            if o: rows.append(o)
            if n % 200 == 199:
                pd.DataFrame(rows).to_csv(OUT, index=False); print(n + 1, 'done', flush=True)
    pd.DataFrame(rows).to_csv(OUT, index=False); print('FINISHED', len(rows), flush=True)
