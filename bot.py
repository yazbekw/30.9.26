import os
import time
import sqlite3
import logging
from datetime import datetime
import zoneinfo
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

_default_symbols = (
    "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,"
    "ADAUSDT,AVAXUSDT,LINKUSDT,DOGEUSDT,SUIUSDT"
)
_raw_symbols = os.environ.get("SYMBOLS", "").strip()
SYMBOLS = [s.strip().upper() for s in (_raw_symbols or _default_symbols).split(",") if s.strip()]

TIMEZONE = os.environ.get("TIMEZONE", "Asia/Damascus")

TIMEFRAME         = os.environ.get("TIMEFRAME", "1h")
CONFIRM_TIMEFRAME = os.environ.get("CONFIRM_TIMEFRAME", "5m")
CHECK_INTERVAL    = int(os.environ.get("CHECK_INTERVAL", "300"))

PIVOT_LEFT        = int(os.environ.get("PIVOT_LEFT", "5"))
PIVOT_RIGHT       = int(os.environ.get("PIVOT_RIGHT", "5"))
CLUSTER_TOLERANCE = float(os.environ.get("CLUSTER_TOLERANCE", "0.6"))
MIN_TOUCHES       = int(os.environ.get("MIN_TOUCHES", "2"))

ENTRY_PCT         = float(os.environ.get("ENTRY_PCT", "0.8"))
EARLY_PCT         = float(os.environ.get("EARLY_PCT", "2.5"))
MIN_RANGE_WIDTH   = float(os.environ.get("MIN_RANGE_WIDTH", "1.5"))
MIN_RR            = float(os.environ.get("MIN_RR", "1.3"))
STOP_BUFFER_PCT   = float(os.environ.get("STOP_BUFFER_PCT", "0.6"))

# تشغيل/إيقاف الإنذار المبكر
USE_EARLY_ALERT   = os.environ.get("USE_EARLY_ALERT", "true").lower() == "true"

# فلتر ADX
USE_ADX_FILTER    = os.environ.get("USE_ADX_FILTER", "true").lower() == "true"
ADX_PERIOD        = int(os.environ.get("ADX_PERIOD", "14"))
ADX_MAX_FOR_ENTRY = float(os.environ.get("ADX_MAX_FOR_ENTRY", "25"))

# تأكيد 5 دقائق
USE_5M_CONFIRM    = os.environ.get("USE_5M_CONFIRM", "true").lower() == "true"
WICK_BODY_RATIO   = float(os.environ.get("WICK_BODY_RATIO", "1.5"))

# Volume Profile
USE_VOLUME_PROFILE = os.environ.get("USE_VOLUME_PROFILE", "true").lower() == "true"
VP_BUCKETS         = int(os.environ.get("VP_BUCKETS", "60"))

COOLDOWN_EARLY    = int(os.environ.get("COOLDOWN_EARLY", "7200"))
COOLDOWN_ENTRY    = int(os.environ.get("COOLDOWN_ENTRY", "3600"))

DB_PATH = os.environ.get("DB_PATH", "alerts.db")
BINANCE_URL = "https://api.binance.com/api/v3/klines"

# ================== التوقيت المحلي ==================
def _tz():
    try:
        return zoneinfo.ZoneInfo(TIMEZONE)
    except Exception:
        return zoneinfo.ZoneInfo("UTC")

def local_now_str():
    return datetime.now(_tz()).strftime("%Y-%m-%d %H:%M:%S")

def ms_to_local_str(ms):
    try:
        return datetime.fromtimestamp(ms / 1000, tz=_tz()).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"

# ================== قاعدة البيانات ==================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_state (
            symbol     TEXT NOT NULL,
            kind       TEXT NOT NULL,
            level      REAL NOT NULL,
            alert_type TEXT NOT NULL,
            last_ts    REAL NOT NULL,
            PRIMARY KEY (symbol, kind, level, alert_type)
        )
    """)
    conn.commit()
    conn.close()

def get_last_alert(symbol, kind, level, alert_type):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT last_ts FROM alert_state "
        "WHERE symbol=? AND kind=? AND level=? AND alert_type=?",
        (symbol, kind, round(level, 2), alert_type)
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0.0

def set_last_alert(symbol, kind, level, alert_type, ts):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO alert_state "
        "(symbol, kind, level, alert_type, last_ts) VALUES (?, ?, ?, ?, ?)",
        (symbol, kind, round(level, 2), alert_type, ts)
    )
    conn.commit()
    conn.close()

def should_alert(symbol, kind, level, alert_type, cooldown):
    now = time.time()
    if now - get_last_alert(symbol, kind, level, alert_type) < cooldown:
        return False
    set_last_alert(symbol, kind, level, alert_type, now)
    return True

# ================== Binance ==================
def fetch_klines(symbol, interval="1h", limit=300):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(BINANCE_URL, params=params, timeout=15)
    r.raise_for_status()
    return [{
        "time":   k[0],
        "open":   float(k[1]),
        "high":   float(k[2]),
        "low":    float(k[3]),
        "close":  float(k[4]),
        "volume": float(k[5]),
    } for k in r.json()]

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

def cluster_levels(levels, tolerance_pct):
    if not levels:
        return []
    levels = sorted(levels, key=lambda x: x["price"])
    clusters = [[levels[0]]]
    for lvl in levels[1:]:
        cur = clusters[-1]
        avg = sum(x["price"] for x in cur) / len(cur)
        if abs(lvl["price"] - avg) / avg * 100 <= tolerance_pct:
            cur.append(lvl)
        else:
            clusters.append([lvl])
    return [{
        "price":     sum(x["price"] for x in cl) / len(cl),
        "touches":   len(cl),
        "last_time": max(x["time"] for x in cl),
        "source":    "pivot",
    } for cl in clusters]

# ================== Volume Profile ==================
def volume_profile(candles, buckets=60):
    if not candles:
        return None, None, None
    pmin = min(c["low"] for c in candles)
    pmax = max(c["high"] for c in candles)
    if pmax <= pmin:
        return None, None, None

    step = (pmax - pmin) / buckets
    vol = [0.0] * buckets

    for c in candles:
        lo, hi, v = c["low"], c["high"], c["volume"]
        if hi <= lo or v <= 0:
            idx = min(max(int((c["close"] - pmin) / step), 0), buckets - 1)
            vol[idx] += v
            continue
        b_start = max(int((lo - pmin) / step), 0)
        b_end   = min(int((hi - pmin) / step), buckets - 1)
        span = max(b_end - b_start + 1, 1)
        per = v / span
        for b in range(b_start, b_end + 1):
            vol[b] += per

    poc_idx = max(range(buckets), key=lambda i: vol[i])
    poc = pmin + (poc_idx + 0.5) * step

    total = sum(vol)
    target = total * 0.7
    included = {poc_idx}
    acc = vol[poc_idx]
    left, right = poc_idx - 1, poc_idx + 1
    while acc < target and (left >= 0 or right < buckets):
        lv = vol[left] if left >= 0 else -1
        rv = vol[right] if right < buckets else -1
        if rv >= lv and right < buckets:
            included.add(right); acc += vol[right]; right += 1
        elif left >= 0:
            included.add(left); acc += vol[left]; left -= 1
        elif right < buckets:
            included.add(right); acc += vol[right]; right += 1
        else:
            break

    vah = pmin + (max(included) + 1) * step
    val = pmin + min(included) * step
    return poc, vah, val

def merge_vp_into_zones(zones, vp_levels, tolerance_pct):
    for vp in vp_levels:
        merged = False
        for z in zones:
            if abs(z["price"] - vp["price"]) / z["price"] * 100 <= tolerance_pct:
                z["touches"] += 1
                z["source"] = z.get("source", "pivot") + "+" + vp["source"]
                merged = True
                break
        if not merged:
            zones.append({
                "price":     vp["price"],
                "touches":   max(MIN_TOUCHES, 2),
                "last_time": 0,
                "source":    vp["source"],
            })
    return zones

# ================== ADX ==================
def calc_adx(candles, period=14):
    if len(candles) < period * 2 + 2:
        return None
    trs, pdms, mdms = [], [], []
    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        ph, pl, pc = candles[i-1]["high"], candles[i-1]["low"], candles[i-1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        up = h - ph
        dn = pl - l
        pdm = up if (up > dn and up > 0) else 0.0
        mdm = dn if (dn > up and dn > 0) else 0.0
        trs.append(tr); pdms.append(pdm); mdms.append(mdm)

    def wilder(vals, n):
        out = [sum(vals[:n])]
        for v in vals[n:]:
            out.append(out[-1] - out[-1] / n + v)
        return out

    atr = wilder(trs, period)
    pdm_s = wilder(pdms, period)
    mdm_s = wilder(mdms, period)

    dxs = []
    for a, p, m in zip(atr, pdm_s, mdm_s):
        if a == 0:
            dxs.append(0.0); continue
        pdi = 100 * p / a
        mdi = 100 * m / a
        denom = pdi + mdi
        dxs.append(100 * abs(pdi - mdi) / denom if denom > 0 else 0.0)

    if len(dxs) < period:
        return None
    adx = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        adx = (adx * (period - 1) + dx) / period
    return adx

# ================== تأكيد 5 دقائق ==================
def check_rejection_5m(symbol, side, level, proximity_pct=0.6):
    try:
        candles = fetch_klines(symbol, CONFIRM_TIMEFRAME, 20)
    except Exception as e:
        log.warning(f"{symbol}: فشل جلب {CONFIRM_TIMEFRAME}: {e}")
        return False, "فشل جلب البيانات"

    if len(candles) < 3:
        return False, "بيانات غير كافية"

    last = candles[-2]
    prev = candles[-3]

    body = abs(last["close"] - last["open"])
    upper_wick = last["high"] - max(last["open"], last["close"])
    lower_wick = min(last["open"], last["close"]) - last["low"]

    touched = (last["low"] <= level <= last["high"]) or \
              (abs(last["close"] - level) / level * 100 <= proximity_pct)
    if not touched:
        return False, "لم تلمس الشمعة المستوى"

    if side == "RES":
        if body > 0 and upper_wick / body >= WICK_BODY_RATIO and last["close"] < last["open"]:
            return True, "Pin Bar هابطة (ظل علوي طويل)"
        if (last["close"] < last["open"] and prev["close"] > prev["open"]
                and last["open"] >= prev["close"] and last["close"] <= prev["open"]):
            return True, "Bearish Engulfing"
    else:
        if body > 0 and lower_wick / body >= WICK_BODY_RATIO and last["close"] > last["open"]:
            return True, "Pin Bar صاعدة (ظل سفلي طويل)"
        if (last["close"] > last["open"] and prev["close"] < prev["open"]
                and last["open"] <= prev["close"] and last["close"] >= prev["open"]):
            return True, "Bullish Engulfing"

    return False, "لا يوجد نمط رفض"

# ================== تلغرام ==================
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

# ================== بناء الرسالة ==================
def build_message(alert_type, symbol, label, zone, current_price, dist,
                  range_width=None, rr=None, adx=None,
                  confirm_text=None, confirm_reason=None):
    header = "🚨 <b>تنبيه دخول</b>" if alert_type == "ENTRY" else "⏳ <b>إنذار مبكر</b>"

    lines = [
        header,
        f"🕐 <b>الوقت:</b> {local_now_str()} ({TIMEZONE})",
        "",
        f"<b>العملة:</b> {symbol}",
        f"<b>النوع:</b> {label}",
        f"<b>الإطار:</b> {TIMEFRAME}",
        f"<b>المستوى:</b> {zone['price']:.4f}",
        f"<b>المصدر:</b> {zone.get('source', 'pivot')}",
        f"<b>السعر الحالي:</b> {current_price:.4f}",
        f"<b>المسافة:</b> {dist:.2f}%",
        f"<b>عدد اللمسات:</b> {zone['touches']}",
    ]

    if zone.get("last_time"):
        lines.append(f"<b>آخر لمسة للمستوى:</b> {ms_to_local_str(zone['last_time'])}")

    if adx is not None:
        lines.append(f"<b>ADX:</b> {adx:.1f}")
    if range_width is not None:
        lines.append(f"<b>عرض النطاق:</b> {range_width:.2f}%")
    if rr is not None:
        lines.append(f"<b>R:R المتوقع:</b> {rr:.2f}")
    if confirm_text:
        lines.append("")
        lines.append(f"<b>تأكيد {CONFIRM_TIMEFRAME}:</b> {confirm_text}")
    return "\n".join(lines)

# ================== منطق الفحص ==================
def check_symbol(symbol):
    candles = fetch_klines(symbol, TIMEFRAME, 300)
    if len(candles) < 50:
        return

    current_price = candles[-1]["close"]

    highs, lows = find_pivots(candles, PIVOT_LEFT, PIVOT_RIGHT)
    res_zones = cluster_levels(highs, CLUSTER_TOLERANCE)
    sup_zones = cluster_levels(lows,  CLUSTER_TOLERANCE)

    if USE_VOLUME_PROFILE:
        poc, vah, val = volume_profile(candles, VP_BUCKETS)
        vp_levels = []
        if poc: vp_levels.append({"price": poc, "source": "POC"})
        if vah: vp_levels.append({"price": vah, "source": "VAH"})
        if val: vp_levels.append({"price": val, "source": "VAL"})
        res_zones = merge_vp_into_zones(res_zones, vp_levels, CLUSTER_TOLERANCE)
        sup_zones = merge_vp_into_zones(sup_zones, vp_levels, CLUSTER_TOLERANCE)

    res_zones = [z for z in res_zones
                 if z["touches"] >= MIN_TOUCHES and z["price"] > current_price]
    sup_zones = [z for z in sup_zones
                 if z["touches"] >= MIN_TOUCHES and z["price"] < current_price]

    nearest_res = min(res_zones, key=lambda z: z["price"] - current_price) if res_zones else None
    nearest_sup = max(sup_zones, key=lambda z: z["price"]) if sup_zones else None

    res_dist = (nearest_res["price"] - current_price) / current_price * 100 if nearest_res else 999.0
    sup_dist = (current_price - nearest_sup["price"]) / current_price * 100 if nearest_sup else 999.0

    if res_dist <= sup_dist:
        side, label, zone, dist = "RES", "🟥 مقاومة", nearest_res, res_dist
    else:
        side, label, zone, dist = "SUP", "🟩 دعم", nearest_sup, sup_dist

    if zone is None:
        return

    range_width = None
    if nearest_res and nearest_sup:
        range_width = (nearest_res["price"] - nearest_sup["price"]) / current_price * 100

    adx_val = calc_adx(candles[-100:], ADX_PERIOD) if USE_ADX_FILTER else None

    # ========= المسار 1: تنبيه دخول =========
    if dist <= ENTRY_PCT:
        if range_width is not None and range_width < MIN_RANGE_WIDTH:
            log.info(f"{symbol}: نطاق ضيق ({range_width:.2f}%) - تجاهل")
            return

        if USE_ADX_FILTER and adx_val is not None and adx_val > ADX_MAX_FOR_ENTRY:
            log.info(f"{symbol}: ADX مرتفع ({adx_val:.1f}) - ترند قوي، تجاهل")
            return

        opposite = nearest_sup if side == "RES" else nearest_res
        rr = None
        if opposite is not None:
            reward = abs(zone["price"] - opposite["price"]) / current_price * 100
            risk = STOP_BUFFER_PCT + dist
            rr = reward / risk if risk > 0 else 0
            if rr < MIN_RR:
                log.info(f"{symbol}: R:R ضعيف ({rr:.2f}) - تجاهل")
                return

        confirm_reason = None
        if USE_5M_CONFIRM:
            ok, confirm_reason = check_rejection_5m(symbol, side, zone["price"])
            if not ok:
                log.info(f"{symbol}: فشل تأكيد {CONFIRM_TIMEFRAME} ({confirm_reason})")
                return

        if should_alert(symbol, side, zone["price"], "ENTRY", COOLDOWN_ENTRY):
            msg = build_message("ENTRY", symbol, label, zone, current_price,
                                dist, range_width, rr, adx_val,
                                confirm_text=f"✅ {confirm_reason}" if confirm_reason else None)
            send_telegram(msg)
            log.info(f"ENTRY: {symbol} {label} @ {zone['price']:.4f} "
                     f"dist={dist:.2f}% rr={rr} adx={adx_val} conf={confirm_reason}")
        return

    # ========= المسار 2: إنذار مبكر =========
    if USE_EARLY_ALERT and dist <= EARLY_PCT:
        if should_alert(symbol, side, zone["price"], "EARLY", COOLDOWN_EARLY):
            msg = build_message("EARLY", symbol, label, zone, current_price,
                                dist, range_width, None, adx_val)
            send_telegram(msg)
            log.info(f"EARLY: {symbol} {label} @ {zone['price']:.4f} dist={dist:.2f}%")

# ================== Health Check ==================
class HealthHandler(BaseHTTPRequestHandler):
    def _headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self):
        self._headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self._headers()

    def log_message(self, *args, **kwargs):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    log.info(f"Health server on 0.0.0.0:{port} (GET+HEAD)")
    server.serve_forever()

# ================== الحلقة الرئيسية ==================
def main_loop():
    send_telegram(
        f"✅ البوت بدأ العمل\n"
        f"🕐 {local_now_str()} ({TIMEZONE})\n"
        f"العملات: {len(SYMBOLS)}\n"
        f"ADX Filter: {USE_ADX_FILTER} (max {ADX_MAX_FOR_ENTRY})\n"
        f"تأكيد {CONFIRM_TIMEFRAME}: {USE_5M_CONFIRM}\n"
        f"Volume Profile: {USE_VOLUME_PROFILE}\n"
        f"الإنذار المبكر: {USE_EARLY_ALERT}"
    )
    log.info(f"Bot started | symbols={len(SYMBOLS)} tf={TIMEFRAME} "
             f"tz={TIMEZONE} early={USE_EARLY_ALERT}")

    while True:
        for symbol in SYMBOLS:
            try:
                check_symbol(symbol)
            except Exception as e:
                log.exception(f"Error on {symbol}: {e}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    init_db()
    threading.Thread(target=run_health_server, daemon=True).start()
    main_loop()
