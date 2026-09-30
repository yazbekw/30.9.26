import os
import time
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("signals")

# ================== الإعدادات ==================
TELEGRAM_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOLS = [s.strip().upper() for s in os.environ.get(
    "SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT").split(",") if s.strip()]

TIMEFRAME         = os.environ.get("TIMEFRAME", "1h")
CHECK_INTERVAL    = int(os.environ.get("CHECK_INTERVAL", "300"))     # ثواني بين كل فحص
PIVOT_LEFT        = int(os.environ.get("PIVOT_LEFT", "5"))
PIVOT_RIGHT       = int(os.environ.get("PIVOT_RIGHT", "5"))
CLUSTER_TOLERANCE = float(os.environ.get("CLUSTER_TOLERANCE", "0.6"))# % لتجميع المستويات المتقاربة
MIN_TOUCHES       = int(os.environ.get("MIN_TOUCHES", "2"))          # أقل عدد لمسات لاعتبار المستوى "حقيقياً"
PROXIMITY_PCT     = float(os.environ.get("PROXIMITY_PCT", "0.8"))    # % قرب السعر من المستوى للتنبيه
ALERT_COOLDOWN    = int(os.environ.get("ALERT_COOLDOWN", "3600"))    # ثواني قبل إعادة تنبيه نفس المستوى

BINANCE_URL = "https://api.binance.com/api/v3/klines"

# ذاكرة التنبيهات: { (symbol, kind, level_rounded): last_alert_ts }
ALERT_STATE = {}

# ================== Binance ==================
def fetch_klines(symbol, interval="1h", limit=300):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(BINANCE_URL, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    candles = []
    for k in data:
        candles.append({
            "time":   k[0],
            "open":   float(k[1]),
            "high":   float(k[2]),
            "low":    float(k[3]),
            "close":  float(k[4]),
            "volume": float(k[5]),
        })
    return candles

# ================== اكتشاف القمم والقيعان ==================
def find_pivots(candles, left, right):
    highs, lows = [], []
    for i in range(left, len(candles) - right):
        c = candles[i]
        left_window  = candles[i - left:i]
        right_window = candles[i + 1:i + right + 1]
        window = left_window + right_window

        if all(c["high"] > x["high"] for x in window):
            highs.append({"price": c["high"], "time": c["time"]})
        if all(c["low"] < x["low"] for x in window):
            lows.append({"price": c["low"], "time": c["time"]})
    return highs, lows

# ================== تجميع المستويات المتقاربة ==================
def cluster_levels(levels, tolerance_pct):
    if not levels:
        return []
    levels = sorted(levels, key=lambda x: x["price"])
    clusters = [[levels[0]]]

    for lvl in levels[1:]:
        current = clusters[-1]
        avg = sum(x["price"] for x in current) / len(current)
        if abs(lvl["price"] - avg) / avg * 100 <= tolerance_pct:
            current.append(lvl)
        else:
            clusters.append([lvl])

    result = []
    for cl in clusters:
        avg_price = sum(x["price"] for x in cl) / len(cl)
        result.append({
            "price":     avg_price,
            "touches":   len(cl),
            "last_time": max(x["time"] for x in cl),
        })
    return result

# ================== إرسال تلغرام ==================
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code != 200:
            log.warning(f"Telegram error {r.status_code}: {r.text}")
    except Exception as e:
        log.exception(f"Telegram exception: {e}")

# ================== منطق الفحص ==================
def should_alert(symbol, kind, level):
    key = (symbol, kind, round(level, 2))
    now = time.time()
    last = ALERT_STATE.get(key, 0)
    if now - last < ALERT_COOLDOWN:
        return False
    ALERT_STATE[key] = now
    return True

def check_symbol(symbol):
    candles = fetch_klines(symbol, TIMEFRAME, 300)
    if len(candles) < 50:
        return

    current_price = candles[-1]["close"]

    highs, lows = find_pivots(candles, PIVOT_LEFT, PIVOT_RIGHT)
    resistance_zones = cluster_levels(highs, CLUSTER_TOLERANCE)
    support_zones    = cluster_levels(lows,  CLUSTER_TOLERANCE)

    # مقاومة = فوق السعر الحالي، دعم = تحت السعر الحالي
    resistance_zones = [z for z in resistance_zones
                        if z["touches"] >= MIN_TOUCHES and z["price"] > current_price]
    support_zones    = [z for z in support_zones
                        if z["touches"] >= MIN_TOUCHES and z["price"] < current_price]

    if resistance_zones:
        nearest_res = min(resistance_zones, key=lambda z: z["price"] - current_price)
    else:
        nearest_res = None

    if support_zones:
        nearest_sup = max(support_zones, key=lambda z: z["price"])
    else:
        nearest_sup = None

    alerts = []

    if nearest_res:
        dist = (nearest_res["price"] - current_price) / current_price * 100
        if dist <= PROXIMITY_PCT:
            if should_alert(symbol, "RES", nearest_res["price"]):
                alerts.append(("🟥 مقاومة", nearest_res, dist))

    if nearest_sup:
        dist = (current_price - nearest_sup["price"]) / current_price * 100
        if dist <= PROXIMITY_PCT:
            if should_alert(symbol, "SUP", nearest_sup["price"]):
                alerts.append(("🟩 دعم", nearest_sup, dist))

    for label, zone, dist in alerts:
        msg = (
            f"🔔 <b>فرصة محتملة</b>\n\n"
            f"<b>العملة:</b> {symbol}\n"
            f"<b>النوع:</b> {label}\n"
            f"<b>الإطار:</b> {TIMEFRAME}\n"
            f"<b>المستوى:</b> {zone['price']:.4f}\n"
            f"<b>السعر الحالي:</b> {current_price:.4f}\n"
            f"<b>المسافة:</b> {dist:.2f}%\n"
            f"<b>عدد اللمسات:</b> {zone['touches']}"
        )
        send_telegram(msg)
        log.info(f"Alert sent: {symbol} {label} @ {zone['price']:.4f} (dist {dist:.2f}%)")

# ================== خادم بسيط لـ Render ==================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args, **kwargs):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    log.info(f"Health server on port {port}")
    server.serve_forever()

# ================== الحلقة الرئيسية ==================
def main_loop():
    send_telegram("✅ البوت بدأ العمل")
    log.info(f"Bot started | symbols={SYMBOLS} tf={TIMEFRAME} interval={CHECK_INTERVAL}s")

    while True:
        for symbol in SYMBOLS:
            try:
                check_symbol(symbol)
            except Exception as e:
                log.exception(f"Error on {symbol}: {e}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    main_loop()
