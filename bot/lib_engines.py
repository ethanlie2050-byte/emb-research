"""Clean-room indicators + engines per SPEC_v5 sections 2-5."""
import numpy as np, pandas as pd, json, os
try:
    import numba                     # only needed for the research backtest functions below
except Exception:                    # live bot runs fine without it (e.g. Windows blocking its DLLs)
    class _NB:
        @staticmethod
        def njit(*a, **k):
            if a and callable(a[0]):
                return a[0]
            return lambda f: f
    numba = _NB()
from lib_data import P15, X1H, ALL13

PEN = 0.002   # v2.4: = research PEN of v2.3 (engine state now matches the backtest exactly)
import os
E5Z = 1.95   # v3.1: E5 dip depth 1.95 (was 2.0)
E3R = -0.055; RB1S = 0.6; RB1H = 168   # v3.0: RB1 max hold 168 x 15m (was 192); v2.10: E3 needs a 5.5% 7-day drop (was 6.5%); v2.6: RB1 stop 0.6 (was 0.75)
E4STOP = 8.0  # Addendum 2
DUR = {'15m': pd.Timedelta('15min'), '1h': pd.Timedelta('1h'), '4h': pd.Timedelta('4h')}
BPD = {'15m': 96, '1h': 24}
BPH = {'15m': 4, '1h': 1}


def indicators(d, tf):
    o, h, l, c = d.open, d.high, d.low, d.close
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    z = (c - c.rolling(20).mean()) / c.rolling(20).std()
    areg = atr / atr.rolling(500, min_periods=100).mean()
    bpd, bph = BPD[tf], BPH[tf]
    f = pd.DataFrame(index=d.index)
    f['atr'] = atr; f['z'] = z; f['areg'] = areg
    f['ret1d'] = c / c.shift(bpd) - 1
    f['ret7d'] = c / c.shift(7 * bpd) - 1
    f['ret30d'] = c / c.shift(30 * bpd) - 1
    f['speed'] = c / c.shift(bph) - 1
    cd = c.resample('1D').last().dropna()
    m = cd / cd.rolling(200).mean() - 1
    m.index = m.index + pd.Timedelta('1D')
    f['ma200d'] = m.reindex(d.index, method='ffill')
    f['sma20'] = c.rolling(20).mean()
    if tf == '1h':
        r4 = c / c.shift(4) - 1
        sd = r4.rolling(1440, min_periods=240).std().shift(1)
        f['zz'] = r4 / sd
    return f


# ---------------------------------------------------------------- sim_engine
@numba.njit(cache=True)
def sim_engine(o, h, l, c, atr, sig, gate, s, lv, fr, Wn, tpm, stm, H, tgt, pen):
    n = len(c); K = len(lv)
    # outputs
    out_i = []; out_f = []; out_x = []; out_xp = []; out_r = []; out_rej = []
    out_fb = []; out_fp = []
    i = 0
    while i < n - 1:
        if (not sig[i]) or np.isnan(atr[i]):
            i += 1; continue
        cc = c[i]; a = atr[i]
        Lk = np.empty(K)
        for k in range(K):
            Lk[k] = cc - s * lv[k] * a
        f = -1
        jmax = min(i + Wn, n - 1)
        for j in range(i + 1, jmax + 1):
            if s > 0:
                if l[j] <= Lk[0] * (1 - pen):
                    f = j; break
            else:
                if h[j] >= Lk[0] * (1 + pen):
                    f = j; break
        if f < 0:
            i = i + Wn + 1; continue
        if not gate[f]:
            out_i.append(i); out_f.append(f); out_x.append(-1); out_xp.append(np.nan); out_r.append(-1)
            out_rej.append(1)
            fbr = np.full(3, -1); fpr = np.full(3, np.nan)
            out_fb.append(fbr); out_fp.append(fpr)
            i = f + 1; continue
        stop = cc - s * stm * a if stm > 0 else np.nan
        fb = np.full(3, -1); fp = np.full(3, np.nan)
        last_fill = f
        j = f
        xb = -1; xp = np.nan; reason = -1
        while True:
            if j >= n:
                xb = n - 1; xp = c[n - 1]; reason = 3; break  # eod
            for k in range(K):
                if fb[k] >= 0:
                    continue
                if s > 0:
                    if l[j] <= Lk[k] * (1 - pen):
                        fp[k] = min(Lk[k], o[j]); fb[k] = j; last_fill = j
                else:
                    if h[j] >= Lk[k] * (1 + pen):
                        fp[k] = max(Lk[k], o[j]); fb[k] = j; last_fill = j
            if stm > 0:
                if s < 0:
                    if h[j] >= stop:
                        xb = j; xp = max(stop, o[j]) * 1.001; reason = 1; break
                else:
                    if l[j] <= stop:
                        xb = j; xp = min(stop, o[j]) * 0.999; reason = 1; break
            if tpm > 0 and j > last_fill:
                num = 0.0; den = 0.0
                for k in range(K):
                    if fb[k] >= 0:
                        num += fr[k]; den += fr[k] / fp[k]
                avg = num / den
                tp = avg + s * tpm * a
                if j - 1 >= 0 and not np.isnan(tgt[j - 1]):
                    if s > 0:
                        tp = max(tp, tgt[j - 1])
                    else:
                        tp = min(tp, tgt[j - 1])
                if s > 0:
                    if h[j] >= tp * (1 + pen):
                        xb = j; xp = max(tp, o[j]); reason = 0; break
                else:
                    if l[j] <= tp * (1 - pen):
                        xb = j; xp = min(tp, o[j]); reason = 0; break
            if j == f + H - 1:
                xb = j; xp = c[j]; reason = 2; break
            j += 1
        out_i.append(i); out_f.append(f); out_x.append(xb); out_xp.append(xp); out_r.append(reason)
        out_rej.append(0); out_fb.append(fb); out_fp.append(fp)
        i = xb + 1
    return out_i, out_f, out_x, out_xp, out_r, out_rej, out_fb, out_fp


@numba.njit(cache=True)
def sim_market(o, h, l, c, sig, dist, sm, H):
    n = len(c)
    out_i = []; out_f = []; out_x = []; out_xp = []; out_r = []
    i = 0
    while i < n - 1:
        if (not sig[i]) or np.isnan(dist[i]):
            i += 1; continue
        a = dist[i]; f = i + 1; entry = o[f]
        stop = c[i] - a * sm
        tp = entry + a
        j = f
        while True:
            if j >= n:
                xb = n - 1; xp = c[n - 1]; r = 3; break
            if l[j] <= stop:
                xb = j; xp = min(stop, o[j]) * 0.999; r = 1; break
            if j > f and h[j] >= tp:
                xb = j; xp = max(tp, o[j]); r = 0; break
            if j == f + H - 1:
                xb = j; xp = c[j]; r = 2; break
            j += 1
        out_i.append(i); out_f.append(f); out_x.append(xb); out_xp.append(xp); out_r.append(r)
        i = xb + 1
    return out_i, out_f, out_x, out_xp, out_r


REASON = {0: 'tp', 1: 'stop', 2: 'time', 3: 'eod'}

# ---------------------------------------------------------------- quality
QT = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'qt_wf.json')))


QTHR = {'E1': 1.9, 'E2': 1.9}   # v3.4: 1.9 (v3.0-v3.3: 1.95, before 2.0); E5 keeps 2.0


def qok(F, table, thr=2.0):
    tabs = QT[table]
    years = F.index.year.values
    out = np.zeros(len(F), bool)
    for y in np.unique(years):
        m = years == y
        T = tabs.get(str(y))
        if T is None:
            continue
        sc = np.zeros(m.sum())
        for f in ['speed', 'areg', 'ret1d', 'ma200d']:
            arr = np.asarray(T[f], float)
            v = F[f].values[m].copy()
            v[np.isnan(v)] = T['med'][f]
            cdf = np.searchsorted(arr, v, side='left') / len(arr)
            if f in ('areg', 'ma200d'):
                sc += cdf
            else:
                sc += 1 - cdf
        r7 = F['ret7d'].values[m]; r30 = F['ret30d'].values[m]
        with np.errstate(invalid='ignore'):
            q = (sc >= thr) | (r7 >= T['ret7d_p80']) | (r30 >= T['ret30d_p80'])
        out[m] = q
    return pd.Series(out, F.index)


def crash_series(raws, feats, coins, tf):
    """raws: dict coin->bool Series of raw signal; returns dict coin->crash bool Series"""
    R = pd.DataFrame({c: raws[c] for c in coins})
    R = R.fillna(False).astype(int)
    br = R.rolling(3, min_periods=1).max().sum(axis=1)
    btc = feats['BTC']['ret1d']
    out = {}
    for c in coins:
        idx = raws[c].index
        b = br.reindex(idx)
        b1 = btc.reindex(idx, method='ffill')
        _cf = __import__('os').environ.get('CRFRAC')                 # research: crash = this share of coins signalling (default 3 coins)
        cr = (b >= (max(3, round(float(_cf) * len(coins))) if _cf else 3)).values
        if c != 'BTC':
            cr = cr | (b1 <= -0.05).values
        out[c] = pd.Series(cr, idx)
    return out


def shift1(x):
    v = np.zeros(len(x), bool)
    v[1:] = np.asarray(x)[:-1]
    return v


# ---------------------------------------------------------------- RB
def basic(df):
    c = df.close
    ret = c.pct_change()
    z1 = ret / ret.rolling(100).std()
    dlt = c.diff()
    up = dlt.clip(lower=0).ewm(alpha=1 / 14).mean()
    dn = (-dlt.clip(upper=0)).ewm(alpha=1 / 14).mean()
    rsi = 100 - 100 / (1 + up / dn)
    ap = (df.high + df.low + c) / 3
    esa = ap.ewm(span=9, adjust=False).mean()
    dd = (ap - esa).abs().ewm(span=9, adjust=False).mean()
    ci = (ap - esa) / (0.015 * dd)
    vmc = ci.ewm(span=12, adjust=False).mean()
    volx = df.volume / df.volume.rolling(20).mean().shift(1)
    rng = df.high - df.low
    rngx = rng / rng.rolling(20).mean().shift(1)
    return pd.DataFrame(dict(z1=z1, rsi=rsi, vmc=vmc, volx=volx, rngx=rngx))


def rb1_signal(m15, h1, h4):
    H = basic(h1); F = basic(h4)
    H.index = H.index + pd.Timedelta('1h'); F.index = F.index + pd.Timedelta('4h')
    H = H.reindex(m15.index, method='ffill'); F = F.reindex(m15.index, method='ffill')
    m = ((H.z1 > 3) & ((F.vmc > 60) | (F.rsi > 70))) | ((H.volx > 3) & (F.vmc > 75)) | \
        ((H.rngx > 2.5) & (F.rsi > 75))
    m = m.fillna(False).astype(bool).values
    sig = m & ~np.r_[False, m[:-1]]
    return sig


def rb5_signal(b4, m15):
    o, h, l, c, v = b4.open, b4.high, b4.low, b4.close, b4.volume
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    ret = c.pct_change(); sd = ret.rolling(100).std()
    z18 = c.pct_change(18) / (sd * np.sqrt(18))
    z42 = c.pct_change(42) / (sd * np.sqrt(42))
    clv = (c - l) / (h - l); body = (c - o) / (h - l)
    day = b4.index.normalize()
    tp = (h + l + c) / 3
    vw = (tp * v).groupby(day).cumsum() / v.groupby(day).cumsum()
    dvwap = (c - vw) / atr
    wk_end = b4.index.normalize() + pd.to_timedelta(6 - b4.index.weekday, unit='D')
    wh = h.groupby(wk_end).max()
    prev_hi = pd.Series(wh.reindex(wk_end - pd.Timedelta('7D')).values, b4.index)
    d_pwh = (c - prev_hi) / atr
    g = m15.index.floor('4h')
    lows = m15.low.values
    df = pd.DataFrame({'g': g, 'low': lows})
    df['pos'] = df.groupby('g').cumcount()
    df['n'] = df.groupby('g')['low'].transform('size')
    df['gmin'] = df.groupby('g')['low'].transform('min')
    first = df[df.low == df.gmin].groupby('g').first()
    tl = first['pos'] / np.maximum(first['n'] - 1, 1)
    t_low = tl.reindex(b4.index)
    Fz = dict(z42=1.095796, z18=1.445295, d_pwh=2.384172, clv=0.260694, body=-0.432072, dvwap=-0.28395, t_low=0.8)
    sig = ((z42 >= Fz['z42']) | (z18 >= Fz['z18']) | (d_pwh >= Fz['d_pwh'])) & (clv <= Fz['clv']) & \
          (body <= Fz['body']) & (dvwap <= Fz['dvwap']) & (t_low >= Fz['t_low'])
    return sig.fillna(False).astype(bool).values, atr.values


# ---------------------------------------------------------------- build all
ENG = {
    'E1': dict(tf='15m', coins=P15, s=1, lv=[1, 2, 4], fr=[.25, .25, .5], Wn=1, tp=3.0, st=0, H=96),   # v2.8 tp 3.0 (v2.3: 2.5)
    'E2': dict(tf='1h', coins=ALL13, s=1, lv=[1.25], fr=[1.], Wn=3, tp=1.25, st=3.0, H=15),   # v3.1: 15h hold (v2.5-v3.0: 18, before 12)
    'E3': dict(tf='1h', coins=ALL13, s=-1, lv=[1, 2, 4], fr=[.25, .25, .5], Wn=3, tp=1.75, st=6.0, H=192),   # v2.9 tp 1.75 (was 1.5)
    'E4': dict(tf='1h', coins=ALL13, s=1, lv=[1.5], fr=[1.], Wn=6, tp=0, st=E4STOP, H=240),
    'E5': dict(tf='15m', coins=P15, s=1, lv=[1.], fr=[1.], Wn=1, tp=1.6, st=0, H=48),   # v3.4: tp 1.6 (was 1.5)
}

import os as _os
for _kv in _os.environ.get('ENGP', '').split(','):       # research: override engine settings, e.g. ENGP=E1.tp:3.75,E5.H:60
    if _kv:
        _k, _v = _kv.split(':'); _e, _p = _k.split('.')
        ENG[_e][_p] = type(ENG[_e][_p])(float(_v)) if not isinstance(ENG[_e][_p], list) else [float(x) for x in _v.split('/')]


def build(D=None):
    if D is None:
        D = load_all()
    F = {'15m': {c: indicators(D['m15'][c], '15m') for c in P15},
         '1h': {c: indicators(D['h1'][c], '1h') for c in ALL13}}
    raw = {}
    for c in P15:
        f = F['15m'][c]
        with np.errstate(invalid='ignore'):
            raw[('E1', c)] = ((f.z <= -2.0) & (f.areg >= 1.8))
            raw[('E5', c)] = ((f.z <= -E5Z) & (f.areg >= 1.0))
    btc1 = D['h1']['BTC'].close
    b3 = (btc1 / btc1.shift(72) - 1).rolling(72, min_periods=1).min()
    for c in ALL13:
        f = F['1h'][c]
        raw[('E2', c)] = ((f.z <= -1.5) & (f.areg >= 1.6))
        raw[('E3', c)] = ((f.ret7d <= E3R) & (f.z >= 1.5) & (f.areg >= 1.5))
        zz = f.zz.values
        with np.errstate(invalid='ignore'):
            hi = zz >= 7.0
        r0 = hi & ~np.r_[False, hi[:-1]]
        bb = b3.reindex(f.index, method='ffill').values
        with np.errstate(invalid='ignore'):
            blk = bb <= -0.06   # v2.8: E4 blocked when BTC 3-day drop <= 6% (was 8%)
        raw[('E4', c)] = pd.Series(r0 & ~blk, f.index)
    crash = {}
    for E, tf, coins in [('E1', '15m', P15), ('E5', '15m', P15), ('E2', '1h', ALL13)]:
        crash[E] = crash_series({c: raw[(E, c)] for c in coins}, F[tf], coins, tf)
    trades = []
    for E, p in ENG.items():
        tf = p['tf']
        for c in p['coins']:
            d = D['m15'][c] if tf == '15m' else D['h1'][c]
            f = F[tf][c]
            sig = raw[(E, c)].fillna(False).astype(bool).values
            n = len(d)
            if E in ('E1', 'E2', 'E5'):
                q = qok(f, 'E2' if E == 'E2' else 'E1', QTHR.get(E, 2.0)).values
                cok = np.ones(n, bool) if c in ('BTC', 'ETH') else ~crash[E][c].values
                gate = shift1(q & cok)
                cf = shift1(crash['E5' if E == 'E1' else E][c].values)
            else:
                gate = np.ones(n, bool)
                cf = np.zeros(n, bool)
            tgt = f.sma20.values if E == 'E3' else np.full(n, np.nan)
            res = sim_engine(d.open.values, d.high.values, d.low.values, d.close.values, f.atr.values,
                             sig, gate, p['s'], np.array(p['lv'], float), np.array(p['fr'], float), p['Wn'],
                             float(p['tp']), float(p['st']), p['H'], tgt, PEN)
            idx = d.index
            for (i, fb0, xb, xp, r, rej, fbs, fps) in zip(*res):
                t = dict(eng=E, coin=c, tf=tf, sig_bar=i, f_bar=fb0, sig_time=idx[i], fill_time=idx[fb0],
                         rejected=bool(rej), crash=bool(cf[fb0]), atr_sig=float(f.atr.values[i]), c_sig=float(d.close.values[i]))
                if not rej:
                    t.update(exit_bar=xb, exit_time=idx[xb] + DUR[tf], exit_px=xp, reason=REASON[r],
                             fr=list(p['fr']), fb=[int(x) for x in fbs[:len(p['lv'])]],
                             fp=[float(x) for x in fps[:len(p['lv'])]],
                             ft=[idx[x] if x >= 0 else pd.NaT for x in fbs[:len(p['lv'])]], s=p['s'],
                             fe=0.0002)
                trades.append(t)
    # RB1
    m = D['m15']['BTC']
    sig = rb1_signal(m, D['h1']['BTC'], D['btc4'])
    res = sim_market(m.open.values, m.high.values, m.low.values, m.close.values, sig, 0.03 * m.close.values, RB1S, RB1H)
    for E, d, tf, res in [('RB1', m, '15m', res)] + [('RB5', D['btc4'], '4h', None)]:
        if res is None:
            s5, atr4 = rb5_signal(D['btc4'], m)
            b = D['btc4']
            res = sim_market(b.open.values, b.high.values, b.low.values, b.close.values, s5, 1.5 * atr4, 1.0, 60)
        idx = d.index; o = d.open.values
        for (i, f, xb, xp, r) in zip(*res):
            trades.append(dict(eng=E, coin='BTC', tf=tf, sig_bar=i, f_bar=f, sig_time=idx[i], fill_time=idx[f],
                               rejected=False, crash=False, atr_sig=np.nan, c_sig=float(d.close.values[i]), exit_bar=xb, exit_time=idx[xb] + DUR[tf], exit_px=xp,
                               reason=REASON[r], fr=[1.0], fb=[f], fp=[float(o[f])], ft=[idx[f]], s=1, fe=0.00085))
    T = pd.DataFrame(trades)
    return T, F


def add_returns(T):
    ret = np.zeros(len(T)); fund = np.zeros(len(T))
    for n, t in enumerate(T.itertuples()):
        if t.rejected:
            ret[n] = np.nan; fund[n] = np.nan; continue
        fx = 0.0002 if t.reason == 'tp' else 0.00085
        X = t.exit_px
        for w, p, fb, ft in zip(t.fr, t.fp, t.fb, t.ft):
            if fb < 0:
                continue
            ret[n] += w * t.s * (X / p - 1) - w * t.fe - fx * w * X / p
            fund[n] += 0.0001 * w * ((t.exit_time - ft) / pd.Timedelta('1h')) / 8
    T['ret'] = ret; T['fund'] = fund; T['ret_f'] = ret - fund
    return T


if __name__ == '__main__':
    import time, pickle
    t0 = time.time()
    T, F = build()
    T = add_returns(T)
    pickle.dump(T, open(os.environ.get('TRADES','/home/claude/cleanroom/trades_f18.pkl'), 'wb'))
    print('built', time.time() - t0)
    for E, g in T.groupby('eng'):
        print(E, 'trades', (~g.rejected).sum(), 'rejected', g.rejected.sum())
