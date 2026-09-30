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
TELEGRAM_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOLS = [s.strip().upper() for s in os.environ.get(
    "SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT").split(",") if s.strip()]

TIMEFRAME         = os.environ.get("TIMEFRAME", "1h")
CHECK_INTERVAL    = int(os.environ.get("CHECK_INTERVAL", "300"))
PIVOT_LEFT        = int(os.environ.get("PIVOT_LEFT", "5"))
PIVOT_RIGHT       = int(os.environ.get("PIVOT_RIGHT", "5"))
CLUSTER_TOLERANCE = float(os.environ.get("CLUSTER_TOLERANCE", "0.6"))
MIN_TOUCHES       = int(os.environ.get("MIN_TOUCHES", "2"))

# نطاقات التنبيه (نسبة مئوية من السعر الحالي)
ENTRY_PCT         = float(os.environ.get("ENTRY_PCT", "0.8"))   # مسافة الدخول الفعلي
EARLY_PCT         = float(os.environ.get("EARLY_PCT", "2.5"))   # مسافة الإنذار المبكر

# فلاتر جودة الصفقة (تُطبق على تنبيه الدخول فقط، وليس على الإنذار المبكر)
MIN_RANGE_WIDTH   = float(os.environ.get("MIN_RANGE_WIDTH", "1.5"))  # أدنى عرض للنطاق %
MIN_RR            = float(os.environ.get("MIN_RR", "1.3"))           # أدنى نسبة ربح/خسارة

# مدة كتم التنبيهات (ثواني) لكل نوع
COOLDOWN_EARLY    = int(os.environ.get("COOLDOWN_EARLY", "7200"))    # ساعتان
COOLDOWN_ENTRY    = int(os.environ.get("COOLDOWN_ENTRY", "3600"))    # ساعة

# مسافة وقف الخسارة الافتراضية (نسبة مئوية) لحساب R:R
STOP_BUFFER_PCT   = float(os.environ.get("STOP_BUFFER_PCT", "0.6"))

BINANCE_URL = "https://api.binance.com/api/v3/klines"

# ذاكرة التنبيهات: {(symbol, kind, level, alert_type): last_ts}
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
        window = candles[i - left:i] + candles[i + 1:i + right + 1]

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

# ================== منع التكرار ==================
def should_alert(symbol, kind, level, alert_type, cooldown):
    key = (symbol, kind, round(level, 2), alert_type)
    now = time.time()
    if now - ALERT_STATE.get(key, 0) < cooldown:
        return False
    ALERT_STATE[key] = now
    return True

# ================== رسالة التنبيه ==================
def build_message(alert_type, symbol, label, zone, current_price, dist,
                  range_width=None, rr=None):
    if alert_type == "ENTRY":
        header = "🚨 <b>تنبيه دخول</b>"
    else:
        header = "⏳ <b>إنذار مبكر</b>"

    lines = [
        header,
        "",
        f"<b>العملة:</b> {symbol}",
        f"<b>النوع:</b> {label}",
        f"<b>الإطار:</b> {TIMEFRAME}",
        f"<b>المستوى:</b> {zone['price']:.4f}",
        f"<b>السعر الحالي:</b> {current_price:.4f}",
        f"<b>المسافة:</b> {dist:.2f}%",
        f"<b>عدد اللمسات:</b> {zone['touches']}",
    ]
    if range_width is not None:
        lines.append(f"<b>عرض النطاق:</b> {range_width:.2f}%")
    if rr is not None:
        lines.append(f"<b>R:R المتوقع:</b> {rr:.2f}")
    return "\n".join(lines)

# ================== منطق الفحص ==================
def check_symbol(symbol):
    candles = fetch_klines(symbol, TIMEFRAME, 300)
    if len(candles) < 50:
        return

    current_price = candles[-1]["close"]

    highs, lows = find_pivots(candles, PIVOT_LEFT, PIVOT_RIGHT)
    resistance_zones = cluster_levels(highs, CLUSTER_TOLERANCE)
    support_zones    = cluster_levels(lows,  CLUSTER_TOLERANCE)

    resistance_zones = [z for z in resistance_zones
                        if z["touches"] >= MIN_TOUCHES and z["price"] > current_price]
    support_zones    = [z for z in support_zones
                        if z["touches"] >= MIN_TOUCHES and z["price"] < current_price]

    nearest_res = min(resistance_zones, key=lambda z: z["price"] - current_price) \
                  if resistance_zones else None
    nearest_sup = max(support_zones, key=lambda z: z["price"]) \
                  if support_zones else None

    # حساب المسافات
    res_dist = (nearest_res["price"] - current_price) / current_price * 100 \
               if nearest_res else 999.0
    sup_dist = (current_price - nearest_sup["price"]) / current_price * 100 \
               if nearest_sup else 999.0

    # تحديد الجهة الأقرب (إشارة واحدة لكل عملة في كل دورة)
    if res_dist <= sup_dist:
        side = "RES"
        label = "🟥 مقاومة"
        zone = nearest_res
        dist = res_dist
    else:
        side = "SUP"
        label = "🟩 دعم"
        zone = nearest_sup
        dist = sup_dist

    if zone is None:
        return

    # عرض النطاق (فقط إن وُجد الطرفان)
    range_width = None
    if nearest_res and nearest_sup:
        range_width = (nearest_res["price"] - nearest_sup["price"]) / current_price * 100

    # ============ المسار 1: تنبيه دخول ============
    if dist <= ENTRY_PCT:
        # فلتر النطاق الضيق
        if range_width is not None and range_width < MIN_RANGE_WIDTH:
            log.info(f"{symbol}: نطاق ضيق ({range_width:.2f}%) - تجاهل تنبيه الدخول")
            return

        # حساب R:R تقديري
        opposite = nearest_sup if side == "RES" else nearest_res
        rr = None
        if opposite is not None:
            reward = abs(zone["price"] - opposite["price"]) / current_price * 100
            risk = STOP_BUFFER_PCT + dist          # من السعر الحالي إلى ما بعد المستوى
            rr = reward / risk if risk > 0 else 0
            if rr < MIN_RR:
                log.info(f"{symbol}: R:R ضعيف ({rr:.2f}) - تجاهل تنبيه الدخول")
                return

        if should_alert(symbol, side, zone["price"], "ENTRY", COOLDOWN_ENTRY):
            msg = build_message("ENTRY", symbol, label, zone,
                                current_price, dist, range_width, rr)
            send_telegram(msg)
            log.info(f"ENTRY alert: {symbol} {label} @ {zone['price']:.4f} "
                     f"dist={dist:.2f}% rr={rr}")
        return

    # ============ المسار 2: إنذار مبكر ============
    if dist <= EARLY_PCT:
        if should_alert(symbol, side, zone["price"], "EARLY", COOLDOWN_EARLY):
            msg = build_message("EARLY", symbol, label, zone,
                                current_price, dist, range_width, None)
            send_telegram(msg)
            log.info(f"EARLY alert: {symbol} {label} @ {zone['price']:.4f} "
                     f"dist={dist:.2f}%")

# ================== خادم صحي لـ Render ==================
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
    send_telegram("✅ البوت بدأ العمل (نسخة بمساري التنبيه)")
    log.info(f"Bot started | symbols={SYMBOLS} tf={TIMEFRAME} "
             f"entry={ENTRY_PCT}% early={EARLY_PCT}%")

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
