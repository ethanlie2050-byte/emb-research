"""One-time helper for phone alerts.  Usage:  python telegram_setup.py
1. In Telegram, talk to @BotFather, send /newbot, follow the steps, copy the token it gives you.
2. Open your new bot in Telegram and send it any message (e.g. "hi").
3. Run this script and paste the token. It finds your chat id, sends a test message and
   saves both into config.json (your API keys in config.json are not touched or shown).
"""
import json, os, sys, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
cfgp = os.path.join(HERE, 'config.json')
if not os.path.exists(cfgp):
    sys.exit('config.json not found - run SWITCH_TO_V26.bat first')
token = input('Paste the bot token from @BotFather: ').strip()
try:
    r = json.load(urllib.request.urlopen(f'https://api.telegram.org/bot{token}/getUpdates', timeout=15))
except Exception as e:
    sys.exit(f'Could not reach Telegram with this token: {e}')
chats = [u['message']['chat'] for u in r.get('result', []) if 'message' in u and u['message']['chat'].get('type') == 'private']
if not chats:
    sys.exit('No message found. Open your bot in Telegram, send it "hi", then run this again.')
c = chats[-1]; chat = str(c['id'])
who = ' '.join(x for x in (c.get('first_name'), c.get('last_name'), '@' + c['username'] if c.get('username') else None) if x)
if input(f'Alerts will go to: {who}. Is that you? (y/n): ').strip().lower() != 'y':
    sys.exit('Cancelled. Make sure only you have messaged the bot, then run this again.')
data = urllib.parse.urlencode(dict(chat_id=chat, text='ExtremeMoveBot: alerts are working.')).encode()
urllib.request.urlopen(f'https://api.telegram.org/bot{token}/sendMessage', data=data, timeout=15).read()
cfg = json.load(open(cfgp))
cfg['telegram_token'] = token; cfg['telegram_chat'] = chat
tmp = cfgp + '.tmp'
with open(tmp, 'w') as fh:
    json.dump(cfg, fh, indent=1)
os.replace(tmp, cfgp)                 # atomic: config.json is never left half-written
print('Test message sent and config.json updated. Restart the bot to activate alerts.')
