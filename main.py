"""
Ema100-Ema21-Tunel Bot - Ana Döngü

Her 30 saniyede tarama yapar:
- Açık pozisyonlar için çıkış kontrolü
- Açık olmayan coinler için giriş kontrolü
- 60 saniyede bir klines cache yenilenir
- 5 dakikada bir Telegram durum raporu
"""
import time
import traceback
from datetime import datetime, timezone
from typing import Dict, List, Optional

import config
import indicators
import strategy
import telegram_bot as tg
from bybit_client import BybitClient
from position_manager import Position, PositionManager


# ============================================================
# GLOBAL STATE
# ============================================================
STAKE_USDT: float = 0.0
ACTIVE_SYMBOLS: List[str] = []          # 50x destekleyen coinler
PREVIOUS_PRICES: Dict[str, float] = {}  # Geçen tarama fiyatları (kesişim için)
TUNNEL_CACHE: Dict[str, dict] = {}      # Klines cache: {symbol: {tunnels, last_update_ts}}
EXTERNAL_POSITIONS: set = set()         # Manuel açılmış pozisyonlar

# Session stats
SESSION_START_BALANCE: float = 0.0
SESSION_PNL: float = 0.0
RECENT_TRADES: List[dict] = []          # Son 5dk içinde kapanan işlemler


# ============================================================
# HELPERS
# ============================================================
def now_ts() -> float:
    return time.time()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def compute_initial_sl(side: str, entry_price: float) -> float:
    """Calculate %1 stop loss price."""
    if side == "Buy":
        return entry_price * (1 - config.INITIAL_SL_PERCENT)
    return entry_price * (1 + config.INITIAL_SL_PERCENT)


# ============================================================
# STARTUP - 50x KALDIRAÇ KONTROLÜ
# ============================================================
def check_leverage_support(client: BybitClient) -> tuple:
    """
    Her coin için 50x destekleniyor mu kontrol eder.
    Returns: (active_symbols, skipped_symbols)
    """
    active: List[str] = []
    skipped: List[str] = []

    for symbol in config.SYMBOLS:
        try:
            info = client.get_instrument_info(symbol)
            max_lev = info["max_leverage"]
            if max_lev < config.LEVERAGE:
                skipped.append(symbol)
                tg.send_leverage_unsupported(symbol, max_lev)
                print(f"[SKIP] {symbol} max leverage {max_lev}x < {config.LEVERAGE}x")
                continue

            # 50x destekliyor → isolated + leverage ayarla
            try:
                client.set_isolated_margin(symbol, config.LEVERAGE)
            except Exception as e:
                print(f"[WARN] set_isolated_margin {symbol}: {e}")
            try:
                client.set_leverage(symbol, config.LEVERAGE)
            except Exception as e:
                msg = str(e)
                if "110013" in msg:
                    # Leverage limit exceeded
                    skipped.append(symbol)
                    tg.send_leverage_unsupported(symbol, max_lev)
                    print(f"[SKIP] {symbol}: 110013 leverage exceeded")
                    continue
                print(f"[WARN] set_leverage {symbol}: {e}")

            active.append(symbol)
            print(f"[OK] {symbol} 50x ready")
            time.sleep(0.15)  # rate limit

        except Exception as e:
            print(f"[ERR] check_leverage {symbol}: {e}")
            skipped.append(symbol)

    return active, skipped


# ============================================================
# KLINES & TUNNEL CACHE
# ============================================================
def refresh_tunnels(client: BybitClient, symbol: str, force: bool = False) -> Optional[dict]:
    """
    Klines'i cache'e alır ve tünel değerlerini hesaplar.
    """
    now = now_ts()
    cached = TUNNEL_CACHE.get(symbol)
    if not force and cached and (now - cached["ts"] < config.KLINE_REFRESH_INTERVAL):
        return cached["tunnels"]

    try:
        klines = client.get_klines(symbol, config.TIMEFRAME, config.KLINE_LIMIT)
        # Son (açık) mumu çıkar - kapanmış mumlardan EMA hesaplanır
        if len(klines) >= 2:
            klines = klines[:-1]
        tunnels = indicators.compute_tunnels(
            klines,
            config.EMA_TUNNEL_PERIOD,
            config.EMA_SIGNAL_PERIOD,
        )
        if tunnels:
            TUNNEL_CACHE[symbol] = {"tunnels": tunnels, "ts": now}
        return tunnels
    except Exception as e:
        print(f"[ERR] refresh_tunnels {symbol}: {e}")
        return None


# ============================================================
# POZISYON AÇMA - Limit emir ile retry, sonra market
# ============================================================
def open_position(client: BybitClient, pm: PositionManager,
                  symbol: str, side: str, tunnels: dict) -> None:
    """Pozisyon aç: önce limit emir (3 deneme), sonra market."""
    try:
