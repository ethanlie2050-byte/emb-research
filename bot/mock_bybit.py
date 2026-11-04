"""In-process stand-in for BybitREST, driven by historical bars, to test the whole live stack
(feed -> signals -> bot -> connector -> fills -> reconciliation) without network access.
Matching: 15m bars (1h bars for AVAX/LINK/DOT). A resting limit fills when the bar trades through
its price by 0.1%; price = limit, or the bar open if the order existed before the bar opened and the
open is better. Conditional market orders trigger on the bar's low/high, fill at trigger (or worse
open) with 0.1% slippage. Market orders fill at the last close +/- 0.05%. Fees: maker 0.02%,
taker 0.055%. Funding ignored."""
import math, itertools, os
SX = float(os.environ.get('SLIPX', '1')); FX = float(os.environ.get('FEEX', '1'))   # research: slippage / fee multipliers
import numpy as np, pandas as pd

X1H = ('AVAX', 'LINK', 'DOT')


class Err(Exception):
    pass


class MockREST:
    def __init__(self, D, equity=10000.0):
        from bybit_ex import BybitError
        self.BE = BybitError
        self.D = D; self.now = None; self.offset_ms = 0
        self.cash = equity; self.pos = {}; self.avg = {}
        self.orders = {}; self.execs = []; self.eid = itertools.count(1)
        self.bars = {}
        for c, d in D['m15'].items():
            self.bars[c] = ('15m', d)
        for c in X1H:
            if c in D['h1']: self.bars[c] = ('1h', D['h1'][c])
        self.calls = {}

    def _c(self, n):
        self.calls[n] = self.calls.get(n, 0) + 1

    def now_ms(self):
        return int(self.now.value // 10**6)

    def sync_time(self):
        return 0

    # ---------------------------------------------------------------- market data
    def klines(self, symbol, interval_min, start_ms, end_ms):
        self._c('klines')
        c = symbol[:-4]
        d = self.D['m15'][c] if interval_min == 15 else self.D['h1'][c]
        s = pd.Timestamp(start_ms, unit='ms'); e = pd.Timestamp(end_ms, unit='ms')
        dur = pd.Timedelta(minutes=interval_min)
        x = d[(d.index >= s) & (d.index <= e) & (d.index <= self.now)]   # includes the bar in progress
        out = []
        for t, r in x.iterrows():
            if t + dur > self.now:     # in-progress bar: partial values (here: just open) like the real API
                out.append([int(t.value // 10**6), r.open, r.open, r.open, r.open, 1.0])
            else:
                out.append([int(t.value // 10**6), r.open, r.high, r.low, r.close, r.volume])
        return out[::-1]

    def instrument(self, symbol):
        c = symbol[:-4]
        px = float(self.bars[c][1].close.iloc[-1])
        tick = 10 ** (math.floor(math.log10(px)) - 4)
        step = max(10 ** (math.floor(math.log10(10 / px))), 1e-3) if px > 10 else 1.0
        return dict(priceFilter=dict(tickSize=str(tick)),
                    lotSizeFilter=dict(qtyStep=str(step), minOrderQty=str(step), minNotionalValue='5'),
                    leverageFilter=dict(maxLeverage='50'))

    def set_leverage(self, symbol, lev):
        self._c('lev')

    # ---------------------------------------------------------------- orders
    def _last(self, c):
        tf, d = self.bars[c]
        dur = pd.Timedelta('15min') if tf == '15m' else pd.Timedelta('1h')
        j = d.index.searchsorted(self.now - dur, 'right') - 1
        return float(d.close.values[j])

    def create(self, **b):
        self._c('create')
        link = b['orderLinkId']
        if link in self.orders:
            raise self.BE(110072, 'duplicate orderLinkId')
        c = b['symbol'][:-4]; side = 1 if b['side'] == 'Buy' else -1; q = float(b['qty'])
        o = dict(link=link, coin=c, side=side, qty=q, filled=0.0, type=b['orderType'], reduce=b.get('reduceOnly', False),
                 price=float(b['price']) if 'price' in b else None,
                 trig=float(b['triggerPrice']) if 'triggerPrice' in b else None, placed=self.now, status='New',
                 symbol=b['symbol'])
        if o['trig'] is not None:
            last = self._last(c)
            if (side < 0 and last <= o['trig']) or (side > 0 and last >= o['trig']):
                raise self.BE(110092, 'expect falling/rising, but trigger_price is beyond last price')
        if o['type'] == 'Market' and o['trig'] is None:
            px = self._last(c) * (1 + 0.0005 * SX * side)
            self.orders[link] = o
            p0 = self.pos.get(c, 0.0)
            self._fill(o, q, px, 0.00055 * FX)
            if o['status'] == 'New':      # Bybit: a market order never rests; a truncated reduce-only remainder is cancelled
                o['status'] = 'PartiallyFilledCanceled' if o['filled'] > 0 else 'Cancelled'
                self.truncated = getattr(self, 'truncated', []) + [(str(self.now), link, c, q, p0)]
            return dict(orderId=link, orderLinkId=link)
        self.orders[link] = o
        return dict(orderId=link, orderLinkId=link)

    def amend(self, **b):
        self._c('amend')
        o = self.orders.get(b['orderLinkId'])
        if o is None or o['status'] != 'New':
            raise self.BE(110001, 'order not exists')
        if 'qty' in b: o['qty'] = float(b['qty'])
        if 'price' in b: o['price'] = float(b['price'])
        if 'triggerPrice' in b: o['trig'] = float(b['triggerPrice'])
        return {}

    def cancel(self, symbol, link):
        self._c('cancel')
        o = self.orders.get(link)
        if o is None or o['status'] != 'New':
            raise self.BE(110001, 'order not exists')
        o['status'] = 'Cancelled'
        return {}

    def open_orders(self):
        return [dict(orderLinkId=o['link'], symbol=o['symbol'], side='Buy' if o['side'] > 0 else 'Sell')
                for o in self.orders.values() if o['status'] == 'New']

    def order_info(self, symbol, link):
        o = self.orders.get(link)
        if o is None:
            return None
        return dict(orderStatus=o['status'], cumExecQty=str(o['filled']), avgPrice=str(o.get('avgp', '')) if o['filled'] else '')

    def order_status(self, symbol, link):
        o = self.orders.get(link)
        return None if o is None else o['status']

    def executions(self, start_ms):
        self._c('exec')
        return [e for e in self.execs if int(e['execTime']) >= start_ms]

    def positions(self):
        return [dict(symbol=f'{c}USDT', side='Buy' if p > 0 else 'Sell' if p < 0 else '', size=str(abs(p)))
                for c, p in self.pos.items()]

    def equity(self):
        u = sum(p * (self._last(c) - self.avg[c]) for c, p in self.pos.items() if p)
        return self.cash + u

    # ---------------------------------------------------------------- matching
    def _fill(self, o, q, px, fee):
        c = o['coin']; s = o['side']; p0 = self.pos.get(c, 0.0)
        if o['reduce']:
            if p0 == 0 or np.sign(p0) == s:
                o['status'] = 'Cancelled'; return
            q = min(q, abs(p0))
        # realize pnl on the reducing part
        if p0 != 0 and np.sign(p0) != s:
            red = min(q, abs(p0))
            self.cash += red * (px - self.avg[c]) * np.sign(p0)
            p1 = p0 + s * q
            if np.sign(p1) == np.sign(p0) or p1 == 0:
                pass
            else:
                self.avg[c] = px
        else:
            p1 = p0 + s * q
            self.avg[c] = (self.avg.get(c, 0.0) * abs(p0) + px * q) / abs(p1)
        self.pos[c] = round(p1, 10)
        self.cash -= fee * q * px
        o['avgp'] = (o.get('avgp', 0.0) * o['filled'] + px * q) / (o['filled'] + q)
        o['filled'] += q
        if o['filled'] >= o['qty'] - 1e-12:
            o['status'] = 'Filled'
        self.execs.append(dict(execId=str(next(self.eid)), orderLinkId=o['link'], symbol=o['symbol'],
                               side='Buy' if s > 0 else 'Sell', execPrice=str(px), execQty=str(q),
                               execTime=str(self.now_ms()), execType='Trade'))

    def advance(self, bar_start, tf):
        """process the bar [bar_start, bar_start+dur) for coins whose matching timeframe is tf"""
        live = [o for o in self.orders.values() if o['status'] == 'New' and self.bars[o['coin']][0] == tf]
        # stops first (conservative, as in the backtest), then limits
        live.sort(key=lambda o: (0 if o['trig'] is not None else 1, o['link']))
        for o in live:
            if o['status'] != 'New':
                continue
            tfc, d = self.bars[o['coin']]
            j = d.index.searchsorted(bar_start)
            if j >= len(d) or d.index[j] != bar_start:
                continue
            op, h, l = d.open.values[j], d.high.values[j], d.low.values[j]
            before = o['placed'] <= bar_start
            if o['trig'] is not None:
                if o['side'] < 0 and l <= o['trig']:
                    px = (min(o['trig'], op) if before else o['trig']) * (1 - 0.001 * SX)
                elif o['side'] > 0 and h >= o['trig']:
                    px = (max(o['trig'], op) if before else o['trig']) * (1 + 0.001 * SX)
                else:
                    continue
                self._fill(o, o['qty'] - o['filled'], px, 0.00055 * FX)
            else:
                P = o['price']
                if o['side'] > 0 and l <= P * (1 - 0.001 * SX):
                    px = min(P, op) if before else P
                elif o['side'] < 0 and h >= P * (1 + 0.001 * SX):
                    px = max(P, op) if before else P
                else:
                    continue
                self._fill(o, o['qty'] - o['filled'], px, 0.0002 * FX)
