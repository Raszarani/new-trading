import numpy as np
import pandas as pd
import yfinance as yf
import json
import os

# ====================================================================
# 1. KONFIGURACJA I ZARZĄDZANIE PLIKAMI
# ====================================================================
DEFAULT_CONFIG = {
    "atr_multiplier_sl": 1.5,          # ATR x value → Stop Loss
    "atr_multiplier_tp": 3.0,          # ATR x value → Take Profit
    "volatility_risk_factor": 1.0,     # Mnożnik bazowy ryzyka
    "sentiment_risk_factor": 1.0,      # Mnożnik dla nastrojów rynku
    "correlation_limit": 0.85,         # Max dopuszczalna korelacja
    "max_new_trades_per_min": 3        # Ochrona przed spamem zleceń
}

RISK_FILE = "risk_engine.json"

def load_risk_config():
    """Ładuje ustawienia ryzyka z pliku JSON lub zwraca domyślne."""
    if not os.path.exists(RISK_FILE):
        save_risk_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    try:
        with open(RISK_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_CONFIG.copy()

def save_risk_config(cfg):
    """Zapisuje bieżącą konfigurację do pliku."""
    with open(RISK_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

# ====================================================================
# 2. KALKULACJA ATR (Average True Range)
# ====================================================================
def get_atr(symbol, interval="5m", period=14):
    """Oblicza zmienność ATR dla dynamicznego SL/TP."""
    try:
        df = yf.Ticker(symbol).history(period="5d", interval=interval).tail(100)
        if len(df) < period:
            return None

        # Obliczanie True Range
        df["H-L"] = df["High"] - df["Low"]
        df["H-PC"] = abs(df["High"] - df["Close"].shift(1))
        df["L-PC"] = abs(df["Low"] - df["Close"].shift(1))
        tr = df[["H-L", "H-PC", "L-PC"]].max(axis=1)
        
        # Średnia krocząca z True Range
        atr = tr.rolling(period).mean().iloc[-1]
        return float(atr)
    except Exception:
        return None

# ====================================================================
# 3. KOREKTY RYZYKA (Sentyment, Zmienność, Korelacja)
# ====================================================================
def sentiment_adjust(sentiment):
    """Zmniejsza ryzyko, jeśli system wykryje niebezpieczeństwo (DANGER)."""
    return 1.0 if sentiment == "SAFE" else 0.5

def volatility_adjust(symbol, interval="5m"):
    """Zmniejsza ryzyko, gdy rynek staje się zbyt chaotyczny (wysoki STD)."""
    try:
        df = yf.Ticker(symbol).history(period="2d", interval=interval)
        if len(df) < 20:
            return 1.0

        std = df["Close"].pct_change().rolling(20).std().iloc[-
