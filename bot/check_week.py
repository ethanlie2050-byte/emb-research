"""Weekly live-vs-backtest check for the Extreme Move bot (v3.4).
Usage (in the bot folder):  python check_week.py            (last 7 days)
                            python check_week.py 14         (last 14 days)
Reads only the bot's own log files (logs/*.jsonl) plus public Bybit 15m prices (no API keys needed).
Writes logs/weekly_report_<date>.txt and prints it."""
import json, os, sys, time, datetime as dt
from collections import Counter, defaultdict

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 7
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, 'logs')
# backtest reference (v3.4, 2022 - Sep 2026, bot-true add-on, real funding everywhere)
BT_RANGE = {7: dict(p10=-5.0, p25=-1.5, p50=0.7, p75=7.2, p90=22.2),     # % return over any 7 / 14 / 30 days
            14: dict(p10=-6.4, p25=-2.2, p50=2.9, p75=16.5, p90=36.1),
            30: dict(p10=-6.9, p25=-1.0, p50=14.1, p75=35.8, p90=70.4)}
BT_7D = BT_RANGE[min(BT_RANGE, key=lambda k: abs(k - DAYS))]      # measured range of the nearest period
_SC = DAYS / min(BT_RANGE, key=lambda k: abs(k - DAYS))           # small linear stretch for other lengths
BT_TRADES_PER_WEEK = 2.06                                         # main-engine trades (v3.4, 2022-2026); 27% of weeks have none
FILL_PEN = 0.002                                                  # backtest needs price to trade 0.2% through a limit


def jl(name):
    p = os.path.join(LOG, name)
    if not os.path.exists(p): return []
    out = []
    with open(p, encoding='utf-8') as f:
        for line in f:
            try: out.append(json.loads(line))
            except Exception: pass
    return out


def ts(s):
    return dt.datetime.fromisoformat(str(s).replace('Z', '')[:26])


def kline_close(coin, t):
    """close of the 15m bar that contains time t (public Bybit endpoint)."""
    try:
        import requests
        start = int((t - dt.timedelta(minutes=15)).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        r = requests.get('https://api.bybit.com/v5/market/kline', params=dict(category='linear', symbol=coin + 'USDT', interval='15',
                         start=start, limit=3), timeout=10).json()
        rows = sorted(r['result']['list'], key=lambda x: int(x[0]))
        for row in rows:
            b0 = dt.datetime.utcfromtimestamp(int(row[0]) / 1000)
            if b0 <= t < b0 + dt.timedelta(minutes=15): return float(row[4])
    except Exception:
        return None
    return None


def main():
    now = dt.datetime.utcnow(); t0 = now - dt.timedelta(days=DAYS)
    L = []
    p = L.append
    p(f'EXTREME MOVE BOT - live check, last {DAYS} days ({t0:%Y-%m-%d %H:%M} -> {now:%Y-%m-%d %H:%M} UTC)')
    p('=' * 78)

    # ---- equity
    eq = [e for e in jl('equity.jsonl') if 't' in e and ts(e['t']) >= t0]
    if eq:
        e0, e1 = eq[0]['equity'], eq[-1]['equity']; ret = (e1 / e0 - 1) * 100
        lo = min(e['equity'] for e in eq); hi_before = max(e['equity'] for e in eq)
        p(f'Equity: {e0:,.2f} -> {e1:,.2f} USDT  ({ret:+.2f}%)   lowest {lo:,.2f}')
        band = 'normal' if BT_7D['p10'] * _SC <= ret <= BT_7D['p90'] * _SC else ('BELOW the usual range' if ret < BT_7D['p10'] * _SC else 'above the usual range')
        p(f'  backtest: a {DAYS}-day stretch is usually between {BT_7D["p10"]*_SC:+.1f}% and {BT_7D["p90"]*_SC:+.1f}% (median {BT_7D["p50"]*_SC:+.1f}%) -> {band}')
        # gaps in the 15-minute loop
        gaps = []
        for a, b in zip(eq, eq[1:]):
            d = (ts(b['t']) - ts(a['t'])).total_seconds() / 60
            if d > 16: gaps.append((a['t'], d))
        p(f'Bot loop: {len(eq)} steps logged; missed-time gaps: {len(gaps)}' + (f' (longest {max(g[1] for g in gaps):.0f} min at {max(gaps, key=lambda g: g[1])[0]})' if gaps else ''))
    else:
        p('Equity: no equity log lines in this period - IS THE BOT RUNNING?')

    # ---- decisions
    D = [d for d in jl('decisions.jsonl') if 'time' in d and ts(d['time']) >= t0]
    k = Counter(d['kind'] for d in D)
    p('')
    p(f'Signals: {k.get("orders", 0)} entry orders placed, {k.get("fill", 0)} fills, {k.get("exit", 0)} exits, '
      f'{k.get("expire", 0)} setups expired unfilled, {k.get("reject", 0)} blocked by the quality filter, {k.get("skip", 0)} skipped (limits/caps)')
    opened = {d['trade'] for d in D if d['kind'] == 'fill'}
    p(f'Main-engine trades opened: {len(opened)}  (backtest average {BT_TRADES_PER_WEEK*DAYS/7:.1f} for {DAYS} days; ~27% of weeks have none)')

    # ---- fills vs intended levels
    orders = {d['trade']: d for d in jl('decisions.jsonl') if d.get('kind') == 'orders'}
    fl = [d for d in D if d['kind'] == 'fill']
    if fl:
        p('')
        p('Entry fills (limit orders): fill price vs planned level')
        for d in fl:
            o = orders.get(d['trade']); lv = None
            if o and o.get('levels') and d.get('level') is not None and d['level'] < len(o['levels']): lv = o['levels'][d['level']]
            if lv:
                bp = (d['px'] / lv - 1) * 1e4
                p(f'  {d["time"][:16]} {d["eng"]:4s} {d["coin"]:5s} level {d["level"]}: planned {lv:.6g} filled {d["px"]:.6g} ({bp:+.1f} bp)'
                  + ('  <-- worse than planned' if bp > 1 else ''))
            else:
                p(f'  {d["time"][:16]} {d["eng"]:4s} {d["coin"]:5s} filled {d["px"]:.6g}')

    # ---- exits
    ex = [d for d in D if d['kind'] == 'exit']
    if ex:
        p('')
        p('Closed trades:')
        tot = 0.0; by = defaultdict(list)
        for d in ex:
            tot += d.get('pnl', 0.0); by[d['eng']].append(d.get('ret_f', 0.0))
            extra = ''
            if d.get('reason') in ('time', 'stop'):          # market exits: compare with the 15m close
                c = kline_close(d['coin'], ts(d['time']))
                if c: extra = f'  (15m close {c:.6g}, slippage {(d["px"]/c-1)*1e4:+.1f} bp)'
            p(f'  {d["time"][:16]} {d["eng"]:4s} {d["coin"]:5s} {d["reason"]:5s} return {d.get("ret_f",0)*100:+.2f}%  pnl {d.get("pnl",0):+,.2f} USDT{extra}')
        p(f'  total realized: {tot:+,.2f} USDT; wins {sum(1 for d in ex if d.get("pnl",0) > 0)}/{len(ex)}')

    # ---- add-on book: altcoin trend add-on (A.....) and capitulation engine (AB....)
    E = [e for e in jl('events.jsonl') if 'wall' in e and ts(e['wall']) >= t0]
    ke = Counter(e['kind'] for e in E)
    p('')
    p(f'Altcoin trend add-on: {ke.get("addon_entry", 0)} entries, {ke.get("addon_exit", 0)} exits')
    p(f'Capitulation engine (BTC/ETH + 10 altcoins after altcoin crashes, ~4-5 events a year): {ke.get("breadth_entry", 0)} entries, '
      f'{ke.get("breadth_exit", 0)} exits, {ke.get("breadth_entry_skipped_open_short", 0) + ke.get("breadth_entry_not_sent", 0)} skipped')
    p(f'Euphoria engine (short BTC/ETH after altcoin spikes, ~3 events a year): {ke.get("euphoria_entry", 0)} entries, '
      f'{ke.get("euphoria_exit", 0)} exits, {ke.get("euphoria_entry_not_sent", 0)} skipped')
    fills = defaultdict(list)
    for f_ in jl('fills.jsonl'):
        tg = str(f_.get('tag') or '')
        if tg.startswith("('AB") or tg.startswith("('AE"):
            tid, role = tg.strip('()').replace("'", '').split(', ')[:2]
            fills[tid].append((role, float(f_['execQty']), float(f_['execPrice']), f_.get('t', '')))
    for tid, fs in sorted(fills.items()):
        en = [x for x in fs if x[0] == 'entry']; xs = [x for x in fs if x[0] != 'entry']
        if not en or not en[0][3] or ts(en[0][3]) < t0:
            continue                                  # only trades opened in this period
        pe = sum(q * x for _, q, x, _ in en) / sum(q for _, q, _, _ in en)
        line = f'  {tid} {"short" if tid.startswith("AE") else "buy"} {en[0][3][:16]} @ {pe:.6g}'
        if xs:
            px_ = sum(q * x for _, q, x, _ in xs) / sum(q for _, q, _, _ in xs)
            sd = -1 if tid.startswith('AE') else 1
            line += f'  exit {xs[-1][3][:16]} @ {px_:.6g}  return {sd * (px_ / pe - 1) * 100:+.2f}% (before fees)'
        else:
            line += '  (still open)'
        p(line)

    # ---- errors / alerts
    cons = [c.get('line', '') for c in jl('console.jsonl')]
    recent = [c for c in cons if c[:10] >= t0.strftime('%Y-%m-%d')]
    neterr = sum('net error' in c or ('ERROR' in c and 'network' in c) for c in recent)
    errs = [c for c in recent if 'ERROR' in c and 'network' not in c]
    alerts = [c for c in recent if 'ALERT' in c and 'not tradable' not in c]
    warn = [c for c in recent if 'WARNING' in c and 'could not verify' not in c]
    p('')
    p(f'Connection errors: {neterr} (short internet/Bybit drops; the bot retries by itself)')
    p(f'ERROR lines: {len(errs)} (the bot recovered from {sum("recovering" in e for e in errs)} of them automatically)' + ''.join('\n  ' + e[:160] for e in errs[-5:]))
    p(f'ALERT/WARNING lines: {len(alerts) + len(warn)}' + ''.join('\n  ' + e[:160] for e in (alerts + warn)[-5:]))

    # ---- verdict
    p('')
    issues = []
    if not eq: issues.append('no equity updates - bot may be stopped')
    if eq and gaps and max(g[1] for g in gaps) > 120: issues.append('bot was offline for more than 2 hours at least once')
    unrec = [e for e in errs if 'recovering' not in e]
    if unrec: issues.append('ERROR lines the bot did not recover from - send this report to Claude')
    if len(errs) > 20: issues.append('many recovered ERROR lines - send this report to Claude')
    if any((d['px'] / orders[d['trade']]['levels'][d['level']] - 1) * 1e4 > 5 for d in fl if d['trade'] in orders and d.get('level') is not None and d['level'] < len(orders[d['trade']].get('levels', []))):
        issues.append('an entry filled more than 5 bp worse than planned')
    p('VERDICT: ' + ('all normal' if not issues else 'CHECK: ' + '; '.join(issues)))
    p('Send this file to Claude every week so live results can be compared with the backtest.')

    txt = '\n'.join(L); print(txt)
    out = os.path.join(LOG, f'weekly_report_{now:%Y-%m-%d}.txt')
    with open(out, 'w', encoding='utf-8') as f: f.write(txt + '\n')
    print(f'\n(saved to {out})')


if __name__ == '__main__':
    main()
