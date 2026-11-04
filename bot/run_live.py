"""Live / paper runner. Usage (on the laptop, inside the bot folder):
    python run_live.py            (reads config.json)
Stop it with Ctrl+C; restart any time - state is saved in state/ and it resumes.
"""
import os, sys, json, time, pickle, traceback
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib_data import ALL13, ADDON_EXTRA
from signals import compute, vt_series, core_position_bear, regime_series, adv_series
from addon import AddOnBook
from dualbook import DualBook
from bot import Bot, CFG
from feed import Feed
from bybit_ex import BybitREST, BybitExchange, BybitError
from notify import Notifier

STEP = pd.Timedelta('15min')


class Logger:
    def __init__(self, d, echo=True):
        self.d = d; os.makedirs(d, exist_ok=True); self.echo = echo
        self.files = {}
        self.notifier = Notifier()          # replaced in main() when config.json has Telegram keys

    def _w(self, name, rec):
        f = self.files.get(name)
        if f is None:
            f = open(os.path.join(self.d, name + '.jsonl'), 'a', encoding='utf-8'); self.files[name] = f
        f.write(json.dumps(rec, default=str) + '\n'); f.flush()

    def p(self, msg):
        line = f"{pd.Timestamp.now('UTC').tz_localize(None).strftime('%Y-%m-%d %H:%M:%S')}Z  {msg}"
        if self.echo:
            print(line, flush=True)
        self._w('console', dict(line=line))
        try:
            if Notifier.wanted(msg):
                self.notifier.send(msg)
        except Exception:
            pass

    def ev(self, kind, **kw):
        self._w('events', dict(wall=str(pd.Timestamp.now('UTC').tz_localize(None)), kind=kind, **kw))
        if kind not in ('setup',):
            self.p(f'{kind} ' + ' '.join(f'{k}={v}' for k, v in kw.items()))

    def order(self, what, rec):
        r = {k: v for k, v in rec.items() if k != 'body'}
        self._w('orders', dict(wall=str(pd.Timestamp.now('UTC').tz_localize(None)), what=what, **r))
        if what in ('reject', 'too_small'):
            self.p(f"order {what}: {rec['coin']} {rec['tag']} qty={rec['qty']} px={rec.get('price')} {rec.get('err', '')}")

    def fill(self, e, rec):
        self._w('fills', dict(wall=str(pd.Timestamp.now('UTC').tz_localize(None)), tag=str(rec['tag']) if rec else None, **e))
        self.p(f"FILL {e['symbol']} {e['side']} {e['execQty']} @ {e['execPrice']}  {rec['tag'] if rec else '(not ours)'}")

    def bot_events(self, events):
        for e in events:
            self._w('decisions', e)
            if e['kind'] in ('orders', 'fill', 'exit', 'cluster_exit', 'trim', 'reject_at_fill', 'skip', 'cancel_conflict'):
                self.p('BOT ' + ' '.join(f'{k}={v}' for k, v in e.items() if k not in ('levels',)))
            if e['kind'] in ('fill', 'exit', 'cluster_exit', 'trim'):
                try:
                    m = 'trade ' + e['kind'] + ': ' + ' '.join(f'{k}={v}' for k, v in e.items() if k not in ('levels', 'kind'))
                    self.notifier.send(m, key=m)
                except Exception:
                    pass


def keep_awake():
    """Windows: ask the OS not to sleep while the bot runs (screen may still turn off)."""
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        except Exception:
            pass



def settings_line(bot):
    from lib_engines import RB1S, ENG, PEN, E3R, RB1H, QTHR, E5Z
    from addon import P as AP
    import inspect
    from signals import core_position_bear
    sw = inspect.signature(core_position_bear).parameters['sw'].default
    c = bot.cfg
    return (f"settings: leverage kd={c['kd']} E3 weight={c['W']['E3']} e3tot={c['e3tot']} disabled={c.get('disabled')} "
            f"caps long={c.get('grossl')} E3={c.get('e3gross')} RB1 stop={RB1S} E2 hold={ENG['E2']['H']}h fill-model pen={PEN} core short={sw} "
            f"E1 weight={c['W']['E1']} core weight={c.get('wcore')} liquidity cap={c.get('liqf')}x daily volume "
            f"E1 tp={ENG['E1']['tp']} E3 tp={ENG['E3']['tp']} E3 drop={E3R} E4 bear size={c['regw_bear'].get('E4')} quality={QTHR['E1']} RB1 hold={RB1H} E5 depth={E5Z} capitulation=BTC,ETH+10 alts euphoria=short BTC,ETH add-on N={AP['N']} cap={AP['hi']} E5 tp={ENG['E5']['tp']} [v3.4] safety: halt at -45% in 24h or 8x exposure")

class Live:
    def __init__(self, rest, cfg, L, clock=None):
        self.r = rest; self.cfg = cfg; self.L = L
        self.clock = clock or (lambda: pd.Timestamp(self.r.now_ms(), unit='ms'))
        self.sdir = cfg.get('state_dir', os.path.join(HERE, 'state')); os.makedirs(self.sdir, exist_ok=True)
        self.spath = os.path.join(self.sdir, 'state.pkl')
        self.feed = Feed(rest.klines, cfg.get('data_dir', os.path.join(HERE, 'data')),
                         start=cfg.get('data_start', '2025-04-01'), log=L.p)
        self.bot = None; self.last_t = None
        if os.path.exists(self.spath):
            st = pickle.load(open(self.spath, 'rb'))
            self.bot = st['bot']; self.ex = st['ex']; self.last_t = st['last_t']
            self.ex.attach(self.bot, rest, L)
            L.p(f'resumed state: last step {self.last_t}, open trades {len(self.bot.open_trades())}, '
                f'pending {sum(q.status == "pending" for q in self.bot.trades.values())}')
            L.p(settings_line(self.bot))
            if getattr(self.bot, 'cfg_loaded', None) and self.bot.cfg_loaded.get('kd') != self.bot.cfg['kd']:
                L.p(f"note: saved state came from an older version (kd {self.bot.cfg_loaded.get('kd')}); now using this version's settings")
        else:
            self.ex = BybitExchange(rest, ALL13 + ADDON_EXTRA, L, max_lev=cfg.get('max_leverage', 25))
        if self.ex.addon is not None and not isinstance(self.ex.addon, DualBook):
            self.ex.addon = DualBook.upgrade(self.ex.addon)     # v3.0: keep open trend positions, add the capitulation engine
            L.p('note: add-on book upgraded to v3.0 (trend add-on + capitulation engine); open add-on positions kept')
        if self.ex.addon is None and cfg.get('addon', True):
            self.ex.addon = DualBook()
        for c in ADDON_EXTRA:
            if c not in self.ex.coins: self.ex.coins.append(c)
        self.ex.setup()
        from lib_engines import QT
        yr = str(self.clock().year)
        if yr not in QT.get('E1', {}) or yr not in QT.get('E2', {}):
            L.p(f'WARNING: no quality table for {yr}: dip engines E1/E2/E5 will not open trades. Ask Claude for the yearly refit.')
        self.t_poll = 0; self.t_check = 0
        self.core_on = cfg.get('core', True)

    def reload(self):
        """go back to the last consistent saved state (a step that failed half-way is redone cleanly)"""
        if os.path.exists(self.spath):
            st = pickle.load(open(self.spath, 'rb'))
            self.bot = st['bot']; self.ex = st['ex']; self.last_t = st['last_t']
            self.ex.attach(self.bot, self.r, self.L)
            if self.bot is not None and self.bot.S is None and hasattr(self, '_S'):
                self.bot.reattach(self.ex, self._S)
        self.startup_checks()

    def save(self):
        tmp = self.spath + '.tmp'
        with open(tmp, 'wb') as f:
            pickle.dump(dict(bot=self.bot, ex=self.ex, last_t=self.last_t), f)
        os.replace(tmp, self.spath)

    # -------------------------------------------------------------- startup safety
    def startup_checks(self):
        oo = self.r.open_orders()
        unknown = [o for o in oo if o.get('orderLinkId') not in self.ex.orders]
        for o in unknown:
            self.L.ev('cancel_unknown_order', symbol=o['symbol'], link=o.get('orderLinkId'), side=o['side'])
            try:
                self.r.cancel(o['symbol'], o.get('orderLinkId')) if o.get('orderLinkId') else None
            except BybitError as e:
                self.L.ev('cancel_failed', err=str(e))
        if self.bot is None:
            pos = [p for p in self.r.positions() if float(p['size']) != 0]
            core = [p for p in pos if p['symbol'][:-4] in self.ex.CORE_COINS]
            eq0 = None
            for p in core:                       # fresh start: keep BTC/ETH core positions (no needless round trip)
                c = p['symbol'][:-4]; v = float(p['size']) * (1 if p['side'] == 'Buy' else -1)
                px = float(p.get('markPrice') or p.get('avgPrice') or 0)
                if px > 0:
                    if eq0 is None: eq0 = self.r.equity()
                    cap = CFG['wcore'] * 0.5 * eq0 / px      # the largest core position possible
                    if c in self.ex.inst:
                        cap = self.ex._qty(c, cap)          # v3.3 fix: a whole number of exchange steps (else endless mismatch)
                    if abs(v) > cap:
                        self.L.p(f'fresh start: {c} position {v} is larger than any core position; adopting only {cap:.4f}'
                                 f' (reconcile closes the rest)')
                        v = cap if v > 0 else -cap
                self.ex.core_qty[c] = v
                self.L.p(f'fresh start: adopted existing {c} position {v} as core')
            pos = [p for p in pos if p not in core]
            if pos:
                self.L.p('WARNING: account has open positions but the bot has no saved state: '
                         + ', '.join(f"{p['symbol']} {p['side']} {p['size']}" for p in pos)
                         + '  -> they will be closed by reconciliation in ~3 minutes. Close them yourself if you prefer.')

    # -------------------------------------------------------------- 15-minute step
    def boundary(self, now, force=False):
        t = now.floor(STEP)
        ok = self.feed.update(t)
        if not ok and not force:
            return False                       # data not complete yet; retried by the loop
        if not ok:
            self.L.p('ALERT: market data for some coin is still missing after 10 minutes; '
                     'stepping with the data available so open trades keep being managed')
        D = self.feed.build(t)
        S = compute(D); self._S = S
        if self.bot is None:
            self.bot = Bot(self.ex, S, accounting='live')
            self.L.p(settings_line(self.bot))
            self.ex.attach(self.bot)
            self.bot.warmup(t)
            steps = [t]
            self.L.p(f'fresh start: warmed up all engines to {t}')
        else:
            self.bot.reattach(self.ex, S)
            steps = list(pd.date_range(self.last_t + STEP, t, freq=STEP)) if self.last_t is not None else [t]
            if len(steps) > 1:
                self.L.p(f'catching up {len(steps) - 1} missed 15-minute steps (no new entries for missed bars)')
        self.bot.vtm = vt_series(D).to_dict()
        self.bot.reg = regime_series(D).to_dict()
        self.bot.adv = adv_series(D)
        self.ex.now = now
        self.ex.poll(now)
        for tt in steps:
            self.ex.catchup = tt < t
            self.ex.step_t = tt
            self.bot.step(tt)
            if self.ex.addon is not None:
                self.ex.addon.step(tt, D, self.ex, self.L)        # v2.3 trend add-on (4h boundaries)
        self.ex.catchup = False; self.ex.step_t = None
        self.ex.sync(); self.ex.sync_addon()
        if self.core_on:
            self.ex.step_t = t                     # per-step order ids: a redone step never doubles a core order
            try:
                self.core(D, t)
            finally:
                self.ex.step_t = None
        self.L.bot_events(self.bot.events); self.bot.events = []
        eq = self.ex.equity()
        self.daily_loss_guard(t, eq)
        self.emergency_check(t, eq)
        op = self.bot.open_trades()
        self.L._w('equity', dict(t=str(t), equity=eq, open=[(q.id, q.E, q.coin) for q in op],
                                 pending=[(q.id, q.E, q.coin) for q in self.bot.trades.values() if q.status == 'pending'],
                                 core=self.ex.core_qty,
                                 addon=[(tid, c) for tid, c in (self.ex.addon.tids() if self.ex.addon else [])]))
        if t.hour == 0 and t.minute == 0:
            self.daily_summary(t, eq)
        self.last_t = t
        self.save()
        with open(os.path.join(self.L.d, 'status.txt'), 'w') as f:
            f.write(f'last step {t} UTC\nequity {eq:.2f} USDT\nopen trades: '
                    + (', '.join(f'{q.E} {q.coin}' for q in op) or 'none')
                    + f'\ncore: {self.ex.core_qty}\ntrend add-on: '
                    + (', '.join(c for _, c in self.ex.addon.tids()) if self.ex.addon else 'off') + '\n')
        ad = self.ex.addon.tids() if self.ex.addon else []
        self.L.p(f'step {t}  equity {eq:,.2f}  open {len(op)}  ' + ' '.join(f'{q.E}:{q.coin}' for q in op)
                 + (f'  | add-on {len(ad)}: ' + ' '.join(c for _, c in ad) if ad else ''))
        return True

    def emergency_check(self, t, eq, xmax=8.0, crash=0.45):
        """v3.4 catastrophe switches (never triggered in the 2022-2026 test of this code: worst 24h drop -37%,
        largest total exposure 6.35x equity). Either one closes EVERYTHING and halts the bot until you restart it by hand."""
        h = [(a, b) for a, b in getattr(self, 'eq_hist', []) if a > t - pd.Timedelta('24h')] + [(t, eq)]
        self.eq_hist = h
        hi = max(b for _, b in h); why = None
        if eq > 0 and eq < hi * (1 - crash):
            why = f'equity fell {1 - eq / hi:.0%} within 24 hours ({hi:,.2f} -> {eq:,.2f}); limit {crash:.0%}'
        else:
            gross = sum(abs(float(p.get('size') or 0)) * float(p.get('markPrice') or p.get('avgPrice') or 0)
                        for p in self.r.positions())
            if eq > 0 and gross > xmax * eq:
                self.xcount = getattr(self, 'xcount', 0) + 1
                if self.xcount >= 2:
                    why = f'total open positions {gross / eq:.1f}x equity (limit {xmax:.0f}x) on two checks in a row'
            else:
                self.xcount = 0
        if why:
            self.emergency_stop(why)

    def emergency_stop(self, why):
        """cancel every order, close every position at market, park the saved state, write HALT.txt, stop"""
        self.L.p(f'ALERT: EMERGENCY STOP - {why}. Cancelling all orders and closing every position.')
        self.L.ev('emergency_stop', why=why)
        try:
            for o in self.r.open_orders():
                try: self.r.cancel(o['symbol'], o.get('orderLinkId'))
                except Exception as e: self.L.p(f'emergency: cancel failed {o.get("symbol")}: {e!r}')
        except Exception as e:
            self.L.p(f'emergency: could not list orders: {e!r}')
        for attempt in range(3):
            left = []
            try:
                for p in self.r.positions():
                    sz = abs(float(p.get('size') or 0))
                    if sz == 0: continue
                    side = 'Sell' if p['side'] == 'Buy' else 'Buy'
                    try:
                        self.r.create(symbol=p['symbol'], side=side, orderType='Market', qty=p['size'], reduceOnly=True,
                                      orderLinkId=f"halt-{p['symbol'][:10]}-{int(time.time() * 1000) % 10**9}")
                    except Exception as e:
                        left.append(p['symbol']); self.L.p(f'emergency: close failed {p["symbol"]}: {e!r}')
            except Exception as e:
                left.append('?'); self.L.p(f'emergency: could not list positions: {e!r}')
            if not left: break
            time.sleep(3)
        try:
            if os.path.exists(self.spath): os.replace(self.spath, self.spath + '.halted')
        except Exception: pass
        with open(os.path.join(HERE, 'HALT.txt'), 'w') as f:
            f.write(f'{pd.Timestamp.utcnow()} EMERGENCY STOP: {why}\n'
                    'All orders were cancelled and all positions closed. The bot will not trade while this file exists.\n'
                    'Check your Bybit account, then delete HALT.txt and start the bot again (it starts fresh).\n')
        self.L.p('HALTED. Positions closed' + (f' (could NOT close: {left} - close them in the Bybit app!)' if left else '')
                 + '. Delete HALT.txt to allow a restart.')
        raise EmergencyStop(why)

    def daily_loss_guard(self, t, eq, limit=0.25):
        """malfunction guard: if equity falls 25% within one UTC day, stop opening new trades for the rest
        of that day (open trades stay managed normally). v3.0 backtest 2020-2026 (15-min marks): triggers on 3 days
        (2020-11-26, 2020-12-23, 2026-06-05), blocks 1 trade (a loser) and no ladder fill; net effect ~0."""
        day = t.normalize()
        if getattr(self, 'dg_day', None) != day:
            self.dg_day = day; self.dg_start = eq
        if eq < self.dg_start * (1 - limit) and self.ex.block_entries != day:
            self.ex.block_entries = day
            n = self.ex.cancel_all_entries()
            self.L.p(f'ALERT: equity fell {1 - eq / self.dg_start:.0%} today ({self.dg_start:,.2f} -> {eq:,.2f}). '
                     f'No new trades until 00:00 UTC; cancelled {n} entry orders. Open trades stay protected.')
            self.L.ev('daily_loss_guard', start=self.dg_start, equity=eq)

    def shutdown(self, why):
        """leave nothing behind that could open a position while the bot is off"""
        try:
            if self.bot is not None:
                n = self.ex.cancel_all_entries()
                self.L.p(f'{why}: cancelled {n} resting entry orders; open trades keep their exchange stops')
                self.save()
        except Exception as e:
            self.L.p(f'shutdown cleanup problem: {e!r}')

    def core(self, D, t):
        """core: BTC and ETH trend positions (long, or full-size short below all SMAs since v2.6), half weight each"""
        if not (t.minute == 0 and t.hour % 4 == 0):
            return
        if self.bot.daily:
            estart = self.bot.daily[max(self.bot.daily)]
        else:
            estart = self.ex.equity()
        for coin, b4 in (('BTC', D['btc4']), ('ETH', D['eth4'])):
            p = core_position_bear(b4)
            pv = float(p.iloc[-1]) if len(p) and not np.isnan(p.iloc[-1]) else 0.0
            px = float(D['m15'][coin].close.iloc[-1])
            target = estart * CFG['wcore'] * 0.5 * pv / px     # v2.7: core weight 0.45 of equity (was 0.65*0.5=0.325)
            self.ex.core_rebalance(target, px, why=f'p={pv:.3f}', coin=coin)

    def daily_summary(self, t, eq):
        d = t - pd.Timedelta('1D')
        closed = [q for q in self.bot.closed if q.exit and d <= pd.Timestamp(q.exit['time']) < t]
        self.L._w('daily', dict(day=str(d.date()), equity=eq, trades_closed=len(closed),
                                trades=[(q.E, q.coin, q.exit.get('reason'), round(q.realized, 2)) for q in closed]))
        try:
            op = self.bot.open_trades()
            self.L.notifier.send(f'daily {d.date()}: equity {eq:,.2f} USDT, closed {len(closed)} trades, open: '
                                 + (', '.join(f'{q.E} {q.coin}' for q in op) or 'none') + ' (bot alive)', key=f'daily{d.date()}')
        except Exception:
            pass

    # -------------------------------------------------------------- between steps
    def idle(self, now, force=False):
        ts = now.value / 1e9
        if force or ts - self.t_poll >= 5:
            self.t_poll = ts
            if self.bot is not None:
                self.ex.poll(now); self.ex.sync(); self.ex.sync_addon()
                if self.bot.events:
                    self.L.bot_events(self.bot.events); self.bot.events = []
        if ts - getattr(self, 't_sync', 0) >= 1800 and hasattr(self.r, 'sync_time'):
            self.t_sync = ts
            try: self.r.sync_time()
            except Exception: pass
        if self.bot is not None and (force or ts - self.t_check >= 60):
            self.t_check = ts
            self.ex.check_orders(); self.ex.reconcile()
            self.save()

    def prime(self):
        """after loading saved state: load market data so the engines have their arrays again"""
        if self.bot is not None and self.bot.S is None:
            t = self.clock().floor(STEP)
            for _ in range(20):
                if self.feed.update(t): break
                time.sleep(3)
            S = compute(self.feed.build(t)); self._S = S
            self.bot.reattach(self.ex, S)

    def run_forever(self):
        for k in range(30):
            try:
                self.prime(); self.startup_checks(); break
            except KeyboardInterrupt:
                return
            except Exception as e:
                self.L.p(f'startup problem ({e!r}); retrying in 20 s')
                try: self.r.sync_time()
                except Exception: pass
                time.sleep(20)
        while True:
            try:
                keep_awake()
                now = self.clock()
                t = now.floor(STEP)
                if (self.last_t is None or t > self.last_t) and now >= t + pd.Timedelta('3s'):
                    late = self.clock() - t > pd.Timedelta('10min')
                    if not self.boundary(now, force=late):
                        self.idle(now)                 # keep managing fills/stops while waiting for data
                        time.sleep(3)
                        continue
                else:
                    self.idle(now)
                    time.sleep(1)
            except KeyboardInterrupt:
                self.shutdown('stopped by user')
                return
            except EmergencyStop:
                return
            except Exception as e:
                if 'retCode=10002' in str(e):
                    try: self.r.sync_time()
                    except Exception: pass
                self.L.p('ERROR ' + repr(e) + '  (details in logs/errors.jsonl; recovering from last saved state)')
                self.L._w('errors', dict(err=traceback.format_exc()))
                time.sleep(10)
                try:
                    self.reload()
                except Exception as e2:
                    self.L.p('reload failed: ' + repr(e2))
                try:
                    self.r.sync_time()
                except Exception:
                    pass


def on_console_close(live):
    """Windows: closing the window also cancels resting entry orders (Windows allows ~5 s)"""
    if os.name != 'nt':
        return
    try:
        import ctypes
        H = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        def handler(ev):
            if ev in (2, 5, 6):                # close window, logoff, shutdown (Ctrl+C is handled in the loop)
                live.shutdown('window closed')
            return 0
        live._ch = H(handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(live._ch, True)
    except Exception:
        pass


class EmergencyStop(Exception):
    pass


def main():
    hp = os.path.join(HERE, 'HALT.txt')
    if os.path.exists(hp):
        print(open(hp).read()); print('Bot is HALTED - not trading. Close this window when you have read the above.')
        while os.path.exists(hp):
            time.sleep(60)
        print('HALT.txt removed - starting.')
    cfgp = os.path.join(HERE, 'config.json')
    cfg = json.load(open(cfgp))
    L = Logger(cfg.get('log_dir', os.path.join(HERE, 'logs')))
    L.notifier = Notifier(cfg.get('telegram_token'), cfg.get('telegram_chat'))
    if not cfg.get('demo', True):
        L.p('REAL MONEY MODE (demo=false)')
    rest = BybitREST(cfg['api_key'], cfg['api_secret'], demo=cfg.get('demo', True), log=L.p)
    rest.sync_time()
    eq0 = rest.equity()
    L.p(f"connected; clock offset {rest.offset_ms} ms; equity {eq0:,.2f} USDT")
    L.notifier.send(f"bot started ({'DEMO' if cfg.get('demo', True) else 'REAL MONEY'}); equity {eq0:,.2f} USDT", key='start')
    live = Live(rest, cfg, L)
    on_console_close(live)
    live.run_forever()


if __name__ == '__main__':
    main()
