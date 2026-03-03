import json
import os
import numpy as np

AI_WEIGHTS_FILE = "ai_weights.json"


# ==========================================================
# 1. Domyślne wagi AI — jeśli nie istnieje plik z wagami
# ==========================================================
DEFAULT_WEIGHTS = {
    "rsi_weight": 1.0,
    "volume_weight": 1.0,
    "trend_weight": 1.0,
    "oracle_weight": 1.0,

    "risk_adjust": 1.0,
    "sl_adjust": 1.0,
    "tp_adjust": 1.0,

    "learning_rate": 0.05,   # prędkość uczenia AI
    "max_weight": 3.0,
    "min_weight": 0.25
}


# ==========================================================
# 2. Ładowanie wag AI
# ==========================================================
def load_ai_weights():
    if not os.path.exists(AI_WEIGHTS_FILE):
        save_ai_weights(DEFAULT_WEIGHTS)
        return DEFAULT_WEIGHTS

    try:
        with open(AI_WEIGHTS_FILE, "r") as f:
            return json.load(f)
    except:
        return DEFAULT_WEIGHTS.copy()


# ==========================================================
# 3. Zapisywanie wag AI
# ==========================================================
def save_ai_weights(weights):
    with open(AI_WEIGHTS_FILE, "w") as f:
        json.dump(weights, f, indent=4)


# ==========================================================
# 4. Ocena jakości transakcji na podstawie sygnału wejściowego
#
# trend_score = zgodność sygnału
# volume_score = siła wolumenu
# rsi_score = "czystość" wejścia
# oracle_score = trafność Oracle Path
#
# Wynik transakcji -> aktualizacja wag
# ==========================================================
def evaluate_entry_signal(entry_context, pnl_pln):
    """
    entry_context:
        {
            "rsi": 52.1,
            "slope": 0.023,
            "vol": 1.8,
            "oracle_diff": 0.5
        }
    """
    rsi = entry_context.get("rsi", 50)
    slope = entry_context.get("slope", 0)
    vol = entry_context.get("vol", 1)
    oracle_signal = entry_context.get("oracle_diff", 0)

    # Im bliżej środka RSI, tym lepsza jakość wejścia (bez ekstremów)
    rsi_score = 1 - abs(rsi - 50) / 50

    # trend_score = znak slope + siła
    trend_score = min(max(abs(slope) * 5, 0), 1)

    # volume spike
    volume_score = min(vol / 2, 1)

    # oracle
    oracle_score = min(abs(oracle_signal) / 2, 1)

    # jakość transakcji
    base_quality = (rsi_score + trend_score + volume_score + oracle_score) / 4

    # penalizacja gdy strata
    quality = base_quality if pnl_pln > 0 else -base_quality

    return {
        "rsi_score": rsi_score,
        "trend_score": trend_score,
        "volume_score": volume_score,
        "oracle_score": oracle_score,
        "quality": quality
    }


# ==========================================================
# 5. Aktualizacja wag AI
# ==========================================================
def update_ai_weights(entry_context, pnl_pln):
    weights = load_ai_weights()
    lr = weights["learning_rate"]

    eval_scores = evaluate_entry_signal(entry_context, pnl_pln)

    # Aktualizacje wag:
    weights["rsi_weight"] += lr * eval_scores["rsi_score"] * np.sign(pnl_pln)
    weights["trend_weight"] += lr * eval_scores["trend_score"] * np.sign(pnl_pln)
    weights["volume_weight"] += lr * eval_scores["volume_score"] * np.sign(pnl_pln)
    weights["oracle_weight"] += lr * eval_scores["oracle_score"] * np.sign(pnl_pln)

    # Aktualizacja składników ryzyka:
    weights["risk_adjust"] += lr * (1 if pnl_pln > 0 else -1)
    weights["sl_adjust"] += lr * (-1 if pnl_pln > 0 else 1)
    weights["tp_adjust"] += lr * (1 if pnl_pln > 0 else -1)

    # Ograniczenia
    for key in weights:
        if key.endswith("_weight") or key.endswith("_adjust"):
            weights[key] = float(
                max(
                    DEFAULT_WEIGHTS["min_weight"],
                    min(weights[key], DEFAULT_WEIGHTS["max_weight"])
                )
            )

    save_ai_weights(weights)
    return weights


# ==========================================================
# 6. Funkcja pomocnicza — AI wylicza korekty SL/TP/RISK
# ==========================================================
def ai_adjust_params(base_risk, base_sl, base_tp):
    weights = load_ai_weights()

    new_risk = base_risk * weights["risk_adjust"]
    new_sl = base_sl * weights["sl_adjust"]
    new_tp = base_tp * weights["tp_adjust"]

    return {
        "risk": round(new_risk, 3),
        "sl": round(new_sl, 3),
        "tp": round(new_tp, 3),
        "weights": weights
    }