"""Causal signal/gate/flag series for every engine, computed exactly like the verified
clean-room backtest (lib_engines.build), but returned as per-bar arrays so a live/replay
engine can step through them bar by bar."""
import numpy as np, pandas as pd
from lib_engines import (indicators, qok, crash_series, shift1, rb1_signal, rb5_signal, ENG, E3R, QTHR, E5Z)
from lib_data import P15, X1H, ALL13


def _resample(d, tf):
    r = d.resample({'30m': '30min', '1h': '1h'}[tf], label='left', closed='left').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
    return r[r.volume != 0]

E2A = 1.6
E1Z = 1.75          # v2.3 (was 2.0)
import os as _os                                   # research overrides (unset = normal settings)
if _os.environ.get('E1Z'): E1Z = float(_os.environ['E1Z'])
if _os.environ.get('E5Z'): E5Z = float(_os.environ['E5Z'])
if _os.environ.get('E3R'): E3R = float(_os.environ['E3R'])
if _os.environ.get('QE1'): QTHR['E1'] = float(_os.environ['QE1'])
if _os.environ.get('QE5'): QTHR['E5'] = float(_os.environ['QE5'])
REGN = 200          # v2.3 regime: BTC daily close vs SMA200


def compute(D):
    """D: dict(m15={coin:df}, h1={coin:df}, btc4=df). Returns dict:
       S[(E,coin)] = DataFrame(index=bar open time; open high low close atr sig gate cf tgt)
       S[('RB1','BTC')], S[('RB5','BTC')] = DataFrame(open high low close sig dist)"""
    F = {'15m': {c: indicators(D['m15'][c], '15m') for c in P15},
         '1h': {c: indicators(D['h1'][c], '1h') for c in ALL13}}
    # research: E1 on its own candle size / indicator lengths (E1TF, E1ZW, E1ATRN, E1AREGN, E1AR); defaults = v3.4
    E1TF = _os.environ.get('E1TF', '15m')
    M1 = D['m15'] if E1TF == '15m' else {c: _resample(D['m15'][c], E1TF) for c in P15}
    F1 = {c: indicators(M1[c], E1TF, int(_os.environ.get('E1ZW', 20)), int(_os.environ.get('E1ATRN', 14)),
                        int(_os.environ.get('E1AREGN', 500))) for c in P15}
    E1AR = float(_os.environ.get('E1AR', 1.8))
    # research: E2 as a 1h dip engine with its own indicator lengths (E2ZW, E2ATRN, E2AREGN, E2Z, E2AR, QE2, E2QT)
    F2 = {c: indicators(D['h1'][c], '1h', int(_os.environ.get('E2ZW', 20)), int(_os.environ.get('E2ATRN', 14)),
                        int(_os.environ.get('E2AREGN', 500))) for c in ALL13} if _os.environ.get('E2ZW') else F['1h']
    if _os.environ.get('QE2'): QTHR['E2'] = float(_os.environ['QE2'])
    raw = {}
    for c in P15:
        f = F['15m'][c]
        with np.errstate(invalid='ignore'):
            raw[('E1', c)] = ((F1[c].z <= -E1Z) & (F1[c].areg >= E1AR))
            raw[('E5', c)] = ((f.z <= -E5Z) & (f.areg >= float(_os.environ.get('E5AR', 1.0))))   # research E5AR
    btc1 = D['h1']['BTC'].close
    b3 = (btc1 / btc1.shift(72) - 1).rolling(72, min_periods=1).min()
    for c in ALL13:
        f = F['1h'][c]
        with np.errstate(invalid='ignore'):
            raw[('E2', c)] = ((F2[c].z <= -float(_os.environ.get('E2Z', 1.5))) & (F2[c].areg >= float(_os.environ.get('E2AR', E2A))))
        if _os.environ.get('SH2'):      # research: E2 slot becomes a rip-short engine: z >= +Z and areg >= A (use ENGP=E2.s:-1)
            _z, _a = (float(v) for v in _os.environ['SH2'].split('/'))
            with np.errstate(invalid='ignore'):
                raw[('E2', c)] = ((f.z >= _z) & (f.areg >= _a))
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
        crash[E] = crash_series({c: raw[(E, c)] for c in coins}, F1 if E == 'E1' else F2 if E == 'E2' else F[tf], coins, tf)
    S = {}
    for E, p in ENG.items():
        tf = p['tf']
        for c in p['coins']:
            d = M1[c] if E == 'E1' else D['m15'][c] if tf == '15m' else D['h1'][c]
            f = F1[c] if E == 'E1' else F2[c] if E == 'E2' else F[tf][c]
            n = len(d)
            sig = raw[(E, c)].fillna(False).astype(bool).values
            if E in ('E1', 'E2', 'E5') and not (E == 'E2' and _os.environ.get('SH2')):
                q = qok(f, 'E2' if (E == 'E2' and _os.environ.get('E2QT', 'E2') == 'E2') else 'E1', QTHR.get(E, 2.0)).values
                cok = np.ones(n, bool) if c in ('BTC', 'ETH') else ~crash[E][c].values
                gnext = np.asarray(q & cok, bool)                 # gate of the NEXT bar (known at this close)
                cnext = np.asarray(crash['E5' if (E == 'E1' and E1TF == '15m') else E][c].values, bool)
                gate = shift1(gnext); cf = shift1(cnext)
            else:
                gate = np.ones(n, bool); cf = np.zeros(n, bool); gnext = gate; cnext = cf
            tgt = f.sma20.values if E == 'E3' else np.full(n, np.nan)
            S[(E, c)] = pd.DataFrame(dict(open=d.open.values, high=d.high.values, low=d.low.values,
                                          close=d.close.values, atr=f.atr.values, sig=sig, gate=gate,
                                          cf=cf, tgt=tgt, gnext=gnext, cnext=cnext), index=d.index)
    m = D['m15']['BTC']
    s1 = rb1_signal(m, D['h1']['BTC'], D['btc4'])
    S[('RB1', 'BTC')] = pd.DataFrame(dict(open=m.open.values, high=m.high.values, low=m.low.values,
                                          close=m.close.values, sig=s1, dist=float(_os.environ.get('RB1D', 0.03)) * m.close.values), index=m.index)
    if _os.environ.get('RB1E') and 'eth4' in D:     # research: BTC breakout engine also on ETH
        me = D['m15']['ETH']; s1e = rb1_signal(me, D['h1']['ETH'], D['eth4'])
        S[('RB1', 'ETH')] = pd.DataFrame(dict(open=me.open.values, high=me.high.values, low=me.low.values,
                                              close=me.close.values, sig=s1e, dist=float(_os.environ.get('RB1D', 0.03)) * float(_os.environ['RB1E']) * me.close.values), index=me.index)
    s5, atr4 = rb5_signal(D['btc4'], m)
    b = D['btc4']
    S[('RB5', 'BTC')] = pd.DataFrame(dict(open=b.open.values, high=b.high.values, low=b.low.values,
                                          close=b.close.values, sig=s5, dist=1.5 * atr4), index=b.index)
    return S


def regime_series(D, n=REGN):
    """True = bear day (BTC daily close below its n-day SMA), known from the NEXT day (research REGW)."""
    cd = D['m15']['BTC'].close.resample('1D').last().dropna()
    bear = cd < cd.rolling(n).mean()
    bear.index = bear.index + pd.Timedelta('1D')
    return bear


def adv_series(D, W=20):
    """v2.7 liquidity cap input: per coin, 20-day mean of daily $ volume (close x volume), known from the NEXT day
    (research LIQF in bt_portfolio). 15m bars where the bot has them, else 1h bars (same daily sums)."""
    out = {}
    src = dict(D.get('h1', {})); src.update(D.get('m15', {}))
    for c, d in src.items():
        if d is None or not len(d): continue
        dv = (d.close * d.volume).resample('1D').sum()
        a = dv.rolling(W, min_periods=5).mean(); a.index = a.index + pd.Timedelta('1D')
        out[c] = a.dropna()
    return out


def vt_series(D, VT=0.60, W=30, FLOOR=0.3):
    cd = D['m15']['BTC'].close.resample('1D').last().dropna()
    vol = cd.pct_change().rolling(W).std(ddof=1) * np.sqrt(365)
    vol.index = vol.index + pd.Timedelta('1D')
    return (VT / vol).clip(FLOOR, 1.0)


def core_position(L):
    """BTC trend core: target exposure p (0..1) per 4h bar, known at bar close, applied to next bar."""
    c = L.close
    e = sum((c > c.rolling(w).mean()).astype(float) for w in [120, 300, 600, 900]) / 4
    vol = c.pct_change().rolling(180).std() * np.sqrt(6 * 365)
    raw = e * np.minimum(0.4 / vol, 1.0)
    p = raw.where((raw - raw.shift(1)).abs() > 0.1 * raw.shift(1).abs()).ffill()
    return p


def core_position_bear(L, sw=1.0, wins=(120, 300, 600, 900), vt=0.4, h=0.1):
    """v2.3 core (research trend_lab.sig_sma_bear): long fraction of SMAs below the close; short sw when the
    close is below ALL of them; vol-targeted to 40%/yr; 10% relative hysteresis. Per 4h bar, known at the
    bar close, applied to the next bar."""
    if _os.environ.get('COREW'): wins = tuple(int(x) for x in _os.environ['COREW'].split('/'))   # research: trend SMA lengths (4h bars)
    if _os.environ.get('CORESW'): sw = float(_os.environ['CORESW'])                              # research: trend short size
    c = L.close
    above = sum((c > c.rolling(w).mean()).astype(float) for w in wins) / len(wins)
    e = above.copy()
    e[above == 0] = -sw
    vol = c.pct_change().rolling(180).std() * np.sqrt(6 * 365)
    raw = e * np.minimum(vt / vol, 1.0)
    if _os.environ.get('COREH'): h = float(_os.environ['COREH'])   # research: core rebalance hysteresis
    return raw.where((raw - raw.shift(1)).abs() > h * raw.shift(1).abs()).ffill()

