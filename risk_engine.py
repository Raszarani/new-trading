import numpy as np
import pandas as pd
import yfinance as yf
import json
import os


# ====================================================================
#  CONFIG DOMYŚLNY
# ====================================================================
DEFAULT_CONFIG = {
    "atr_multiplier_sl": 1.5,          # ATR * x → Stop Loss
    "atr_multiplier_tp": 3.0,          # ATR * x → Take Profit
    "volatility_risk_factor": 1.0,     # mnożnik ryzyka dla zmienności
    "sentiment_risk_factor": 1.0,      # mnożnik ryzyka dla sentymentu
    "correlation_limit": 0.85,         # max korelacja przed redukcją ryzyka
    "max_new_trades_per_min": 3        # ochrona przed overtradingiem
}

RISK_FILE = "risk_engine.json"



# ====================================================================
#  ŁADOWANIE I ZAPIS KONFIGURACJI
# ====================================================================

def load_risk_config():
    if not os.path.exists(RISK_FILE):
        save_risk_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG

    try:
        with open(RISK_FILE, "r") as f:
            return json.load(f)
    except:
        return DEFAULT_CONFIG.copy()


def save_risk_config(cfg):
    with open(RISK_FILE, "w") as f:
        json.dump(cfg, f, indent=4)



# ====================================================================
#  ATR — Average True Range (zmienność)
# ====================================================================

def get_atr(symbol, interval="5m", period=14):
    try:
        df = yf.Ticker(symbol).history(period="7d", interval=interval).tail(200)
        if len(df) < period:
            return None

        df["H-L"] = df["High"] - df["Low"]
        df["H-PC"] = abs(df["High"] - df["Close"].shift(1))
        df["L-PC"] = abs(df["Low"] - df["Close"].shift(1))

        tr = df[["H-L", "H-PC", "L-PC"]].max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]

        return atr

    except Exception as e:
        return None



# ====================================================================
#  RISK ADJUST — SENTYMENT
# ====================================================================

def sentiment_adjust(sentiment):
    """
    sentiment: SAFE / DANGER
    """
    if sentiment == "SAFE":
        return 1.0
    else:
        return 0.5   # tryb DANGER zmniejsza ryzyko o połowę



# ====================================================================
#  RISK ADJUST — ZMIENNOŚĆ RYNKU
# ====================================================================

def volatility_adjust(symbol, interval="5m"):
    """
    Na podstawie rolling STD ostatnich 20 świec.
    """
    try:
        df = yf.Ticker(symbol).history(period="2d", interval=interval)
        if len(df) < 20:
            return 1.0

        std = df["Close"].pct_change().rolling(20).std().iloc[-1]

        # Logika dopasowania ryzyka do zmienności
        if std < 0.005:
            return 1.2   # bardzo niska zmienność → można zwiększyć risk
        if std < 0.01:
            return 1.0
        if std < 0.02:
            return 0.8
        if std < 0.04:
            return 0.6
        return 0.4   # bardzo wysoka zmienność → min risk

    except:
        return 1.0



# ====================================================================
#  RISK ADJUST — KORELACJA SYMBOLI
# ====================================================================

def correlation_protection(journal, target_symbol):
    """
    Redukcja ryzyka, gdy aktywo jest mocno skorelowane z otwartymi.
    """
    if len(journal) == 0:
        return 1.0

    try:
        symbols = list({target_symbol} | {t["symbol"] for t in journal})
        data = {}

        # Pobieramy dane dla korelacji
        for s in symbols:
            df = yf.Ticker(s).history(period="7d", interval="1h")
            if df.empty:
                continue
            data[s] = df["Close"].pct_change().fillna(0)

        df_corr = pd.DataFrame(data).corr()

        if target_symbol not in df_corr:
            return 1.0

        corr_vals = df_corr[target_symbol].abs().drop(target_symbol)

        if corr_vals.empty:
            return 1.0

        max_corr = corr_vals.max()

        # Progi redukcji ryzyka
        if max_corr > 0.85:
            return 0.5   # bardzo duża korelacja → duża redukcja ryzyka
        if max_corr > 0.7:
            return 0.75  # średnia korelacja → lekka redukcja

        return 1.0

    except:
        return 1.0



# ====================================================================
#  FINAL RISK CALCULATION
# ====================================================================

def calculate_risk(symbol, base_risk, sentiment, journal, interval="5m"):
    cfg = load_risk_config()

    # Adjustments:
    vol_adj = volatility_adjust(symbol, interval)
    sent_adj = sentiment_adjust(sentiment)
    corr_adj = correlation_protection(journal, symbol)

    # Final risk
    final_risk = base_risk * vol_adj * sent_adj * corr_adj * cfg["volatility_risk_factor"]

    # Constraints:
    final_risk = max(1.0, min(final_risk, 20.0))

    return round(final_risk, 2)



# ====================================================================
#  ATR-BASED SL AND TP
# ====================================================================

def calculate_sl_tp(symbol, entry_price, side, interval="5m"):
    cfg = load_risk_config()
    atr = get_atr(symbol, interval)

    if not atr:
        return None, None

    sl_distance = atr * cfg["atr_multiplier_sl"]
    tp_distance = atr * cfg["atr_multiplier_tp"]

    if side == "Long":
        sl = entry_price - sl_distance
        tp = entry_price + tp_distance
    else:
        sl = entry_price + sl_distance
        tp = entry_price - tp_distance

    return round(sl, 5), round(tp, 5)



# ====================================================================
#  LIMIT NOWYCH TRANSAKCJI (overtrading protection)
# ====================================================================

def can_open_new_trade(journal, max_positions):
    open_positions = [t for t in journal if t["status"] == "OPEN"]
    return len(open_positions) < max_positions



# ====================================================================
#  PODSUMOWANIE RYZYKA – używane przed otwarciem pozycji
# ====================================================================

def risk_summary(symbol, entry_price, side, base_risk, sentiment, journal, interval="5m"):
    final_risk = calculate_risk(symbol, base_risk, sentiment, journal, interval)
    sl, tp = calculate_sl_tp(symbol, entry_price, side, interval)

    return {
        "risk": final_risk,
        "sl": sl,
        "tp": tp
    }
