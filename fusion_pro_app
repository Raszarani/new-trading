import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import time
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ai_engine import ai_adjust_params, update_ai_weights
from risk_engine import calculate_risk, calculate_sl_tp, risk_summary, can_open_new_trade
from xtb_connector import XTBConnector

import os
import requests
import json



# =====================================================
# KONFIGURACJA SYSTEMU
# =====================================================

TELEGRAM_TOKEN = "8622309404:AAEisB06Qsc_7oupAJ5ofADbA6cTbNbqX4U"
TELEGRAM_CHAT_ID = "6252399256"

DB_FILE = "trade_history_fusion_xtb.csv"

# XTB DEMO — ZALOGUJ SIĘ
XTB_LOGIN = "20190862"
XTB_PASSWORD = "Ryszard96"

xtb = XTBConnector(XTB_LOGIN, XTB_PASSWORD, mode="demo")
xtb.connect()
xtb.stream_connect()



# =====================================================
# TELEGRAM
# =====================================================

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        req = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        requests.post(url, json=req, timeout=3)
    except:
        pass



# =====================================================
# STAN SESJI
# =====================================================

if "journal" not in st.session_state:
    st.session_state.journal = []

if "balance_pln" not in st.session_state:
    st.session_state.balance_pln = 4000.0

if "logs" not in st.session_state:
    st.session_state.logs = []

if "xtb_prices" not in st.session_state:
    st.session_state.xtb_prices = {}



# =====================================================
# LOGI
# =====================================================

def add_log(msg):
    t = datetime.now().strftime("%H:%M:%S")
    st.session_state.logs.append(f"[{t}] {msg}")
    if len(st.session_state.logs) > 200:
        st.session_state.logs.pop(0)



# =====================================================
# CENY XTB – aktualizacja przez streaming
# =====================================================

def create_xtb_price_callback(symbol):
    def callback(bid, ask):
        st.session_state.xtb_prices[symbol] = {"bid": bid, "ask": ask}
    return callback



# MAPOWANIE SYMBOLI
XTB_SYMBOLS = {
    "BTC-USD": "BTCUSD",
    "ETH-USD": "ETHUSD",
    "SOL-USD": "SOLUSD",
    "XRP-USD": "XRPUSD",
    "DOGE-USD": "DOGEUSD",
    "SHIB-USD": "SHIBUSD",
    "FET-USD": "FETUSD",
    "NEAR-USD": "NEARUSD",
    "ADA-USD": "ADAUSD",
    "LINK-USD": "LINKUSD",
    "PEPE-USD": "PEPEUSD",

    "NVDA": "NVDA.US",
    "TSLA": "TSLA.US",
    "SMCI": "SMCI.US",
    "ARM": "ARM.US",
    "RIOT": "RIOT.US",
    "GME": "GME.US"
}


# subskrypcja cen
for yf_symbol, xtb_symbol in XTB_SYMBOLS.items():
    xtb.subscribe_price(xtb_symbol, create_xtb_price_callback(yf_symbol))



# =====================================================
# FUNKCJE POMOCNICZE
# =====================================================

def get_yf_price(symbol):
    """
    Fallback — jeśli stream XTB nie działa.
    """
    try:
        d = yf.Ticker(symbol).history(period="1d", interval="1m")
        return float(d["Close"].iloc[-1])
    except:
        return None


def get_live_price(yf_symbol):
    """
    Zwraca: (price, source)
    """
    if yf_symbol in st.session_state.xtb_prices:
        p = st.session_state.xtb_prices[yf_symbol]
        mid = (p["bid"] + p["ask"]) / 2
        return mid, "XTB"
    else:
        fallback = get_yf_price(yf_symbol)
        return fallback, "YF"



# =====================================================
# HISTORIA
# =====================================================

def save_trade_to_db(trade):
    clean = {k: v for k, v in trade.items() if k != "data"}
    df = pd.DataFrame([clean])
    df.to_csv(DB_FILE, mode="a", header=not os.path.exists(DB_FILE), index=False)


def load_history():
    if os.path.exists(DB_FILE):
        try:
            return pd.read_csv(DB_FILE)
        except:
            return pd.DataFrame()
    return pd.DataFrame()



# =====================================================
# SENTYMENT BTC
# =====================================================

def get_market_sentiment():
    try:
        df = yf.Ticker("BTC-USD").history(period="1d", interval="15m")
        if len(df) < 5:
            return "SAFE"
        change = (df["Close"].iloc[-1] / df["Close"].iloc[-5] - 1) * 100
        return "DANGER" if change < -1.2 else "SAFE"
    except:
        return "SAFE"



# =====================================================
# ANALIZA — RSI + slope + vol + oracle path
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

        # trend
        y = df["Close"].tail(20).values
        slope = np.polyfit(np.arange(len(y)), y, 1)[0]

        # vol index
        vol_idx = df["Volume"].iloc[-1] / (df["Volume"].mean() + 1e-9)

        # oracle path
        forward = slope * np.arange(len(y)-1, len(y)+12) + y[-1]

        return {
            "df": df,
            "px": df["Close"].iloc[-1],
            "rsi": rsi.iloc[-1],
            "slope": slope,
            "vol": vol_idx,
            "f_y": forward
        }

    except:
        return None
# =====================================================
# TRYBY DZIAŁANIA
# SAFE / BALANCED / AGGRESSIVE
# =====================================================

def get_mode_params(mode):
    if mode == 1:     # SAFE
        return {
            "max_positions": 3,
            "vol_limit": 1.5,
            "rsi_max_long": 60,
            "rsi_min_short": 40,
            "desc": "Tryb bezpieczny"
        }
    if mode == 2:     # BALANCED
        return {
            "max_positions": 6,
            "vol_limit": 1.3,
            "rsi_max_long": 65,
            "rsi_min_short": 35,
            "desc": "Tryb zbalansowany"
        }
    if mode == 3:     # AGGRESSIVE
        return {
            "max_positions": 10,
            "vol_limit": 1.1,
            "rsi_max_long": 70,
            "rsi_min_short": 30,
            "desc": "Tryb agresywny"
        }



# =====================================================
# UI – PANEL KONTROLNY
# =====================================================

st.set_page_config(page_title="Fusion PRO vX – XTB AI HYBRID", layout="wide")

st.sidebar.header("⚙️ Fusion PRO vX – AI + XTB HYBRID")

mode = st.sidebar.slider(
    "Tryb działania",
    1, 3, 2,
    format="SAFE(1) – BALANCED(2) – AGGRESSIVE(3)"
)

mode_params = get_mode_params(mode)

interval = st.sidebar.selectbox("Interwał:", ["1m", "5m", "15m", "1h"], index=1)

base_risk = st.sidebar.slider("Bazowe ryzyko (%)", 1.0, 20.0, 8.0)

sl_user = st.sidebar.slider("SL (%) – wstępne", 0.5, 10.0, 2.5)
tp_user = st.sidebar.slider("TP (%) – wstępne", 1.0, 30.0, 5.0)

be_toggle = st.sidebar.toggle("Break-Even (BE)", True)
trailing_toggle = st.sidebar.toggle("Trailing Stop", True)
auto_trade = st.sidebar.toggle("AutoTrade 🤖", True)

assets_raw = st.sidebar.text_area(
    "Skaner aktywów (symbole Yahoo Finance)",
    ", ".join([
        "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD",
        "SHIB-USD", "ADA-USD", "LINK-USD", "FET-USD",
        "NVDA", "TSLA", "RIOT", "GME"
    ])
)

ASSETS = [x.strip().upper() for x in assets_raw.split(",") if x.strip()]

sentiment = get_market_sentiment()
st.sidebar.metric("Sentyment Rynku (BTC)", sentiment)



# =====================================================
# SKANER RYNKU
# =====================================================

scan_results = []
for s in ASSETS:
    a = get_analysis(s, interval)
    if a:
        scan_results.append({
            "Symbol": s,
            "Cena": round(a["px"], 4),
            "Vol": round(a["vol"], 2),
            "RSI": round(a["rsi"], 1),
            "Trend": "UP" if a["slope"] > 0 else "DOWN",
            "data": a
        })

if scan_results:
    df_s = pd.DataFrame(scan_results).sort_values("Vol", ascending=False)
    st.subheader("🔍 Skaner – TOP wg wolumenu")
    st.dataframe(df_s[["Symbol", "Cena", "Vol", "RSI", "Trend"]], use_container_width=True)



# =====================================================
# WYKRES – świeczki + wolumen + Oracle Path
# =====================================================

if scan_results:
    target = st.selectbox("🎯 Wybierz aktywo:", [x["Symbol"] for x in scan_results])

    t_data = next(x["data"] for x in scan_results if x["Symbol"] == target)
    df_p = t_data["df"]

    step = int("".join(filter(str.isdigit, interval))) if "m" in interval else 60
    future_x = [df_p.index[-1] + timedelta(minutes=step * i) for i in range(12)]

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.03
    )

    fig.add_trace(go.Candlestick(
        x=df_p.index,
        open=df_p["Open"],
        high=df_p["High"],
        low=df_p["Low"],
        close=df_p["Close"],
        name="Cena"
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=future_x,
        y=t_data["f_y"],
        mode="lines",
        line=dict(color="yellow", width=2, dash="dot"),
        name="Oracle Path"
    ), row=1, col=1)

    colors = ["green" if c >= o else "red"
              for o, c in zip(df_p["Open"], df_p["Close"])]
    fig.add_trace(go.Bar(
        x=df_p.index,
        y=df_p["Volume"],
        marker_color=colors,
        name="Wolumen"
    ), row=2, col=1)

    fig.update_layout(template="plotly_dark", height=600, xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True)



# =====================================================
# OTWIERANIE POZYCJI – XTB HYBRID
# =====================================================

def execute_trade_xtb(symbol, yf_symbol, side, px, context):
    journal = st.session_state.journal

    if not can_open_new_trade(journal, mode_params["max_positions"]):
        add_log("Limit pozycji osiągnięty.")
        return

    # AI modyfikacja SL/TP/risk
    ai_mod = ai_adjust_params(base_risk, sl_user, tp_user)
    risk_ai = ai_mod["risk"]
    sl_ai = ai_mod["sl"]
    tp_ai = ai_mod["tp"]

    # Risk engine – final risk + dynamic ATR-based SL/TP
    rs = risk_summary(symbol, px, side, risk_ai, sentiment, journal, interval)

    risk_final = rs["risk"]
    sl_final = rs["sl"]
    tp_final = rs["tp"]

    # wolumen XTB – np. 0.1 = 0.1 lota
    volume = max(0.01, risk_final / 100)

    if side == "Long":
        order = xtb.market_buy(symbol, volume, sl_final, tp_final)
    else:
        order = xtb.market_sell(symbol, volume, sl_final, tp_final)

    if not order.get("status"):
        add_log(f"Błąd XTB: {order}")
        return

    position_id = order["returnData"]["order"]

    trade = {
        "symbol": yf_symbol,
        "xtb_symbol": symbol,
        "side": side,
        "entry_px": px,
        "volume": volume,
        "sl": sl_final,
        "tp": tp_final,
        "risk": risk_final,
        "ticket": position_id,
        "time_open": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "OPEN",
        "data": context
    }

    st.session_state.journal.append(trade)

    add_log(f"OTWARTO {yf_symbol} {side} | SL={sl_final} TP={tp_final} | risk={risk_final}")
    send_telegram(f"🚀 *OPEN {yf_symbol} {side}*\nCena: `{px}`\nSL: `{sl_final}`\nTP: `{tp_final}`")
# =====================================================
# MONITORING POZYCJI – HYBRID MODE (BE + Trailing lokalnie)
# =====================================================

st.subheader("📊 Monitoring pozycji")

active_positions = [t for t in st.session_state.journal if t["status"] == "OPEN"]

for t in active_positions:
    try:
        yf_symbol = t["symbol"]
        xtb_symbol = t["xtb_symbol"]
        side = t["side"]

        # Aktualna cena (XTB streaming)
        curr_px, src = get_live_price(yf_symbol)

        pnl = (curr_px - t["entry_px"]) * t["volume"] * 1000 if side == "Long" else \
              (t["entry_px"] - curr_px) * t["volume"] * 1000

        # Break Even
        if be_toggle:
            if side == "Long":
                if curr_px > t["entry_px"] + abs(t["entry_px"] - t["sl"]) and t["sl"] < t["entry_px"]:
                    t["sl"] = round(t["entry_px"], 5)
                    xtb.market_buy(xtb_symbol, t["volume"], sl=t["sl"], tp=t["tp"])
                    add_log(f"BE aktywne na {yf_symbol}")
            else:
                if curr_px < t["entry_px"] - abs(t["entry_px"] - t["sl"]) and t["sl"] > t["entry_px"]:
                    t["sl"] = round(t["entry_px"], 5)
                    xtb.market_sell(xtb_symbol, t["volume"], sl=t["sl"], tp=t["tp"])
                    add_log(f"BE aktywne na {yf_symbol}")

        # Trailing Stop
        if trailing_toggle:
            if side == "Long":
                if curr_px > t.get("high", t["entry_px"]):
                    t["high"] = curr_px
                    t["sl"] = round(curr_px * 0.98, 5)
                    xtb.market_buy(xtb_symbol, t["volume"], sl=t["sl"], tp=t["tp"])
            else:
                if curr_px < t.get("low", t["entry_px"]):
                    t["low"] = curr_px
                    t["sl"] = round(curr_px * 1.02, 5)
                    xtb.market_sell(xtb_symbol, t["volume"], sl=t["sl"], tp=t["tp"])

        # Zamknięcie SL/TP
        hit = (
            (side == "Long" and (curr_px <= t["sl"] or curr_px >= t["tp"])) or
            (side == "Short" and (curr_px >= t["sl"] or curr_px <= t["tp"]))
        )

        # Interfejs pozycji
        c1, c2, c3 = st.columns([4, 4, 2])
        c1.write(f"**{yf_symbol}** ({side})")
        c1.write(f"In: {t['entry_px']} → Now: {curr_px} ({src})")

        c2.write(f"PNL: **{pnl:+.2f} zł**")
        c2.write(f"SL: {t['sl']} | TP: {t['tp']}")

        if c3.button("Zamknij", key=f"close_{t['ticket']}") or hit:
            t["status"] = "CLOSED"
            t["time_close"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            t["pnl_pln"] = pnl

            xtb.close_position(t["ticket"])  # zamknięcie w XTB
            save_trade_to_db(t)

            update_ai_weights(t["data"], pnl)  # AI learning
            add_log(f"ZAMKNIĘTO {yf_symbol} wynik={pnl:.2f}")
            send_telegram(f"✅ CLOSED {yf_symbol}\nWynik: `{pnl:.2f} PLN`")

            st.rerun()

    except Exception as e:
        st.write(f"Błąd monitora {e}")
        continue



# =====================================================
# AUTO-TRADING – silnik decyzji
# =====================================================

if auto_trade and scan_results and sentiment == "SAFE":
    for _, row in df_s.head(mode_params["max_positions"]).iterrows():

        yf_symbol = row["Symbol"]
        xtb_symbol = XTB_SYMBOLS[yf_symbol]

        vol = row["Vol"]
        rsi = row["RSI"]
        trend = row["Trend"]

        if vol < mode_params["vol_limit"]:
            continue

        side = None
        if trend == "UP" and rsi < mode_params["rsi_max_long"]:
            side = "Long"
        if trend == "DOWN" and rsi > mode_params["rsi_min_short"]:
            side = "Short"

        if side:
            px, src = get_live_price(yf_symbol)
            execute_trade_xtb(xtb_symbol, yf_symbol, side, px, row["data"])



# =====================================================
# STATYSTYKI + EQUITY CURVE
# =====================================================

history_df = load_history()

st.divider()
st.subheader("📈 Equity Curve")

if not history_df.empty:
    history_df["time_close"] = pd.to_datetime(history_df["time_close"])
    history_df = history_df.sort_values("time_close")
    history_df["cum_pnl"] = history_df["pnl_pln"].cumsum()

    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        x=history_df["time_close"],
        y=history_df["cum_pnl"],
        fill="tozeroy",
        line=dict(color="#00ffcc", width=3)
    ))

    fig_eq.update_layout(template="plotly_dark", height=300)
    st.plotly_chart(fig_eq, use_container_width=True)

else:
    st.info("Brak historii do wyświetlenia.")



# =====================================================
# LOGI
# =====================================================

st.divider()
st.subheader("📜 Logi systemowe")

with st.expander("Pokaż logi", expanded=False):
    for l in reversed(st.session_state.logs):
        st.write(l)



# =====================================================
# AUTO REFRESH
# =====================================================

time.sleep(15)
st.rerun()
