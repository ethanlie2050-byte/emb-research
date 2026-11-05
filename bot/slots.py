"""Incremental, bar-by-bar version of the backtest's per-engine state machine (sim_engine /
sim_market). One Slot per (engine, coin). It tracks the engine's *virtual* path exactly as the
backtest does (fills judged on bar data with 0.1% trade-through), which decides when an engine
is busy and when a new setup may start. Live orders are managed separately by the bot; the slot
tells the bot which setups are pending (levels, window) and when the backtest would consider the
setup dead/finished."""
import numpy as np
from lib_engines import ENG, PEN, RB1S, RB1H

RBP = {'RB1': dict(sm=RB1S, H=RB1H), 'RB5': dict(sm=1.0, H=60)}


class Slot:
    def __init__(self, E, coin, S):
        self.E = E; self.coin = coin
        self.mode = 'idle'; self.next_ok = 0; self.cur = None
        self.refresh(S)

    def refresh(self, S):
        """(re)load the per-bar arrays; state (mode/cur/next_ok) is kept. Bar positions must be stable
        (data only ever appended), which the live feed guarantees by keeping a fixed start date."""
        E = self.E; coin = self.coin
        self.d = S[(E, coin)]
        self.idx = self.d.index
        self.o = self.d.open.values; self.h = self.d.high.values; self.l = self.d.low.values; self.c = self.d.close.values
        self.sig = self.d.sig.values.astype(bool)
        if E.startswith('RB'):
            self.market = True
            self.dist = self.d.dist.values
            self.sm = RBP[E]['sm']; self.H = RBP[E]['H']
            self.tf = '15m' if E == 'RB1' else '4h'
            self.s = 1
        else:
            self.market = False
            p = ENG[E]
            self.p = p; self.tf = p['tf']; self.s = p['s']
            self.lv = np.array(p['lv'], float); self.fr = np.array(p['fr'], float)
            self.Wn = p['Wn']; self.tpm = float(p['tp']); self.stm = float(p['st']); self.H = p['H']
            self.atr = self.d.atr.values; self.gate = self.d.gate.values.astype(bool)
            self.cf = self.d.cf.values.astype(bool); self.tgt = self.d.tgt.values
            self.gnext = self.d.gnext.values.astype(bool); self.cnext = self.d.cnext.values.astype(bool)
        self.sigidx = np.flatnonzero(self.sig)

    def bar_at(self, t):
        """index of the bar that starts at t: an existing (closed) bar, or len(idx) for the bar now
        in progress (live). None if t is not a bar start of this timeframe."""
        j = self.idx.searchsorted(t)
        if j < len(self.idx):
            return int(j) if self.idx[j] == t else None
        import pandas as pd
        dur = {'15m': pd.Timedelta('15min'), '30m': pd.Timedelta('30min'), '1h': pd.Timedelta('1h'), '4h': pd.Timedelta('4h')}[self.tf]
        return int(j) if t == self.idx[-1] + dur else None

    def gate_at(self, j):
        return bool(self.gate[j]) if j < len(self.gate) else bool(self.gnext[j - 1])

    def cf_at(self, j):
        return bool(self.cf[j]) if j < len(self.cf) else bool(self.cnext[j - 1])

    def __getstate__(self):
        keep = ('E', 'coin', 'mode', 'next_ok', 'cur', 'last')
        return {k: v for k, v in self.__dict__.items() if k in keep}

    # ------------------------------------------------------------------
    def close_bar(self, j):
        """Process the close of bar j (all data of bar j known). Returns list of events."""
        ev = []
        if self.market:
            self._close_market(j, ev)
        else:
            self._close_ladder(j, ev)
        return ev

    def _start_if_signal(self, j, ev):
        if j < self.next_ok or not self.sig[j]:
            return
        if self.market:
            if np.isnan(self.dist[j]):
                return
            a = self.dist[j]
            self.mode = 'wait'
            self.cur = dict(i=j, a=a, stop=self.c[j] - a * self.sm, c_sig=self.c[j])
            ev.append(('setup', dict(self.cur)))
            return
        a = self.atr[j]
        if np.isnan(a):
            return
        cc = self.c[j]
        Lk = cc - self.s * self.lv * a
        self.mode = 'wait'
        self.cur = dict(i=j, a=a, c_sig=cc, Lk=Lk, wend=j + self.Wn,
                        stop=(cc - self.s * self.stm * a) if self.stm > 0 else np.nan)
        ev.append(('setup', dict(self.cur)))

    # ladder engines (E1..E5) ------------------------------------------
    def _close_ladder(self, j, ev):
        s = self.s
        if self.mode == 'idle':
            self._start_if_signal(j, ev)
            return
        cur = self.cur
        if self.mode == 'wait':
            i = cur['i']
            if j <= i:
                return
            L0 = cur['Lk'][0]
            crossed = (self.l[j] <= L0 * (1 - PEN)) if s > 0 else (self.h[j] >= L0 * (1 + PEN))
            if crossed:
                if not self.gate[j]:
                    ev.append(('reject', dict(i=i, f=j)))
                    self.mode = 'idle'; self.next_ok = j + 1; self.cur = None
                    return
                cur.update(f=j, fb=np.full(len(self.lv), -1), fp=np.full(len(self.lv), np.nan), last_fill=j,
                           crash=bool(self.cf[j]))
                self.mode = 'trade'
                ev.append(('vfill', dict(i=i, f=j)))
                self._trade_bar(j, ev)
                return
            if j >= cur['wend']:
                ev.append(('expire', dict(i=i)))
                self.mode = 'idle'; self.next_ok = cur['wend'] + 1; self.cur = None
            return
        if self.mode == 'trade':
            self._trade_bar(j, ev)

    def _trade_bar(self, j, ev):
        cur = self.cur; s = self.s; o, h, l, c = self.o, self.h, self.l, self.c
        Lk = cur['Lk']; fb = cur['fb']; fp = cur['fp']
        for k in range(len(Lk)):
            if fb[k] >= 0:
                continue
            if s > 0:
                if l[j] <= Lk[k] * (1 - PEN):
                    fp[k] = min(Lk[k], o[j]); fb[k] = j; cur['last_fill'] = j
            else:
                if h[j] >= Lk[k] * (1 + PEN):
                    fp[k] = max(Lk[k], o[j]); fb[k] = j; cur['last_fill'] = j
        xb = -1; reason = None; xp = np.nan
        if self.stm > 0:
            st = cur['stop']
            if s < 0 and h[j] >= st:
                xb = j; xp = max(st, o[j]) * 1.001; reason = 'stop'
            elif s > 0 and l[j] <= st:
                xb = j; xp = min(st, o[j]) * 0.999; reason = 'stop'
        if xb < 0 and self.tpm > 0 and j > cur['last_fill']:
            m = fb >= 0
            avg = self.fr[m].sum() / (self.fr[m] / fp[m]).sum()
            tp = avg + s * self.tpm * cur['a']
            if j - 1 >= 0 and not np.isnan(self.tgt[j - 1]):
                tp = max(tp, self.tgt[j - 1]) if s > 0 else min(tp, self.tgt[j - 1])
            if s > 0 and h[j] >= tp * (1 + PEN):
                xb = j; xp = max(tp, o[j]); reason = 'tp'
            elif s < 0 and l[j] <= tp * (1 - PEN):
                xb = j; xp = min(tp, o[j]); reason = 'tp'
        if xb < 0 and j == cur['f'] + self.H - 1:
            xb = j; xp = c[j]; reason = 'time'
        if xb >= 0:
            ev.append(('vexit', dict(i=cur['i'], f=cur['f'], x=xb, xp=xp, reason=reason, fb=fb.copy(), fp=fp.copy())))
            self.mode = 'idle'; self.next_ok = xb + 1; self.cur = None

    # market engines (RB1/RB5) -----------------------------------------
    def _close_market(self, j, ev):
        if self.mode == 'idle':
            self._start_if_signal(j, ev)
            return
        cur = self.cur
        if self.mode == 'wait':
            # entry at open of bar i+1 == j
            f = cur['i'] + 1
            if j != f:
                return
            cur.update(f=f, entry=self.o[f], tp=self.o[f] + cur['a'])
            self.mode = 'trade'
            ev.append(('vfill', dict(i=cur['i'], f=f)))
        if self.mode == 'trade':
            l, h, o, c = self.l, self.h, self.o, self.c
            xb = -1
            if l[j] <= cur['stop']:
                xb = j; xp = min(cur['stop'], o[j]) * 0.999; r = 'stop'
            elif j > cur['f'] and h[j] >= cur['tp']:
                xb = j; xp = max(cur['tp'], o[j]); r = 'tp'
            elif j == cur['f'] + self.H - 1:
                xb = j; xp = c[j]; r = 'time'
            if xb >= 0:
                ev.append(('vexit', dict(i=cur['i'], f=cur['f'], x=xb, xp=xp, reason=r)))
                self.mode = 'idle'; self.next_ok = xb + 1; self.cur = None

    # fast-forward through history (no event collection needed) -------
    def run_until(self, jend):
        """Advance state through bars 0..jend (inclusive) quickly. Used for warm-up."""
        j = 0
        while j <= jend:
            if self.mode == 'idle':
                k = np.searchsorted(self.sigidx, max(j, self.next_ok))
                if k >= len(self.sigidx) or self.sigidx[k] > jend:
                    self.last = jend
                    return
                j = self.sigidx[k]
            self.close_bar(j)
            j += 1
        self.last = jend
