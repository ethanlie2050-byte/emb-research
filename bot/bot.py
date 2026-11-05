"""Strategy bot: portfolio / risk / order-management layer.
Works against an Exchange interface (simex.SimExchange for replay, bybit_ex.BybitExchange live).
Rules follow SPEC_v5.md + addenda 1-10 (final settings)."""
import numpy as np
VOLS = {}
EXPO_FN = None      # research/live: function -> {coin: signed position notional} from the exchange
CRASH = lambda c: 0.15 if c == 'BTC' else 0.20 if c == 'ETH' else 0.60   # 10 Oct 2025 observed flash-crash depths
import numpy as np, pandas as pd, math, json, csv, os
from lib_engines import ENG
from slots import Slot

STEP = pd.Timedelta('15min')
DUR = {'15m': pd.Timedelta('15min'), '1h': pd.Timedelta('1h'), '4h': pd.Timedelta('4h')}
EO = {e: i for i, e in enumerate(['E1', 'E2', 'E3', 'E4', 'E5', 'RB1', 'RB5'])}
G = {'E1', 'E2', 'E5'}
GD = set(__import__('os').environ.get('DIPG', 'E1,E2,E5').split(',')) - {''}   # research DIPG: engines limited to one open dip trade at a time
SHORTE = {'E3'} | ({'E2'} if __import__('os').environ.get('SH2') else set())   # research: short engines skip long-exposure caps

CFG = dict(
    kd=22.0, fd=0.35, kc=0.5, kmax=18,   # v3.3 at x22 (caps x1.1)
    caps={'E4': 3, 'RB1': 3, 'RB5': 3, 'E3': 4},
    W={'E1': 0.4125, 'E2': 0.2475, 'E3': 0.297, 'E4': 0.099, 'E5': 0.202125, 'RB1': 0.33, 'RB5': 0.165},
    crsc=0.5, cool_steps=288, e3risk=0.45, e3tot=0.72, e3ps=0.30, e4tt=1.5, e4tf=0.5,
    fee_maker=0.0002, fee_taker=0.00085, slip_stop=0.001, fund8h=0.0001, pen=0.002,
    # v2.3
    regw_bear={'E4': 0.25, 'E3': 0.65, 'RB5': 1.0}, regw_bull={'E3': 0.8, 'RB5': 1.0},
    e3gross=3.3, grossl=3.3, rb_tp_pen=0.002,   # x22: caps scaled x1.1
    # v2.4 (reviewer round, 2026-10-10): x20, E3 at half size (W 0.495->0.2475, e3tot 1.2->0.6), RB5 never admitted
    disabled=('RB5',) + tuple(x for x in __import__('os').environ.get('DIS', '').split(',') if x),
    # v2.7 (round 10, 2026-10-13): E1 weight x1.25 (0.33->0.4125), BTC/ETH core weight 0.325->0.45 of equity,
    # liquidity cap: one position <= liqf x the coin's 20-day average daily Bybit $ volume (binds only at large size)
    wcore=0.45, liqf=0.02,
    # v2.8 (2026-10-15, robustness round): E1 take-profit 3.0 ATR (lib_engines), E4 BTC-drop block 6% (lib_engines/signals),
    # E4 at quarter size in bear markets (regw_bear E4 0.25); v2.9 (2026-10-15): E3 take-profit 1.75 ATR; v2.10: E3 7-day drop -5.5% (lib_engines)
    # v3.0 (2026-10-15, all-years round): E3 size x1.2 (W 0.2475->0.297, e3tot 0.6->0.72), E3 bull-market size 0.8 (was 0.65),
    # E1/E2 quality threshold 1.95 (lib_engines QTHR), RB1 max hold 168 bars (lib_engines RB1H), + capitulation engine (dualbook.py)
    # v3.1 (2026-10-15): E2 hold 15h, E5 dip depth 1.95 (lib_engines), capitulation engine also buys 10 altcoins (breadth.py)
    # v3.2 (2026-10-15): euphoria engine - short BTC/ETH 0.5 each for 24h after 5+ altcoins jump 15%+ (breadth.py, dualbook.py)
    # v3.3 (2026-10-15): altcoin trend add-on breakout 30 bars (was 20), size cap 2.0 (was 1.5) (addon.py)
)
for _kv in __import__('os').environ.get('CFGO', '').split(','):   # research: override numeric settings, e.g. CFGO=kd:26,wcore:0.3
    if _kv:
        _k, _v = _kv.split(':'); CFG[_k] = float(_v)


class Trade:
    _n = 0

    def __init__(self, **kw):
        Trade._n += 1
        self.id = f"T{Trade._n:05d}"
        self.__dict__.update(kw)
        self.status = 'pending'; self.levels = []; self.f_time = None; self.f_idx = None
        self.last_fill_idx = None; self.trimmed = False; self.realized = 0.0
        self.stop_oid = None; self.tp_oid = None; self.exit = None


class Bot:
    def __init__(self, ex, S, cfg=None, start=None, log_dir=None, accounting='backtest', core=False):
        self.ex = ex; self.S = S; self.cfg = dict(CFG, **(cfg or {}))
        self.slots = {}
        for E, p in ENG.items():
            for c in p['coins']:
                self.slots[(E, c)] = Slot(E, c, S)
        self.slots[('RB1', 'BTC')] = Slot('RB1', 'BTC', S); self.slots[('RB5', 'BTC')] = Slot('RB5', 'BTC', S)
        if ('RB1', 'ETH') in S: self.slots[('RB1', 'ETH')] = Slot('RB1', 'ETH', S)   # research RB1E
        self.trades = {}           # id -> Trade (pending/open)
        self.closed = []
        self.setup_trade = {}      # (E,coin,i) -> trade id
        self.cash = 1.0 if accounting == 'backtest' else None
        self.accounting = accounting
        self.lastloss = {}; self.vtm = {}; self.reg = {}; self.core = core; self.adv = {}
        self.log_dir = log_dir; self.events = []
        self.daily = {}; self.intra = {}; self.nskip = 0; self.nps = 0
        self.start = start

    # ------------------------------------------------------------ helpers
    def log(self, t, kind, **kw):
        rec = dict(time=str(t), kind=kind, **{k: (float(v) if isinstance(v, (np.floating,)) else v) for k, v in kw.items()})
        self.events.append(rec)

    def warmup(self, t0):
        """Advance every slot through all bars that closed before t0 (no trading)."""
        for (E, c), sl in self.slots.items():
            jend = np.searchsorted(sl.idx, t0 - DUR[sl.tf], 'left') - 1  # bars that ended strictly before t0
            sl.run_until(jend)
        self.t_prev = t0

    def bar_index(self, sl, start_time):
        """position of the bar starting at start_time; live, that bar may still be in progress
        (not yet in the arrays) -> its future position (data has no gaps on Bybit)."""
        j = sl.idx.searchsorted(start_time)
        if j < len(sl.idx) and sl.idx[j] == start_time:
            return int(j)
        d = DUR[sl.tf]
        return int(len(sl.idx) - 1 + round((start_time - sl.idx[-1]) / d))

    def __getstate__(self):
        d = dict(self.__dict__); d['ex'] = None; d['S'] = None; d['_trade_n'] = Trade._n
        return d

    def __setstate__(self, d):
        self.__dict__.update(d); Trade._n = max(Trade._n, d.get('_trade_n', 0))
        # strategy settings always come from THIS version's CFG (a state saved by an older version must not keep
        # its old leverage/weights); keys only present in the saved state are kept
        self.cfg_loaded = dict(self.cfg)
        self.cfg = dict(self.cfg, **CFG)
        self.__dict__.setdefault('reg', {}); self.__dict__.setdefault('adv', {})

    def reattach(self, ex, S):
        self.ex = ex; self.refresh(S)

    def refresh(self, S):
        self.S = S
        for sl in self.slots.values():
            sl.refresh(S)

    def last_close(self, sl, t):
        """close of last completed bar of slot timeframe at time t"""
        j = np.searchsorted(sl.idx, t - DUR[sl.tf], 'right') - 1
        return sl.c[j]

    def open_trades(self):
        return [q for q in self.trades.values() if q.status == 'open']

    def mark(self, q, t, worst=False):
        """unrealized return per unit notional (uc) and funding accrual (fa), backtest definition"""
        sl = self.slots[(q.E, q.coin)]
        if worst:
            j = np.searchsorted(sl.idx, t, 'right') - 1   # bar containing t
            px = (sl.l if q.s > 0 else sl.h)[j]
        else:
            px = self.last_close(sl, t)
        uc = 0.0; fa = 0.0
        for lv in q.levels:
            if lv['fp'] is None:
                continue
            if not worst and t < lv['ft'] + DUR[sl.tf]:
                continue      # fill bar not completed yet
            uc += lv['w'] * (q.s * (px / lv['fp'] - 1) - q.fe)
        for lv in q.levels:
            if lv['fp'] is not None and t > lv['ft']:
                fa += self.cfg['fund8h'] * lv['w'] * ((t - lv['ft']) / pd.Timedelta('1h')) / 8
        return uc, fa

    def equity(self, t):
        if self.accounting != 'backtest':
            return self.ex.equity()
        return self.cash + sum(q.nt * (lambda u: u[0] - u[1])(self.mark(q, t)) for q in self.open_trades())

    # ------------------------------------------------------------ exchange callbacks
    def on_fill(self, oid, tag, px, qty, bar_start, t):
        """entry level filled. tag=(trade_id,'entry',k). Called by exchange in priority order."""
        tid, role, k = tag
        q = self.trades.get(tid)
        if q is None:
            return 'ignore'
        if q.status == 'pending':
            # final admission check against state at bar start + earlier admissions this bar
            ok, why = self.admissible(q.E, q.coin, t_bar=bar_start, exclude=q.id)
            if not ok:
                self.log(t, 'reject_at_fill', trade=q.id, eng=q.E, coin=q.coin, why=why)
                self.cancel_trade_orders(q); self.trades.pop(q.id, None)
                return 'cancelled'
            if self.gross_room(q.E, [p for p in self.open_trades() if p.id != q.id], self.equity(bar_start)) <= \
                    (0 if q.E == 'E3' else 0.05 * q.nt):
                self.log(t, 'reject_at_fill', trade=q.id, eng=q.E, coin=q.coin, why='gross')
                self.cancel_trade_orders(q); self.trades.pop(q.id, None)
                return 'cancelled'
            q.status = 'open'; q.f_time = bar_start; q.nt0 = q.nt
            sl = self.slots[(q.E, q.coin)]; q.f_idx = self.bar_index(sl, bar_start)
            self.resolve_conflicts(q, bar_start)
            if not math.isnan(q.stop):
                q.stop_oid = self.ex.place_stop(q.coin, -q.s, q.stop, None, tf=q.tf, tag=(q.id, 'stop', 0),
                                                slip=self.cfg['slip_stop'])
        lv = q.levels[k]
        lv['fp'] = px; lv['ft'] = bar_start; lv['oid'] = None
        q.last_fill_idx = self.bar_index(self.slots[(q.E, q.coin)], bar_start)
        if q.tp_oid:   # TP never active on a bar with a new fill (backtest rule)
            self.ex.cancel(q.tp_oid); q.tp_oid = None
        self.log(t, 'fill', trade=q.id, eng=q.E, coin=q.coin, level=k, px=px, bar=str(bar_start))
        return 'ok'

    def on_exit(self, oid, tag, px, bar_start, t, reason):
        tid = tag[0]
        q = self.trades.get(tid)
        if q is None or q.status != 'open':
            return
        self.close_trade(q, px, t, reason)

    # ------------------------------------------------------------ portfolio rules
    def admissible(self, E, c, t_bar, exclude=None):
        if E in self.cfg.get('disabled', ()): return False, 'disabled'
        op = [q for q in self.open_trades() if q.id != exclude]
        if len(op) >= self.cfg['kmax']: return False, 'kmax'
        if any(q.coin == c for q in op): return False, 'coin'
        caps = self.cfg['caps']
        if E in caps and sum(q.E == E for q in op) >= caps[E]: return False, 'cap'
        if E in GD and any(q.E in GD for q in op): return False, 'dipmax'
        if E in G and c in self.lastloss and (t_bar - self.lastloss[c]) < self.cfg['cool_steps'] * STEP:
            return False, 'cooldown'
        if E == 'E3':
            eq = self.equity(t_bar)
            used = sum(q.nt * 6 * q.atrp for q in op if q.E == 'E3')
            if self.cfg['e3tot'] * eq - used <= 0: return False, 'e3tot'
            if self.cfg.get('e3gross') and self.cfg['e3gross'] * eq - sum(q.nt for q in op if q.E == 'E3') <= 0:
                return False, 'e3gross'
        return True, ''

    def gross_room(self, E, op, eq):
        """v2.3 caps on total open notional (research E3GROSS / GROSSL)"""
        if E == 'E3':
            return self.cfg['e3gross'] * eq - sum(q.nt for q in op if q.E == 'E3') if self.cfg.get('e3gross') else np.inf
        return self.cfg['grossl'] * eq - sum(q.nt for q in op if q.E != 'E3') if self.cfg.get('grossl') else np.inf

    def size(self, E, c, t_bar, crash, atrp):
        eq = self.equity(t_bar)
        nt = self.cfg['kd'] * self.cfg['fd'] * self.cfg['W'][E] * eq
        _wm = dict(x.split(':') for x in __import__('os').environ.get('WMUL', '').split(',') if x)
        if E in _wm: nt *= float(_wm[E])
        if crash and E in G:
            nt *= self.cfg['crsc']
        mD = self.vtm.get(t_bar.normalize(), np.nan)
        if np.isnan(mD): mD = 1.0
        nt *= mD
        if self.cfg.get('regw_bear') is not None:
            bear = bool(self.reg.get(t_bar.normalize(), False))
            nt *= (self.cfg['regw_bear'] if bear else self.cfg['regw_bull']).get(E, 1.0)
        if self.cfg.get('liqf'):          # v2.7 liquidity cap (research LIQF; volume known at the start of the day)
            a = self.adv.get(c)
            if a is not None and len(a):
                av = a.asof(t_bar.normalize())
                if av == av and av > 0:
                    nt = min(nt, self.cfg['liqf'] * av)
        if E == 'E3':
            nt = min(nt, self.cfg['e3risk'] * eq / (6.0 * atrp))
            used = sum(q.nt * 6 * q.atrp for q in self.open_trades() if q.E == 'E3')
            nt = min(nt, (self.cfg['e3tot'] * eq - used) / (6 * atrp))
        import os as _o
        if getattr(self, '_pause', None) is not None and t_bar < self._pause: nt = 0.0   # research: PSTOP pause
        if _o.environ.get('BEARX') and E not in SHORTE and bool(self.reg.get(t_bar.normalize(), False)):
            nt *= float(_o.environ['BEARX'])                                     # research: shrink longs in a bear regime
        if _o.environ.get('DDT'):                                               # research: equity-drawdown brake x/y
            _x, _y = (float(v) for v in _o.environ['DDT'].split('/'))
            self._pk = max(getattr(self, '_pk', eq), eq)
            if eq < self._pk * (1 - _x): nt *= _y
        if _o.environ.get('CAP'): nt = min(nt, float(_o.environ['CAP']) * eq)   # research: per-trade size limit
        if _o.environ.get('CAPALT') and c not in ('BTC', 'ETH'): nt = min(nt, float(_o.environ['CAPALT']) * eq)   # research: smaller limit for altcoins
        if _o.environ.get('VR') and c in VOLS:                                    # research: volatility limit
            v = VOLS[c].asof(t_bar.normalize())
            if v == v and v > 0: nt = min(nt, float(_o.environ['VR']) * eq / v)
        if _o.environ.get('PCAP') and E not in SHORTE:        # research: total long exposure per coin (all engines) limit
            pm, pa = (float(x) for x in _o.environ['PCAP'].split('/'))
            ex = EXPO_FN() if EXPO_FN is not None else {}
            have = max(ex.get(c, 0.0), 0.0)
            for q in self.trades.values():
                if q.coin == c and q.E not in SHORTE and q.status in ('pending', 'open'):
                    have += q.nt * (1.0 if q.status == 'pending' or EXPO_FN is None else sum(lv['w'] for lv in q.levels if lv.get('fp') is None))
            nt = min(nt, max(0.0, (pm if c in ('BTC', 'ETH') else pa) * eq - have))
        if _o.environ.get('TCAP') and E not in SHORTE:        # research: total long exposure (all coins, all engines) limit
            ex = EXPO_FN() if EXPO_FN is not None else {}
            cur = sum(v for v in ex.values() if v > 0)
            for q in self.trades.values():
                if q.status in ('pending', 'open') and q.E not in SHORTE:
                    cur += q.nt * (1.0 if q.status == 'pending' or EXPO_FN is None else sum(lv['w'] for lv in q.levels if lv.get('fp') is None))
            nt = min(nt, max(0.0, float(_o.environ['TCAP']) * eq - cur))
        if _o.environ.get('SB') and E not in SHORTE:          # stress budget: loss if a 10-Oct-2025-style crash hit right now
            ex = EXPO_FN() if EXPO_FN is not None else {}
            cur = sum(CRASH(k) * v for k, v in ex.items() if v > 0)
            for q in self.trades.values():
                if q.status in ('pending', 'open') and q.E not in SHORTE:
                    unf = 1.0 if q.status == 'pending' or EXPO_FN is None else sum(lv['w'] for lv in q.levels if lv.get('fp') is None)
                    cur += CRASH(q.coin) * q.nt * unf
            nt = min(nt, max(0.0, (float(_o.environ['SB']) * eq - cur) / CRASH(c)))
        room = self.gross_room(E, self.open_trades(), eq)
        if E == 'E3':
            if room <= 0: nt = 0.0
            else: nt = min(nt, room)
        elif room <= 0.05 * nt:
            nt = 0.0
        else:
            nt = min(nt, room)
        return nt, eq, mD

    def resolve_conflicts(self, q, bar_start):
        """after q became open, cancel/resize pending orders that are no longer admissible"""
        for p in list(self.trades.values()):
            if p.status != 'pending':
                continue
            ok, why = self.admissible(p.E, p.coin, bar_start)
            if not ok:
                self.log(bar_start, 'cancel_conflict', trade=p.id, eng=p.E, coin=p.coin, why=why, because=q.id)
                self.cancel_trade_orders(p); self.trades.pop(p.id, None)
            else:
                nt, _, _ = self.size(p.E, p.coin, bar_start, p.crash, p.atrp)
                if p.E.startswith('RB'):
                    continue
                if nt <= 0:
                    self.log(bar_start, 'cancel_conflict', trade=p.id, eng=p.E, coin=p.coin, why='gross', because=q.id)
                    self.cancel_trade_orders(p); self.trades.pop(p.id, None)
                elif nt < p.nt:
                    p.nt = nt
                    for lv in p.levels:
                        if lv['oid']:
                            self.ex.amend(lv['oid'], qty=nt * lv['w'] / lv['L'], notional=nt * lv['w'])

    def cancel_trade_orders(self, q):
        for lv in q.levels:
            if lv.get('oid'):
                self.ex.cancel(lv['oid']); lv['oid'] = None
        for a in ('stop_oid', 'tp_oid'):
            if getattr(q, a):
                self.ex.cancel(getattr(q, a)); setattr(q, a, None)

    def close_trade(self, q, X, t, reason):
        """realize trade at exit price X (backtest accounting in replay)."""
        fx = self.cfg['fee_maker'] if reason == 'tp' else self.cfg['fee_taker']
        ret = 0.0; fund = 0.0
        for lv in q.levels:
            if lv['fp'] is None: continue
            ret += lv['w'] * q.s * (X / lv['fp'] - 1) - lv['w'] * q.fe - fx * lv['w'] * X / lv['fp']
            fund += self.cfg['fund8h'] * lv['w'] * ((t - lv['ft']) / pd.Timedelta('1h')) / 8
        r = ret - fund
        pnl = q.nt * r
        if self.accounting == 'backtest':
            self.cash += pnl
        q.realized += pnl
        if q.E in G and r < 0:
            self.lastloss[q.coin] = t
        q.status = 'closed'; q.exit = dict(time=t, px=X, reason=reason, ret_f=r)
        self.cancel_trade_orders(q)
        self.trades.pop(q.id, None); self.closed.append(q)
        self.log(t, 'exit', trade=q.id, eng=q.E, coin=q.coin, px=X, reason=reason, ret_f=r, pnl=pnl)

    # ------------------------------------------------------------ main step
    def step(self, t):
        """t = time of a 15-minute boundary; all bars ending at or before t are closed."""
        import os as _o
        if _o.environ.get('PSTOP'):            # research: portfolio stop x/h - equity < peak*(1-x): close everything, pause h hours
            _x, _h = (float(v) for v in _o.environ['PSTOP'].split('/'))
            _eq = self.equity(t)
            self._ppk = max(getattr(self, '_ppk', _eq), _eq)
            if _eq < self._ppk * (1 - _x) and self.open_trades():
                for q in list(self.open_trades()):
                    sl = self.slots[(q.E, q.coin)]
                    px = self.last_close(sl, t) * (1 - 0.0015 * q.s)      # extra 0.15% slippage on panic exit
                    if any(lv['fp'] is not None for lv in q.levels):
                        self.ex.market(q.coin, -q.s, None, tag=(q.id, 'time', 0))
                    self.close_trade(q, px, t, 'pstop')
                self._pause = t + pd.Timedelta(hours=_h)
                self._ppk = self.equity(t)
                self.log(t, 'pstop', eq=_eq)
        # A. time exits for bars that closed at t
        for q in list(self.open_trades()):
            sl = self.slots[(q.E, q.coin)]
            if t - DUR[sl.tf] < sl.idx[0]: continue
            jc = np.searchsorted(sl.idx, t - DUR[sl.tf], 'right') - 1
            if sl.idx[jc] + DUR[sl.tf] != t: continue
            if jc == q.f_idx + q.H - 1:
                self.ex.market(q.coin, -q.s, None, tag=(q.id, 'time', 0))
                self.close_trade(q, sl.c[jc], t, 'time')
        # B. slot updates for bars closing at t
        newsetups = []
        for key, sl in self.slots.items():
            bs = t - DUR[sl.tf]
            j = sl.idx.searchsorted(bs)
            if j >= len(sl.idx) or sl.idx[j] != bs:
                continue
            for kind, info in sl.close_bar(j):
                sk = (key[0], key[1], info['i'])
                if kind == 'setup':
                    newsetups.append(key)
                elif kind in ('expire', 'reject'):
                    tid = self.setup_trade.pop(sk, None)
                    q = self.trades.get(tid) if tid else None
                    if q is not None and q.status == 'pending':
                        self.cancel_trade_orders(q); self.trades.pop(q.id, None)
                    self.log(t, kind, eng=key[0], coin=key[1], sig=str(sl.idx[info['i']]))
                elif kind == 'vfill':
                    tid = self.setup_trade.get(sk)
                    q = self.trades.get(tid) if tid else None
                    if q is None or q.status != 'open':
                        if q is not None:
                            self.cancel_trade_orders(q); self.trades.pop(q.id, None)
                        self.log(t, 'virtual', eng=key[0], coin=key[1], sig=str(sl.idx[info['i']]))
                elif kind == 'vexit':
                    self.setup_trade.pop(sk, None)
        # C. E3 cluster emergency exit
        e3 = [q for q in self.open_trades() if q.E == 'E3']
        if e3 and self.cfg['e3ps'] > 0:
            U3 = sum(q.nt * (lambda u: u[0] - u[1])(self.mark(q, t)) for q in e3)
            if U3 < -self.cfg['e3ps'] * self.equity(t):
                self.nps += 1
                for q in e3:
                    uc, fa = self.mark(q, t)
                    pnl = q.nt * (uc - fa) - q.nt * (1 - uc) * (self.cfg['fee_taker'] + self.cfg['slip_stop'])
                    self.ex.market(q.coin, -q.s, None, tag=(q.id, 'cluster', 0))
                    if self.accounting == 'backtest': self.cash += pnl
                    q.realized += pnl; q.status = 'closed'; q.exit = dict(time=t, reason='cluster')
                    self.cancel_trade_orders(q); self.trades.pop(q.id, None); self.closed.append(q)
                    self.log(t, 'cluster_exit', trade=q.id, coin=q.coin, pnl=pnl)
        # D. E4 trim
        for q in self.open_trades():
            if q.E == 'E4' and not q.trimmed:
                uc, fa = self.mark(q, t)
                if uc >= self.cfg['e4tt']:
                    part = self.cfg['e4tf'] * q.nt
                    pnl = part * (uc - fa) - part * (1 + uc) * self.cfg['fee_taker']
                    self.ex.market(q.coin, -q.s, None, tag=(q.id, 'trim', 0), frac=self.cfg['e4tf'])
                    if self.accounting == 'backtest': self.cash += pnl
                    q.realized += pnl; q.nt -= part; q.trimmed = True
                    self.log(t, 'trim', trade=q.id, coin=q.coin, pnl=pnl)
        # E. day end bookkeeping
        if t.hour == 0 and t.minute == 0:
            self.daily[t - pd.Timedelta('1D')] = self.equity(t)
        # F. new orders for bars starting at t (entries), in backtest priority order
        cands = []
        for key, sl in self.slots.items():
            if sl.mode != 'wait':
                continue
            j = sl.bar_at(t)
            if j is None:
                continue   # not a bar start for this timeframe
            cur = sl.cur
            if sl.market:
                if j != cur['i'] + 1: continue
            else:
                if not (cur['i'] < j <= cur['wend']): continue
            cands.append((EO[key[0]], key[1], key, j))
        cands.sort()
        for _, c, key, j in cands:
            E = key[0]; sl = self.slots[key]; cur = sl.cur
            sk = (E, c, cur['i'])
            tid = self.setup_trade.get(sk)
            q = self.trades.get(tid) if tid else None
            if q is not None and q.status == 'open':
                continue
            gate_ok = True if sl.market else sl.gate_at(j)
            ok, why = self.admissible(E, c, t) if gate_ok else (False, 'gate')
            if not ok:
                if q is not None:
                    self.cancel_trade_orders(q); self.trades.pop(q.id, None); self.setup_trade.pop(sk, None)
                if why not in ('gate',):
                    self.log(t, 'skip', eng=E, coin=c, why=why, sig=str(sl.idx[cur['i']]))
                continue
            crash = False if sl.market else sl.cf_at(j)
            atrp = (cur['a'] / cur['c_sig']) if E == 'E3' else np.nan
            nt, eq, mD = self.size(E, c, t, crash, atrp)
            if nt <= 0:
                if q is not None:                 # no room any more (v2.3 caps): withdraw the resting orders
                    self.log(t, 'skip', eng=E, coin=c, why='gross', sig=str(sl.idx[cur['i']]))
                    self.cancel_trade_orders(q); self.trades.pop(q.id, None); self.setup_trade.pop(sk, None)
                continue
            if sl.market:
                q = Trade(E=E, coin=c, tf=sl.tf, s=1, eo=EO[E], nt=nt, fe=self.cfg['fee_taker'], a=cur['a'],
                          stop=cur['stop'], H=sl.H, crash=False, atrp=np.nan, sig_i=cur['i'], mD=mD)
                q.levels = [dict(k=0, L=None, w=1.0, oid=None, fp=None, ft=None)]
                self.trades[q.id] = q; self.setup_trade[sk] = q.id
                px = sl.o[j] if j < len(sl.o) else sl.c[j - 1]   # live: open of the new bar ~ last close
                self.ex.market(c, +1, nt / px, tag=(q.id, 'entry', 0), at='open', bar_start=t, tf=sl.tf, px=px)
                continue
            if q is None:
                q = Trade(E=E, coin=c, tf=sl.tf, s=sl.s, eo=EO[E], nt=nt, fe=self.cfg['fee_maker'], a=cur['a'],
                          stop=cur['stop'], H=sl.H, crash=crash, atrp=atrp, sig_i=cur['i'], mD=mD,
                          Lk=cur['Lk'], fr=sl.fr, tpm=sl.tpm)
                q.levels = [dict(k=k, L=float(cur['Lk'][k]), w=float(sl.fr[k]), oid=None, fp=None, ft=None)
                            for k in range(len(sl.fr))]
                self.trades[q.id] = q; self.setup_trade[sk] = q.id
                for lv in q.levels:
                    lv['oid'] = self.ex.place_limit(c, q.s, lv['L'], nt * lv['w'] / lv['L'], tf=sl.tf,
                                                    tag=(q.id, 'entry', lv['k']), pen=self.cfg['pen'],
                                                    prio=(t, EO[E], c), notional=nt * lv['w'])
                self.log(t, 'orders', trade=q.id, eng=E, coin=c, nt=nt, eq=eq, mD=mD, crash=crash,
                         levels=[round(x, 8) for x in cur['Lk']], sig=str(sl.idx[cur['i']]))
            else:
                # re-size pending (multi-bar window) at new bar start
                q.nt = nt; q.crash = crash; q.mD = mD
                for lv in q.levels:
                    if lv['oid']:
                        self.ex.amend(lv['oid'], qty=nt * lv['w'] / lv['L'], prio=(t, EO[E], c), notional=nt * lv['w'])
                    else:
                        lv['oid'] = self.ex.place_limit(c, q.s, lv['L'], nt * lv['w'] / lv['L'], tf=sl.tf,
                                                        tag=(q.id, 'entry', lv['k']), pen=self.cfg['pen'],
                                                        prio=(t, EO[E], c), notional=nt * lv['w'])
        # pending trades whose bar does not allow orders this bar (gate false) were cancelled above.
        # G. take-profit orders for open trades at their bar starts
        for q in self.open_trades():
            sl = self.slots[(q.E, q.coin)]
            j = sl.bar_at(t)
            if j is None:
                continue
            if q.E.startswith('RB'):
                if q.tp_oid is None and j > q.f_idx:
                    q.tp_oid = self.ex.place_limit(q.coin, -1, q.tp, None, tf=q.tf, tag=(q.id, 'tp', 0), pen=self.cfg.get('rb_tp_pen', 0.0),
                                                   reduce=True, prio=(t, 0, q.coin))
                continue
            if q.tpm <= 0 or j <= q.last_fill_idx:
                continue
            m = [lv for lv in q.levels if lv['fp'] is not None]
            avg = sum(lv['w'] for lv in m) / sum(lv['w'] / lv['fp'] for lv in m)
            tp = avg + q.s * q.tpm * q.a
            tg = sl.tgt[j - 1]
            if not np.isnan(tg):
                tp = max(tp, tg) if q.s > 0 else min(tp, tg)
            if q.tp_oid is None:
                q.tp_oid = self.ex.place_limit(q.coin, -q.s, tp, None, tf=q.tf, tag=(q.id, 'tp', 0), pen=self.cfg['pen'],
                                               reduce=True, prio=(t, 0, q.coin))
            else:
                self.ex.amend(q.tp_oid, price=tp)
        self.t_prev = t

    # market entry confirmation from exchange (RB engines)
    def on_market_fill(self, tag, px, bar_start, t):
        tid, role, k = tag
        q = self.trades.get(tid)
        if q is None or role != 'entry':
            return
        ok, why = self.admissible(q.E, q.coin, t_bar=bar_start, exclude=q.id)
        if ok and self.gross_room(q.E, [p for p in self.open_trades() if p.id != q.id], self.equity(bar_start)) <= \
                0.05 * q.nt:
            ok, why = False, 'gross'
        if not ok:
            self.log(t, 'reject_at_fill', trade=q.id, eng=q.E, coin=q.coin, why=why)
            self.trades.pop(q.id, None)
            return 'cancelled'
        q.status = 'open'; q.f_time = bar_start; q.nt0 = q.nt
        self.resolve_conflicts(q, bar_start)
        sl = self.slots[(q.E, q.coin)]; q.f_idx = self.bar_index(sl, bar_start); q.last_fill_idx = q.f_idx
        q.levels[0]['fp'] = px; q.levels[0]['ft'] = bar_start
        q.tp = px + q.a
        q.stop_oid = self.ex.place_stop(q.coin, -1, q.stop, None, tf=q.tf, tag=(q.id, 'stop', 0), slip=self.cfg['slip_stop'])
        self.log(t, 'fill', trade=q.id, eng=q.E, coin=q.coin, level=0, px=px, bar=str(bar_start))
