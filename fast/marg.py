"""which setting values help in general (median over thousands of random configs), train vs unseen test"""
import sys, json, pandas as pd, numpy as np
d = pd.read_csv(sys.argv[1]); d = d[d.n >= int(sys.argv[2]) if len(sys.argv) > 2 else 30].copy()
P = d.P.apply(json.loads)
for k in ['tf', 'zw', 'atrn', 'aregn', 'Wn', 'hold_h', 'q', 'trend', 'volx', 'conf']:
    d[k] = P.apply(lambda p: p.get(k)).fillna('none') if k == 'trend' else P.apply(lambda p: p.get(k))
d['levels'] = P.apply(lambda p: len(p['lv'])); d['stop'] = P.apply(lambda p: 'none' if p['st'] == 0 else 'yes')
d['tp2step'] = P.apply(lambda p: 'yes' if p.get('tpf', 1) < 1 else 'no')
d['be'] = P.apply(lambda p: p.get('be', '-'))
for k, b in [('z', [0, 1.4, 1.8, 2.2, 2.6, 9]), ('ar', [0, 1.2, 1.6, 2.0, 9]), ('tp', [0, 2, 3, 4, 5, 9]),
             ('lv1', [-1, 0.8, 1.6, 9]), ('r7', [-1, -0.11, -0.07, 0])]:
    if k == 'lv1': v = P.apply(lambda p: p['lv'][0])
    elif k in P.iloc[0]: v = P.apply(lambda p: p.get(k))
    else: continue
    d[k] = pd.cut(v, b)
print(len(d), 'configs; median Calmar (realistic costs, intra-candle drawdown) | share profitable in test')
for k in ['trend', 'volx', 'conf', 'tf', 'zw', 'atrn', 'aregn', 'z', 'ar', 'q', 'levels', 'lv1', 'Wn', 'tp', 'tp2step', 'be', 'stop', 'hold_h', 'r7']:
    if k not in d: continue
    g = d.groupby(k, observed=True)
    t = pd.DataFrame(dict(n=g.size(), train=g.tr_cal.median(), test=g.te_cal.median(), test_profit=g.te_cagr.apply(lambda x: (x > 0).mean()),
                          top10_test=g.te_cal.apply(lambda x: x.nlargest(10).median())))
    print(f'\n[{k}]'); print(t.round(2).to_string())
