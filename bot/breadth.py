"""BE-2 altcoin-capitulation engine (research: opt/r18 addbr17.py, ev.run).

Rules (closed 4h bars, UTC 00/04/08/12/16/20):
  count(bar)   = number of the 17 add-on altcoins whose 4h close is at least 15% below their close 6 bars (24h) earlier
  signal       = count(bar) >= 5 and count(previous bar) < 5                    -> buy at the next bar open
  coins/size   = BTC and ETH 0.5 x total equity each; v3.1 also 10 altcoins (ALTS) ALT x total equity each
                 (research: opt/r25 brlib.stream2, same signal, same 24h hold) (market orders)
  exit         = after 6 bars: market sell when the 6th bar after entry has closed (24h hold), no stop/take-profit
  one position per coin at a time; a new signal while holding is ignored (research: next signal only after exit).
v3.2 euphoria engine (research: opt/r25 brlib.stream2 side=-1): the mirror signal with the SAME settings -
  count_up(bar) = number of the 17 altcoins whose 4h close is at least 15% ABOVE their close 24h earlier;
  count_up >= 5 and previous < 5 -> SHORT BTC and ETH 0.5 x equity each at the next open, buy back 24h later.
Positions live in the exchange's add-on book (netted with core and engine trades; 35% exchange-side disaster stop)."""
import numpy as np, pandas as pd
from addon import AddOnBook, ADDON_COINS

H4 = pd.Timedelta('4h')
ALT = 0.05            # v3.1: 10 altcoins x 0.05 (diversified: one coin collapsing costs at most ~2.5% of equity)
ALTS = ('SOL', 'BNB', 'XRP', 'DOGE', 'ADA', 'AVAX', 'LINK', 'DOT', 'BCH', 'TRX')
import os as _os
if _os.environ.get('UNIV'):
    from lib_data import ALL13 as _A
    ALTS = tuple(c for c in _A if c not in ('BTC', 'ETH'))[:10]
P = dict(drop=0.15, look=6, k=5, hold=6, size=0.5, coins=('BTC', 'ETH') + ALTS,
         sizes=dict({'BTC': 0.5, 'ETH': 0.5}, **{c: ALT for c in ALTS}))


EUPH = dict(coins=('BTC', 'ETH'), sizes={'BTC': 0.5, 'ETH': 0.5})      # v3.2 euphoria shorts
KIND = {'B': dict(up=False, prefix='AB', side=+1, coins=P['coins'], sizes=P['sizes']),      # capitulation: buy
        'E': dict(up=True, prefix='AE', side=-1, coins=EUPH['coins'], sizes=EUPH['sizes'])}  # euphoria: short


def counts(A4, up=False):
    R = pd.DataFrame({c: d.close / d.close.shift(P['look']) - 1 for c, d in A4.items() if len(d)})
    return (R >= P['drop']).sum(axis=1) if up else (R <= -P['drop']).sum(axis=1)


class BreadthLogic:
    def __init__(self, kind='B'):
        self.state = {}      # coin -> dict(tid, t_entry, t_exit, real)
        self.n = 0
        self.kind = kind

    def __setstate__(self, d):
        self.__dict__.update(d); self.__dict__.setdefault('kind', 'B')     # objects saved by v3.0/v3.1

    @property
    def cfg(self):
        return KIND[self.kind]

    def release(self, tid):
        for c, st in list(self.state.items()):
            if st['tid'] == tid:
                st['real'] = False

    def step(self, bar_start, A4, F=None, bup=None, virtual=False):
        entries, exits = [], []
        nxt = bar_start + H4
        for c, st in list(self.state.items()):
            if bar_start >= st['t_last']:            # the 6th held bar has closed -> exit at next open
                del self.state[c]
                if st['real']:
                    exits.append(dict(coin=c, tid=st['tid'], t_exit=nxt))
        n = counts(A4, self.cfg['up'])
        if bar_start in n.index:
            j = n.index.get_loc(bar_start)
            now_ = n.values[j]; prev = n.values[j - 1] if j > 0 else 0
            if now_ >= P['k'] and prev < P['k']:
                for c in self.cfg['coins']:
                    if c in self.state:
                        continue
                    self.n += 1; tid = f"{self.cfg['prefix']}{self.n:05d}"   # 'A' prefix: the connector routes add-on-book fills by it
                    self.state[c] = dict(tid=tid, t_entry=nxt, t_last=nxt + (P['hold'] - 1) * H4, real=not virtual)
                    if not virtual:
                        entries.append(dict(coin=c, tid=tid, t_entry=nxt, count=int(now_)))
        return entries, exits


class BreadthBook(AddOnBook):
    """Same order handling as the add-on book; different logic, sizing and price source."""

    def __init__(self):
        super().__init__(); self.logic = BreadthLogic()

    def step(self, t, D, ex, L):
        bs = t.floor('4h') - H4
        if self.last_bs is not None and bs <= self.last_bs:
            return
        A4 = {c: d for c, d in D['a4'].items() if c in ADDON_COINS and c not in getattr(ex, 'disabled', set())}
        late = t - (bs + H4)
        missing = [c for c, d in A4.items() if len(d) and d.index[-1] == bs - H4]
        px_ok = len(D['btc4']) and D['btc4'].index[-1] >= bs and len(D['eth4']) and D['eth4'].index[-1] >= bs
        if (missing or not px_ok) and late < pd.Timedelta('30min'):
            return
        if missing:
            L.p(f'ALERT: capitulation engine: no closed 4h bar {bs} for {missing} after 30 min; counted without them')
        A4 = {c: d[d.index <= bs] for c, d in A4.items()}
        if not self.warm:
            self.warm = True; L.ev('breadth_warmup', bar=str(bs))
        entries, exits = self.logic.step(bs, A4)
        self.last_bs = bs
        for x in exits:
            p = self.pos.get(x['tid'])
            if p is not None:
                L.ev('breadth_exit', trade=x['tid'], coin=x['coin'])
                p['exiting'] = True
                p['exit_link'] = ex.market(x['coin'], -1, None, tag=(x['tid'], 'aexit', 0), bar_start=t, tf='4h')
        if not entries:
            return
        eq = ex.equity()
        shorts = {q.coin for q in (ex.bot.open_trades() if ex.bot is not None else []) if q.s < 0}
        for e in entries:
            c = e['coin']; px = float((D['btc4'] if c == 'BTC' else D['eth4']).close.iloc[-1])
            if c in shorts:
                self.logic.release(e['tid']); self.logic.state.pop(c, None)
                L.ev('breadth_entry_skipped_open_short', trade=e['tid'], coin=c); continue
            qty = P['size'] * eq / px
            L.p(f'capitulation signal: {e["count"]} altcoins down 15%+ in 24h -> buying {c}')
            L.ev('breadth_entry', trade=e['tid'], coin=c, frac=P['size'], qty=qty, count=e['count'])
            self.pos[e['tid']] = dict(coin=c, px=None, stop_oid=None, opened=str(t))
            link = ex.market(c, +1, qty, tag=(e['tid'], 'entry', 0), bar_start=t, tf='4h', px=px)
            if link is None:
                self.pos.pop(e['tid'], None); self.logic.release(e['tid']); self.logic.state.pop(c, None)
                L.ev('breadth_entry_not_sent', trade=e['tid'], coin=c)
            else:
                self.pos[e['tid']]['entry_link'] = link
