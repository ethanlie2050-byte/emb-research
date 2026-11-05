"""Bybit v5 connector (USDT linear perpetuals, one-way mode, unified account).
Private calls go to the Demo Trading domain (api-demo.bybit.com) unless demo=False.
Market data (klines) always comes from mainnet api.bybit.com (demo uses mainnet prices).

BybitREST     : thin signed REST client (the only part that touches the network)
BybitExchange : implements the order interface the Bot uses + fill polling / order-qty sync /
                position reconciliation / BTC core sleeve orders.
"""
import time, hmac, hashlib, json, math, random, string, urllib.parse
from decimal import Decimal, ROUND_DOWN, ROUND_UP, ROUND_HALF_UP
import pandas as pd

MAIN = 'https://api.bybit.com'
DEMO = 'https://api-demo.bybit.com'


class BybitError(Exception):
    def __init__(self, code, msg, path=''):
        super().__init__(f'{path} retCode={code} {msg}'); self.code = code; self.msg = msg


class BybitREST:
    def __init__(self, key, secret, demo=True, recv_window=20000, timeout=10, log=print):
        import requests
        self.s = requests.Session(); self.key = key; self.secret = secret
        self.base = DEMO if demo else MAIN; self.recv = str(recv_window); self.timeout = timeout
        self.offset_ms = 0; self.log = log

    # ---- low level
    def _get_json(self, url, headers=None, tries=4):
        for k in range(tries):
            try:
                h = headers() if callable(headers) else headers     # fresh timestamp on every attempt
                r = self.s.get(url, headers=h, timeout=self.timeout)
                return r.json()
            except Exception as e:
                self.log(f'net error GET {url[:80]}: {e}'); time.sleep(1 + 2 * k)
        raise BybitError(-1, 'network', url)

    def sync_time(self):
        # several samples, keep the one with the fastest round trip (a slow reply skews the offset)
        best = None
        for _ in range(5):
            t0 = time.time()
            j = self._get_json(self.base + '/v5/market/time')     # the server that checks our signatures
            srv = int(j['time']); t1 = time.time()
            if best is None or t1 - t0 < best[0]:
                best = (t1 - t0, srv - int((t0 + t1) / 2 * 1000))
            time.sleep(0.2)
        self.offset_ms = best[1]
        return self.offset_ms

    def now_ms(self):
        return int(time.time() * 1000) + self.offset_ms

    def _headers(self, payload):
        # Bybit rejects timestamps more than 1 s AHEAD of its clock but accepts up to recv_window behind,
        # so sign with a time slightly in the past (robust to laptop clock jitter)
        ts = str(self.now_ms() - 5000)       # recv_window 20 s covers this
        sig = hmac.new(self.secret.encode(), (ts + self.key + self.recv + payload).encode(), hashlib.sha256).hexdigest()
        return {'X-BAPI-API-KEY': self.key, 'X-BAPI-SIGN': sig, 'X-BAPI-SIGN-TYPE': '2',
                'X-BAPI-TIMESTAMP': ts, 'X-BAPI-RECV-WINDOW': self.recv, 'Content-Type': 'application/json'}

    def get(self, path, params, private=True):
        q = urllib.parse.urlencode(params)
        base = self.base if private else MAIN
        for k in range(8):
            j = self._get_json(f'{base}{path}?{q}', (lambda: self._headers(q)) if private else None)
            if j.get('retCode') in (10006, 10016, 10000):   # rate limit / temporary server error: wait, retry
                time.sleep(2 + 3 * k); continue
            if j.get('retCode') == 10002 and private and k < 3:   # timestamp rejected: re-sync clock, retry
                self.log('clock out of sync with Bybit - re-syncing'); self.sync_time(); continue
            break
        if j.get('retCode') != 0:
            raise BybitError(j.get('retCode'), j.get('retMsg'), path)
        return j['result']

    def post(self, path, body, tries=3):
        """POST is retried only on network errors. Order creation uses orderLinkId, so a duplicate
        after a lost response is rejected by Bybit instead of creating a second order."""
        import requests
        data = json.dumps(body, separators=(',', ':'))
        last = None
        for k in range(tries):
            try:
                r = self.s.post(self.base + path, data=data, headers=self._headers(data), timeout=self.timeout)
                j = r.json()
                if j.get('retCode') == 10002 and k < tries - 1:   # timestamp rejected (request not executed): re-sync, retry
                    self.log('clock out of sync with Bybit - re-syncing'); self.sync_time(); continue
                if j.get('retCode') != 0:
                    raise BybitError(j.get('retCode'), j.get('retMsg'), path)
                return j['result']
            except BybitError:
                raise
            except Exception as e:
                last = e; self.log(f'net error POST {path}: {e}'); time.sleep(1 + 2 * k)
        raise BybitError(-1, f'network {last}', path)

    # ---- endpoints
    def klines(self, symbol, interval_min, start_ms, end_ms):
        out = []
        cur_end = end_ms
        while True:
            time.sleep(0.25)                        # stay far below the public rate limit
            r = self.get('/v5/market/kline', dict(category='linear', symbol=symbol, interval=str(interval_min),
                                                   start=start_ms, end=cur_end, limit=1000), private=False)
            L = r.get('list', [])
            out += L
            if len(L) < 1000:
                break
            oldest = min(int(x[0]) for x in L)
            if oldest <= start_ms:
                break
            cur_end = oldest - 1
        return [[int(x[0])] + [float(v) for v in x[1:6]] for x in out]

    def instrument(self, symbol):
        return self.get('/v5/market/instruments-info', dict(category='linear', symbol=symbol), private=False)['list'][0]

    def set_leverage(self, symbol, lev):
        try:
            self.post('/v5/position/set-leverage', dict(category='linear', symbol=symbol,
                                                        buyLeverage=str(lev), sellLeverage=str(lev)))
        except BybitError as e:
            if e.code != 110043:   # leverage not modified
                raise

    def create(self, **body):
        return self.post('/v5/order/create', dict(category='linear', **body))

    def amend(self, **body):
        return self.post('/v5/order/amend', dict(category='linear', **body))

    def cancel(self, symbol, link):
        return self.post('/v5/order/cancel', dict(category='linear', symbol=symbol, orderLinkId=link))

    def open_orders(self):
        out = []; cursor = ''
        while True:
            p = dict(category='linear', settleCoin='USDT', limit=50)
            if cursor: p['cursor'] = cursor
            r = self.get('/v5/order/realtime', p)
            out += r.get('list', []); cursor = r.get('nextPageCursor', '')
            if not cursor or not r.get('list'): break
        return out

    def order_info(self, symbol, link):
        L = self.get('/v5/order/realtime', dict(category='linear', symbol=symbol, orderLinkId=link)).get('list', [])
        if not L:
            try:
                L = self.get('/v5/order/history', dict(category='linear', symbol=symbol, orderLinkId=link)).get('list', [])
            except BybitError:
                L = []
        return L[0] if L else None

    def order_status(self, symbol, link):
        """orderStatus of one order by orderLinkId (open list first, then history); None if unknown"""
        for path in ('/v5/order/realtime', '/v5/order/history'):
            try:
                L = self.get(path, dict(category='linear', symbol=symbol, orderLinkId=link)).get('list', [])
            except BybitError:
                L = []
            if L:
                return L[0].get('orderStatus')
        return None

    def executions(self, start_ms):
        out = []; cursor = ''
        while True:
            p = dict(category='linear', startTime=start_ms, limit=100)
            if cursor: p['cursor'] = cursor
            r = self.get('/v5/execution/list', p)
            out += r.get('list', []); cursor = r.get('nextPageCursor', '')
            if not cursor or not r.get('list'): break
        return out

    def positions(self):
        out = []; cursor = ''
        while True:
            p = dict(category='linear', settleCoin='USDT', limit=200)
            if cursor: p['cursor'] = cursor
            r = self.get('/v5/position/list', p)
            out += r.get('list', []); cursor = r.get('nextPageCursor', '')
            if not cursor or not r.get('list'): break
        return out

    def account_info(self):
        return self.get('/v5/account/info', {})

    def api_key_info(self):
        return self.get('/v5/user/query-api', {})

    def equity(self):
        r = self.get('/v5/account/wallet-balance', dict(accountType='UNIFIED'))
        return float(r['list'][0]['totalEquity'])


# =====================================================================================
def _round(x, step, mode):
    d = Decimal(str(step))
    return float((Decimal(str(x)) / d).to_integral_value(rounding=mode) * d)


def _fmt(x, step):
    d = Decimal(str(step)).normalize()
    places = max(0, -d.as_tuple().exponent)
    return f'{x:.{places}f}'


DUR = {'15m': pd.Timedelta('15min'), '30m': pd.Timedelta('30min'), '1h': pd.Timedelta('1h'), '4h': pd.Timedelta('4h')}


class BybitExchange:
    def __init__(self, rest, coins, logger, max_lev=25):
        self.r = rest; self.coins = coins; self.L = logger; self.max_lev = max_lev
        self.inst = {}
        self.orders = {}          # link -> order record
        self.tradepos = {}        # trade id -> signed qty actually held
        self.seen = {}            # execId -> time ms (for de-dup)
        self.exec_from = None     # ms
        self.core_qty = {'BTC': 0.0, 'ETH': 0.0}   # held by the core sleeve (signed), v2.3: BTC + ETH
        self.addon = None         # v2.3 trend add-on position book (AddOnBook), attached by the runner
        self.nonce = ''.join(random.choices(string.ascii_lowercase, k=3))
        self.n = 0
        self.bot = None; self.catchup = False
        self._eq = (0.0, None); self.mismatch = {}
        self.now = None
        self.step_t = None            # bar time of the step being executed (idempotent exit links)
        self.block_entries = None     # UTC day on which new entries are blocked (daily-loss guard)
        self.last_eq = None           # last accepted equity reading (sanity check)
        self.safety = dict(dstop=0.35, max_order_x=4.0, eq_jump=0.30)

    def __getstate__(self):
        d = dict(self.__dict__); d['r'] = None; d['L'] = None; d['bot'] = None; return d

    def __setstate__(self, d):
        self.__dict__.update(d)
        if not isinstance(self.__dict__.get('core_qty'), dict):
            self.__dict__['core_qty'] = {'BTC': float(self.__dict__.get('core_qty') or 0.0), 'ETH': 0.0}
        self.__dict__.setdefault('addon', None); self.__dict__.setdefault('disabled', set())
        for k, v in dict(step_t=None, block_entries=None, last_eq=None,
                         safety=dict(dstop=0.35, max_order_x=4.0, eq_jump=0.30)).items():
            self.__dict__.setdefault(k, v)

    def attach(self, bot, rest=None, logger=None):
        self.bot = bot
        if rest is not None: self.r = rest
        if logger is not None: self.L = logger

    # ---------------------------------------------------------------- setup
    def setup(self):
        from lib_data import ADDON_EXTRA
        self.disabled = set()
        for c in self.coins:
            held = self._owners(c) > 0 or (self.addon is not None and self.addon.holds(c))
            try:
                i = self.r.instrument(f'{c}USDT')
                if str(i.get('status', 'Trading')) not in ('Trading',):
                    if held:     # never drop a coin we hold: keep managing (and exiting) it
                        self.L.p(f"ALERT: {c}USDT status {i.get('status')} while a position is held; still managing it")
                    else:
                        raise ValueError(f"status {i.get('status')}")
            except Exception as e:
                net = isinstance(e, BybitError) and e.code == -1
                if c in self.inst and (net or held):
                    self.L.p(f'WARNING: instrument info for {c} unavailable ({e!r}); using the saved values')
                    continue
                if c in ADDON_EXTRA and not net and not held:
                    self.disabled.add(c)
                    self.L.p(f'ALERT: {c}USDT not tradable ({e!r}); add-on will skip {c}')
                    continue
                raise                                   # network trouble at first start / a main coin: retry startup
            self.inst[c] = dict(tick=float(i['priceFilter']['tickSize']), step=float(i['lotSizeFilter']['qtyStep']),
                                minq=float(i['lotSizeFilter']['minOrderQty']),
                                minn=float(i['lotSizeFilter'].get('minNotionalValue', 5) or 5),
                                maxlev=float(i['leverageFilter']['maxLeverage']))
            lev = int(min(self.max_lev, self.inst[c]['maxlev']))
            try:
                self.r.set_leverage(f'{c}USDT', lev)
            except BybitError as e:
                self.L.p(f'note: could not set leverage for {c}: {e}')
            self.L.ev('setup', coin=c, lev=lev, **self.inst[c])
        if self.exec_from is None:
            self.exec_from = self.r.now_ms() - 60_000
        self.fast_until = time.time() + 900    # first 15 min after a (re)start: correct position mismatches fast
        self.safety_checks()

    def safety_checks(self):
        """refuse to run with isolated margin or a key that can withdraw"""
        try:
            mm = self.r.account_info().get('marginMode', '')
            self.L.ev('margin_mode', mode=mm)
            if mm == 'ISOLATED_MARGIN':
                raise SystemExit('SAFETY STOP: account is in ISOLATED margin mode. Switch Bybit to CROSS margin '
                                 '(Unified account > margin mode) and start again.')
        except Exception as e:
            self.L.p(f'WARNING: could not verify margin mode ({e!r}); make sure the account uses CROSS margin')
        try:
            k = self.r.api_key_info(); perms = k.get('permissions', {}) or {}
            wallet = [x for x in (perms.get('Wallet') or [])]
            self.L.ev('api_key', read_only=k.get('readOnly'), wallet=wallet, ips=k.get('ips'))
            if any('withdraw' in str(x).lower() for x in wallet) or perms.get('Withdraw'):
                raise SystemExit('SAFETY STOP: this API key has WITHDRAW permission. Create a key with trading '
                                 'permission only and run SETUP.bat again.')
            if wallet:
                self.L.p(f'WARNING: API key has wallet permissions {wallet}; trading-only is safer')
            if not k.get('ips') or k.get('ips') in (['*'], '*'):
                self.L.p('note: API key has no IP whitelist (recommended for real money)')
        except Exception as e:
            self.L.p(f'WARNING: could not verify API key permissions ({e!r})')

    # ---------------------------------------------------------------- helpers
    def _link(self, tag):
        self.n += 1
        tid, role, k = tag
        return f'{self.nonce}-{tid}-{role[:2]}{k}-{self.n}'[:36]

    IDEMP = ('time', 'cluster', 'trim', 'entry', 'aexit')

    def _det_link(self, tag):
        """same link if the same step is executed again after a crash -> no double orders"""
        tid, role, k = tag
        m = int(pd.Timestamp(self.step_t).value // 60_000_000_000) % 10**8
        return f'{self.nonce}-{tid}-{role[:2]}{k}-m{m}'[:36]

    def _qty(self, c, q):
        # v3.3 fix: a float sum of fills (e.g. 2.948 + 2.976 = 5.923999999999999) must not round DOWN one step below
        # what is held (that left 0.001 dust that reconcile had to sell); snap values within 1e-6 step upward first
        st = self.inst[c]['step']
        return _round(abs(q) + st * 1e-6, st, ROUND_DOWN)

    def _px(self, c, p, mode=ROUND_HALF_UP):
        return _round(p, self.inst[c]['tick'], mode)

    def _ok_size(self, c, qty, px):
        i = self.inst[c]
        return qty >= i['minq'] and qty * px >= i['minn']

    def _send(self, rec, body):
        rec['body'] = body
        try:
            res = self.r.create(**body)
            rec['status'] = 'open'; rec['orderId'] = res.get('orderId')
            self.orders[rec['link']] = rec
            self.L.order('create', rec)
            return rec['link']
        except BybitError as e:
            if e.code in (110072,):   # duplicate orderLinkId -> the first attempt went through
                rec['status'] = 'open'; self.orders[rec['link']] = rec; return rec['link']
            if e.code == -1:
                # network failure: the order may or may not have reached Bybit -> ask, else track as unconfirmed
                info = None
                try:
                    info = self.r.order_info(rec['body']['symbol'], rec['link'])
                except BybitError:
                    rec['status'] = 'open'; rec['unconfirmed'] = str(self.now); self.orders[rec['link']] = rec
                    self.L.ev('order_unconfirmed', link=rec['link'], tag=str(rec['tag']))
                    return rec['link']
                if info is not None:
                    rec['status'] = 'open'; rec['orderId'] = info.get('orderId'); self.orders[rec['link']] = rec
                    self.L.order('create', rec); return rec['link']
            rec['status'] = 'rejected'; rec['err'] = str(e); rec['code'] = e.code
            self.L.order('reject', rec)
            return None

    def _entry_allowed(self, coin, qty, px, tag):
        """hard limits for NEW exposure (never applied to exits)"""
        if self.block_entries is not None and self.now is not None and pd.Timestamp(self.now).normalize() == self.block_entries:
            self.L.ev('entry_blocked_daily_loss', coin=coin, tag=str(tag)); return False
        eq = self.last_eq
        if eq and qty * px > self.safety['max_order_x'] * eq:
            self.L.p(f'ALERT: order {coin} {tag} notional {qty * px:,.0f} > {self.safety["max_order_x"]}x equity '
                     f'{eq:,.0f} -> refused')
            self.L.ev('order_too_large', coin=coin, tag=str(tag), notional=qty * px, equity=eq); return False
        return True

    CORE_COINS = ('BTC', 'ETH')

    def _holder_sides(self, c):
        """direction (+1/-1) of every component that holds coin c: engine trades, add-on, core"""
        sides = [q.s for q in (self.bot.open_trades() if self.bot is not None else []) if q.coin == c]
        if self.addon is not None and self.addon.holds(c):
            sides += sorted(set(self.addon.sides(c))) if hasattr(self.addon, 'sides') else [1]   # v3.2: add-on shorts
        cq = self.core_qty.get(c, 0.0) if isinstance(self.core_qty, dict) else 0.0
        if c in self.inst and abs(cq) >= self.inst[c]['minq'] * 0.5:
            sides.append(1 if cq > 0 else -1)
        return sides

    def _owners(self, c):
        return len(self._holder_sides(c))

    def _reduce_ok(self, c):
        # reduce-only is safe whenever every holder of the coin is on the SAME side: then any exit order of one
        # holder reduces the net position and can never flip it (Bybit truncates reduce-only orders to the
        # position). Holders on OPPOSITE sides (e.g. core long + E3 short) need plain orders; that case is kept
        # rare (the add-on does not buy a coin with an open short) and is cleaned up by reconcile().
        return len(set(self._holder_sides(c))) <= 1

    # ---------------------------------------------------------------- Bot interface
    def place_limit(self, coin, side, price, qty, tf, tag, pen=0.0, prio=None, notional=None, reduce=False):
        tid, role, k = tag
        if role == 'entry' and self.catchup:
            return None
        if qty is None:
            qty = abs(self.tradepos.get(tid, 0.0))
        mode = ROUND_DOWN if side > 0 else ROUND_UP
        px = self._px(coin, price, mode); q = self._qty(coin, qty)
        rec = dict(link=self._link(tag), coin=coin, side=side, kind='limit', price=px, qty=q, tf=tf, tag=tag,
                   reduce=reduce, filled=0.0, vwap=0.0, t=str(self.now))
        if not self._ok_size(coin, q, px):
            rec['status'] = 'too_small'; self.L.order('too_small', rec); return None
        if role == 'entry' and not self._entry_allowed(coin, q, px, tag):
            return None
        body = dict(symbol=f'{coin}USDT', side='Buy' if side > 0 else 'Sell', orderType='Limit',
                    qty=_fmt(q, self.inst[coin]['step']), price=_fmt(px, self.inst[coin]['tick']),
                    timeInForce='GTC', positionIdx=0, orderLinkId=rec['link'])
        if reduce and self._reduce_ok(coin):
            body['reduceOnly'] = True
        rec['ro'] = bool(body.get('reduceOnly'))
        return self._send(rec, body)

    def place_stop(self, coin, side, trigger, qty, tf, tag, slip=0.0):
        tid = tag[0]; explicit = qty is not None
        if qty is None:
            qty = abs(self.tradepos.get(tid, 0.0))
        mode = ROUND_DOWN if side < 0 else ROUND_UP
        px = self._px(coin, trigger, mode); q = self._qty(coin, qty)
        rec = dict(link=self._link(tag), coin=coin, side=side, kind='stop', price=px, qty=q, tf=tf, tag=tag,
                   reduce=True, filled=0.0, vwap=0.0, t=str(self.now))
        if q <= 0:
            rec['status'] = 'too_small'; self.L.order('too_small', rec); return None
        body = dict(symbol=f'{coin}USDT', side='Buy' if side > 0 else 'Sell', orderType='Market',
                    qty=_fmt(q, self.inst[coin]['step']), triggerPrice=_fmt(px, self.inst[coin]['tick']),
                    triggerDirection=1 if side > 0 else 2, triggerBy='LastPrice', positionIdx=0,
                    orderLinkId=rec['link'])
        if self._reduce_ok(coin):
            body['reduceOnly'] = True; body['closeOnTrigger'] = True
        rec['ro'] = bool(body.get('reduceOnly'))
        link = self._send(rec, body)
        if link is None and rec.get('status') == 'rejected' and explicit and \
                rec.get('code') not in (110092, 110093) and q > abs(self.tradepos.get(tid, 0.0)) + 1e-12:
            # a stop sized for the full planned ladder was refused -> fall back to the quantity actually held
            return self.place_stop(coin, side, trigger, None, tf, tag, slip)
        if link is None and rec.get('status') == 'rejected' and rec.get('code') in (110092, 110093):
            # price is already beyond the trigger -> the backtest would stop out now
            self.L.ev('stop_rejected_market_exit', coin=coin, tag=str(tag), err=rec.get('err'))
            m = self.market(coin, side, None, tag=(tid, 'stopnow', 0))
            if m is None:
                return None            # exit failed too: stop_oid stays empty -> sync() tries again in seconds
            rec['status'] = 'replaced_by_market'
            self.orders[rec['link']] = rec
            return rec['link']
        if link is None:
            self.L.p(f'ALERT: stop for {coin} {tag} could not be placed ({rec.get("err")}); retrying')
        return link

    def market(self, coin, side, qty, tag, at='close', bar_start=None, tf=None, frac=None, reduce_only=False, px=None):
        tid, role, k = tag
        if coin not in self.inst:
            self.L.p(f'ALERT: no instrument info for {coin}; market order {tag} not sent'); return None
        if role == 'entry' and self.catchup:
            return None
        if qty is None:
            qty = abs(self.tradepos.get(tid, 0.0)) * (frac or 1.0)
            reduce = True
        else:
            reduce = bool(reduce_only)
        q = self._qty(coin, qty)
        det = role in self.IDEMP and self.step_t is not None
        link = self._det_link(tag) if det else self._link(tag)
        if det:
            prev = self.orders.get(link)
            if prev is None:
                try:
                    prev = self.r.order_info(f'{coin}USDT', link)
                except BybitError:
                    prev = None
            if prev is not None:
                if link not in self.orders:      # sent before a crash: track it so its fills are not lost
                    self.orders[link] = dict(link=link, coin=coin, side=side, kind='market', price=None,
                                             qty=float(prev.get('qty') or qty), tf=tf, tag=tag, reduce=reduce,
                                             filled=0.0, vwap=0.0, t=str(self.now), bar_start=str(bar_start),
                                             status='open')
                self.L.ev('duplicate_step_order_skipped', link=link, tag=str(tag)); return link
        rec = dict(link=link, coin=coin, side=side, kind='market', price=None, qty=q, tf=tf, tag=tag,
                   reduce=reduce, filled=0.0, vwap=0.0, t=str(self.now), bar_start=str(bar_start))
        if q <= 0 or q < self.inst[coin]['minq']:
            rec['status'] = 'too_small'; self.L.order('too_small', rec); return None
        if role == 'entry':
            if not px:
                self.L.p(f'ALERT: market entry {coin} {tag} without a price -> refused'); return None
            if not self._entry_allowed(coin, q, px, tag):
                return None
        body = dict(symbol=f'{coin}USDT', side='Buy' if side > 0 else 'Sell', orderType='Market',
                    qty=_fmt(q, self.inst[coin]['step']), positionIdx=0, orderLinkId=rec['link'])
        # 'flat' undoes a fill that belongs to no open holder: reduce-only only if nobody else holds the coin,
        # otherwise the order could also close (part of) another component's position
        ro = (self._owners(coin) == 0) if role == 'flat' else self._reduce_ok(coin)
        if reduce and ro:
            body['reduceOnly'] = True
        return self._send(rec, body)

    def amend(self, oid, qty=None, price=None, prio=None, notional=None):
        rec = self.orders.get(oid)
        if rec is None or rec.get('status') != 'open':
            return
        c = rec['coin']; body = dict(symbol=f'{c}USDT', orderLinkId=oid); ch = False
        if qty is not None:
            q = self._qty(c, qty)
            if q < self.inst[c]['minq']:
                self.cancel(oid); return
            if abs(q - rec['qty']) > 1e-12:
                body['qty'] = _fmt(q, self.inst[c]['step']); ch = True
        if price is not None:
            mode = ROUND_DOWN if rec['side'] > 0 else ROUND_UP
            p = self._px(c, price, mode)
            if abs(p - rec['price']) > 1e-12:
                body['triggerPrice' if rec['kind'] == 'stop' else 'price'] = _fmt(p, self.inst[c]['tick']); ch = True
        if not ch:
            return
        try:
            self.r.amend(**body)
            if 'qty' in body: rec['qty'] = float(body['qty'])
            if 'price' in body or 'triggerPrice' in body: rec['price'] = float(body.get('price', body.get('triggerPrice')))
            self.L.order('amend', rec)
        except BybitError as e:
            self.L.ev('amend_failed', link=oid, err=str(e))

    def cancel(self, oid):
        rec = self.orders.get(oid)
        if rec is None or rec.get('status') != 'open':
            return
        self._cancel_send(oid, rec)
        rec['status'] = 'cancelling'; rec['cancel_t'] = str(self.now); self.L.order('cancel', rec)

    def _cancel_send(self, oid, rec):
        rec['cancel_try'] = time.time()
        try:
            self.r.cancel(f"{rec['coin']}USDT", oid)
        except BybitError as e:
            if e.code not in (110001, 110008, 110010):   # already gone / filled / cancelled
                self.L.ev('cancel_failed', link=oid, err=str(e))

    def equity(self):
        t, v, n = self._eq if len(self._eq) == 3 else (0.0, None, None)
        if v is None or time.time() - t > 3 or n != self.now:
            v = self.r.equity()
            if self.last_eq and abs(v / self.last_eq - 1) > self.safety['eq_jump']:
                time.sleep(1); v2 = self.r.equity()
                self.L.p(f'ALERT: equity jumped {self.last_eq:,.2f} -> {v:,.2f} (re-read {v2:,.2f})')
                self.L.ev('equity_jump', before=self.last_eq, first=v, second=v2)
                v = v2
            if v > 0:
                self.last_eq = v
            self._eq = (time.time(), v, self.now)
        return v

    # ---------------------------------------------------------------- fills
    def poll(self, now):
        """Fill detection from the ORDERS themselves (cumExecQty / avgPrice / orderStatus by orderLinkId).
        Bybit's trade-history endpoint can lag by minutes on the demo server; order status does not."""
        self.now = now
        for link, rec in list(self.orders.items()):
            if rec.get('status') not in ('open', 'cancelling'):
                continue
            try:
                info = self.r.order_info(f"{rec['coin']}USDT", link)
            except BybitError as e:
                self.L.ev('order_query_failed', link=link, err=str(e)); continue
            if info is None:
                if rec.get('status') == 'cancelling' and rec.get('cancel_t') and now - pd.Timestamp(rec['cancel_t']) > pd.Timedelta('10min'):
                    rec['status'] = 'cancelled'
                elif rec.get('unconfirmed') and now - pd.Timestamp(rec['unconfirmed']) > pd.Timedelta('2min'):
                    rec['status'] = 'cancelled'; self.L.ev('unconfirmed_order_absent', link=link)
                    self._gone(link, rec, 'never_arrived')
                continue
            rec.pop('unconfirmed', None)
            if rec.get('status') == 'cancelling' and info.get('orderStatus') in ('New', 'PartiallyFilled', 'Untriggered') \
                    and time.time() - rec.get('cancel_try', 0) > 20:
                self.L.ev('cancel_retry', link=link)
                self._cancel_send(link, rec)
            cum = float(info.get('cumExecQty') or 0)
            avg = float(info.get('avgPrice') or 0) if info.get('avgPrice') not in (None, '', '0') else 0.0
            st = info.get('orderStatus', '')
            if cum > rec['filled'] + 1e-12:
                dq = cum - rec['filled']
                if avg > 0:
                    px = (avg * cum - rec['vwap'] * rec['filled']) / dq
                else:
                    px = rec.get('price') or 0.0
                self._dispatch(link, rec, dq, px, now)
            if st in ('Filled',) or (rec['filled'] >= rec['qty'] - 1e-12 and rec['qty'] > 0):
                rec['status'] = 'filled'
            elif st in ('Cancelled', 'Rejected', 'Deactivated', 'PartiallyFilledCanceled'):
                was = rec['status']; rec['status'] = 'cancelled'
                if was == 'open':
                    self._gone(link, rec, st)

    def _dispatch(self, link, rec, q, px, now):
        sgn = rec['side']
        self.L.fill(dict(symbol=f"{rec['coin']}USDT", side='Buy' if sgn > 0 else 'Sell', execQty=q, execPrice=px,
                         t=str(now)), rec)
        first = rec['filled'] == 0
        rec['vwap'] = (rec['vwap'] * rec['filled'] + px * q) / (rec['filled'] + q); rec['filled'] += q
        tid, role, k = rec['tag']
        if tid == 'CORE':
            self.core_qty[rec['coin']] = self.core_qty.get(rec['coin'], 0.0) + sgn * q; return
        self.tradepos[tid] = self.tradepos.get(tid, 0.0) + sgn * q
        if tid == 'FIX':
            return
        if tid.startswith('A'):                       # v2.3 trend add-on
            if self.addon is not None:
                self.addon.on_fill(tid, role, rec['coin'], sgn, q, px, now, self)
            return
        tr = self.bot.trades.get(tid)
        if role == 'entry':
            if first:
                bs = now.floor(DUR[rec['tf']])
                if rec['kind'] == 'market':
                    res = self.bot.on_market_fill(rec['tag'], px, pd.Timestamp(rec.get('bar_start')), now)
                else:
                    res = self.bot.on_fill(link, rec['tag'], px, q, bs, now)
                tr = self.bot.trades.get(tid)
                if res == 'cancelled' or tr is None:
                    # admission failed at fill time (another trade won the race) -> flatten what was bought
                    self.L.ev('flatten_unadmitted', trade=tid, coin=rec['coin'])
                    self.cancel(link)
                    # v3.4 fix: flatten exactly THIS order's fill. (Was: the whole trade position, which double-closed
                    # when two ladder orders of the same unadmitted trade filled in one poll.)
                    self.market(rec['coin'], -sgn, q, tag=(tid, 'flat', 0), reduce_only=self._reduce_ok(rec['coin']))
            elif tr is None or tr.status != 'open':
                # late partial fill on an order of a trade that is already gone -> flatten that piece
                self.market(rec['coin'], -sgn, q, tag=(tid, 'flat', 0))
            return
        if role in ('stop', 'tp', 'stopnow') and tr is not None and self.tradepos[tid] * tr.s < -self.inst[rec['coin']]['minq'] * 0.5:
            # an exit order closed MORE than this trade held (shared coin) -> give the excess back at once
            ex_q = abs(self.tradepos[tid])
            self.L.p(f"ALERT: {role} on {rec['coin']} overshot trade {tid} by {ex_q}; flattening the excess")
            self.market(rec['coin'], tr.s, ex_q, tag=(tid, 'flat', 0), reduce_only=self._reduce_ok(rec['coin']))
            self.bot.on_exit(link, rec['tag'], rec['vwap'], now.floor('15min'), now, 'tp' if role == 'tp' else 'stop')
            return
        if role in ('stop', 'tp', 'stopnow') and tr is not None and tr.status == 'open':
            if abs(self.tradepos[tid]) < self.inst[rec['coin']]['minq'] * 0.5:
                self.bot.on_exit(link, rec['tag'], rec['vwap'], now.floor('15min'), now,
                                 'tp' if role == 'tp' else 'stop')

    def _gone(self, link, rec, st):
        """the exchange cancelled/rejected one of our live orders"""
        self.L.ev('order_gone', link=link, tag=str(rec['tag']), status=st)
        tid, role, k = rec['tag']
        q = self.bot.trades.get(tid) if self.bot else None
        if q is None:
            return
        if q.status == 'open' and role == 'stop':
            q.stop_oid = None          # sync() re-places it
        elif role == 'tp':
            q.tp_oid = None            # re-placed at the next bar start
        elif role == 'entry':
            for lv in q.levels:
                if lv.get('oid') == link: lv['oid'] = None

    def planned_qty(self, q):
        """coin quantity of the whole entry ladder (filled + still resting)"""
        tot = 0.0
        for lv in q.levels:
            if lv.get('L'):
                tot += q.nt0 * lv['w'] / lv['L'] if hasattr(q, 'nt0') else q.nt * lv['w'] / lv['L']
        return tot

    def stop_price(self, q):
        """strategy stop, or (engines without one) a disaster stop dstop away from the first fill"""
        if not (q.stop != q.stop):
            return q.stop, False
        fps = [lv['fp'] for lv in q.levels if lv.get('fp')]
        if not fps:
            return None, True
        return fps[0] * (1 - q.s * self.safety['dstop']), True

    def sync(self):
        """keep every open trade protected by an exchange-side stop:
        - the stop covers the whole planned ladder (reduce-only, so it never exceeds the position) on every coin
          except BTC (shared with the core sleeve, no reduce-only) where it equals what the trade holds;
        - trades of engines without a strategy stop (E1/E5) get a disaster stop dstop (35%) from the first fill;
        - TP quantity always equals what the trade holds."""
        for q in list(self.bot.open_trades()):
            held = abs(self.tradepos.get(q.id, 0.0))
            ro_now = self._reduce_ok(q.coin)
            for a in ('stop_oid', 'tp_oid'):
                rec = self.orders.get(getattr(q, a)) if getattr(q, a) else None
                if rec is not None and rec.get('status') == 'open' and rec.get('ro', True) != ro_now:
                    # sharing of the coin changed -> this order's reduce-only flag is wrong: replace it
                    self.L.ev('replace_for_sharing', trade=q.id, coin=q.coin, order=a, ro_now=ro_now)
                    self.cancel(getattr(q, a)); setattr(q, a, None)
            full = max(held, self.planned_qty(q)) if (ro_now and self._owners(q.coin) <= 1) else held
            for oid, want_raw in ((q.stop_oid, full), (q.tp_oid, held)):
                rec = self.orders.get(oid) if oid else None
                if rec is None or rec.get('status') != 'open':
                    continue
                if not rec.get('ro', True):
                    want_raw = held                       # never more than held without reduce-only
                want = self._qty(q.coin, want_raw)
                if want > 0 and abs(want - rec['qty']) >= self.inst[q.coin]['step'] * 0.5:
                    self.amend(oid, qty=want)
            if q.stop_oid is None and held > 0:
                px, disaster = self.stop_price(q)
                if px is not None:
                    q.stop_oid = self.place_stop(q.coin, -q.s, px, full if self._reduce_ok(q.coin) else None,
                                                 tf=q.tf, tag=(q.id, 'stop', 0))
                    if disaster and q.stop_oid:
                        self.L.ev('disaster_stop', trade=q.id, coin=q.coin, trigger=px)

    def check_orders(self):
        """forget finished orders older than a day (fills/cancels are detected in poll())"""
        cut = str(self.now - pd.Timedelta('1D')) if self.now is not None else ''
        self.orders = {k: v for k, v in self.orders.items()
                       if v.get('status') in ('open', 'cancelling') or v.get('t', '') >= cut}

    def sync_addon(self):
        """every add-on position keeps an exchange-side disaster stop (35% beyond its entry: below for longs, above for shorts)"""
        if self.addon is None:
            return
        for tid, c in self.addon.tids():
            if c not in self.inst:                    # never let one coin without exchange info stall the bot
                if not getattr(self, '_noinst_warned', {}).get(c):
                    self._noinst_warned = dict(getattr(self, '_noinst_warned', {}), **{c: True})
                    self.L.p(f'ALERT: add-on holds {c} but its instrument info is missing; close {c} by hand on Bybit')
                continue
            held = abs(self.tradepos.get(tid, 0.0)); p = self.addon.pos[tid]
            if p.get('exiting'):
                xr = self.orders.get(p.get('exit_link')) if p.get('exit_link') else None
                if held >= self.inst[c]['minq'] * 0.5 and (xr is None or xr.get('status') not in ('open', 'cancelling')) \
                        and time.time() - p.get('exit_try_t', 0) >= 60:
                    p['exit_tries'] = p.get('exit_tries', 0) + 1; p['exit_try_t'] = time.time()
                    if p['exit_tries'] % 5 == 0:
                        self.L.p(f'ALERT: add-on exit for {c} still not done after {p["exit_tries"]} tries')
                    p['exit_link'] = self.market(c, -p.get('side', 1), None, tag=(tid, 'aexit', 0))
                elif held < self.inst[c]['minq'] * 0.5:
                    if p.get('stop_oid'): self.cancel(p['stop_oid'])
                    self.addon.pos.pop(tid, None); continue
            if p.get('px') is None and held < self.inst[c]['minq'] * 0.5:
                er = self.orders.get(p.get('entry_link')) if p.get('entry_link') else None
                if er is None or er.get('status') in ('cancelled', 'rejected', 'too_small'):
                    self.L.ev('addon_entry_failed', trade=tid, coin=c); self.addon.pos.pop(tid, None)
                    self.addon.logic.release(tid); continue
            rec = self.orders.get(p.get('stop_oid')) if p.get('stop_oid') else None
            if rec is not None and rec.get('status') == 'open' and rec.get('ro', True) != self._reduce_ok(c):
                self.cancel(p['stop_oid']); p['stop_oid'] = None; rec = None
            if rec is not None and rec.get('status') == 'open':
                want = self._qty(c, held)
                if want > 0 and abs(want - rec['qty']) >= self.inst[c]['step'] * 0.5:
                    self.amend(p['stop_oid'], qty=want)
                continue
            if held > 0 and p.get('px'):
                sd = p.get('side', 1)                  # v3.2: shorts get a buy stop 35% ABOVE the entry
                p['stop_oid'] = self.place_stop(c, -sd, p['px'] * (1 - sd * self.safety['dstop']), None, tf='4h',
                                                tag=(tid, 'stop', 0))

    def reconcile(self):
        """compare exchange positions with what the bot believes; flatten orphans."""
        pos = {p['symbol'][:-4]: (float(p['size']) * (1 if p['side'] == 'Buy' else -1 if p['side'] == 'Sell' else 0))
               for p in self.r.positions() if p['symbol'].endswith('USDT')}
        exp = {}
        for q in self.bot.open_trades():
            exp[q.coin] = exp.get(q.coin, 0.0) + self.tradepos.get(q.id, 0.0)
        for c, v in self.core_qty.items():
            exp[c] = exp.get(c, 0.0) + v
        if self.addon is not None:
            for tid, c in self.addon.tids():
                exp[c] = exp.get(c, 0.0) + self.tradepos.get(tid, 0.0)
        out = {}
        for c in set(pos) | set(exp):
            if c not in self.inst:
                continue
            a = pos.get(c, 0.0); e = exp.get(c, 0.0); tol = self.inst[c]['step'] * 0.5
            if abs(a - e) > tol:
                n = self.mismatch.get(c, 0) + 1; self.mismatch[c] = n
                self.L.ev('position_mismatch', coin=c, exchange=a, expected=e, count=n)
                if n >= (2 if time.time() < getattr(self, 'fast_until', 0) else 10):
                    # persistent difference: trade the exchange back to the expected position
                    d = e - a
                    if self._qty(c, abs(d)) < self.inst[c]['minq']:
                        # v3.3 fix: a difference smaller than one tradable step can not be traded away: keep the
                        # exchange position as the truth for the holder that can absorb it, instead of retrying forever
                        if c in getattr(self, 'core_qty', {}) and abs(self.core_qty.get(c, 0.0)) > 0:
                            self.core_qty[c] += (a - e)
                            self.L.p(f'note: {c} difference {a - e:+.6f} is below one order step; core position set to {self.core_qty[c]:.6f}')
                        self.mismatch[c] = 0; out[c] = a; continue
                    shrink = (e == 0 or (e * a > 0 and abs(e) < abs(a))) and self._reduce_ok(c)
                    self.L.p(f'ALERT: position mismatch {c}: exchange {a} vs expected {e}; correcting')
                    self.market(c, 1 if d > 0 else -1, abs(d), tag=('FIX', 'fix', 0), reduce_only=shrink)
                    self.mismatch[c] = 0
            else:
                self.mismatch[c] = 0
            out[c] = a
        return out

    def cancel_all_entries(self):
        n = 0
        for link, rec in list(self.orders.items()):
            if rec.get('status') == 'open' and rec['tag'][1] == 'entry' and rec.get('kind') == 'limit':
                self.cancel(link); n += 1
        return n

    # ---------------------------------------------------------------- BTC core sleeve
    def core_rebalance(self, target_qty, px, why, coin='BTC'):
        held = self.core_qty.get(coin, 0.0)
        d = target_qty - held
        if abs(d) * px < max(self.inst[coin]['minn'], 0.05 * abs(target_qty) * px) and not (target_qty == 0 and held != 0):
            return
        q = self._qty(coin, abs(d))
        if q < self.inst[coin]['minq']:
            return
        if abs(target_qty) * px > 0.5 * (self.last_eq or float('inf')):
            self.L.p(f'ALERT: core target {coin} {target_qty} looks too large; skipped'); return
        self.L.ev('core_rebalance', coin=coin, target=target_qty, held=held, delta=d, why=why)
        if self.step_t is not None:
            m = int(pd.Timestamp(self.step_t).value // 60_000_000_000) % 10**8
            link = f'{self.nonce}-CORE-{coin}-m{m}'[:36]
            try:
                prev = self.orders.get(link) or self.r.order_info(f'{coin}USDT', link)
            except BybitError:
                prev = None
            if prev is not None:
                if link not in self.orders:      # sent before a crash: track it so its fill updates core_qty
                    self.orders[link] = dict(link=link, coin=coin, side=1 if d > 0 else -1, kind='market', price=None,
                                             qty=float(prev.get('qty') or q), tf='4h', tag=('CORE', 'core', 0),
                                             reduce=False, filled=0.0, vwap=0.0, t=str(self.now), status='open')
                self.L.ev('duplicate_core_order_skipped', link=link); return
        else:
            link = self._link(('CORE', 'core', 0))
        rec = dict(link=link, coin=coin, side=1 if d > 0 else -1, kind='market', price=None,
                   qty=q, tf='4h', tag=('CORE', 'core', 0), reduce=False, filled=0.0, vwap=0.0, t=str(self.now))
        body = dict(symbol=f'{coin}USDT', side='Buy' if d > 0 else 'Sell', orderType='Market',
                    qty=_fmt(q, self.inst[coin]['step']), positionIdx=0, orderLinkId=rec['link'])
        self._send(rec, body)
