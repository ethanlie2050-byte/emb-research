"""v2.3 trend add-on (research: trend_hf.run + trend_hf2.f_both + sleeve.simulate, ALT 17 coins, 4h).

Rules (all on closed 4h bars, UTC 00/04/08/12/16/20):
  filter(bar)  = coin SMA50 > SMA200 (4h closes)  AND  BTC 4h close > SMA300 of BTC 4h closes
  entry signal = filter(bar) and close(bar) > max(high of the previous N=30 bars)     -> buy at next bar open (v3.3; was 20)
  exit check   = from the entry bar on, at every close: pk = max close since entry bar,
                 stop = max(min(low of previous 10 bars), pk - 5 * ATR14(signal bar));
                 exit if close < stop or filter false                                 -> sell at next bar open
  per coin the signal sequence is independent of admission (a trade rejected because 8 are open still
  blocks the coin until its own exit, exactly like the research trade list).
  admission    = at most 8 positions (a position keeps its slot until its exit bar has closed), 1 per coin.
  size         = 0.3 * 1.5 / 8 * clip(0.03 / ATR%, 0.25, 2.0) * total equity   (v3.3 cap 2.0; was 1.5)
"""
import numpy as np, pandas as pd

ADDON_COINS = ['BNB', 'XRP', 'ADA', 'TRX', 'DOGE', 'BCH', 'ZEC', 'SOL', 'AVAX', 'LINK', 'DOT',
               'HBAR', 'NEAR', 'SUI', 'WLD', 'ONDO', 'ENA']
import os as _os
if _os.environ.get('UNIV'):
    from lib_data import ALL13 as _A, ADDON_EXTRA as _X
    ADDON_COINS = [c for c in _A if c not in ('BTC', 'ETH')] + list(_X)
P = dict(N=30, X=10, trail=5.0, K=8, lev=1.5, w=0.3, btc_sma=300, f_fast=50, f_slow=200, hi=2.0)   # v3.3: N 30 (was 20), size cap 2.0 (was 1.5)
H4 = pd.Timedelta('4h')


def features(d):
    """d: 4h OHLC (index = bar open). Same formulas as the research (lib indicators atr, rolling windows)."""
    c = d.close; pc = c.shift(1)
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    f = pd.DataFrame(index=d.index)
    f['atr'] = tr.rolling(14).mean()
    f['hi'] = d.high.rolling(P['N']).max().shift(1)
    f['lo'] = d.low.rolling(P['X']).min().shift(1)
    f['coin_up'] = (c.rolling(P['f_fast']).mean() > c.rolling(P['f_slow']).mean())
    return f


def btc_up(btc4):
    return btc4.close > btc4.close.rolling(P['btc_sma']).mean()


class AddOnLogic:
    """Pure decision logic. step(bar_start) processes the 4h bar that STARTED at bar_start (closed at +4h)
    and returns (entries, exits) to execute at the next bar open (= bar_start + 4h)."""

    def __init__(self):
        self.state = {}      # coin -> dict(t_entry, pk, atr_sig, real(bool), tid, exit_bar=None)
        self.slots = []      # real positions: dict(coin, tid, free_at)
        self.n = 0

    def release(self, tid):
        """an entry could not be executed: keep following the coin's signal (as research), but free the slot"""
        for st in self.state.values():
            if st['tid'] == tid:
                st['real'] = False
        self.slots = [s for s in self.slots if s['tid'] != tid]

    def step(self, bar_start, A4, F, bup, virtual=False):
        entries, exits = [], []
        nxt = bar_start + H4
        # 1. exits / trail updates for coins in a position (real or virtual)
        for c, st in list(self.state.items()):
            d = A4.get(c); f = F.get(c)
            if d is None or bar_start not in d.index:
                continue
            j = d.index.get_loc(bar_start)
            if bar_start < st['t_entry']:
                continue
            cl = d.close.values[j]
            filt = bool(f.coin_up.values[j]) and bool(bup.get(bar_start, False))
            if not filt:
                cl_eff = -np.inf
            else:
                cl_eff = cl
            if cl_eff > st['pk']:
                st['pk'] = cl_eff
            lo = f.lo.values[j]
            stop = max(lo if lo == lo else -np.inf, st['pk'] - P['trail'] * st['atr_sig'])
            if cl_eff < stop:
                del self.state[c]
                if st['real']:
                    exits.append(dict(coin=c, tid=st['tid'], t_exit=nxt))
                    for s in self.slots:
                        if s['tid'] == st['tid']:
                            s['free_at'] = nxt + H4      # research: slot busy until the exit bar has closed
                st['done_bar'] = bar_start
                self._last_exit = getattr(self, '_last_exit', {}); self._last_exit[c] = bar_start
        # 2. new signals (a coin whose position exited on this very bar can not signal on it)
        self.slots = [s for s in self.slots if s.get('free_at') is None or s['free_at'] > nxt]
        last_exit = getattr(self, '_last_exit', {})
        cands = []
        for c in ADDON_COINS:
            if c in self.state or last_exit.get(c) == bar_start:
                continue
            d = A4.get(c); f = F.get(c)
            if d is None or bar_start not in d.index:
                continue
            j = d.index.get_loc(bar_start)
            if j + 1 >= len(d) + 1:
                continue
            filt = bool(f.coin_up.values[j]) and bool(bup.get(bar_start, False))
            hi = f.hi.values[j]; a = f.atr.values[j]; cl = d.close.values[j]
            if filt and hi == hi and a == a and cl > hi:
                cands.append((c, a, cl))
        for c, a, cl in cands:
            self.n += 1; tid = f'A{self.n:05d}'
            busy = [s for s in self.slots if s.get('free_at') is None or s['free_at'] > nxt]
            real = (not virtual) and len(busy) < P['K']
            # research: pk starts at the close of the ENTRY bar (next bar) -> start at -inf, updated next step
            self.state[c] = dict(t_entry=nxt, pk=-np.inf, atr_sig=a, real=real, tid=tid)
            if real:
                self.slots.append(dict(coin=c, tid=tid, free_at=None))
                entries.append(dict(coin=c, tid=tid, t_entry=nxt, atrp=a / cl))
        return entries, exits


def size_frac(atrp):
    """position notional as a fraction of total equity"""
    return P['w'] * P['lev'] / P['K'] * float(np.clip(0.03 / atrp, 0.25, P['hi'])) if atrp == atrp and atrp > 0 else 0.0


class AddOnBook:
    """Live side of the add-on: turns AddOnLogic decisions into orders and tracks what is actually held.
    Lives on the exchange object (saved with it). Never touches main-engine trades."""

    def __init__(self):
        self.logic = AddOnLogic()
        self.pos = {}            # tid -> dict(coin, px, stop_oid, opened, entry_link, exiting, exit_link)
        self.warm = False; self.last_bs = None

    def __setstate__(self, d):
        self.__dict__.update(d)
        self.__dict__.setdefault('warm', False); self.__dict__.setdefault('last_bs', None)

    def holds(self, c):
        return any(p['coin'] == c for p in self.pos.values())

    def sides(self, c):
        """direction of every add-on position on coin c (+1 long, -1 short; v3.2 euphoria positions are short)"""
        return [p.get('side', 1) for p in self.pos.values() if p['coin'] == c]

    def tids(self):
        return [(tid, p['coin']) for tid, p in self.pos.items()]

    def step(self, t, D, ex, L):
        """call at every 15-min boundary t; acts only at 4h boundaries (bar t-4h just closed)"""
        bs = t.floor('4h') - H4                     # latest 4h bar that has closed by t
        if self.last_bs is not None and bs <= self.last_bs:
            return                                  # bar already processed (e.g. step redone after a crash)
        A4 = {c: d for c, d in D['a4'].items() if c not in getattr(ex, 'disabled', set())}
        late = t - (bs + H4)
        # wait only for coins whose newest bar is the one just before bs (normal late data); coins with older
        # data are stale (halted/delisted) and are not waited for
        missing = [c for c, d in A4.items() if len(d) and d.index[-1] == bs - H4]
        btc_ok = len(D['btc4']) and D['btc4'].index[-1] >= bs
        if (missing or not btc_ok) and late < pd.Timedelta('30min'):
            return                                  # some 4h bar not complete yet: retry at the next 15-min step
        if not btc_ok:
            L.p(f'ALERT: add-on: BTC 4h bar {bs} missing after 30 min; add-on bar skipped')
            self.last_bs = bs; return
        if missing:
            L.p(f'ALERT: add-on: no closed 4h bar {bs} for {missing} after 30 min; those coins skip this bar')
        F = {c: features(d) for c, d in A4.items() if len(d)}
        bup = btc_up(D['btc4']).to_dict()
        if not self.warm:
            # first start: replay the signal history WITHOUT positions so the per-coin state matches research
            hist = sorted(set().union(*[set(d.index[d.index < bs]) for d in A4.values() if len(d)]))
            for b in hist:
                self.logic.step(b, A4, F, bup, virtual=True)
            self.warm = True
            L.ev('addon_warmup', bars=len(hist), virtual_positions=len(self.logic.state))
        stale_exit = []
        for tid, c in self.tids():
            if str(tid).startswith(('AB', 'AE')):
                continue                               # v3.0/v3.2: capitulation / euphoria position (dualbook.py), not a trend trade
            d = A4.get(c)
            if d is None or not len(d) or d.index[-1] < bs - H4:
                stale_exit.append((tid, c))            # no data for 2+ bars (halted / delisted) -> get out
        entries, exits = self.logic.step(bs, A4, F, bup)
        for tid, c in stale_exit:
            if not any(x['tid'] == tid for x in exits):
                L.p(f'ALERT: add-on {c}: no market data for 2+ bars; closing the position')
                exits.append(dict(coin=c, tid=tid, t_exit=bs + H4))
                self.logic.release(tid)                 # free the slot (the position is being closed)
                self.logic.state.pop(c, None)
        self.last_bs = bs
        for x in exits:
            p = self.pos.get(x['tid'])
            if p is not None:
                L.ev('addon_exit', trade=x['tid'], coin=x['coin'])
                p['exiting'] = True
                p['exit_link'] = ex.market(x['coin'], -1, None, tag=(x['tid'], 'aexit', 0), bar_start=t, tf='4h')
        if not entries:
            return
        eq = ex.equity()
        shorts = {q.coin for q in (ex.bot.open_trades() if ex.bot is not None else []) if q.s < 0}
        for e in entries:
            c = e['coin']; px = float(A4[c].close.iloc[-1])
            if c in shorts:
                self.logic.release(e['tid'])
                L.ev('addon_entry_skipped_open_short', trade=e['tid'], coin=c); continue
            frac = size_frac(e['atrp']); qty = frac * eq / px
            L.ev('addon_entry', trade=e['tid'], coin=c, frac=round(frac, 4), qty=qty)
            self.pos[e['tid']] = dict(coin=c, px=None, stop_oid=None, opened=str(t))
            link = ex.market(c, +1, qty, tag=(e['tid'], 'entry', 0), bar_start=t, tf='4h', px=px)
            if link is None:
                self.pos.pop(e['tid'], None); self.logic.release(e['tid'])
                L.ev('addon_entry_not_sent', trade=e['tid'], coin=c)
            else:
                self.pos[e['tid']]['entry_link'] = link

    def on_fill(self, tid, role, coin, sgn, q, px, now, ex):
        p = self.pos.get(tid)
        if p is None:
            if role == 'entry':                  # late ENTRY fill for a position we no longer track -> give it back
                ex.market(coin, -sgn, q, tag=(tid, 'flat', 0), reduce_only=ex._reduce_ok(coin))
            return
        held = ex.tradepos.get(tid, 0.0) * p.get('side', 1)      # > 0 while the position is open (long or short)
        if held < -ex.inst[coin]['minq'] * 0.5:
            # an exit traded more than the add-on held (only possible on a coin with opposite-side holders):
            # the position is closed; reconcile() restores the coin's total expected position
            ex.L.p(f'ALERT: add-on {coin} {role} closed {-held} more than held; reconcile will correct the coin')
            ex.tradepos[tid] = 0.0
            if p.get('stop_oid'): ex.cancel(p['stop_oid'])
            ex.L.ev('addon_closed', trade=tid, coin=coin, how=role, px=px)
            self.pos.pop(tid, None); return
        if role == 'entry':
            p['px'] = px if p['px'] is None else p['px']
        elif role in ('aexit', 'stop', 'stopnow', 'flat') and abs(held) < ex.inst[coin]['minq'] * 0.5:
            if role != 'stop' and p.get('stop_oid'):
                ex.cancel(p['stop_oid'])
            ex.L.ev('addon_closed', trade=tid, coin=coin, how=role, px=px)
            self.pos.pop(tid, None)
