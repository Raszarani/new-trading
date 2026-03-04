import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests, os, time
from datetime import datetime, timedelta
from risk_engine import calculate_risk, calculate_sl_tp, can_open_new_trade, get_atr
from ai_engine import ai_adjust_params, update_ai_weights, load_ai_weights

# =====================================================
# 1. KONFIGURACJA SYSTEMU + TELEGRAM
# =====================================================
TELEGRAM_TOKEN = "8622309404:AAEisB06Qsc_7oupAJ5ofADbA6cTbNbqX4U"
TELEGRAM_CHAT_ID = "6252399256"
DB_FILE = "trade_history_fusion.csv"
MAX_POSITIONS = 10     # twoje ustawienie

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=3)
    except:
        pass


# =====================================================
# 2. PAMIĘĆ SYSTEMOWA + BAZA DANYCH
# =====================================================
if "journal" not in st.session_state: st.session_state.journal = []
if "balance_pln" not in st.session_state: st.session_state.balance_pln = 4000.0
if "logs" not in st.session_state: st.session_state.logs = []
if "notified_symbols" not in st.session_state: st.session_state.notified_symbols = set()
if "atr_cache" not in st.session_state: 
    st.session_state.atr_cache = {}  # Format: {"SYMBOL": (wartość, timestamp)}

def get_cached_atr(symbol, interval):
    now = time.time()
    if symbol in st.session_state.atr_cache:
        val, ts = st.session_state.atr_cache[symbol]
        if now - ts < 300: # Dane ważne przez 5 minut
            return val
    
    new_val = get_atr(symbol, interval)
    if new_val is not None:
        st.session_state.atr_cache[symbol] = (new_val, now)
    return new_val

def add_log(msg):
    st.session_state.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    if len(st.session_state.logs) > 200:
        st.session_state.logs.pop(0)
        # Dodaj to pod st.session_state.notified_symbols
if "atr_cache" not in st.session_state: 
    st.session_state.atr_cache = {}  # Format: {"BTC-USD": (wartość, timestamp)}

def save_trade_to_db(trade):
    clean = {k: v for k, v in trade.items() if k != "data"}
    df = pd.DataFrame([clean])
    df.to_csv(DB_FILE, mode="a", index=False, header=not os.path.exists(DB_FILE))


def load_history():
    if os.path.exists(DB_FILE):
        try:
            return pd.read_csv(DB_FILE)
        except:
            return pd.DataFrame()
    return pd.DataFrame()


# =====================================================
# 3. KURSY, SENTYMENT, RYZYKO ADAPTACYJNE
# =====================================================
@st.cache_data(ttl=1800)
def get_usdpln():
    try:
        d = yf.Ticker("USDPLN=X").history(period="1d")
        return round(d["Close"].iloc[-1], 4)
    except:
        return 4.00

USDPLN = get_usdpln()


def get_market_sentiment():
    """
    Bezpiecznik rynku – jeśli BTC-USD spadł ostatnio > 1.2%, włącz tryb DANGER
    """
    try:
        df = yf.Ticker("BTC-USD").history(period="1d", interval="15m")
        if len(df) < 5:
            return "SAFE"

        change = (df["Close"].iloc[-1] / df["Close"].iloc[-5] - 1) * 100
        return "DANGER" if change < -1.2 else "SAFE"
    except:
        return "SAFE"


def get_adaptive_risk():
    """
    Automatyczne dostosowanie ryzyka na podstawie 10 ostatnich pozycji.
    """
    if not os.path.exists(DB_FILE):
        return 8.0

    try:
        df = pd.read_csv(DB_FILE)
        if len(df) < 10:
            return 8.0

        recent = df.tail(10)
        win_rate = (recent["pnl_pln"] > 0).sum() / len(recent)

        if win_rate < 0.4:
            return 4.0
        if win_rate > 0.7:
            return 12.0
        return 8.0
    except:
        return 8.0


# =====================================================
# 4. ANALIZA – ORACLE PATH, TREND, WOLUMEN, RSI
# =====================================================
def get_analysis(symbol, interval):
    try:
        df = yf.Ticker(symbol).history(period="2d", interval=interval)
        if len(df) < 20:
            return None

        # RSI
        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + gain / (loss + 1e-9)))

        # Trend
        y = df["Close"].tail(20).values
        slope = np.polyfit(np.arange(len(y)), y, 1)[0]

        # Volume Spike Index
        vol_idx = df["Volume"].iloc[-1] / (df["Volume"].mean() + 1e-9)

        # Oracle Path – prognoza 12 świec
        f_y = slope * np.arange(len(y)-1, len(y)+12) + y[-1]

        return {
            "df": df,
            "px": df["Close"].iloc[-1],
            "rsi": rsi.iloc[-1],
            "slope": slope,
            "vol": vol_idx,
            "f_y": f_y
        }

    except:
        return None


# =====================================================
# 5. TRYBY DZIAŁANIA (SAFE / BALANCED / AGGRESSIVE)
# =====================================================
def get_mode_params(mode):
    """
    mode = 1 → SAFE
    mode = 2 → BALANCED
    mode = 3 → AGGRESSIVE
    """

    if mode == 1:
        return {
            "max_positions": 3,
            "vol_limit": 1.5,
            "rsi_max_long": 60,
            "rsi_min_short": 40,
            "trend_strict": True
        }

    if mode == 2:
        return {
            "max_positions": 6,
            "vol_limit": 1.3,
            "rsi_max_long": 65,
            "rsi_min_short": 35,
            "trend_strict": False
        }

    if mode == 3:
        return {
            "max_positions": 10,
            "vol_limit": 1.1,
            "rsi_max_long": 70,
            "rsi_min_short": 30,
            "trend_strict": False
        }
def execute_trade(symbol, price, side, risk_percent, sl_percent, tp_percent, data):
    # Sprawdzenie limitu pozycji
    active_positions = [t for t in st.session_state.journal if t["status"] == "OPEN"]
    if len(active_positions) >= MAX_POSITIONS:
        return

    # Obliczenia wielkości pozycji (prosty model)
    # Ryzyko oparte na procencie balansu konta
    risk_amount_pln = st.session_state.balance_pln * (risk_percent / 100)
    
    # Wyznaczamy poziomy cenowe SL i TP
    if side == "Long":
        sl_price = round(price * (1 - sl_percent / 100), 5)
        tp_price = round(price * (1 + tp_percent / 100), 5)
    else:
        sl_price = round(price * (1 + sl_percent / 100), 5)
        tp_price = round(price * (1 - tp_percent / 100), 5)

    # Obliczamy ilość jednostek (qty) na podstawie odległości do SL
    price_risk = abs(price - sl_price)
    if price_risk == 0: return
    
    # Ilość w USD, potem przeliczone na jednostki aktywa
    qty = (risk_amount_pln / USDPLN) / price_risk 

    # Tworzenie transakcji
    trade = {
        "symbol": symbol,
        "side": side,
        "entry_usd": price,
        "qty": qty,
        "sl": sl_price,
        "tp": tp_price,
        "status": "OPEN",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "be_active": False,
        "high_seen": price,
        "val_pln": risk_amount_pln,
        "data": data # Przechowujemy kontekst dla AI
    }

    st.session_state.journal.append(trade)
    st.session_state.balance_pln -= risk_amount_pln
    
    msg = f"🚀 OTWARTKO {side} na {symbol}\nCena: `{price}`\nSL: `{sl_price}` | TP: `{tp_price}`"
    add_log(msg)
    send_telegram(msg)
# =====================================================
# 6. INTERFEJS UI + SKANER + WYKRESY (ROZBUDOWANE)
# =====================================================

st.set_page_config(page_title="FUSION PRO vX", layout="wide")

st.sidebar.header("🛡️ FUSION PRO vX – PANEL STEROWANIA")

# Tryb działania
mode = st.sidebar.slider("Tryb działania", 1, 3, 2,
                         format="SAFE (1) • BALANCED (2) • AGGRESSIVE (3)")

mode_params = get_mode_params(mode)

# Interwał – domyślnie 5m
interval = st.sidebar.selectbox("Interwał", ["1m", "5m", "15m", "1h"], index=1)

# Best AI-based risk
adaptive_r = get_adaptive_risk()

risk_v = st.sidebar.slider("Ryzyko (%)", 1.0, 15.0, adaptive_r)
sl_v = st.sidebar.slider("Stop Loss (%)", 0.5, 8.0, 2.5)
tp_v = st.sidebar.slider("Take Profit (%)", 1.0, 20.0, 5.0)

auto_trade = st.sidebar.toggle("Auto-Trading 🤖", True)
be_toggle = st.sidebar.toggle("Break-Even 🛡️", True)
trailing_toggle = st.sidebar.toggle("Trailing Stop 📈", True)

# Znajdź tę linię w okolicy 100-110 i zamień na:
sassets_raw = st.sidebar.text_area(
    "Symbole (rozdzielone przecinkiem):", 
    value="BTC-USD, ETH-USD, SOL-USD, BNB-USD, ADA-USD, XRP-USD, DOT-USD, LINK-USD, AVAX-USD, MATIC-USD, DOGE-USD, NEAR-USD, NVDA, TSLA, AAPL, MSFT, AMD, GOOGL, META, AMZN, PLTR, SMCI, JPM, GS, V, XOM, LLY, COST, NFLX, PYPL"
)
ASSETS = [s.strip().upper() for s in assets_raw.split(",") if s.strip()]

market_guard = get_market_sentiment()

st.sidebar.metric("Sentyment Rynku (BTC)", market_guard)

# =====================================================
# 6A. SKANER
# =====================================================

scan_results = []
for s in ASSETS:
    res = get_analysis(s, interval)
    if res:
        scan_results.append({
            "Symbol": s,
            "Cena": round(res["px"], 4),
            "Vol": round(res["vol"], 2),
            "RSI": round(res["rsi"], 1),
            "Trend": "UP" if res["slope"] > 0 else "DOWN",
            "data": res
        })

if scan_results:
    df_s = pd.DataFrame(scan_results).sort_values("Vol", ascending=False)
    st.subheader("🔍 TOP 5 AKTYWÓW (wg wolumenu)")
    st.dataframe(df_s[["Symbol", "Cena", "Vol", "RSI", "Trend"]].head(5), width='stretch')

# =====================================================
# 6B. WYKRES (ŚWIECE + WOLUMEN + ORACLE PATH)
# =====================================================

if scan_results:
    target = st.selectbox("🎯 Szczegóły aktywa", df_s["Symbol"].tolist())

    t_data = next(x["data"] for x in scan_results if x["Symbol"] == target)
    df_p = t_data["df"]

    step = int("".join(filter(str.isdigit, interval))) if "m" in interval else 60
    f_dts = [df_p.index[-1] + timedelta(minutes=step * i) for i in range(0, 12)]

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.03
    )

    # Candle chart
    fig.add_trace(go.Candlestick(
        x=df_p.index,
        open=df_p["Open"],
        high=df_p["High"],
        low=df_p["Low"],
        close=df_p["Close"],
        name="Cena"
    ), row=1, col=1)

    # Oracle Path forecast
    fig.add_trace(go.Scatter(
        x=f_dts,
        y=t_data["f_y"],
        mode="lines",
        line=dict(color="yellow", dash="dot", width=3),
        name="Oracle Path"
    ), row=1, col=1)

    # Volume bars
    colors = ["green" if c >= o else "red" for o, c in zip(df_p["Open"], df_p["Close"])]
    fig.add_trace(go.Bar(
        x=df_p.index,
        y=df_p["Volume"],
        marker_color=colors,
        name="Wolumen"
    ), row=2, col=1)

    fig.update_layout(template="plotly_dark", height=600, xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, width='stretch')
    
    # Manual trading buttons
    cA, cB = st.columns(2)
    if cA.button(f"🟢 KUP LONG: {target}"):
        execute_trade(target, t_data["px"], "Long", risk_v, sl_v, tp_v, t_data)
    if cB.button(f"🔴 SPRZEDAJ SHORT: {target}"):
        execute_trade(target, t_data["px"], "Short", risk_v, sl_v, tp_v, t_data)


# =====================================================
# 7. SILNIK TRANSAKCYJNY – BE, TRAILING, SL/TP
# =====================================================


st.subheader("📝 Monitoring pozycji")

active = [t for t in st.session_state.journal if t["status"] == "OPEN"]

for t in active:
    try:
        curr_px = yf.Ticker(t["symbol"]).history(period="1d", interval="1m")["Close"].iloc[-1]
        t["last_change"] = ((curr_px - t["entry_usd"]) / t["entry_usd"]) * 100

        pnl_pln = (curr_px - t["entry_usd"]) * t["qty"] * USDPLN if t["side"] == "Long" \
                   else (t["entry_usd"] - curr_px) * t["qty"] * USDPLN

        # Break Even
        if be_toggle and not t["be_active"]:
            prof = ((curr_px / t["entry_usd"] - 1) * 100) if t["side"] == "Long" \
                   else ((t["entry_usd"] / curr_px - 1) * 100)

            if prof >= tp_v / 2:
                t["sl"] = t["entry_usd"]
                t["be_active"] = True
                add_log(f"{t['symbol']} -> BreakEven aktywowany")

        # Trailing Stop
        # Inteligentny Trailing Stop z Cache i AI
        if trailing_toggle:
            atr_val = get_cached_atr(t["symbol"], interval) 
            if atr_val:
                # Pobieramy wagi z ai_engine.py
                weights = load_ai_weights()
                # Dystans = ATR * 2.0 * korekta AI
                trail_dist = atr_val * 2.0 * weights.get("sl_adjust", 1.0)
                
                if t["side"] == "Long":
                    if curr_px > t["high_seen"]:
                        t["high_seen"] = curr_px
                    
                    potential_sl = round(curr_px - trail_dist, 5)
                    if potential_sl > t["sl"]:
                        t["sl"] = potential_sl
                        add_log(f"📈 Trailing UP {t['symbol']}: SL na {t['sl']}")

                elif t["side"] == "Short":
                    if curr_px < t["high_seen"]:
                        t["high_seen"] = curr_px
                    
                    potential_sl = round(curr_px + trail_dist, 5)
                    if potential_sl < t["sl"]:
                        t["sl"] = potential_sl
                        add_log(f"📉 Trailing DOWN {t['symbol']}: SL na {t['sl']}")

        # Czy osiągnięto SL / TP?
        hit = (t["side"] == "Long" and (curr_px <= t["sl"] or curr_px >= t["tp"])) or \
              (t["side"] == "Short" and (curr_px >= t["sl"] or curr_px <= t["tp"]))

        # INTERFEJS
        c1, c2, c3 = st.columns([4, 4, 2])

        icon = "🟢 LONG" if t["side"] == "Long" else "🔴 SHORT"
        c1.write(f"**{t['symbol']}** {icon} | Ilość: `{t['qty']:.6f}`")
        c1.write(f"In: `{t['entry_usd']}` → Teraz: `{curr_px}`")

        c2.write(f"PNL: **{pnl_pln:+.2f} zł**")
        c2.write(f"SL: `{t['sl']}` | TP: `{t['tp']}`")

        if c3.button("ZAMKNIJ", key=f"close_{t['symbol']}_{t['time']}") or hit:
            t["status"] = "CLOSED"
            t["pnl_pln"] = pnl_pln
            st.session_state.balance_pln += t["val_pln"] + pnl_pln
            save_trade_to_db(t)
            add_log(f"ZAMKNIĘTO {t['symbol']} wynik {pnl_pln:.2f} zł")
            send_telegram(f"✅ *ZAMKNIĘTO {t['symbol']}*\nWynik: `{pnl_pln:.2f} PLN`")
            st.rerun()

    except:
        continue


# =====================================================
# 8. AUTO-TRADING (Z trybem działania)
# =====================================================

# Dodaj to na początku sekcji 8 w fusion_pro_app.py
#if auto_trade:
#    add_log(f"DEBUG: Skaner aktywny, Sentyment: {market_guard}")
# =====================================================
# 8. AUTO-TRADING (Z blokadą duplikatów)
# =====================================================
if auto_trade and scan_results and market_guard == "SAFE":
    # Pobieramy symbole obecnie otwartych pozycji
    current_symbols = [t["symbol"] for t in st.session_state.journal if t["status"] == "OPEN"]
    
    for _, row in df_s.head(mode_params["max_positions"]).iterrows():
        symbol = row["Symbol"]

        # BLOKADA DUPLIKATÓW: Jeśli już mamy tę akcję, pomiń ją
        if symbol in current_symbols:
            continue

        # FILTR wolumenu
        if row["Vol"] < mode_params["vol_limit"]:
            continue

        # FILTR trendu + RSI
        side = None
        if row["Trend"] == "UP" and row["RSI"] < mode_params["rsi_max_long"]:
            side = "Long"
        elif row["Trend"] == "DOWN" and row["RSI"] > mode_params["rsi_min_short"]:
            side = "Short"

        if side:
            execute_trade(symbol, row["Cena"], side, risk_v, sl_v, tp_v, row["data"])
# =====================================================
# 9. PORTFEL – STATYSTYKI GŁÓWNE
# =====================================================

st.divider()
st.subheader("📊 Podsumowanie Portfela")

active_count = len([t for t in st.session_state.journal if t["status"] == "OPEN"])

history_df = load_history()
win_rate = 0.0
if not history_df.empty:
    win_rate = (history_df["pnl_pln"] > 0).sum() / len(history_df) * 100

c1, c2, c3, c4 = st.columns(4)

c1.metric("💰 Saldo", f"{st.session_state.balance_pln:.2f} PLN")
c2.metric("🎯 WIN RATE", f"{win_rate:.1f}%")
c3.metric("📈 Aktywne pozycje", f"{active_count}/{MAX_POSITIONS}")
c4.metric("💵 USD/PLN", USDPLN)


# =====================================================
# 10. EQUITY CURVE – KRZYWA KAPITAŁU
# =====================================================

if not history_df.empty:

    st.divider()
    st.subheader("📈 Krzywa Kapitału (Equity Curve)")

    try:
        history_df["time_close"] = pd.to_datetime(history_df["time_close"])
        history_df = history_df.sort_values("time_close")
        history_df["cum_pnl"] = history_df["pnl_pln"].cumsum()

        fig_eq = go.Figure()
        fig_eq.add_trace(go.Scatter(
            x=history_df["time_close"],
            y=history_df["cum_pnl"],
            fill="tozeroy",
            line=dict(color="#00ffcc", width=3),
            name="Zysk łączny"
        ))

        fig_eq.update_layout(template="plotly_dark", height=350)
        st.plotly_chart(fig_eq, width='stretch')

    except:
        st.warning("⚠️ Nie udało się załadować historii transakcji.")


# =====================================================
# 11. LOGI SYSTEMOWE + RESET
# =====================================================

st.divider()
st.subheader("📜 Logi Systemowe")

with st.expander("Pokaż logi", expanded=False):
    for l in reversed(st.session_state.logs):
        st.write(l)

st.sidebar.divider()

if st.sidebar.button("🚨 Reset systemu (czyści pamięć)", width='stretch'):
    st.session_state.journal = []
    st.session_state.logs = []
    st.session_state.notified_symbols = set()
    st.success("System wyczyszczony!")
    st.rerun()


# =====================================================
# 12. AUTO-REFRESH
# =====================================================

time.sleep(15)
st.rerun()
