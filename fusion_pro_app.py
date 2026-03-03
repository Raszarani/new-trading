import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import time
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ai_engine import ai_adjust_params, update_ai_weights, load_ai_weights
from risk_engine import calculate_risk, calculate_sl_tp, risk_summary, can_open_new_trade

import os
import requests

# =====================================================
# KONFIGURACJA SECRETS (Dla Streamlit Cloud)
# =====================================================
# Ustaw te wartości w Settings -> Secrets na share.streamlit.io
TELEGRAM_TOKEN = st.secrets.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "")
DB_FILE = "paper_trade_history.csv"

def send_telegram(msg):
    if not TELEGRAM_TOKEN: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=3)
    except: pass

# =====================================================
# STAN SESJI (Wirtualny Portfel)
# =====================================================
if "journal" not in st.session_state:
    st.session_state.journal = []
if "balance" not in st.session_state:
    st.session_state.balance = 10000.0  # Twój początkowy kapitał
if "logs" not in st.session_state:
    st.session_state.logs = []

def add_log(msg):
    t = datetime.now().strftime("%H:%M:%S")
    st.session_state.logs.append(f"[{t}] {msg}")

# =====================================================
# STATYSTYKI I ANALIZA WYNIKÓW
# =====================================================
def show_performance_dashboard(history):
    if history.empty:
        st.info("Czekam na pierwsze zamknięte transakcje, aby wygenerować statystyki...")
        return

    st.divider()
    st.header("📊 Panel Analityczny AI")
    
    col1, col2, col3, col4 = st.columns(4)
    
    total_trades = len(history)
    win_rate = (len(history[history['pnl_pln'] > 0]) / total_trades) * 100
    total_pnl = history['pnl_pln'].sum()
    avg_trade = history['pnl_pln'].mean()

    col1.metric("Łączny Zysk", f"{total_pnl:.2f} PLN")
    col2.metric("Skuteczność (WR)", f"{win_rate:.1f}%")
    col3.metric("Liczba Handli", total_trades)
    col4.metric("Średni Trade", f"{avg_trade:.2f} PLN")

    # Wykres Equity Curve
    history["cum_pnl"] = history["pnl_pln"].cumsum() + 10000
    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(x=history.index, y=history["cum_pnl"], mode='lines+markers', name="Kapitał", line=dict(color="#00ffcc")))
    fig_eq.update_layout(title="Krzywa Kapitału (Wirtualna)", template="plotly_dark", height=300)
    st.plotly_chart(fig_eq, use_container_width=True)

    # Analiza wag AI
    weights = load_ai_weights()
    st.subheader("🧠 Aktualne priorytety AI")
    w_cols = st.columns(4)
    w_cols[0].progress(min(weights['rsi_weight']/3, 1.0), text=f"RSI: {weights['rsi_weight']:.2f}")
    w_cols[1].progress(min(weights['trend_weight']/3, 1.0), text=f"Trend: {weights['trend_weight']:.2f}")
    w_cols[2].progress(min(weights['volume_weight']/3, 1.0), text=f"Vol: {weights['volume_weight']:.2f}")
    w_cols[3].progress(min(weights['oracle_weight']/3, 1.0), text=f"Oracle: {weights['oracle_weight']:.2f}")

# =====================================================
# SILNIK TRANSAKCYJNY (PAPER TRADING)
# =====================================================
def execute_paper_trade(symbol, side, px, context):
    ai_mod = ai_adjust_params(8.0, 2.5, 5.0) # bazowe parametry
    rs = risk_summary(symbol, px, side, ai_mod["risk"], "SAFE", st.session_state.journal, "5m")
    
    # Obliczanie wielkości pozycji na podstawie kapitału
    pos_size = (st.session_state.balance * (rs["risk"]/100)) / px

    trade = {
        "symbol": symbol,
        "side": side,
        "entry_px": px,
        "size": pos_size,
        "sl": rs["sl"],
        "tp": rs["tp"],
        "status": "OPEN",
        "time_open": datetime.now(),
        "data": context
    }
    st.session_state.journal.append(trade)
    add_log(f"🤖 AI otwiera {side} na {symbol} po {px}")
    send_telegram(f"🚀 *AUTO-TRADE:* {side} {symbol} @ {px}")
    
# =====================================================
# SEKCJA ANALIZY I SKANERA (Wymuszenie wyświetlania)
# =====================================================
st.header("🔍 Analiza Rynku i Akcje AI")

# Pobieranie wyników skanowania
scan_results = []
for s in assets:
    with st.spinner(f"Analizowanie {s}..."):
        a = get_analysis(s, "5m") # Interwał z Twojego kodu 
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
    # 1. Tabela akcji
    df_scan = pd.DataFrame(scan_results)
    st.subheader("📈 Aktywne Skanowanie")
    st.dataframe(df_scan[["Symbol", "Cena", "Vol", "RSI", "Trend"]], use_container_width=True)

    # 2. Wykres techniczny dla pierwszego symbolu
    target = st.selectbox("Wybierz aktywo do szczegółowej analizy:", [x["Symbol"] for x in scan_results])
    t_data = next(x["data"] for x in scan_results if x["Symbol"] == target)
    
    # Renderowanie wykresu (Candlesticks + Oracle Path) 
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3])
    fig.add_trace(go.Candlestick(x=t_data["df"].index, open=t_data["df"]["Open"], high=t_data["df"]["High"], 
                                 low=t_data["df"]["Low"], close=t_data["df"]["Close"], name="Cena"), row=1, col=1)
    
    # Dodanie Oracle Path (prognoza AI) 
    fig.add_trace(go.Scatter(y=t_data["f_y"], mode="lines", name="Oracle Path", line=dict(color="yellow", dash="dot")), row=1, col=1)
    
    st.plotly_chart(fig, use_container_width=True)
else:
    st.warning("Nie udało się pobrać danych z Yahoo Finance. Sprawdź połączenie z internetem.")
    
# =====================================================
# UI I PĘTLA GŁÓWNA
# =====================================================
st.set_page_config(page_title="Fusion SOLO AI", layout="wide")
st.title("🤖 Fusion SOLO – Autonomiczny Bot Analityczny")

# Sidebar - ustawienia
st.sidebar.header("Ustawienia Symulacji")
init_balance = st.sidebar.number_input("Kapitał startowy (PLN)", value=10000)
auto_trade = st.sidebar.toggle("Uruchom Bota AI", value=True)

# Pobieranie danych i skanowanie 
assets = ["BTC-USD", "ETH-USD", "NVDA", "TSLA", "SOL-USD"]
active_trades = [t for t in st.session_state.journal if t["status"] == "OPEN"]

# Monitoring pozycji i zamykanie (SL/TP)
for t in active_trades:
    # Symulacja pobrania ceny (w realu: yfinance)
    curr_df = yf.Ticker(t["symbol"]).history(period="1d", interval="1m")
    if curr_df.empty: continue
    curr_px = curr_df["Close"].iloc[-1]
    
    pnl = (curr_px - t["entry_px"]) * t["size"] if t["side"] == "Long" else (t["entry_px"] - curr_px) * t["size"]
    
    # Logika zamknięcia
    hit_sl = (t["side"] == "Long" and curr_px <= t["sl"]) or (t["side"] == "Short" and curr_px >= t["sl"])
    hit_tp = (t["side"] == "Long" and curr_px >= t["tp"]) or (t["side"] == "Short" and curr_px <= t["tp"])
    
    if hit_sl or hit_tp:
        t["status"] = "CLOSED"
        t["pnl_pln"] = pnl
        st.session_state.balance += pnl
        update_ai_weights(t["data"], pnl) # AI uczy się na wyniku
        add_log(f"✅ Zamknięto {t['symbol']} | Wynik: {pnl:.2f} PLN")
        send_telegram(f"💰 *TRADE CLOSED:* {t['symbol']}\nWynik: {pnl:.2f} PLN")

# Dashboard statystyk
history_df = pd.DataFrame([t for t in st.session_state.journal if t["status"] == "CLOSED"])
show_performance_dashboard(history_df)

# Auto-refresh pętli
st.write(f"Ostatnia aktualizacja: {datetime.now().strftime('%H:%M:%S')}")
time.sleep(15)
st.rerun()
