"""v3.0-v3.2: one add-on book that runs the v2.3 altcoin trend add-on, the capitulation engine (BE-2, v3.1 with
10 altcoins) and (v3.2) the euphoria engine. All share the exchange's add-on order handling (fills routed by tids
starting with 'A', 35% disaster stops). Trend tids 'A00001'..., capitulation 'AB00001'..., euphoria 'AE00001'...
Capitulation positions are long (side +1), euphoria positions short (side -1); every position records its side."""
import pandas as pd
from addon import AddOnBook, AddOnLogic, ADDON_COINS
from breadth import BreadthLogic, KIND

H4 = pd.Timedelta('4h')
NAME = {'B': ('capitulation', 'down', 'buying'), 'E': ('euphoria', 'up', 'shorting')}


class RoutedLogic(AddOnLogic):
    """trend logic; release() of a capitulation / euphoria tid is forwarded to that engine's logic"""
    def __init__(self):
        super().__init__(); self.other = BreadthLogic('B'); self.euph = BreadthLogic('E')

    def __setstate__(self, d):
        self.__dict__.update(d)
        if 'euph' not in self.__dict__:
            self.euph = BreadthLogic('E')           # state saved by v3.0 / v3.1

    def release(self, tid):
        if str(tid).startswith('AB'):
            self.other.release(tid)    # the coin stays (virtually) held for its 24h, exactly as the research trade list
        elif str(tid).startswith('AE'):
            self.euph.release(tid)
        else:
            super().release(tid)


class DualBook(AddOnBook):
    def __init__(self):
        super().__init__(); self.logic = RoutedLogic(); self.b_last_bs = None; self.b_warm = False; self.e_warm = False

    def __setstate__(self, d):
        super().__setstate__(d)
        for k, v in dict(b_last_bs=None, b_warm=False, e_warm=False).items():
            self.__dict__.setdefault(k, v)

    @classmethod
    def upgrade(cls, old):
        """turn a saved v2.x AddOnBook (with its open positions and signal state) into a DualBook"""
        nb = cls.__new__(cls); nb.__dict__.update(old.__dict__)
        rl = RoutedLogic.__new__(RoutedLogic); rl.__dict__.update(old.logic.__dict__)
        rl.other = BreadthLogic('B'); rl.euph = BreadthLogic('E')
        nb.logic = rl; nb.b_last_bs = None; nb.b_warm = False; nb.e_warm = False
        return nb

    def sides(self, c):
        """direction of every add-on position on coin c (+1 long, -1 short)"""
        return [p.get('side', 1) for p in self.pos.values() if p['coin'] == c]

    def step(self, t, D, ex, L):
        if not __import__('os').environ.get('NOTREND'):
            AddOnBook.step(self, t, D, ex, L)          # trend add-on (unchanged v2.x code)
        self._breadth(t, D, ex, L)

    def _breadth(self, t, D, ex, L):
        bs = t.floor('4h') - H4
        if self.b_last_bs is not None and bs <= self.b_last_bs:
            return
        A4 = {c: d for c, d in D['a4'].items() if c in ADDON_COINS and c not in getattr(ex, 'disabled', set())}
        late = t - (bs + H4)
        missing = [c for c, d in A4.items() if len(d) and d.index[-1] == bs - H4]
        px_ok = len(D['btc4']) and D['btc4'].index[-1] >= bs and len(D['eth4']) and D['eth4'].index[-1] >= bs
        if (missing or not px_ok) and late < pd.Timedelta('30min'):
            return
        if missing:
            L.p(f'ALERT: capitulation/euphoria engines: no closed 4h bar {bs} for {missing} after 30 min; counted without them')
        A4 = {c: d[d.index <= bs] for c, d in A4.items()}
        for kind, flag in (('B', 'b_warm'), ('E', 'e_warm')):
            if not getattr(self, flag):
                # first start: replay the last 12 bars WITHOUT positions so a signal that is still inside its 24h hold
                # blocks the coin exactly as in the research trade list (no late entry, no extra trade)
                lg = self.logic.other if kind == 'B' else self.logic.euph
                for b in pd.date_range(bs - 12 * H4, bs - H4, freq=H4):
                    lg.step(b, A4, virtual=True)
                setattr(self, flag, True)
                L.ev('breadth_warmup' if kind == 'B' else 'euphoria_warmup', bar=str(bs), virtual_positions=len(lg.state))
        work = []
        for kind in ('B', 'E'):
            lg = self.logic.other if kind == 'B' else self.logic.euph
            entries, exits = lg.step(bs, A4)
            if kind == 'E' and __import__('os').environ.get('NOEUPH'): entries = []   # research switch: euphoria off
            if kind == 'B' and __import__('os').environ.get('CAPSZ') == '0': entries = []   # research switch: crash-buyer off
            work.append((kind, lg, entries, exits))
        self.b_last_bs = bs
        for kind, lg, entries, exits in work:
            ev = 'breadth' if kind == 'B' else 'euphoria'
            for x in exits:
                p = self.pos.get(x['tid'])
                if p is not None:
                    L.ev(f'{ev}_exit', trade=x['tid'], coin=x['coin'])
                    p['exiting'] = True
                    p['exit_link'] = ex.market(x['coin'], -p.get('side', 1), None, tag=(x['tid'], 'aexit', 0), bar_start=t, tf='4h')
        if not any(w[2] for w in work):
            return
        eq = ex.equity()
        op = ex.bot.open_trades() if ex.bot is not None else []
        shorts = {q.coin for q in op if q.s < 0}
        for kind, lg, entries, exits in work:
            ev = 'breadth' if kind == 'B' else 'euphoria'
            side = KIND[kind]['side']; name, word, verb = NAME[kind]
            for e in entries:
                c = e['coin']
                b4 = D['btc4'] if c == 'BTC' else D['eth4'] if c == 'ETH' else A4.get(c)
                if b4 is None or not len(b4):
                    lg.release(e['tid']); L.ev(f'{ev}_entry_no_price', trade=e['tid'], coin=c); continue
                px = float(b4.close.iloc[-1])
                if side > 0 and c in shorts:
                    lg.release(e['tid'])
                    L.ev('breadth_entry_skipped_open_short', trade=e['tid'], coin=c); continue
                frac = KIND[kind]['sizes'][c] * (float(__import__('os').environ.get('CAPSZ', '1')) if kind == 'B' else 1.0); qty = frac * eq / px   # research: CAPSZ scales crash-buyer size
                L.p(f'{name} signal: {e["count"]} altcoins {word} 15%+ in 24h -> {verb} {c}')
                L.ev(f'{ev}_entry', trade=e['tid'], coin=c, frac=frac, qty=qty, count=e['count'], side=side)
                self.pos[e['tid']] = dict(coin=c, px=None, stop_oid=None, opened=str(t), side=side)
                link = ex.market(c, side, qty, tag=(e['tid'], 'entry', 0), bar_start=t, tf='4h', px=px)
                if link is None:
                    self.pos.pop(e['tid'], None); lg.release(e['tid'])
                    L.ev(f'{ev}_entry_not_sent', trade=e['tid'], coin=c)
                else:
                    self.pos[e['tid']]['entry_link'] = link
