import streamlit as st 
import pandas as pd 
import numpy as np 
import yfinance as yf 
import time 
from datetime import datetime, timedelta 
import plotly.graph_objects as go 
from Bybit_Connector import BybitConnector 
from ai_engine import ai_adjust_params, update_ai_weights
from risk_engine import calculate_risk, calculate_sl_tp, risk_summary, can_open_new_trade
import requests 
import os 
import json

# ======================================================================
# KONFIGURACJA
# ======================================================================
# UWAGA: Zaleca się używanie st.secrets zamiast wpisywania kluczy tutaj!
API_KEY = "e8NXf5lJalgYDLkqzk" 
API_SECRET = "1OBKVsydXfPs50Gb0Rf9P4X7swY4x3IjfXwn"

LEVERAGE = 5 
ACCOUNT_COIN = "USDT"

# Inicjalizacja konektora Bybit
bybit = BybitConnector(API_KEY, API_SECRET, leverage=LEVERAGE)

DB_FILE = "trade_history_bybit.csv" 
TELEGRAM_TOKEN = "WPISZ_SWOJ_TOKEN" 
TELEGRAM_CHAT_ID = "WPISZ_CHAT_ID"

# ======================================================================
# TELEGRAM
# ======================================================================
def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except:
        pass

# ======================================================================
# SESJA I LOGI
# ======================================================================
if "journal" not in st.session_state: 
    st.session_state.journal = [] 

if "logs" not in st.session_state: 
    st.session_state.logs = []

if "prices" not in st.session_state: 
    st.session_state.prices = {}

def add_log(text):
    t = datetime.now().strftime("%H:%M:%S")
    st.session_state.logs.append("[" + t + "] " + text)
    if len(st.session_state.logs) > 250:
        st.session_state.logs.pop(0)

# ======================================================================
# STREAMING CEN BYBIT (tick)
# ======================================================================
def price_callback(symbol):
    def inner(price):
        st.session_state.prices[symbol] = price
    return inner

# Mapowanie symboli
BYBIT_SYMBOL_MAP = { 
    "BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT", "SOL-USD": "SOLUSDT", 
    "XRP-USD": "XRPUSDT", "DOGE-USD": "DOGEUSDT", "SHIB-USD": "SHIBUSDT", 
    "FET-USD": "FETUSDT", "NEAR-USD": "NEARUSDT", "ADA-USD": "ADAUSDT", 
    "LINK-USD": "LINKUSDT", "PEPE-USD": "PEPEUSDT", "TSLA": "TSLAUSDT", 
    "NVDA": "NVDAUSDT", "GME": "GMEUSDT", "RIOT": "RIOTUSDT", 
    "SMCI": "SMCIUSDT", "ARM": "ARMUSDT" 
}

# Subskrypcja cen
for yf_symbol, bybit_symbol in BYBIT_SYMBOL_MAP.items(): 
    bybit.subscribe_price(bybit_symbol, price_callback(yf_symbol))

# ======================================================================
# FUNKCJE POMOCNICZE
# ======================================================================
def get_price(symbol): 
    if symbol in st.session_state.prices: 
        return st.session_state.prices[symbol], "BYBIT" 
    try: 
        d = yf.Ticker(symbol).history(period="1d", interval="1m") 
        return float(d["Close"].iloc[-1]), "YF" 
    except: 
        return None, "NONE"

def save_trade_to_db(trade): 
    df = pd.DataFrame([trade]) 
    df.to_csv(DB_FILE, mode="a", index=False, header=not os.path.exists(DB_FILE))

def load_history(): 
    if os.path.exists(DB_FILE): 
        try: 
            return pd.read_csv(DB_FILE) 
        except: 
            return pd.DataFrame() 
    return pd.DataFrame()

def get_sentiment_btc(): 
    try: 
        df = yf.Ticker("BTC-USD").history(period="1d", interval="15m") 
        if len(df) < 5: 
            return "SAFE" 
        ch = (df["Close"].iloc[-1] / df["Close"].iloc[-5] - 1) * 100 
        return "DANGER" if ch < -1.2 else "SAFE" 
    except: 
        return "SAFE"

# ======================================================================
# ANALIZA: RSI, slope, Volume index, Oracle path
# ======================================================================
def analyze(symbol, interval): 
    try: 
        df = yf.Ticker(symbol).history(period="2d", interval=interval) 
        if len(df) < 20: 
            return None
        
        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + gain / (loss + 1e-9)))
        y = df["Close"].tail(20).values
        slope = np.polyfit(np.arange(len(y)), y, 1)[0]
        vol_idx = df["Volume"].iloc[-1] / (df["Volume"].mean() + 1e-9)
        pred = slope * np.arange(len(y)-1, len(y)+12) + y[-1]
        
        return {
            "df": df,
            "px": df["Close"].iloc[-1],
            "rsi": rsi.iloc[-1],
            "slope": slope,
            "vol": vol_idx,
            "f_y": pred
        }
    except:
        return None

# ======================================================================
# TRYBY DZIAŁANIA (SAFE/BALANCED/AGGRESSIVE)
# ======================================================================
def mode_params(mode): 
    if mode == 1: 
        return {"maxpos": 3, "rsi_max_long": 60, "rsi_min_short": 40, "vol": 1.5} 
    if mode == 2: 
        return {"maxpos": 6, "rsi_max_long": 65, "rsi_min_short": 35, "vol": 1.3} 
    if mode == 3: 
        return {"maxpos": 10, "rsi_max_long": 70, "rsi_min_short": 30, "vol": 1.1} 
    return {"maxpos": 3, "rsi_max_long": 60, "rsi_min_short": 40, "vol": 1.5}

# ======================================================================
# UI
# ======================================================================
st.set_page_config(page_title="Fusion PRO vX – Bybit Futures", layout="wide")

st.sidebar.header("Panel Sterowania – Bybit Futures")

mode = st.sidebar.slider("Tryb działania", 1, 3, 2) 
params = mode_params(mode)

interval = st.sidebar.selectbox("Interwał", ["1m", "5m", "15m"], index=1) 
risk_base = st.sidebar.slider("Bazowe ryzyko (%)", 1.0, 20.0, 8.0) 
sl_user = st.sidebar.slider("SL (%)", 0.5, 10.0, 2.0) 
tp_user = st.sidebar.slider("TP (%)", 1.0, 30.0, 5.0)

be_toggle = st.sidebar.toggle("Break-Even", True) 
trailing_toggle = st.sidebar.toggle("Trailing Stop", True) 
auto_trade = st.sidebar.toggle("Auto-trading", False)

assets_raw = st.sidebar.text_area("Skaner aktywów", "BTC-USD, ETH-USD, SOL-USD, XRP-USD, DOGE-USD, SHIB-USD, FET-USD, TSLA, NVDA, GME") 
ASSETS = [x.strip().upper() for x in assets_raw.split(",") if x.strip()]

sent = get_sentiment_btc() 
st.sidebar.metric("Sentyment BTC", sent)

# ======================================================================
# SKANER AKTYWÓW
# ======================================================================
scan = [] 
for s in ASSETS: 
    a = analyze(s, interval) 
    if a: 
        scan.append({
            "Symbol": s, 
            "Cena": round(a["px"], 4), 
            "Vol": round(a["vol"],2), 
            "RSI": round(a["rsi"],1), 
            "Trend": "UP" if a["slope"]>0 else "DOWN", 
            "data": a
        })

if scan: 
    df_s = pd.DataFrame(scan).sort_values("Vol", ascending=False) 
    st.subheader("TOP wg wolumenu") 
    st.dataframe(df_s, use_container_width=True)

# ======================================================================
# OTWIERANIE POZYCJI (Bybit HYBRID)
# ======================================================================
def open_position(yfsym, side, context): 
    bb = BYBIT_SYMBOL_MAP[yfsym] 
    px, src = get_price(yfsym)

    # AI modifications
    ai_mod = ai_adjust_params(risk_base, sl_user, tp_user)
    r = ai_mod["risk"]
    slp = ai_mod["sl"]
    tpp = ai_mod["tp"]
    
    # risk engine summary
    rs = risk_summary(bb, px, side, r, sent, st.session_state.journal, interval)
    risk_final = rs["risk"]
    sl_final = rs["sl"]
    tp_final = rs["tp"]
    qty = round(risk_final / 100, 3)
    
    bybit.set_leverage(bb, LEVERAGE)
    order = bybit.place_order(bb, "Buy" if side=="Long" else "Sell", qty, sl_final, tp_final)
    
    if not "retCode" in order or order["retCode"] != 0:
        add_log("Błąd Bybit: " + str(order))
        return
        
    trade = {
        "symbol": yfsym,
        "bb_symbol": bb,
        "side": side,
        "entry": px,
        "sl": sl_final,
        "tp": tp_final,
        "volume": qty,
        "risk": risk_final,
        "ticket": order["result"]["orderId"],
        "time_open": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "OPEN",
        "data": context
    }
    st.session_state.journal.append(trade)
    add_log("Otworzono " + yfsym + " " + side)
    send_telegram("OPEN " + yfsym + " " + side)

# ======================================================================
# WYKRES
# ======================================================================
if scan: 
    target = st.selectbox("Aktywo", [x["Symbol"] for x in scan]) 
    td = next(x for x in scan if x["Symbol"] == target)["data"] 
    df = td["df"]

    step = int("".join(filter(str.isdigit, interval)))
    fdt = [df.index[-1] + timedelta(minutes=step * i) for i in range(12)]
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"]
    ))
    fig.add_trace(go.Scatter(x=fdt, y=td["f_y"], mode="lines",
                             line=dict(color="yellow", dash="dot")))
    st.plotly_chart(fig, use_container_width=True)
    
    c1, c2 = st.columns(2)
    if c1.button("Kup LONG"):
        open_position(target, "Long", td)
    if c2.button("Sprzedaj SHORT"):
        open_position(target, "Short", td)

# ======================================================================
# MONITORING POZYCJI
# ======================================================================
st.subheader("Monitoring pozycji")

for t in st.session_state.journal: 
    if t["status"] != "OPEN": 
        continue

    px, src = get_price(t["symbol"])
    side = t["side"]
    if px is None:
        continue
        
    pnl = (px - t["entry"]) * t["volume"] * 1000 if side=="Long" else \
          (t["entry"] - px) * t["volume"] * 1000
          
    c1, c2, c3 = st.columns([4,4,2])
    c1.write(t["symbol"] + " (" + side + ")")
    c2.write("PNL: " + str(round(pnl,2)) + " PLN")
    
    if c3.button("Zamknij", key=t["ticket"]):
        bybit.close_order(t["bb_symbol"], "Buy" if side=="Short" else "Sell", t["volume"])
        t["status"] = "CLOSED"
        t["time_close"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        t["pnl_pln"] = pnl
        save_trade_to_db(t)
        update_ai_weights(t["data"], pnl)
        add_log("Zamknieto " + t["symbol"])
        send_telegram("CLOSED " + t["symbol"])
        st.rerun()

# ======================================================================
# LOGI
# ======================================================================
st.subheader("Logi") 
with st.expander("Pokaż logi", expanded=False): 
    for log in reversed(st.session_state.logs): 
        st.write(log)

# ======================================================================
# AUTO REFRESH
# ======================================================================
time.sleep(8) 
st.rerun()

