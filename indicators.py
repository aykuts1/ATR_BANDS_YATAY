"""
EMA (Exponential Moving Average) ve ATR hesaplamaları.
"""
from typing import List, Optional


def ema(values: List[float], period: int) -> List[Optional[float]]:
    """
    Standard EMA calculation.
    Returns list of EMA values (None until enough data).
    """
    if len(values) < period:
        return [None] * len(values)

    result: List[Optional[float]] = [None] * len(values)
    multiplier = 2.0 / (period + 1)

    # SMA for initial seed
    sma = sum(values[:period]) / period
    result[period - 1] = sma

    # EMA for subsequent values
    prev_ema = sma
    for i in range(period, len(values)):
        curr_ema = (values[i] - prev_ema) * multiplier + prev_ema
        result[i] = curr_ema
        prev_ema = curr_ema

    return result


def atr(klines: List[dict], period: int) -> Optional[float]:
    """
    Average True Range (Wilder's smoothing).
    Returns latest ATR value or None if insufficient data.

    True Range = max(
        high - low,
        abs(high - prev_close),
        abs(low - prev_close)
    )
    """
    if len(klines) < period + 1:
        return None

    # True Range serisi
    trs: List[float] = []
    for i in range(1, len(klines)):
        h = klines[i]["high"]
        l = klines[i]["low"]
        prev_c = klines[i - 1]["close"]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)

    if len(trs) < period:
        return None

    # İlk ATR = ilk N TR'nin ortalaması (Wilder seed)
    atr_val = sum(trs[:period]) / period

    # Wilder smoothing: ATR = (prev_ATR * (period-1) + TR) / period
    for i in range(period, len(trs)):
        atr_val = (atr_val * (period - 1) + trs[i]) / period

    return atr_val


def compute_tunnels(klines: List[dict], tunnel_period: int, signal_period: int,
                    atr_period: int) -> Optional[dict]:
    """
    Compute EMA tunnels + ATR from kline data.
    Returns dict with latest values:
    {
        "ema_tunnel_high": float,   # EMA100 of highs
        "ema_tunnel_low": float,    # EMA100 of lows
        "ema_signal_high": float,   # EMA21 of highs
        "ema_signal_low": float,    # EMA21 of lows
        "ema_signal_close": float,  # EMA21 of closes
        "atr": float,               # ATR(14) - CE için
        "last_close": float,        # Son kapanan mumun close fiyatı
    }
    or None if insufficient data.
    """
    if len(klines) < tunnel_period:
        return None

    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    closes = [k["close"] for k in klines]

    ema_tunnel_high = ema(highs, tunnel_period)
    ema_tunnel_low = ema(lows, tunnel_period)
    ema_signal_high = ema(highs, signal_period)
    ema_signal_low = ema(lows, signal_period)
    ema_signal_close = ema(closes, signal_period)

    # Get latest values
    th = ema_tunnel_high[-1]
    tl = ema_tunnel_low[-1]
    sh = ema_signal_high[-1]
    sl = ema_signal_low[-1]
    sc = ema_signal_close[-1]

    if None in (th, tl, sh, sl, sc):
        return None

    # ATR hesabı
    atr_val = atr(klines, atr_period)
    if atr_val is None or atr_val <= 0:
        return None

    return {
        "ema_tunnel_high": th,
        "ema_tunnel_low": tl,
        "ema_signal_high": sh,
        "ema_signal_low": sl,
        "ema_signal_close": sc,
        "atr": atr_val,
        "last_close": klines[-1]["close"],
    }
