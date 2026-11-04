"""Causal signal/gate/flag series for every engine, computed exactly like the verified
clean-room backtest (lib_engines.build), but returned as per-bar arrays so a live/replay
engine can step through them bar by bar."""
import numpy as np, pandas as pd
from lib_engines import (indicators, qok, crash_series, shift1, rb1_signal, rb5_signal, ENG, E3R, QTHR, E5Z)
from lib_data import P15, X1H, ALL13

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
    raw = {}
    for c in P15:
        f = F['15m'][c]
        with np.errstate(invalid='ignore'):
            raw[('E1', c)] = ((f.z <= -E1Z) & (f.areg >= 1.8))
            raw[('E5', c)] = ((f.z <= -E5Z) & (f.areg >= 1.0))
    btc1 = D['h1']['BTC'].close
    b3 = (btc1 / btc1.shift(72) - 1).rolling(72, min_periods=1).min()
    for c in ALL13:
        f = F['1h'][c]
        raw[('E2', c)] = ((f.z <= -1.5) & (f.areg >= E2A))
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
    S = {}
    for E, p in ENG.items():
        tf = p['tf']
        for c in p['coins']:
            d = D['m15'][c] if tf == '15m' else D['h1'][c]
            f = F[tf][c]
            n = len(d)
            sig = raw[(E, c)].fillna(False).astype(bool).values
            if E in ('E1', 'E2', 'E5'):
                q = qok(f, 'E2' if E == 'E2' else 'E1', QTHR.get(E, 2.0)).values
                cok = np.ones(n, bool) if c in ('BTC', 'ETH') else ~crash[E][c].values
                gnext = np.asarray(q & cok, bool)                 # gate of the NEXT bar (known at this close)
                cnext = np.asarray(crash['E5' if E == 'E1' else E][c].values, bool)
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
                                          close=m.close.values, sig=s1, dist=0.03 * m.close.values), index=m.index)
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
    c = L.close
    above = sum((c > c.rolling(w).mean()).astype(float) for w in wins) / len(wins)
    e = above.copy()
    e[above == 0] = -sw
    vol = c.pct_change().rolling(180).std() * np.sqrt(6 * 365)
    raw = e * np.minimum(vt / vol, 1.0)
    return raw.where((raw - raw.shift(1)).abs() > h * raw.shift(1).abs()).ffill()

