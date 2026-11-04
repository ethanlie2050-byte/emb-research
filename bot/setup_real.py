"""REAL MONEY setup: asks for the Bybit MAINNET API key (sub-account, trade-only, no withdraw), saves config.json, and tests the connection
(reads the balance, places one tiny far-away test order on BTC and cancels it again)."""
import os, sys, json, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    print('=' * 60)
    print(' Bybit REAL MONEY connection setup (mainnet)')
    print('=' * 60)
    print('Paste with Ctrl+V (or right-click), then press Enter.\n')
    key = input('API Key    : ').strip()
    secret = input('API Secret : ').strip()
    if not key or not secret:
        print('\nKey or secret was empty - nothing saved. Run SETUP again.'); return
    cfg = dict(api_key=key, api_secret=secret, demo=False, data_start='2025-04-01', core=True, max_leverage=25)
    json.dump(cfg, open(os.path.join(HERE, 'config.json'), 'w'), indent=2)
    os.system('cls' if os.name == 'nt' else 'clear')    # hide the pasted secret from the screen
    print('Saved config.json (key ending ...' + key[-4:] + '). Testing the connection...\n')
    from bybit_ex import BybitREST, BybitError
    r = BybitREST(key, secret, demo=False, log=print)
    try:
        r.sync_time()
        print(f'  [ok] Bybit server reachable (clock difference {r.offset_ms} ms)')
        eq = r.equity()
        print(f'  [ok] REAL account balance: {eq:,.2f} USD')
        if eq > 1500: print('  [!] More than $1,500 in this account - the plan was a $1,000 test sub-account.')
        info = r.get('/v5/user/query-api', {})
        perms = info.get('permissions', {}) or {}
        if any(perms.get(k) for k in ('Wallet', 'Withdraw')) or any('withdraw' in str(x).lower() for x in perms.get('Wallet', []) or []):
            print('  [STOP] This key has WALLET/WITHDRAW permission. Delete it and make a trade-only key.'); return
        print('  [ok] Key cannot withdraw or transfer')
        print('  [ok] IP whitelist: ' + (', '.join(info.get('ips') or []) if info.get('ips') and info.get('ips') != ['*'] else 'NONE (recommended: add your IP on Bybit)'))
        i = r.instrument('BTCUSDT')
        tick = float(i['priceFilter']['tickSize']); q = i['lotSizeFilter']['minOrderQty']
        k = r.klines('BTCUSDT', 15, int(time.time() * 1000) - 3600_000, int(time.time() * 1000))
        px = float(sorted(k)[-1][4])
        test_px = round(px * 0.7 / tick) * tick
        link = f'setuptest{int(time.time())}'
        r.create(symbol='BTCUSDT', side='Buy', orderType='Limit', qty=q, price=f'{test_px:.2f}',
                 timeInForce='GTC', positionIdx=0, orderLinkId=link)
        print(f'  [ok] Test order placed (buy {q} BTC at {test_px:,.0f}, far below the market - it cannot fill)')
        time.sleep(1)
        r.amend(symbol='BTCUSDT', orderLinkId=link, price=f'{test_px - 10 * tick:.2f}')
        print('  [ok] Test order amended')
        st = r.order_status('BTCUSDT', link)
        print(f'  [ok] Order status readable ({st})')
        r.cancel('BTCUSDT', link)
        print('  [ok] Test order cancelled')
        link2 = f'setupstop{int(time.time())}'
        stop_px = round(px * 0.5 / tick) * tick
        r.create(symbol='BTCUSDT', side='Sell', orderType='Market', qty=q, triggerPrice=f'{stop_px:.2f}',
                 triggerDirection=2, triggerBy='LastPrice', positionIdx=0, orderLinkId=link2)
        n_open = len(r.open_orders())
        print(f'  [ok] Test stop order placed (trigger {stop_px:,.0f}); open orders visible: {n_open}')
        r.cancel('BTCUSDT', link2)
        print('  [ok] Test stop order cancelled')
        r.positions(); r.executions(int(time.time() * 1000) - 86400_000)
        print('  [ok] Positions and trade history readable')
        print('\nREMINDER: the real account must have NO open positions or orders before the first start.')
        print('\nEVERYTHING WORKS. You can now start the bot with START_BOT.bat')
    except BybitError as e:
        print(f'\n  [PROBLEM] {e}')
        if e.code in (10003, 10004, 33004):
            print('  -> The key looks wrong. Make a new key on the real (not Demo) Bybit site for the sub-account and run SETUP_REAL again.')
        elif e.code in (10005, 10024):
            print('  -> The key has no trading permission. Edit the key on Bybit: Read-Write + Unified Trading.')
        else:
            print('  -> Send Claude a screenshot of this window (it does not show your secret).')
    except Exception as e:
        print(f'\n  [PROBLEM] {e!r}\n  -> Send Claude a screenshot of this window.')


if __name__ == '__main__':
    main()
    input('\nPress Enter to close.')
