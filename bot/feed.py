"""Market data feed: Bybit mainnet public klines (linear USDT perps), cached locally as parquet.
Only fully closed bars are kept. 1h bars for the 10 main coins and the BTC 4h bars are resampled
from 15m bars exactly like the backtest (lib_data.resample); AVAX/LINK/DOT use native 1h klines."""
import os, time
import numpy as np, pandas as pd
from lib_data import P15, X1H, ADDON_EXTRA

COLS = ['open', 'high', 'low', 'close', 'volume']
TF_MIN = {'15m': 15, '1h': 60}


def resample(d, rule):
    r = d.resample(rule, label='left', closed='left').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    r = r.dropna()
    return r[r.volume != 0]


class Feed:
    def __init__(self, get_klines, cache_dir, start='2025-04-01', log=print):
        """get_klines(symbol, interval_minutes, start_ms, end_ms) -> list of [startMs, o, h, l, c, vol, ...]"""
        self.get = get_klines; self.dir = cache_dir; os.makedirs(cache_dir, exist_ok=True)
        self.start = pd.Timestamp(start); self.log = log
        self.series = {}
        for c in P15:
            self.series[(c, '15m')] = self._load(c, '15m')
        for c in X1H + ADDON_EXTRA:
            self.series[(c, '1h')] = self._load(c, '1h')

    def _path(self, c, tf):
        return os.path.join(self.dir, f'{c}USDT_{tf}.pkl')

    def _load(self, c, tf):
        p = self._path(c, tf)
        if os.path.exists(p):
            d = pd.read_pickle(p)
            d.index = pd.DatetimeIndex(d.index).as_unit('ns')
            return d[d.index >= self.start]
        return pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([], name='t').as_unit('ns'), dtype='float64')

    def update(self, now):
        """download every closed bar up to `now` (UTC, tz-naive). Returns True if all series are current."""
        ok = True
        for (c, tf), d in list(self.series.items()):
            dur = pd.Timedelta(minutes=TF_MIN[tf])
            last_closed_start = (now - dur).floor(dur) if tf == '15m' else (now - dur).floor('1h')
            nxt = d.index[-1] + dur if len(d) else self.start
            if nxt > last_closed_start:
                continue
            rows = []
            cur = nxt
            while cur <= last_closed_start:
                end = min(cur + dur * 999, last_closed_start + dur)   # +dur: also ask for the bar in progress
                k = self.get(f'{c}USDT', TF_MIN[tf], int(cur.value // 10**6), int(end.value // 10**6))
                if not k:
                    break
                rows += k
                cur = end + dur
            # the exchange has moved past last_closed_start if it returned a newer bar; a closed bar that
            # is still absent then is a genuine exchange gap (no trades), not a delay.
            newest = max((int(r[0]) for r in rows), default=0)
            moved_on = newest > last_closed_start.value // 10**6
            if rows:
                n = pd.DataFrame([r[:6] for r in rows], columns=['t'] + COLS)
                n['t'] = pd.to_datetime(n.t.astype('int64'), unit='ms')
                n = n.set_index('t').astype('float64').sort_index()
                n = n[~n.index.duplicated(keep='last')]
                n = n[(n.index >= nxt) & (n.index <= last_closed_start) & (n.volume != 0)]
                n.index = pd.DatetimeIndex(n.index).as_unit('ns'); n.index.name = 't'
                d = pd.concat([d, n]) if len(d) else n
                self.series[(c, tf)] = d
                d.to_pickle(self._path(c, tf) + '.tmp'); os.replace(self._path(c, tf) + '.tmp', self._path(c, tf))
            if (not len(d) or d.index[-1] < last_closed_start) and not moved_on:
                if c in ADDON_EXTRA:
                    # add-on-only coin: never hold back the main engines; the add-on skips stale bars itself
                    self.log(f'feed: add-on coin {c} {tf} not current (last {d.index[-1] if len(d) else None})')
                    continue
                ok = False
                self.log(f'feed: {c} {tf} last bar {d.index[-1] if len(d) else None} < {last_closed_start}')
        return ok

    def build(self, now):
        """dict(m15, h1, btc4) using only bars that are closed at `now`"""
        m15 = {c: self.series[(c, '15m')] for c in P15}
        m15 = {c: d[d.index + pd.Timedelta('15min') <= now] for c, d in m15.items()}
        h1 = {c: resample(d[d.index < now.floor('1h')], '1h') for c, d in m15.items()}
        for c in X1H:
            d = self.series[(c, '1h')]
            h1[c] = d[d.index + pd.Timedelta('1h') <= now]
        b = m15['BTC']
        def _closed4(d):
            r = resample(d[d.index < now.floor('4h')], '4h')
            if len(r) and r.index[-1] == now.floor('4h') - pd.Timedelta('4h') and \
                    (not len(d) or d.index[-1] < now.floor('4h') - pd.Timedelta('15min')):
                r = r.iloc[:-1]                     # last 4h bar incomplete (15m data missing)
            return r
        btc4 = _closed4(b)
        e = m15['ETH']
        eth4 = _closed4(e)
        a4 = {}                                     # v2.3 trend add-on: closed 4h bars
        for c in P15:
            d = m15[c]; r = resample(d[d.index < now.floor('4h')], '4h')
            if len(r) and r.index[-1] == now.floor('4h') - pd.Timedelta('4h') and \
                    (not len(d) or d.index[-1] < now.floor('4h') - pd.Timedelta('15min')):
                r = r.iloc[:-1]                     # last 4h bar incomplete (15m data missing)
            a4[c] = r
        f4 = now.floor('4h')
        for c in X1H + ADDON_EXTRA:
            d = self.series[(c, '1h')]; d = d[d.index + pd.Timedelta('1h') <= now]
            r = resample(d[d.index < f4], '4h')
            if len(r) and r.index[-1] == f4 - pd.Timedelta('4h') and (not len(d) or d.index[-1] < f4 - pd.Timedelta('1h')):
                r = r.iloc[:-1]                     # last 4h bar incomplete (1h data missing): not a closed bar yet
            a4[c] = r
        return dict(m15=m15, h1=h1, btc4=btc4, eth4=eth4, a4=a4)
