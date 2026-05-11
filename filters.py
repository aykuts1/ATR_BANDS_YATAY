"""
Giriş Filtreleri
5 filtre: ATR% → ADX → KDJ → RSI → 4H Yön Onayı

Sıra mantığı:
  1) ATR%  → ölü/yatay volatilite eler (en ucuz check)
  2) ADX   → trendsiz/yatay piyasa eler
  3) KDJ   → yön sinyali
  4) RSI   → yön teyidi (KDJ ile uyumlu olmalı)
  5) 4H    → üst zaman dilimi yön onayı
"""
import pandas as pd
from indicators import (
    detect_rsi_cross,
    detect_kdj_cross,
    get_adx_value,
    calculate_atr_percent,
)
from config import (
    RSI_FAST, RSI_SLOW,
    KDJ_PERIOD, KDJ_K, KDJ_D,
    ADX_PERIOD, ADX_MIN,
    ATR_PERIOD, ATR_MIN_PCT,
)


def check_atr_filter(df_30m: pd.DataFrame) -> tuple[bool, float]:
    """
    ATR% > ATR_MIN_PCT kontrolü (30dk mumunda, sinyalle aynı mum).
    Returns: (passed, atr_pct_value)
    """
    atr_pct = calculate_atr_percent(df_30m, ATR_PERIOD)
    return (atr_pct > ATR_MIN_PCT), atr_pct


def check_adx_filter(df_30m: pd.DataFrame) -> tuple[bool, float]:
    """
    ADX > ADX_MIN kontrolü (30dk mumunda).
    Returns: (passed, adx_value)
    """
    adx_value = get_adx_value(df_30m, ADX_PERIOD)
    return (adx_value > ADX_MIN), adx_value


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
    Sıra: ATR% → ADX → KDJ → RSI → 4H Yön
    """
    details = {}

    # Filtre 1: ATR% (volatilite tabanı — ölü piyasayı eler)
    atr_ok, atr_pct = check_atr_filter(df_30m)
    details["atr_pct"] = round(atr_pct, 3)
    if not atr_ok:
        return {
            "signal": "none",
            "reason": f"ATR% düşük ({atr_pct:.2f} ≤ {ATR_MIN_PCT})",
            "details": details,
        }

    # Filtre 2: ADX (trend gücü — yatay piyasayı eler)
    adx_ok, adx_val = check_adx_filter(df_30m)
    details["adx"] = round(adx_val, 2)
    if not adx_ok:
        return {
            "signal": "none",
            "reason": f"ADX zayıf ({adx_val:.1f} ≤ {ADX_MIN})",
            "details": details,
        }

    # Filtre 3: KDJ Kesişimi
    kdj_dir = check_kdj_filter(df_30m)
    details["kdj"] = kdj_dir
    if kdj_dir == "none":
        return {"signal": "none", "reason": "KDJ kesişimi yok", "details": details}

    # Filtre 4: RSI Kesişimi (KDJ ile aynı yönde olmalı)
    rsi_dir = check_rsi_filter(df_30m)
    details["rsi"] = rsi_dir
    if rsi_dir == "none":
        return {"signal": "none", "reason": "RSI kesişimi yok", "details": details}
    if rsi_dir != kdj_dir:
        return {
            "signal": "none",
            "reason": "KDJ/RSI yön uyumsuzluğu",
            "details": details,
        }

    # Filtre 5: 4H Yön Onayı
    dir_4h = check_4h_direction(current_price, df_4h)
    details["dir_4h"] = dir_4h
    if dir_4h != kdj_dir:
        return {
            "signal": "none",
            "reason": "4H yön uyumsuzluğu",
            "details": details,
        }

    # Tüm filtreler geçildi
    return {
        "signal": kdj_dir,
        "reason": "Tüm filtreler geçildi (ATR + ADX + KDJ + RSI + 4H)",
        "details": details,
    }
