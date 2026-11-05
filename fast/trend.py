"""fast tester for the slow trend part alone (BTC+ETH, 4h bars, realistic costs). python3 trend.py N SEED OUT.csv"""
import sys, os, json, random, numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DR = 0.6219 / 414; COST = 0.00055 + 0.0005 + DR          # per unit of position traded: taker fee + slippage + realistic drag
T0, TS, T1 = pd.Timestamp('2022-01-01'), pd.Timestamp('2024-07-01'), pd.Timestamp('2026-10-31')


def load():
    D = {}
    for c in ('BTC', 'ETH'):
        d = pd.read_parquet(os.path.join(ROOT, 'data', f'{c}.parquet')).astype(float)
        D[c] = d.resample('4h', label='left', closed='left').agg(dict(open='first', high='max', low='min', close='last')).dropna()
    return D


def position(d, P):
    c = d.close; k = P['kind']
    if k == 'sma':
        ab = sum((c > c.rolling(w).mean()).astype(float) for w in P['wins']) / len(P['wins'])
        e = ab.copy(); e[ab == 0] = -P['sw']
    elif k == 'tsm':
        e = sum(np.sign(c / c.shift(w) - 1) for w in P['wins']) / len(P['wins'])
        e = e.clip(lower=-P['sw'])
    elif k == 'ema':
        f, s = P['wins'][0], P['wins'][1]
        e = np.where(c.ewm(span=f).mean() > c.ewm(span=s).mean(), 1.0, -P['sw']); e = pd.Series(e, c.index)
    elif k == 'donch':
        n, m = P['wins'][0], P['wins'][1]
        up = c >= c.rolling(n).max().shift(1); dn = c <= c.rolling(m).min().shift(1)
        st = pd.Series(np.nan, c.index); st[up] = 1.0; st[dn] = -P['sw']; e = st.ffill().fillna(0)
    vol = c.pct_change().rolling(P['vw']).std() * np.sqrt(6 * 365)
    raw = e * np.minimum(P['vt'] / vol, P.get('lmax', 1.0))
    h = P['h']
    p = raw.where((raw - raw.shift(1)).abs() > h * raw.shift(1).abs()).ffill() if h > 0 else raw
    return p.fillna(0)


def run(P, D, cost=1.0, start=T0, end=T1):
    rets = []
    for c in ('BTC', 'ETH'):
        d = D[c]; p = position(d, P)
        r = d.close.pct_change().fillna(0)
        pos = p.shift(1).fillna(0)                      # decided at the close, held over the next bar
        lowr = d.low / d.close.shift(1) - 1; highr = d.high / d.close.shift(1) - 1
        worst = np.where(pos > 0, pos * lowr, pos * highr)      # worst point inside the bar
        tc = (pos.diff().abs().fillna(0)) * COST * cost
        net = 0.5 * (pos * r - tc); wst = 0.5 * (pd.Series(worst, d.index).fillna(0) - tc)
        rets.append((net, wst))
    net = rets[0][0].add(rets[1][0], fill_value=0); wst = rets[0][1].add(rets[1][1], fill_value=0)
    m = (net.index >= start) & (net.index <= end); net = net[m]; wst = wst[m]
    eq = (1 + net).cumprod(); pk = eq.cummax()
    low = eq.shift(1).fillna(1) * (1 + wst)
    dd = min((low / pk.shift(1).fillna(1) - 1).min(), (eq / pk - 1).min())
    yrs = (end - start).days / 365.25; cg = eq.iloc[-1] ** (1 / yrs) - 1
    return cg, dd, net


def stats(P, D):
    cg, dd, net = run(P, D)
    o = dict(cagr=cg, dd=dd, cal=cg / -dd if dd < 0 else 0)
    for nm, a, b in (('tr', T0, TS), ('te', TS, T1)):
        g, d2, _ = run(P, D, start=a, end=b); o[nm + '_cal'] = g / -d2 if d2 < 0 else 0; o[nm + '_cagr'] = g
    g2, d2, _ = run(P, D, cost=2.0); o['cal2'] = g2 / -d2 if d2 < 0 else 0
    y = (1 + net).groupby(net.index.year).prod() - 1; o['ypos'] = int((y > 0).sum())
    o['turn'] = 0  # placeholder
    return o


def draw(r):
    k = r.choice(['sma', 'sma', 'tsm', 'ema', 'donch'])
    if k == 'sma':
        b = r.choice([30, 60, 90, 120, 180]); wins = sorted({int(b * m) for m in r.choice([(1, 2.5, 5, 7.5), (1, 2, 4), (1, 3), (1,), (1, 2, 3, 4, 5)])})
    elif k == 'tsm':
        b = r.choice([30, 60, 120, 180, 360]); wins = sorted({int(b * m) for m in r.choice([(1,), (1, 2), (1, 2, 4), (0.5, 1, 2)])})
    elif k == 'ema':
        f = r.choice([6, 12, 24, 48, 90]); wins = [f, int(f * r.choice([2, 3, 4, 6, 10]))]
    else:
        n = r.choice([30, 60, 120, 180, 360]); wins = [n, int(n * r.choice([0.25, 0.5, 1.0]))]
    return dict(kind=k, wins=wins, sw=r.choice([0, 0, 0.25, 0.5, 1.0]), vt=r.choice([0.2, 0.3, 0.4, 0.6, 0.8]),
                vw=r.choice([60, 120, 180, 360]), h=r.choice([0, 0.05, 0.1, 0.2, 0.3]), lmax=r.choice([1.0, 1.5, 2.0]))


CORE = dict(kind='sma', wins=[120, 300, 600, 900], sw=1.0, vt=0.4, vw=180, h=0.1, lmax=1.0)

if __name__ == '__main__':
    D = load()
    if len(sys.argv) < 2:
        for sw in (1.0, 0.5, 0.0):
            P = dict(CORE, sw=sw); print('current core, short', sw, {k: round(v, 2) for k, v in stats(P, D).items()})
        sys.exit()
    N, SEED, OUT = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    r = random.Random(SEED); rows = []
    for i in range(N):
        P = draw(r)
        try:
            o = stats(P, D); o['P'] = json.dumps(P); rows.append(o)
        except Exception as e:
            pass
        if i % 500 == 499: pd.DataFrame(rows).to_csv(OUT, index=False); print(i + 1, flush=True)
    pd.DataFrame(rows).to_csv(OUT, index=False); print('FINISHED', len(rows))
