"""
Teknik İndikatörler
ATR, ATR%, ADX, RSI, KDJ hesaplamaları
"""
import pandas as pd
import numpy as np


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range - Wilder's smoothing"""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return atr


def calculate_atr_percent(df: pd.DataFrame, period: int = 14) -> float:
    """
    ATR% = (ATR / son kapanış fiyatı) × 100
    Volatiliteyi fiyattan bağımsız ölçer.
    Düşük ATR% = ölü/yatay piyasa.
    """
    atr = calculate_atr(df, period)
    last_atr = atr.iloc[-1]
    last_price = df["close"].iloc[-1]
    if last_price == 0 or pd.isna(last_atr):
        return 0.0
    return (last_atr / last_price) * 100


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average Directional Index (Wilder's metodu)
    ADX trend gücünü ölçer (yön değil).
    < 20 → trendsiz/yatay
    20-25 → zayıf trend
    > 25 → güçlü trend
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # +DM, -DM (yön hareketleri)
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=df.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=df.index,
    )

    # True Range
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Wilder's smoothing
    atr = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_dm_s = plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    minus_dm_s = minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    # +DI, -DI
    plus_di = 100 * (plus_dm_s / atr)
    minus_di = 100 * (minus_dm_s / atr)

    # DX
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)

    # ADX (DX'in Wilder's smoothing'i)
    adx = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    return adx


def get_adx_value(df: pd.DataFrame, period: int = 14) -> float:
    """Son ADX değerini döner"""
    adx = calculate_adx(df, period)
    val = adx.iloc[-1]
    return float(val) if not pd.isna(val) else 0.0


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI - Wilder's smoothing"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_kdj(df: pd.DataFrame, period: int = 9, k_period: int = 3, d_period: int = 3):
    """KDJ İndikatörü (9, 3, 3)"""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    lowest_low = low.rolling(window=period, min_periods=period).min()
    highest_high = high.rolling(window=period, min_periods=period).max()
    rsv = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    k = rsv.ewm(alpha=1 / k_period, adjust=False, min_periods=k_period).mean()
    d = k.ewm(alpha=1 / d_period, adjust=False, min_periods=d_period).mean()
    j = 3 * k - 2 * d
    return k, d, j


def detect_rsi_cross(df: pd.DataFrame, fast: int = 6, slow: int = 14) -> str:
    """
    RSI(6) - RSI(14) kesişimi son KAPANAN mumda
    iloc[-2] = son kapanan mum, iloc[-3] = bir önceki kapanan mum
    Returns: 'long', 'short', or 'none'
    """
    rsi_fast = calculate_rsi(df["close"], fast)
    rsi_slow = calculate_rsi(df["close"], slow)
    if len(rsi_fast) < 3 or len(rsi_slow) < 3:
        return "none"
    fast_curr = rsi_fast.iloc[-2]
    slow_curr = rsi_slow.iloc[-2]
    fast_prev = rsi_fast.iloc[-3]
    slow_prev = rsi_slow.iloc[-3]
    if any(pd.isna([fast_curr, fast_prev, slow_curr, slow_prev])):
        return "none"
    if fast_prev <= slow_prev and fast_curr > slow_curr:
        return "long"
    if fast_prev >= slow_prev and fast_curr < slow_curr:
        return "short"
    return "none"


def detect_kdj_cross(df: pd.DataFrame, period: int = 9, k_p: int = 3, d_p: int = 3) -> str:
    """
    KDJ kesişimi: J çizgisi K'yı keser (D görmezden gelinir)
    Son kapanmış mumda kontrol edilir.
    """
    k, d, j = calculate_kdj(df, period, k_p, d_p)
    if len(j) < 3:
        return "none"
    j_curr = j.iloc[-2]
    k_curr = k.iloc[-2]
    j_prev = j.iloc[-3]
    k_prev = k.iloc[-3]
    if any(pd.isna([j_curr, j_prev, k_curr, k_prev])):
        return "none"
    crossed_up = (j_prev <= k_prev) and (j_curr > k_curr)
    crossed_down = (j_prev >= k_prev) and (j_curr < k_curr)
    if crossed_up:
        return "long"
    if crossed_down:
        return "short"
    return "none"


def get_atr_value(df: pd.DataFrame, period: int = 14) -> float:
    """Son ATR değerini döner"""
    atr = calculate_atr(df, period)
    val = atr.iloc[-1]
    return float(val) if not pd.isna(val) else 0.0
