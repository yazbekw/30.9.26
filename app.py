"""
لوحة تحليل الأصول الاحترافية
- رسمان بيانيان جنباً إلى جنب
- مؤشرات: SMA, EMA, Bollinger, VWAP, S/R (نقاط), Fibonacci, Pivot, Volume, RSI, MACD
- مصدران: Binance (عملات رقمية) + Yahoo Finance (ذهب، فضة، نفط)
- إطار زمني قابل للاختيار مع نافذة Pivot تلقائية
- تحديث تلقائي كل 5 ثوانٍ
"""
import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ============ إعدادات الصفحة ============
st.set_page_config(
    page_title="لوحة تحليل الأصول",
    layout="wide",
    page_icon="📊",
    initial_sidebar_state="collapsed",
)

# ============ قائمة الأصول ============
SYMBOLS = {
    "BTC (بيتكوين)":   {"code": "BTCUSDT",  "source": "binance", "decimals": 2},
    "ETH (إيثيريوم)":   {"code": "ETHUSDT",  "source": "binance", "decimals": 2},
    "BNB (بينانس)":     {"code": "BNBUSDT",  "source": "binance", "decimals": 2},
    "SOL (سولانا)":     {"code": "SOLUSDT",  "source": "binance", "decimals": 3},
    "XRP (ريبل)":       {"code": "XRPUSDT",  "source": "binance", "decimals": 4},
    "DOGE (دوجكوين)":   {"code": "DOGEUSDT", "source": "binance", "decimals": 5},
    "XAU (الذهب)":      {"code": "GC=F",     "source": "yahoo",   "decimals": 2},
    "XAG (الفضة)":      {"code": "SI=F",     "source": "yahoo",   "decimals": 3},
    "WTI (النفط)":      {"code": "CL=F",     "source": "yahoo",   "decimals": 2},
}

# ============ الأطر الزمنية ============
TIMEFRAMES = {
    "1 دقيقة":  {"binance": "1m",  "yahoo": "1m",  "range": "1d",  "window": 15, "limit": 400},
    "5 دقائق":  {"binance": "5m",  "yahoo": "5m",  "range": "5d",  "window": 10, "limit": 400},
    "15 دقيقة": {"binance": "15m", "yahoo": "15m", "range": "5d",  "window": 8,  "limit": 400},
    "30 دقيقة": {"binance": "30m", "yahoo": "30m", "range": "1mo", "window": 6,  "limit": 400},
    "1 ساعة":   {"binance": "1h",  "yahoo": "60m", "range": "1mo", "window": 5,  "limit": 400},
    "4 ساعات":  {"binance": "4h",  "yahoo": "1d",  "range": "3mo", "window": 5,  "limit": 400},
    "يومي":     {"binance": "1d",  "yahoo": "1d",  "range": "1y",  "window": 3,  "limit": 400},
}

ALL_INDICATORS = [
    "SMA (متوسط بسيط)",
    "EMA (متوسط أسّي)",
    "Bollinger Bands",
    "VWAP",
    "الدعم والمقاومة",
    "Fibonacci Retracement",
    "Pivot Points",
    "Volume (الحجم)",
    "RSI",
    "MACD",
]

DEFAULT_ON = {"SMA (متوسط بسيط)", "Bollinger Bands", "الدعم والمقاومة", "RSI", "Volume (الحجم)"}


# ============ جلب البيانات ============
@st.cache_data(ttl=5, show_spinner=False)
def fetch_binance(symbol: str, interval: str = "15m", limit: int = 400) -> pd.DataFrame:
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    df = pd.DataFrame(data, columns=[
        "time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df[["time", "open", "high", "low", "close", "volume"]]


@st.cache_data(ttl=5, show_spinner=False)
def fetch_yahoo(symbol: str, interval: str = "15m", range_: str = "5d") -> pd.DataFrame:
    """جلب بيانات الذهب/الفضة/النفط من Yahoo Finance بدون مكتبة yfinance"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"interval": interval, "range": range_}
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, params=params, headers=headers, timeout=10)
    r.raise_for_status()
    data = r.json()
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    df = pd.DataFrame({
        "time": pd.to_datetime(timestamps, unit="s"),
        "open": quote["open"],
        "high": quote["high"],
        "low": quote["low"],
        "close": quote["close"],
        "volume": quote["volume"],
    })
    df = df.dropna().reset_index(drop=True)
    return df


def fetch_data(symbol_info: dict, tf_config: dict) -> pd.DataFrame:
    if symbol_info["source"] == "binance":
        return fetch_binance(
            symbol_info["code"],
            interval=tf_config["binance"],
            limit=tf_config["limit"],
        )
    else:
        return fetch_yahoo(
            symbol_info["code"],
            interval=tf_config["yahoo"],
            range_=tf_config["range"],
        )


def get_price_stats(df: pd.DataFrame) -> dict:
    last = float(df["close"].iloc[-1])
    first = float(df["close"].iloc[0])
    change_pct = ((last - first) / first) * 100 if first else 0.0
    return {
        "last": last,
        "change_pct": change_pct,
        "high": float(df["high"].max()),
        "low": float(df["low"].min()),
        "volume": float(df["volume"].sum()),
    }


# ============ المؤشرات ============
def sma(s, p):
    return s.rolling(p).mean()


def ema(s, p):
    return s.ewm(span=p, adjust=False).mean()


def bollinger(s, period=20, k=2):
    m = s.rolling(period).mean()
    sd = s.rolling(period).std()
    return m + k * sd, m, m - k * sd


def rsi(s, period=14):
    delta = s.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(s, fast=12, slow=26, signal=9):
    line = ema(s, fast) - ema(s, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def vwap(df):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).cumsum() / df["volume"].cumsum()


# ============ الدعم والمقاومة كنقاط ============
def find_pivots(df: pd.DataFrame, window: int = 8):
    """إيجاد نقاط الانعكاس (Fractal) — نافذة قابلة للتعديل"""
    highs = []
    lows = []
    h = df["high"].values
    l = df["low"].values
    n = len(df)
    for i in range(window, n - window):
        if h[i] == max(h[i - window:i + window + 1]):
            highs.append({"time": df["time"].iloc[i], "price": float(h[i])})
        if l[i] == min(l[i - window:i + window + 1]):
            lows.append({"time": df["time"].iloc[i], "price": float(l[i])})
    return highs, lows


def fib_levels(df, lookback=120):
    recent = df.tail(lookback)
    hi, lo = float(recent["high"].max()), float(recent["low"].min())
    diff = hi - lo
    return {
        "0.0%":  hi,
        "23.6%": hi - 0.236 * diff,
        "38.2%": hi - 0.382 * diff,
        "50.0%": hi - 0.500 * diff,
        "61.8%": hi - 0.618 * diff,
        "78.6%": hi - 0.786 * diff,
        "100%":  lo,
    }


def pivot_points(df):
    d = df.copy()
    d["date"] = d["time"].dt.date
    daily = d.groupby("date").agg({"high": "max", "low": "min", "close": "last"}).reset_index()
    daily["P"]  = (daily["high"] + daily["low"] + daily["close"]) / 3
    daily["R1"] = 2 * daily["P"] - daily["low"]
    daily["S1"] = 2 * daily["P"] - daily["high"]
    daily["R2"] = daily["P"] + (daily["high"] - daily["low"])
    daily["S2"] = daily["P"] - (daily["high"] - daily["low"])
    daily["R3"] = daily["high"] + 2 * (daily["P"] - daily["low"])
    daily["S3"] = daily["low"]  - 2 * (daily["high"] - daily["P"])
    return daily


# ============ بناء الرسم البياني ============
def build_chart(df: pd.DataFrame, title: str, indicators: set,
                decimals: int = 2, pivot_window: int = 8) -> go.Figure:
    show_vol  = "Volume (الحجم)" in indicators
    show_rsi  = "RSI" in indicators
    show_macd = "MACD" in indicators
    show_sr   = "الدعم والمقاومة" in indicators

    # --- إعداد الصفوف ---
    rows = 1
    heights = [1.0]
    if show_vol:
        rows += 1; heights.append(0.28)
    if show_rsi:
        rows += 1; heights.append(0.25)
    if show_macd:
        rows += 1; heights.append(0.25)
    total = sum(heights)
    heights = [h / total for h in heights]

    fig = make_subplots(
        rows=rows, cols=1, shared_xaxes=True,
        vertical_spacing=0.04, row_heights=heights,
    )

    # --- الشموع ---
    fig.add_trace(go.Candlestick(
        x=df["time"], open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name="Price",
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        increasing_fillcolor="#26a69a", decreasing_fillcolor="#ef5350",
    ), row=1, col=1)

    # --- SMA ---
    if "SMA (متوسط بسيط)" in indicators:
        fig.add_trace(go.Scatter(x=df["time"], y=sma(df["close"], 20),
                                 name="SMA 20",
                                 line=dict(color="#ffeb3b", width=1.2)),
                      row=1, col=1)
        fig.add_trace(go.Scatter(x=df["time"], y=sma(df["close"], 50),
                                 name="SMA 50",
                                 line=dict(color="#ff9800", width=1.2)),
                      row=1, col=1)

    # --- EMA ---
    if "EMA (متوسط أسّي)" in indicators:
        fig.add_trace(go.Scatter(x=df["time"], y=ema(df["close"], 20),
                                 name="EMA 20",
                                 line=dict(color="#00bcd4", width=1.2)),
                      row=1, col=1)
        fig.add_trace(go.Scatter(x=df["time"], y=ema(df["close"], 50),
                                 name="EMA 50",
                                 line=dict(color="#9c27b0", width=1.2)),
                      row=1, col=1)

    # --- Bollinger ---
    if "Bollinger Bands" in indicators:
        up, mid, lo = bollinger(df["close"])
        fig.add_trace(go.Scatter(x=df["time"], y=up, name="BB Upper",
                                 line=dict(color="rgba(120,150,255,0.6)", width=1)),
                      row=1, col=1)
        fig.add_trace(go.Scatter(x=df["time"], y=lo, name="BB Lower",
                                 line=dict(color="rgba(120,150,255,0.6)", width=1),
                                 fill="tonexty",
                                 fillcolor="rgba(120,150,255,0.07)"),
                      row=1, col=1)

    # --- VWAP ---
    if "VWAP" in indicators:
        try:
            fig.add_trace(go.Scatter(x=df["time"], y=vwap(df), name="VWAP",
                                     line=dict(color="#e91e63", width=1.6)),
                          row=1, col=1)
        except Exception:
            pass

    # --- 🔴🟢 الدعم والمقاومة كنقاط ---
    if show_sr:
        highs, lows = find_pivots(df, window=pivot_window)

        if highs:
            fig.add_trace(go.Scatter(
                x=[h["time"] for h in highs],
                y=[h["price"] * 1.0015 for h in highs],
                mode="markers",
                name=f"🔴 مقاومة ({len(highs)})",
                marker=dict(
                    symbol="triangle-down",
                    size=13,
                    color="#ef5350",
                    line=dict(color="white", width=1.5),
                ),
                hovertemplate="<b>مقاومة</b><br>السعر: %{y:.4f}<extra></extra>",
            ), row=1, col=1)

        if lows:
            fig.add_trace(go.Scatter(
                x=[l["time"] for l in lows],
                y=[l["price"] * 0.9985 for l in lows],
                mode="markers",
                name=f"🟢 دعم ({len(lows)})",
                marker=dict(
                    symbol="triangle-up",
                    size=13,
                    color="#26a69a",
                    line=dict(color="white", width=1.5),
                ),
                hovertemplate="<b>دعم</b><br>السعر: %{y:.4f}<extra></extra>",
            ), row=1, col=1)

    # --- Fibonacci ---
    if "Fibonacci Retracement" in indicators:
        fib = fib_levels(df)
        colors = ["#ef5350", "#ff9800", "#ffeb3b", "#8bc34a",
                  "#26a69a", "#00bcd4", "#3f51b5"]
        for (label, val), col in zip(fib.items(), colors):
            fig.add_hline(y=val, line_dash="dot", line_color=col, opacity=0.35,
                          annotation_text=f"Fib {label}",
                          annotation_position="left",
                          annotation_font_size=9,
                          row=1, col=1)

    # --- Pivot Points ---
    if "Pivot Points" in indicators:
        try:
            daily = pivot_points(df)
            if len(daily) >= 2:
                prev = daily.iloc[-2]
                lvls = [("P", "yellow"), ("R1", "#ef5350"), ("R2", "#ef5350"),
                        ("R3", "#ef5350"), ("S1", "#26a69a"), ("S2", "#26a69a"),
                        ("S3", "#26a69a")]
                for name, col in lvls:
                    fig.add_hline(y=prev[name], line_dash="dashdot",
                                  line_color=col, opacity=0.3,
                                  annotation_text=f"{name}: {prev[name]:.4f}",
                                  annotation_position="right",
                                  annotation_font_size=9,
                                  row=1, col=1)
        except Exception:
            pass

    # ============ الصفوف السفلية ============
    cur_row = 2

    # --- Volume (صف منفصل) ---
    if show_vol:
        vol_colors = ["#26a69a" if c >= o else "#ef5350"
                      for c, o in zip(df["close"], df["open"])]
        fig.add_trace(go.Bar(
            x=df["time"], y=df["volume"], name="Volume",
            marker_color=vol_colors, opacity=0.7,
            hovertemplate="الحجم: %{y:,.0f}<extra></extra>",
        ), row=cur_row, col=1)
        fig.update_yaxes(
            title_text="الحجم", row=cur_row, col=1,
            showgrid=True, gridcolor="rgba(255,255,255,0.05)",
            title_font=dict(size=10),
        )
        cur_row += 1

    # --- RSI ---
    if show_rsi:
        fig.add_trace(go.Scatter(x=df["time"], y=rsi(df["close"]),
                                 name="RSI",
                                 line=dict(color="#ba68c8", width=1.5)),
                      row=cur_row, col=1)
        fig.add_hline(y=70, line_dash="dash", line_color="red", opacity=0.5,
                      row=cur_row, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="green", opacity=0.5,
                      row=cur_row, col=1)
        fig.update_yaxes(
            range=[0, 100], title_text="RSI", row=cur_row, col=1,
            showgrid=True, gridcolor="rgba(255,255,255,0.05)",
            title_font=dict(size=10),
        )
        cur_row += 1

    # --- MACD ---
    if show_macd:
        line, sig, hist = macd(df["close"])
        fig.add_trace(go.Scatter(x=df["time"], y=line, name="MACD",
                                 line=dict(color="#42a5f5", width=1.4)),
                      row=cur_row, col=1)
        fig.add_trace(go.Scatter(x=df["time"], y=sig, name="Signal",
                                 line=dict(color="#ffa726", width=1.4)),
                      row=cur_row, col=1)
        fig.add_trace(go.Bar(x=df["time"], y=hist, name="Histogram",
                             marker_color=["#26a69a" if v >= 0 else "#ef5350"
                                           for v in hist], opacity=0.6),
                      row=cur_row, col=1)
        fig.update_yaxes(
            title_text="MACD", row=cur_row, col=1,
            showgrid=True, gridcolor="rgba(255,255,255,0.05)",
            title_font=dict(size=10),
        )

    # ============ التنسيق النهائي ============
    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    padding = (price_max - price_min) * 0.05
    fig.update_yaxes(
        range=[price_min - padding, price_max + padding],
        row=1, col=1, title_text="السعر",
        showgrid=True, gridcolor="rgba(255,255,255,0.05)",
    )

    fig.update_layout(
        template="plotly_dark",
        title=dict(text=title, x=0.5, xanchor="center", font=dict(size=15)),
        height=850,
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        margin=dict(l=10, r=60, t=60, b=10),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.01,
            xanchor="right", x=1, font=dict(size=10),
            bgcolor="rgba(0,0,0,0.3)",
        ),
        dragmode="pan",
        showlegend=True,
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.05)")
    return fig


# ============ لوحة عملة واحدة ============
def render_panel(panel_id: int, default_symbol: str):
    # --- اختيار الأصل والإطار ---
    col_a, col_b = st.columns([2, 1])
    with col_a:
        symbol_label = st.selectbox(
            f"🔸 الأصل — اللوحة {panel_id}",
            options=list(SYMBOLS.keys()),
            index=list(SYMBOLS.keys()).index(default_symbol),
            key=f"symbol_{panel_id}",
        )
    with col_b:
        tf_label = st.selectbox(
            "⏱ الإطار",
            options=list(TIMEFRAMES.keys()),
            index=list(TIMEFRAMES.keys()).index("15 دقيقة"),
            key=f"tf_{panel_id}",
        )

    symbol_info = SYMBOLS[symbol_label]
    tf_config = TIMEFRAMES[tf_label]

    # --- المؤشرات ---
    st.markdown("**🔧 المؤشرات**")
    selected = set()
    cols = st.columns(5)
    for i, ind in enumerate(ALL_INDICATORS):
        with cols[i % 5]:
            if st.checkbox(
                ind,
                value=(ind in DEFAULT_ON),
                key=f"ind_{panel_id}_{ind}",
            ):
                selected.add(ind)

    # --- جلب البيانات ---
    try:
        df = fetch_data(symbol_info, tf_config)
        if df.empty:
            st.error(f"لا توجد بيانات لـ {symbol_label}")
            return
        stats = get_price_stats(df)
    except Exception as e:
        st.error(f"تعذر جلب البيانات: {e}")
        return

    # --- السعر والتغير ---
    d = symbol_info["decimals"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("آخر سعر", f"{stats['last']:,.{d}f}",
              f"{stats['change_pct']:+.2f}%")
    m2.metric("أعلى", f"{stats['high']:,.{d}f}")
    m3.metric("أدنى", f"{stats['low']:,.{d}f}")
    m4.metric("حجم", f"{stats['volume']:,.2f}")

    # --- الرسم البياني ---
    fig = build_chart(
        df,
        f"{symbol_label}  —  {symbol_info['code']}  ({tf_label})",
        selected,
        decimals=d,
        pivot_window=tf_config["window"],
    )
    st.plotly_chart(
        fig,
        width="stretch",
        key=f"chart_{panel_id}",
        config={"displaylogo": False, "scrollZoom": True},
    )


# ============ الواجهة الرئيسية ============
@st.fragment(run_every=5)
def dashboard():
    st.markdown(
        "<h2 style='text-align:center;margin-bottom:0;'>📊 لوحة تحليل الأصول</h2>"
        "<p style='text-align:center;color:#888;margin-top:0;'>"
        "تحديث تلقائي كل 5 ثوانٍ • 🔴 مقاومة • 🟢 دعم</p>",
        unsafe_allow_html=True,
    )
    col1, col2 = st.columns(2, gap="medium")
    with col1:
        render_panel(1, "BTC (بيتكوين)")
    with col2:
        render_panel(2, "XAU (الذهب)")


dashboard()
