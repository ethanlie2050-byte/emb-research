"""python3 plateau.py 'JSON params' -> nudge every setting up/down; a general winner keeps scoring about the same"""
import sys, json, copy, numpy as np
from fast import load, trades, stats, BPD
P0 = json.loads(sys.argv[1]); S = float(sys.argv[2]) if len(sys.argv) > 2 else 2.5
D = {}
def ev(P):
    tf = P['tf']
    if tf not in D: D[tf] = load(tf)
    o = stats(trades(P, D[tf], tf=tf), S); o2 = stats(trades(P, D[tf], tf=tf, cost=2.0), S)
    return o, o2
o, o2 = ev(P0)
print(f"BASE n={o['n']} cagr={o['cagr']:.0%} dd={o['dd']:.0%} cal={o['cal']:.2f} train={o['tr_cal']:.2f} test={o['te_cal']:.2f} 2xcost={o2['cal']:.2f} halfyears+={o['hpos']}/{o['hn']}")
V = []
def var(k, f):
    P = copy.deepcopy(P0); f(P)
    if P == P0: return
    try:
        o, o2 = ev(P); V.append(o['cal'])
        print(f"  {k:14s} n={o['n']:3d} cal={o['cal']:.2f} train={o['tr_cal']:.2f} test={o['te_cal']:.2f} 2x={o2['cal']:.2f} dd={o['dd']:.0%}")
    except Exception as e:
        print('  ', k, 'err', e)
for m in (0.85, 1.15):
    for k in ('z', 'ar', 'tp', 'q'):
        var(f'{k} x{m}', lambda P, k=k, m=m: P.__setitem__(k, round(P[k] * m, 3)))
    var(f'ladder x{m}', lambda P, m=m: P.__setitem__('lv', [round(x * m, 3) for x in P['lv']]))
    var(f'hold x{m}', lambda P, m=m: P.__setitem__('H', max(2, int(P['H'] * m))))
for k, vals in (('zw', (10, 14, 20, 30)), ('atrn', (14, 28)), ('aregn', (1000, 2000)), ('Wn', (1, 2, 4)), ('tf', ('30m', '1h'))):
    for v in vals:
        def f(P, k=k, v=v):
            if k == 'tf':
                P['H'] = max(2, int(P['hold_h'] * BPD[v] / 24))
            P[k] = v
        var(f'{k}={v}', f)
print(f'neighbours: median cal {np.median(V):.2f}, worst {min(V):.2f}, share >= half of base {np.mean(np.array(V) >= o["cal"] / 2):.0%}')
