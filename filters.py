"""
Giriş Filtreleri
3 filtre: KDJ → RSI → 4H Yön Onayı
"""

import pandas as pd

from indicators import detect_rsi_cross, detect_kdj_cross
from config import RSI_FAST, RSI_SLOW, KDJ_PERIOD, KDJ_K, KDJ_D


def check_kdj_filter(df_30m: pd.DataFrame) -> str:
    """KDJ kesişimi son kapanan mumda. Returns: 'long', 'short', or 'none'"""
    return detect_kdj_cross(df_30m, KDJ_PERIOD, KDJ_K, KDJ_D)


def check_rsi_filter(df_30m: pd.DataFrame) -> str:
    """RSI kesişimi son kapanan mumda. Returns: 'long', 'short', or 'none'"""
    return detect_rsi_cross(df_30m, RSI_FAST, RSI_SLOW)


def check_4h_direction(current_price: float, df_4h: pd.DataFrame) -> str:
    """
    4H aktif mumun açılış fiyatına göre yön onayı.
    Anlık fiyat > 4H açılış → long
    Anlık fiyat < 4H açılış → short
    """
    if len(df_4h) == 0:
        return "none"

    active_4h_open = df_4h["open"].iloc[-1]
    if pd.isna(active_4h_open):
        return "none"

    if current_price > active_4h_open:
        return "long"
    elif current_price < active_4h_open:
        return "short"
    return "none"


def evaluate_signal(
    symbol: str,
    current_price: float,
    df_30m: pd.DataFrame,
    df_4h: pd.DataFrame,
) -> dict:
    """
    Tüm filtreleri sırayla kontrol eder.
    Sıra: KDJ → RSI → 4H Yön
    """
    details = {}

    # Filtre 1: KDJ Kesişimi
    kdj_dir = check_kdj_filter(df_30m)
    details["kdj"] = kdj_dir
    if kdj_dir == "none":
        return {"signal": "none", "reason": "KDJ kesişimi yok", "details": details}

    # Filtre 2: RSI Kesişimi
    rsi_dir = check_rsi_filter(df_30m)
    details["rsi"] = rsi_dir
    if rsi_dir == "none":
        return {"signal": "none", "reason": "RSI kesişimi yok", "details": details}

    if rsi_dir != kdj_dir:
        return {
            "signal": "none",
            "reason": f"KDJ/RSI yön uyumsuzluğu",
            "details": details,
        }

    # Filtre 3: 4H Yön Onayı
    dir_4h = check_4h_direction(current_price, df_4h)
    details["dir_4h"] = dir_4h

    if dir_4h != kdj_dir:
        return {
            "signal": "none",
            "reason": f"4H yön uyumsuzluğu",
            "details": details,
        }

    # Tüm filtreler aynı yönü gösterdi
    return {
        "signal": kdj_dir,
        "reason": "Tüm filtreler geçildi (KDJ + RSI + 4H)",
        "details": details,
    }
