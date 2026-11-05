"""Fast engine tester for BTC+ETH (research only; the real bot is verified separately in run_pit.py).
One engine at a time: parameterized signal -> laddered limit entry -> exits (tp / stop / time), per-trade returns
net of fees, funding and the turnover-based 'realistic' drag, then a one-trade-at-a-time equity curve with
intra-trade worst point (so the drawdown includes open-trade losses)."""
import os, sys, json, numpy as np, pandas as pd, numba
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'bot'))
from lib_engines import qok

DR = 0.6219 / 414
VOLSZ = True              # size x min(1, 60%/BTC 30-day vol), like the bot          # realistic extra cost per unit of turnover (same as cmp.py)
T0, T1, TS = pd.Timestamp('2022-01-01'), pd.Timestamp('2026-10-31 23:45'), pd.Timestamp('2024-07-01')
HB = pd.date_range('2022-01-01', '2027-01-01', freq='6MS')


def load(tf='15m'):
    D = {}
    for c in ('BTC', 'ETH'):
        d = pd.read_parquet(os.path.join(ROOT, 'data', f'{c}.parquet')).astype('float64')[['open', 'high', 'low', 'close', 'volume']]
        d = d[d.volume != 0]
        if tf != '15m':
            d = d.resample({'30m': '30min', '1h': '1h', '2h': '2h', '4h': '4h'}[tf], label='left', closed='left').agg(dict(open='first', high='max', low='min', close='last', volume='sum')).dropna()
        D[c] = d
    return D


BPD = {'15m': 96, '30m': 48, '1h': 24, '2h': 12, '4h': 6}


def feats(d, tf, zw=20, atrn=14, aregn=500):
    o, h, l, c = d.open, d.high, d.low, d.close
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(atrn).mean()
    f = pd.DataFrame(index=d.index)
    f['atr'] = atr
    f['z'] = (c - c.rolling(zw).mean()) / c.rolling(zw).std()
    f['areg'] = atr / atr.rolling(aregn, min_periods=min(100, aregn)).mean()
    bpd = BPD[tf]
    f['ret1d'] = c / c.shift(bpd) - 1
    f['ret7d'] = c / c.shift(7 * bpd) - 1
    f['ret30d'] = c / c.shift(30 * bpd) - 1
    f['speed'] = c / c.shift(max(1, bpd // 24)) - 1
    cd = c.resample('1D').last().dropna(); m = cd / cd.rolling(200).mean() - 1; m.index = m.index + pd.Timedelta('1D')
    f['ma200d'] = m.reindex(d.index, method='ffill')
    f['sma'] = c.rolling(zw).mean()
    return f


@numba.njit(cache=True)
def sim(o, h, l, c, atr, sig, gate, s, lv, fr, Wn, tpm, stm, H, pen, tpf, tp2, be):
    """like lib_engines.sim_engine (same fill rules) + optional 2nd take-profit (share tpf at tpm, rest at tp2),
    optional break-even stop after tp1 (be=1), and the worst open-trade return (mae) per trade."""
    n = len(c); K = len(lv)
    R = []
    i = 0
    while i < n - 1:
        if (not sig[i]) or np.isnan(atr[i]):
            i += 1; continue
        cc = c[i]; a = atr[i]
        Lk = np.empty(K)
        for k in range(K):
            Lk[k] = cc - s * lv[k] * a
        f = -1
        for j in range(i + 1, min(i + Wn, n - 1) + 1):
            if (s > 0 and l[j] <= Lk[0] * (1 - pen)) or (s < 0 and h[j] >= Lk[0] * (1 + pen)):
                f = j; break
        if f < 0:
            i = i + Wn + 1; continue
        if not gate[f]:
            i = f + 1; continue
        stop = cc - s * stm * a if stm > 0 else np.nan
        fp = np.full(K, np.nan); filled = np.zeros(K, np.bool_)
        last_fill = f; j = f; mae = 0.0; got = 0.0; rem = 1.0; t1 = False; reason = -1; xb = -1; xp = np.nan
        while True:
            if j >= n:
                xb = n - 1; xp = c[n - 1]; reason = 3; break
            for k in range(K):
                if filled[k]: continue
                if (s > 0 and l[j] <= Lk[k] * (1 - pen)) or (s < 0 and h[j] >= Lk[k] * (1 + pen)):
                    fp[k] = min(Lk[k], o[j]) if s > 0 else max(Lk[k], o[j]); filled[k] = True; last_fill = j
            w = 0.0; den = 0.0
            for k in range(K):
                if filled[k]:
                    w += fr[k]; den += fr[k] / fp[k]
            avg = w / den
            worst = l[j] if s > 0 else h[j]
            u = rem * w * s * (worst / avg - 1) + got
            if u < mae: mae = u
            if not np.isnan(stop):
                if (s > 0 and l[j] <= stop) or (s < 0 and h[j] >= stop):
                    xb = j; xp = (min(stop, o[j]) * 0.999) if s > 0 else (max(stop, o[j]) * 1.001); reason = 1; break
            if tpm > 0 and j > last_fill:
                tp = avg + s * (tp2 if t1 else tpm) * a
                if (s > 0 and h[j] >= tp * (1 + pen)) or (s < 0 and l[j] <= tp * (1 - pen)):
                    px = max(tp, o[j]) if s > 0 else min(tp, o[j])
                    if tpf < 1.0 and not t1:
                        got += tpf * w * s * (px / avg - 1); rem = 1.0 - tpf; t1 = True
                        if be > 0: stop = avg
                    else:
                        xb = j; xp = px; reason = 0; break
            if j == f + H - 1:
                xb = j; xp = c[j]; reason = 2; break
            j += 1
        w = 0.0; den = 0.0
        for k in range(K):
            if filled[k]:
                w += fr[k]; den += fr[k] / fp[k]
        avg = w / den
        ret = got + rem * w * s * (xp / avg - 1)
        R.append((float(i), float(f), float(xb), ret, w, mae, float(reason), 1.0 if t1 else 0.0))
        i = xb + 1
    return R


FEE_M, FEE_T, FUND = 0.0002, 0.00085, 0.0001


def trades(P, D=None, F=None, tf='15m', cost=1.0, G=None):
    """P: dict of engine params. Returns DataFrame of trades over both coins with net per-unit return."""
    D = D or load(tf); out = []
    for c in P.get('coins', ('BTC', 'ETH')):
        d = D[c]
        f = F[c] if F is not None else feats(d, tf, P['zw'], P['atrn'], P['aregn'])
        s = P['s']
        with np.errstate(invalid='ignore'):
            if s > 0:
                sig = (f.z <= -P['z']) & (f.areg >= P['ar'])
            else:
                sig = (f.ret7d <= P['r7']) & (f.z >= P['z']) & (f.areg >= P['ar'])
        sig = sig.fillna(False).values
        if G is not None and c in G:
            gate = G[c]
        elif P.get('q', 0) > 0:
            q = qok(f, 'E1', P['q']).values; gate = np.r_[False, q[:-1]]
        else:
            gate = np.ones(len(d), bool)
        R = sim(d.open.values, d.high.values, d.low.values, d.close.values, f.atr.values, sig, gate, s,
                np.array(P['lv'], float), np.array(P['fr'], float), int(P['Wn']), float(P['tp']), float(P['st']),
                int(P['H']), 0.002 if tf == '15m' else 0.002, float(P.get('tpf', 1.0)), float(P.get('tp2', 0)), float(P.get('be', 0)))
        if not R: continue
        R = np.array(R); idx = d.index
        T = pd.DataFrame(dict(coin=c, sig=idx[R[:, 0].astype(int)], fill=idx[R[:, 1].astype(int)],
                              exit=idx[R[:, 2].astype(int)], gross=R[:, 3], w=R[:, 4], mae=R[:, 5], why=R[:, 6].astype(int), t1=R[:, 7]))
        hrs = (T.exit - T.fill) / pd.Timedelta('1h')
        exitfee = np.where(T.why == 0, FEE_M, FEE_T + np.where(T.why == 1, 0.001, 0.0005))
        T['net'] = T.gross - cost * (T.w * (FEE_M + exitfee + FUND * hrs / 8 + 2 * DR))
        out.append(T)
    if not out: return pd.DataFrame()
    T = pd.concat(out).sort_values('fill').reset_index(drop=True)
    T = T[(T.fill >= T0) & (T.fill <= T1)].copy()
    T['vm'] = VM.asof(T.fill.dt.normalize()).values if len(T) else []
    return T.reset_index(drop=True)


def _vm():
    d = pd.read_parquet(os.path.join(ROOT, 'data', 'BTC.parquet'))['close'].astype(float).resample('1D').last().dropna()
    v = d.pct_change().rolling(30).std(ddof=1) * np.sqrt(365); v.index = v.index + pd.Timedelta('1D')
    return (0.60 / v).clip(0.3, 1.0).fillna(1.0)
VM = _vm()


def portfolio(T, S=2.5, cool=3, one=True):
    """one trade at a time (both coins), cooldown after a loss on that coin, size S x equity"""
    eq = 1.0; pk = 1.0; dd = 0.0; busy = pd.Timestamp('1900'); last_loss = {}
    pts = [(T0, 1.0)]; acc = []
    for t in T.itertuples():
        if one and t.fill <= busy: continue
        if t.coin in last_loss and (t.fill - last_loss[t.coin]) < pd.Timedelta(days=cool): continue
        sz = S * (t.vm if VOLSZ else 1.0)
        low = eq * (1 + sz * t.mae); dd = min(dd, low / pk - 1)
        eq *= max(1e-9, 1 + sz * t.net); pk = max(pk, eq); dd = min(dd, eq / pk - 1)
        busy = t.exit
        if t.net < 0: last_loss[t.coin] = t.exit
        pts.append((t.exit, eq)); acc.append(t.Index)
    return pd.Series(dict(pts)).groupby(level=0).last(), dd, T.loc[acc]


def stats(T, S=2.5):
    if len(T) < 10: return None
    E, dd, A = portfolio(T, S)
    yrs = (T1 - T0).days / 365.25
    cg = E.iloc[-1] ** (1 / yrs) - 1
    o = dict(n=len(A), cagr=cg, dd=dd, cal=cg / -dd if dd < 0 else 0, win=(A.net > 0).mean(), avg=A.net.mean())
    for nm, a, b in (('tr', T0, TS), ('te', TS, T1)):
        X = T[(T.fill >= a) & (T.fill < b)]
        if len(X) < 5: o[nm + '_cal'] = 0; o[nm + '_cagr'] = 0; continue
        e, d2, _ = portfolio(X, S); y = (b - a).days / 365.25; g = e.iloc[-1] ** (1 / y) - 1
        o[nm + '_cal'] = g / -d2 if d2 < 0 else 0; o[nm + '_cagr'] = g
    # half-years positive
    E2 = E.reindex(E.index.union(HB)).ffill().fillna(1.0)
    h = E2.reindex(HB[HB <= T1 + pd.Timedelta('1D')]).values
    o['hpos'] = int((np.diff(np.r_[h, E.iloc[-1]]) > 0).sum()); o['hn'] = len(h)
    return o


E1_BASE = dict(s=1, zw=20, atrn=14, aregn=500, z=1.75, ar=1.8, q=1.7, lv=[1.5, 3, 5], fr=[.25, .25, .5], Wn=1, tp=3.0, st=0, H=96)
E3_BASE = dict(s=-1, zw=20, atrn=14, aregn=500, z=1.5, ar=1.5, r7=-0.07, q=0, lv=[1, 2, 4], fr=[.25, .25, .5], Wn=3, tp=1.75, st=6.0, H=192)

if __name__ == '__main__':
    import time
    t = time.time(); T = trades(E1_BASE); print('E1 base', round(time.time() - t, 1), 's', len(T)); print(stats(T))
    t = time.time(); T = trades(E3_BASE, tf='1h'); print('E3 base', round(time.time() - t, 1), 's', len(T)); print(stats(T, S=1.5))
