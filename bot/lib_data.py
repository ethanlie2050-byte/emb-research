"""Clean-room data loading per SPEC_v5 section 1."""
import numpy as np, pandas as pd, os, pickle

W = '/home/claude/work'
B = '/home/claude/bybit'
P15 = ['BTC', 'ETH', 'BNB', 'XRP', 'ADA', 'TRX', 'DOGE', 'BCH', 'ZEC', 'SOL']
X1H = ['AVAX', 'LINK', 'DOT']
ADDON_EXTRA = ['HBAR', 'NEAR', 'SUI', 'WLD', 'ONDO', 'ENA']
if os.environ.get('UNIV'):                      # research: point-in-time universe, comma list of 19 ranked coins
    _u = os.environ['UNIV'].split(',')
    _m = [c for c in _u[:13] if c not in ('BTC', 'ETH')]
    if os.environ.get('ALL15'):                 # research: every coin on the 15-minute engines (top N), none 1h-only
        _n = int(os.environ.get('UNIVN', '13')); _a = [c for c in _u[:_n] if c not in ('BTC', 'ETH')]
        P15 = ['BTC', 'ETH'] + _a; X1H = []; ADDON_EXTRA = []
    else:
        P15 = ['BTC', 'ETH'] + _m[:8]; X1H = _m[8:11]; ADDON_EXTRA = _u[13:19]
ALL13 = P15 + X1H   # v2.3 trend add-on only (1h feed)
COLS = ['open', 'high', 'low', 'close', 'volume']
START = pd.Timestamp('2019-01-01')


def _norm(d):
    d = d[COLS].astype('float64').copy()
    d.index = pd.DatetimeIndex(d.index).as_unit('ns')
    d.index.name = 't'
    return d


def splice(base, byb):
    byb = byb[byb.volume != 0]
    t0 = byb.index[0]
    out = pd.concat([base[base.index < t0], byb[byb.index >= t0]])
    out = out[~out.index.duplicated(keep='last')].sort_index()
    return out


def resample(d, rule):
    r = d.resample(rule, label='left', closed='left').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    r = r.dropna()
    return r[r.volume != 0]


def load_all(cache='/home/claude/cleanroom/cache_data.pkl'):
    if os.path.exists(cache):
        return pickle.load(open(cache, 'rb'))
    m15, h1 = {}, {}
    for c in P15:
        base = _norm(pd.read_pickle(f'{W}/P15_{c}USDT.pkl'))
        base = base[(base.index >= START) & (base.volume != 0)]
        byb = _norm(pd.read_parquet(f'{B}/{c}USDT_15m.parquet'))
        m15[c] = splice(base, byb)
        h1[c] = resample(m15[c], '1h')
    for c in X1H:
        base = _norm(pd.read_pickle(f'{W}/SK_{c}USDT_1h.pkl'))
        base = base[(base.index >= START) & (base.volume != 0)]
        byb = _norm(pd.read_parquet(f'{B}/{c}USDT_1h.parquet'))
        h1[c] = splice(base, byb)
    btc4 = resample(m15['BTC'], '4h')
    # core L series
    L = _norm(pd.read_pickle(f'{W}/L_4h.pkl'))
    bb = _norm(pd.read_parquet(f'{B}/BTCUSDT_15m.parquet'))
    bb = bb[bb.volume != 0]
    b4 = resample(bb, '4h')
    t0 = b4.index[0]
    Lc = pd.concat([L[L.index < t0], b4])
    out = dict(m15=m15, h1=h1, btc4=btc4, L=Lc)
    pickle.dump(out, open(cache, 'wb'))
    return out


if __name__ == '__main__':
    d = load_all()
    for c in P15:
        print(c, d['m15'][c].index[0], d['m15'][c].index[-1], len(d['m15'][c]), len(d['h1'][c]))
    for c in X1H:
        print(c, d['h1'][c].index[0], d['h1'][c].index[-1], len(d['h1'][c]))
    print(d['btc4'].head(2)); print(d['L'].loc['2020-03-25'])
