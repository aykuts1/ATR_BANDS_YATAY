"""
EMA (Exponential Moving Average) hesaplamaları.
"""
from typing import List, Optional
import config


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


def compute_tunnels(klines: List[dict], tunnel_period: int, signal_period: int) -> Optional[dict]:
    """
    Compute EMA tunnels from kline data.

    Returns dict with latest values:
    {
        "ema_tunnel_high": float,       # EMA100 of highs
        "ema_tunnel_low": float,        # EMA100 of lows
        "ema_signal_high": float,       # EMA21 of highs
        "ema_signal_low": float,        # EMA21 of lows
        "ema_signal_close": float,      # EMA21 of closes (yön + çıkış için)
        "ema_signal_close_avg": float,  # Son N mumun EMA21 close ortalaması
        "last_close": float,            # Son kapanan mumun close fiyatı
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

    # EMA21 close son N mumun ortalaması (yön filtresi için)
    lookback = config.EMA_DIRECTION_LOOKBACK
    recent_closes = [v for v in ema_signal_close[-lookback:] if v is not None]
    if len(recent_closes) < lookback:
        return None
    sc_avg = sum(recent_closes) / len(recent_closes)

    return {
        "ema_tunnel_high": th,
        "ema_tunnel_low": tl,
        "ema_signal_high": sh,
        "ema_signal_low": sl,
        "ema_signal_close": sc,
        "ema_signal_close_avg": sc_avg,
        "last_close": klines[-1]["close"],
    }
