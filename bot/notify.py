"""Optional phone alerts through a Telegram bot (v2.4).

config.json (both keys optional; without them nothing is sent):
    "telegram_token": "123456:ABC...",     from @BotFather
    "telegram_chat":  "123456789"          your chat id (see README)

Sending happens on a background thread: a slow or failing Telegram never delays or stops trading.
Repeated messages (e.g. the same network error every few seconds) are sent at most once per 30 minutes.
"""
import json, re, threading, time, queue, urllib.request, urllib.parse

KEYWORDS = ('ALERT', 'WARNING', 'ERROR', 'REAL MONEY', 'startup problem', 'shutdown', 'window closed',
            'reload failed', 'daily_loss_guard', 'disaster_stop')


class Notifier:
    def __init__(self, token=None, chat=None, name='ExtremeMoveBot'):
        self.on = bool(token and chat)
        self.token, self.chat, self.name = token, str(chat) if chat else None, name
        self.q = queue.Queue(maxsize=200)
        self.last = {}                     # message key -> last send time
        self.sent = []                     # send times (rate limit)
        if self.on:
            threading.Thread(target=self._run, daemon=True).start()

    @staticmethod
    def wanted(msg):
        return any(k in msg for k in KEYWORDS)

    def send(self, msg, key=None):
        if not self.on:
            return
        key = key or re.sub(r'[\d.,:]+', '#', msg)[:80]          # numbers stripped -> repeats share a key
        now = time.time()
        if now - self.last.get(key, 0) < 1800:
            return
        self.last[key] = now
        try:
            self.q.put_nowait(f'[{self.name}] {msg}'[:3500])
        except queue.Full:
            pass

    def _run(self):
        url = f'https://api.telegram.org/bot{self.token}/sendMessage'
        while True:
            msg = self.q.get()
            now = time.time()
            self.sent = [s for s in self.sent if now - s < 60]
            if len(self.sent) >= 15:                              # Telegram limit safety
                time.sleep(60 - (now - self.sent[0]))
            for attempt in range(3):
                try:
                    data = urllib.parse.urlencode(dict(chat_id=self.chat, text=msg)).encode()
                    urllib.request.urlopen(url, data=data, timeout=10).read()
                    self.sent.append(time.time())
                    break
                except Exception:
                    time.sleep(5 * (attempt + 1))
