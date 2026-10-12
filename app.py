"""
لوحة تحليل العملات الرقمية الاحترافية
- رسمان بيانيان جنباً إلى جنب لعملتين مختلفتين
- مؤشرات: SMA, EMA, Bollinger, VWAP, S/R, Fibonacci, Pivot, Volume, RSI, MACD
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
    page_title="لوحة تحليل العملات الرقمية",
    layout="wide",
    page_icon="📊",
    initial_sidebar_state="collapsed",
)

# ============ ثوابت ============
SYMBOLS = {
    "BTC (بيتكوين)":   "BTCUSDT",
    "ETH (إيثيريوم)":   "ETHUSDT",
    "BNB (بينانس)":     "BNBUSDT",
    "SOL (سولانا)":     "SOLUSDT",
    "XRP (ريبل)":       "XRPUSDT",
    "DOGE (دوجكوين)":   "DOGEUSDT",
    "XAU (الذهب/PAXG)": "PAXGUSDT",
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
@st.cache_data(ttl=4, show_spinner=False)
def fetch_klines(symbol: str, interval: str = "1m", limit: int = 400) -> pd.DataFrame:
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


@st.cache_data(ttl=4, show_spinner=False)
def fetch_ticker(symbol: str) -> dict:
    url = "https://api.binance.com/api/v3/ticker/24hr"
    r = requests.get(url, params={"symbol": symbol}, timeout=10)
    return r.json()


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


def support_resistance(df, lookback=60):
    recent = df.tail(lookback)
    return float(recent["low"].min()), float(recent["high"].max())


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
def build_chart(df: pd.DataFrame, title: str, indicators: set) -> go.Figure:
    show_rsi  = "RSI" in indicators
    show_macd = "MACD" in indicators

    rows = 1
    heights = [0.70]
    if show_rsi:
        rows += 1
        heights.append(0.15)
    if show_macd:
        rows += 1
        heights.append(0.15)
    total = sum(heights)
    heights = [h / total for h in heights]

    fig = make_subplots(
        rows=rows, cols=1, shared_xaxes=True,
        vertical_spacing=0.03, row_heights=heights,
    )

    # --- شموع ---
    fig.add_trace(go.Candlestick(
        x=df["time"], open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name="Price",
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
    ), row=1, col=1)

    # --- SMA ---
    if "SMA (متوسط بسيط)" in indicators:
        fig.add_trace(go.Scatter(x=df["time"], y=sma(df["close"], 20),
                                 name="SMA 20", line=dict(color="#ffeb3b", width=1.2)),
                      row=1, col=1)
        fig.add_trace(go.Scatter(x=df["time"], y=sma(df["close"], 50),
                                 name="SMA 50", line=dict(color="#ff9800", width=1.2)),
                      row=1, col=1)

    # --- EMA ---
    if "EMA (متوسط أسّي)" in indicators:
        fig.add_trace(go.Scatter(x=df["time"], y=ema(df["close"], 20),
                                 name="EMA 20", line=dict(color="#00bcd4", width=1.2)),
                      row=1, col=1)
        fig.add_trace(go.Scatter(x=df["time"], y=ema(df["close"], 50),
                                 name="EMA 50", line=dict(color="#9c27b0", width=1.2)),
                      row=1, col=1)

    # --- Bollinger ---
    if "Bollinger Bands" in indicators:
        up, mid, lo = bollinger(df["close"])
        fig.add_trace(go.Scatter(x=df["time"], y=up, name="BB Upper",
                                 line=dict(color="rgba(120,150,255,0.6)", width=1)),
                      row=1, col=1)
        fig.add_trace(go.Scatter(x=df["time"], y=lo, name="BB Lower",
                                 line=dict(color="rgba(120,150,255,0.6)", width=1),
                                 fill="tonexty", fillcolor="rgba(120,150,255,0.07)"),
                      row=1, col=1)

    # --- VWAP ---
    if "VWAP" in indicators:
        fig.add_trace(go.Scatter(x=df["time"], y=vwap(df), name="VWAP",
                                 line=dict(color="#e91e63", width=1.6)),
                      row=1, col=1)

    # --- S/R ---
    if "الدعم والمقاومة" in indicators:
        s, r = support_resistance(df)
        fig.add_hline(y=s, line_dash="dash", line_color="#26a69a", line_width=1.5,
                      annotation_text=f"دعم {s:.4f}", annotation_position="right",
                      row=1, col=1)
        fig.add_hline(y=r, line_dash="dash", line_color="#ef5350", line_width=1.5,
                      annotation_text=f"مقاومة {r:.4f}", annotation_position="right",
                      row=1, col=1)

    # --- Fibonacci ---
    if "Fibonacci Retracement" in indicators:
        fib = fib_levels(df)
        colors = ["#ef5350", "#ff9800", "#ffeb3b", "#8bc34a",
                  "#26a69a", "#00bcd4", "#3f51b5"]
        for (label, val), col in zip(fib.items(), colors):
            fig.add_hline(y=val, line_dash="dot", line_color=col, opacity=0.5,
                          annotation_text=f"Fib {label}", annotation_position="left",
                          row=1, col=1)

    # --- Pivot ---
    if "Pivot Points" in indicators:
        daily = pivot_points(df)
        if len(daily) >= 2:
            prev = daily.iloc[-2]
            lvls = [("P", "yellow"), ("R1", "#ef5350"), ("R2", "#ef5350"),
                    ("R3", "#ef5350"), ("S1", "#26a69a"), ("S2", "#26a69a"),
                    ("S3", "#26a69a")]
            for name, col in lvls:
                fig.add_hline(y=prev[name], line_dash="dashdot", line_color=col,
                              opacity=0.4,
                              annotation_text=f"{name}: {prev[name]:.4f}",
                              annotation_position="right", row=1, col=1)

    # --- Volume ---
    if "Volume (الحجم)" in indicators:
        vol_colors = ["#26a69a" if c >= o else "#ef5350"
                      for c, o in zip(df["close"], df["open"])]
        fig.add_trace(go.Bar(x=df["time"], y=df["volume"], name="Volume",
                             marker_color=vol_colors, opacity=0.35, yaxis="y2"),
                      row=1, col=1)

    # --- RSI ---
    cur = 2
    if show_rsi:
        fig.add_trace(go.Scatter(x=df["time"], y=rsi(df["close"]), name="RSI",
                                 line=dict(color="#ba68c8", width=1.5)),
                      row=cur, col=1)
        fig.add_hline(y=70, line_dash="dash", line_color="red", opacity=0.5,
                      row=cur, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="green", opacity=0.5,
                      row=cur, col=1)
        fig.update_yaxes(range=[0, 100], title_text="RSI", row=cur, col=1)
        cur += 1

    # --- MACD ---
    if show_macd:
        line, sig, hist = macd(df["close"])
        fig.add_trace(go.Scatter(x=df["time"], y=line, name="MACD",
                                 line=dict(color="#42a5f5", width=1.4)),
                      row=cur, col=1)
        fig.add_trace(go.Scatter(x=df["time"], y=sig, name="Signal",
                                 line=dict(color="#ffa726", width=1.4)),
                      row=cur, col=1)
        fig.add_trace(go.Bar(x=df["time"], y=hist, name="Histogram",
                             marker_color=["#26a69a" if v >= 0 else "#ef5350"
                                           for v in hist], opacity=0.6),
                      row=cur, col=1)
        fig.update_yaxes(title_text="MACD", row=cur, col=1)

    # --- التنسيق النهائي ---
    fig.update_layout(
        template="plotly_dark",
        title=dict(text=title, x=0.5, xanchor="center", font=dict(size=16)),
        height=720,
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        margin=dict(l=10, r=10, t=50, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.01,
                    xanchor="right", x=1, font=dict(size=10)),
        dragmode="pan",
    )
    if "Volume (الحجم)" in indicators:
        fig.update_layout(
            yaxis2=dict(overlaying="y", side="right", showgrid=False,
                        range=[0, df["volume"].max() * 4.5],
                        showticklabels=False)
        )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.05)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.05)")
    return fig


# ============ لوحة عملة واحدة ============
def render_panel(panel_id: int, default_symbol: str):
    symbol_label = st.selectbox(
        f"🔸 اختر العملة — اللوحة {panel_id}",
        options=list(SYMBOLS.keys()),
        index=list(SYMBOLS.keys()).index(default_symbol),
        key=f"symbol_{panel_id}",
    )
    symbol_code = SYMBOLS[symbol_label]

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

    try:
        df = fetch_klines(symbol_code, interval="1m", limit=300)
        ticker = fetch_ticker(symbol_code)
    except Exception as e:
        st.error(f"تعذر جلب البيانات: {e}")
        return

    last_price = float(ticker.get("lastPrice", df["close"].iloc[-1]))
    change_pct = float(ticker.get("priceChangePercent", 0.0))
    high_24 = float(ticker.get("highPrice", df["high"].max()))
    low_24  = float(ticker.get("lowPrice",  df["low"].min()))
    vol_24  = float(ticker.get("quoteVolume", 0.0))

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("آخر سعر", f"{last_price:,.4f}", f"{change_pct:+.2f}%")
    m2.metric("أعلى 24س", f"{high_24:,.4f}")
    m3.metric("أدنى 24س", f"{low_24:,.4f}")
    m4.metric("حجم 24س (USDT)", f"{vol_24/1e6:,.2f}M")

    fig = build_chart(df, f"{symbol_label}  —  USDT (1m)", selected)
    st.plotly_chart(
        fig,
        width='stretch',
        key=f"chart_{panel_id}",
        config={"displaylogo": False, "scrollZoom": True},
    )


# ============ الواجهة الرئيسية ============
@st.fragment(run_every=5)
def dashboard():
    st.markdown(
        "<h2 style='text-align:center;margin-bottom:0;'>📊 لوحة تحليل العملات الرقمية</h2>"
        "<p style='text-align:center;color:#888;margin-top:0;'>تحديث تلقائي كل 5 ثوانٍ</p>",
        unsafe_allow_html=True,
    )
    col1, col2 = st.columns(2, gap="medium")
    with col1:
        render_panel(1, "BTC (بيتكوين)")
    with col2:
        render_panel(2, "ETH (إيثيريوم)")


dashboard()
