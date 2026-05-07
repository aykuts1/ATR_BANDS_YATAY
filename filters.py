"""
Giriş Filtreleri
6 filtre: ATR%, ADX, 2H Yön, 4H Yön, KDJ, RSI
"""
import pandas as pd
from indicators import (
    calculate_atr_percent,
    get_adx_value,
    detect_rsi_cross,
    detect_kdj_cross,
)
from config import (
    ATR_PERIOD,
    ADX_PERIOD,
    ATR_MIN_PCT,
    ADX_MIN,
    RSI_FAST,
    RSI_SLOW,
    KDJ_PERIOD,
    KDJ_K,
    KDJ_D,
)


def check_atr_filter(df_30m: pd.DataFrame) -> tuple[bool, float]:
    """ATR% ≥ 0.7 kontrol"""
    atr_pct = calculate_atr_percent(df_30m, ATR_PERIOD)
    return atr_pct >= ATR_MIN_PCT, atr_pct


def check_adx_filter(df_30m: pd.DataFrame) -> tuple[bool, float]:
    """ADX > 25 kontrol"""
    adx = get_adx_value(df_30m, ADX_PERIOD)
    return adx > ADX_MIN, adx


def check_mtf_direction(current_price: float, df_2h: pd.DataFrame, df_4h: pd.DataFrame) -> str:
    """
    2H ve 4H aktif mumun açılış fiyatına göre yön
    Aktif mum = son mum (henüz kapanmamış olan)
    Returns: 'long', 'short', or 'none'
    """
    if len(df_2h) == 0 or len(df_4h) == 0:
        return "none"

    # Aktif mum = en son mum (kapanmamış)
    active_2h_open = df_2h["open"].iloc[-1]
    active_4h_open = df_4h["open"].iloc[-1]

    if pd.isna(active_2h_open) or pd.isna(active_4h_open):
        return "none"

    direction_2h = "long" if current_price > active_2h_open else "short" if current_price < active_2h_open else "none"
    direction_4h = "long" if current_price > active_4h_open else "short" if current_price < active_4h_open else "none"

    if direction_2h == direction_4h and direction_2h != "none":
        return direction_2h
    return "none"


def check_kdj_filter(df_30m: pd.DataFrame) -> str:
    """KDJ kesişimi son kapanan mumda"""
    return detect_kdj_cross(df_30m, KDJ_PERIOD, KDJ_K, KDJ_D)


def check_rsi_filter(df_30m: pd.DataFrame) -> str:
    """RSI kesişimi son kapanan mumda"""
    return detect_rsi_cross(df_30m, RSI_FAST, RSI_SLOW)


def evaluate_signal(
    symbol: str,
    current_price: float,
    df_30m: pd.DataFrame,
    df_2h: pd.DataFrame,
    df_4h: pd.DataFrame,
) -> dict:
    """
    Tüm filtreleri kontrol eder.
    Returns: {
        'signal': 'long' | 'short' | 'none',
        'reason': str (neden işlem açılmadı),
        'details': dict (debug için)
    }
    """
    details = {}

    # Filtre 1: ATR%
    atr_ok, atr_pct = check_atr_filter(df_30m)
    details["atr_pct"] = round(atr_pct, 3)
    if not atr_ok:
        return {"signal": "none", "reason": f"ATR% düşük ({atr_pct:.2f}%)", "details": details}

    # Filtre 2: ADX
    adx_ok, adx_val = check_adx_filter(df_30m)
    details["adx"] = round(adx_val, 2)
    if not adx_ok:
        return {"signal": "none", "reason": f"ADX düşük ({adx_val:.1f})", "details": details}

    # Filtre 3 + 4: MTF Yön
    mtf_dir = check_mtf_direction(current_price, df_2h, df_4h)
    details["mtf_direction"] = mtf_dir
    if mtf_dir == "none":
        return {"signal": "none", "reason": "2H/4H yön çelişkisi", "details": details}

    # Filtre 5: KDJ
    kdj_dir = check_kdj_filter(df_30m)
    details["kdj"] = kdj_dir
    if kdj_dir == "none":
        return {"signal": "none", "reason": "KDJ kesişimi yok", "details": details}

    # Filtre 6: RSI
    rsi_dir = check_rsi_filter(df_30m)
    details["rsi"] = rsi_dir
    if rsi_dir == "none":
        return {"signal": "none", "reason": "RSI kesişimi yok", "details": details}

    # Tüm yönler aynı olmalı
    if mtf_dir == kdj_dir == rsi_dir:
        return {"signal": mtf_dir, "reason": "Tüm filtreler geçildi", "details": details}

    return {
        "signal": "none",
        "reason": f"Yön uyumsuzluğu: MTF={mtf_dir}, KDJ={kdj_dir}, RSI={rsi_dir}",
        "details": details,
    }
