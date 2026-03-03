import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import time
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Importy lokalnych silników (muszą być w tym samym folderze na GitHub)
from ai_engine import ai_adjust_params, update_ai_weights, load_ai_weights
from risk_engine import risk_summary, can_open_new_trade

# =====================================================
# KONFIGURACJA I STAN SESJI
# =====================================================
st.set_page_config(page_title="Fusion AI Solo Bot", layout="wide")

if "journal" not in st.session_state:
    st.session_state.journal = []
if "balance" not in st.session_state:
    st.session_state.balance = 10000.0 # Twój startowy kapitał
if "logs" not in st.session_state:
    st.session_state.logs = []

def add_log(msg):
    t = datetime.now().strftime("%H:%M:%S")
    st.session_state.logs.append(f"[{t}] {msg}")

# Lista aktywów do skanowania
ASSETS = ["BTC-USD", "ETH-USD", "SOL-USD", "NVDA", "TSLA", "EURUSD=X"]

# =====================================================
# ANALIZA AI I SKANER
# =====================================================
def get_analysis(symbol, interval):
    try:
        df = yf.Ticker(symbol).history(period="2d", interval=interval)
        if len(df) < 20: return None

        # RSI - obliczenia techniczne 
        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + gain / (loss + 1e-9)))

        # Trend (slope)
        y = df["Close"].tail(20).values
        slope = np.polyfit(np.arange(len(y)), y, 1)[0]
        
        # Oracle Path (prognoza) 
        forward = slope * np.arange(len(y)-1, len(y)+12) + y[-1]

        return {
            "df": df, "px": df["Close"].iloc[-1], "rsi": rsi.iloc[-1],
            "slope": slope, "vol": df["Volume"].iloc[-1] / (df["Volume"].mean() + 1e-9),
            "f_y": forward
        }
    except: return None

# =====================================================
# PANEL STATYSTYK (WNIOSKI AI)
# =====================================================
def show_stats():
    st.header("📊 Wyniki i Wnioski AI")
    col1, col2, col3 = st.columns(3)
    
    history = pd.DataFrame([t for t in st.session_state.journal if t["status"] == "CLOSED"])
    
    if not history.empty:
        win_rate = (len(history[history['pnl_pln'] > 0]) / len(history)) * 100
        total_pnl = history['pnl_pln'].sum()
        col1.metric("Kapitał", f"{st.session_state.balance:.2f} PLN", f"{total_pnl:.2f}")
        col2.metric("Skuteczność AI", f"{win_rate:.1f}%")
        col3.metric("Zamknięte transakcje", len(history))
        
        # Equity Curve 
        history["cum_pnl"] = history["pnl_pln"].cumsum() + 10000
        fig = go.Figure(go.Scatter(x=history.index, y=history["cum_pnl"], mode='lines+markers', name="Equity"))
        fig.update_layout(template="plotly_dark", height=250)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Bot analizuje rynek. Czekam na pierwsze transakcje...")

# =====================================================
# GŁÓWNA PĘTLA DZIAŁANIA
# =====================================================
st.title("🤖 Fusion PRO – Niezależny Bot AI")

show_stats()

st.subheader("🔍 Skaner i Akcje")
scan_results = []
for s in ASSETS:
    a = get_analysis(s, "5m")
    if a:
        scan_results.append({"Symbol": s, "Cena": a["px"], "RSI": a["rsi"], "Trend": "UP" if a["slope"] > 0 else "DOWN", "data": a})

if scan_results:
    st.table(pd.DataFrame(scan_results).drop(columns="data"))

# Automatyczna analiza i handel (symulacja)
if st.toggle("Uruchom Automatykę AI", value=True):
    for res in scan_results:
        # Logika decyzyjna oparta na Twoich wagach AI
        if res["data"]["rsi"] < 30 and res["Trend"] == "UP":
            # Symulacja otwarcia pozycji 
            add_log(f"AI wykryło okazję na {res['Symbol']} (RSI: {res['data']['rsi']:.1f})")
            # Tu następuje wywołanie risk_summary i update_ai_weights po zamknięciu

st.divider()
st.caption("Logi systemowe:")
st.write(st.session_state.logs[-5:])

time.sleep(15)
st.rerun()
