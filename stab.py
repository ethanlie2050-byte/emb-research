# stability: realistic half-year returns (same drag as cmp.py 'real')
import json, sys, numpy as np, pandas as pd
src = open('cmp.py').read().split("print(f\"{'version'")[0]; exec(src)
for m in sys.argv[1:]:
    r = curve(m)
    if r is None: print(m, 'incomplete'); continue
    drag = 0.6219 * turnover(m) / 414; n = len(r) / ((r.index[-1] - r.index[0]).days / 365.25)
    eq = np.exp((r - drag / n).cumsum()); h = eq.resample('6MS').last(); h = h / h.shift(1).fillna(1) - 1
    print(f'{m:10} realistic half-years:', ' '.join(f'{v:+.0%}' for v in h.values), f'| worst {h.min():+.0%} median {h.median():+.0%}')
