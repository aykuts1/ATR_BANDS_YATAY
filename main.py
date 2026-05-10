"""
Ana Bot Döngüsü
- Her 30 dakikalık mum kapanışında giriş taraması
- Her dakika açık pozisyonların CE takibi
"""

import logging
import time
import threading
from datetime import datetime, timezone

from config import (
    SYMBOLS, TIMEFRAME, MTF_4H,
    LEVERAGE, STAKE_PERCENT, SL_ATR_MULTIPLIER,
    MAX_OPEN_POSITIONS, EXIT_CHECK_INTERVAL,
    BYBIT_TESTNET, LOG_LEVEL, ATR_PERIOD,
)
from exchange import BybitExchange
from filters import evaluate_signal
from indicators import get_atr_value
from position_manager import PositionManager
import telegram_bot as tg

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")

# ============================================================
# GLOBAL STATE
# ============================================================
exchange = BybitExchange()
position_manager = PositionManager()
state_lock = threading.Lock()

INITIAL_BALANCE = 0.0
FIXED_STAKE = 0.0


# ============================================================
# YARDIMCI FONKSIYONLAR
# ============================================================
def calculate_position_qty(price: float, stake: float) -> float:
    """Stake ve fiyata göre miktar (raw float)."""
    if price <= 0:
        return 0.0
    notional = stake * LEVERAGE
    return notional / price


def stop_loss_price_atr(side: str, entry: float, atr: float) -> float:
    """1.5 ATR uzaklıkta borsa SL fiyatı."""
    if side == "long":
        return entry - (SL_ATR_MULTIPLIER * atr)
    else:
        return entry + (SL_ATR_MULTIPLIER * atr)


# ============================================================
# GIRIS TARAMA
# ============================================================
def scan_for_entries():
    logger.info("=== Giriş taraması başladı ===")
    scanned = []           # (symbol, reason)
    errors = []            # (symbol, error)
    opened = []            # (symbol, side)
    skipped_capacity = []  # (symbol, side) - sinyal var ama kapasite dolu

    with state_lock:
        live_positions = exchange.get_open_positions()
        sync_positions(live_positions)

        capacity_full = position_manager.count() >= MAX_OPEN_POSITIONS
        current_balance = exchange.get_available_balance()

        for symbol in SYMBOLS:
            try:
                if position_manager.has_position(symbol):
                    scanned.append((symbol, "Pozisyon zaten açık"))
                    continue

                # Mum verisi
                df_30m = exchange.get_klines(symbol, TIMEFRAME, limit=200)
                df_4h = exchange.get_klines(symbol, MTF_4H, limit=50)

                if df_30m.empty or df_4h.empty:
                    errors.append((symbol, "Mum verisi eksik"))
                    continue

                if len(df_30m) < 30:
                    errors.append((symbol, "Yetersiz mum"))
                    continue

                current_price = exchange.get_current_price(symbol)
                if current_price <= 0:
                    errors.append((symbol, "Fiyat alınamadı"))
                    continue

                # Filtreler
                result = evaluate_signal(symbol, current_price, df_30m, df_4h)
                logger.info(f"{symbol}: signal={result['signal']} reason={result['reason']}")

                if result["signal"] == "none":
                    scanned.append((symbol, result["reason"]))
                    continue

                # Sinyal var
                signal_dir = result["signal"]
                bybit_side = "Buy" if signal_dir == "long" else "Sell"

                # Kapasite dolu mu?
                if capacity_full:
                    skipped_capacity.append((symbol, signal_dir))
                    logger.warning(f"{symbol} {signal_dir} sinyal var ama kapasite dolu (5/5)")
                    continue

                # Bakiye kontrolü
                if current_balance < FIXED_STAKE:
                    tg.notify_insufficient_balance(symbol, FIXED_STAKE, current_balance)
                    logger.warning(f"{symbol} yetersiz bakiye: {current_balance:.2f} < {FIXED_STAKE:.2f}")
                    continue

                # ATR ve SL hesabı
                atr_value = get_atr_value(df_30m, period=ATR_PERIOD)
                if atr_value <= 0:
                    atr_value = current_price * 0.01

                sl_price = stop_loss_price_atr(signal_dir, current_price, atr_value)

                # Miktar hesabı
                raw_qty = calculate_position_qty(current_price, FIXED_STAKE)
                min_qty = exchange.get_min_qty(symbol)
                if raw_qty < min_qty:
                    errors.append((symbol, f"qty<{min_qty}"))
                    continue

                logger.info(f"{symbol} {bybit_side} açılıyor: qty={raw_qty:.6f}, "
                            f"price={current_price}, SL={sl_price:.6f}, ATR={atr_value:.6f}")

                # Pozisyon aç (Limit IOC)
                order_result = exchange.open_position(
                    symbol, bybit_side, raw_qty, sl_price, current_price
                )
                if not order_result["success"]:
                    err = f"{symbol} pozisyon açılamadı: {order_result['message']}"
                    logger.error(err)
                    tg.notify_error(err)
                    errors.append((symbol, "Emir hatası"))
                    continue

                # Pozisyon doğrulama
                time.sleep(2)
                live_pos = exchange.get_position(symbol)
                if not live_pos:
                    time.sleep(2)
                    live_pos = exchange.get_position(symbol)

                if not live_pos:
                    err = f"{symbol} pozisyon açıldı ama doğrulanamadı"
                    logger.error(err)
                    tg.notify_error(err)
                    errors.append((symbol, "Doğrulanamadı"))
                    continue

                actual_entry = live_pos["entry_price"]
                actual_qty = live_pos["size"]

                # Manager'a kaydet
                position_manager.add_position(
                    symbol=symbol,
                    side=signal_dir,
                    entry_price=actual_entry,
                    qty=actual_qty,
                    initial_sl=sl_price,
                    atr_value=atr_value,
                )

                tg.notify_position_opened(
                    symbol=symbol, side=signal_dir,
                    entry=actual_entry, qty=actual_qty,
                    sl=sl_price, atr=atr_value,
                )
                opened.append((symbol, signal_dir))

                # Kapasite kontrolünü güncelle
                if position_manager.count() >= MAX_OPEN_POSITIONS:
                    capacity_full = True

                current_balance -= FIXED_STAKE

            except Exception as e:
                logger.exception(f"{symbol} işlenirken hata: {e}")
                errors.append((symbol, str(e)[:50]))

    # Tarama özeti — HER ZAMAN gönderilir
    try:
        tg.notify_scan_summary(scanned, errors, opened, skipped_capacity, capacity_full)
    except Exception as e:
        logger.exception(f"Tarama özeti gönderilemedi: {e}")

    logger.info("=== Giriş taraması bitti ===")


# ============================================================
# CIKIS TAKIBI
# ============================================================
def track_open_positions():
    with state_lock:
        if position_manager.count() == 0:
            return

        live_positions = exchange.get_open_positions()
        live_symbols = {p["symbol"] for p in live_positions}

        # Borsa SL tetiklenmiş pozisyonlar
        for sym in list(position_manager.positions.keys()):
            if sym not in live_symbols:
                pos = position_manager.get(sym)
                if pos:
                    last_price = exchange.get_current_price(sym)
                    if pos.side == "long":
                        pnl_usdt = (last_price - pos.entry_price) * pos.qty
                    else:
                        pnl_usdt = (pos.entry_price - last_price) * pos.qty
                    pnl_pct = position_manager.calculate_pnl_pct(pos.side, pos.entry_price, last_price)

                    if pos.sl_locked:
                        reason = "Borsa SL tetiklendi (+0.1 ATR kâr kilidi)"
                    else:
                        reason = "Borsa SL tetiklendi (-1.5 ATR)"

                    tg.notify_position_closed(
                        symbol=sym, side=pos.side,
                        entry=pos.entry_price, exit_price=last_price,
                        pnl_usdt=pnl_usdt, pnl_pct=pnl_pct,
                        reason=reason,
                    )
                    position_manager.remove_position(sym)
                    logger.info(f"{sym} borsada kapanmış, manager'dan silindi")

        # Aktif pozisyonları takip et
        for live_p in live_positions:
            symbol = live_p["symbol"]
            pos = position_manager.get(symbol)
            if not pos:
                continue

            current_price = exchange.get_current_price(symbol)
            if current_price <= 0:
                continue

            result = position_manager.update_position(symbol, current_price)

            # Olayları işle
            for event in result.get("events", []):
                if event == "sl_lock_and_ce":
                    new_sl = result.get("new_sl", pos.locked_sl_price)
                    sl_update = exchange.update_stop_loss(symbol, new_sl)
                    if sl_update["success"]:
                        tg.notify_sl_lock_and_ce(symbol, new_sl, result["ce_price"])
                        logger.info(f"{symbol} kâr kilidi aktif (SL: {new_sl:.6f})")
                    else:
                        logger.error(f"{symbol} SL güncellenemedi: {sl_update['message']}")
                        tg.notify_error(f"{symbol} SL güncellenemedi: {sl_update['message']}")
                elif event == "ce_mid_tightened":
                    tg.notify_ce_tightened(symbol, result["ce_price"], 0.75)
                elif event == "ce_tightened":
                    tg.notify_ce_tightened(symbol, result["ce_price"], 0.5)

            # Aksiyon: kapatma
            if result["action"] == "close":
                close_side = "Sell" if pos.side == "long" else "Buy"
                close_result = exchange.close_position(symbol, close_side, pos.qty, current_price)
                if close_result["success"]:
                    if pos.side == "long":
                        pnl_usdt = (current_price - pos.entry_price) * pos.qty
                    else:
                        pnl_usdt = (pos.entry_price - current_price) * pos.qty
                    pnl_pct = position_manager.calculate_pnl_pct(pos.side, pos.entry_price, current_price)

                    tg.notify_position_closed(
                        symbol=symbol, side=pos.side,
                        entry=pos.entry_price, exit_price=current_price,
                        pnl_usdt=pnl_usdt, pnl_pct=pnl_pct,
                        reason=result["reason"],
                    )
                    position_manager.remove_position(symbol)
                    logger.info(f"{symbol} CE ile kapatıldı")
                else:
                    err = f"{symbol} CE ile kapanamadı: {close_result['message']}"
                    logger.error(err)
                    tg.notify_error(err)


def sync_positions(live_positions: list):
    live_symbols = {p["symbol"] for p in live_positions}
    for sym in list(position_manager.positions.keys()):
        if sym not in live_symbols:
            logger.info(f"{sym} borsada yok, manager'dan siliniyor")
            position_manager.remove_position(sym)


# ============================================================
# ZAMANLAMA
# ============================================================
def seconds_until_next_30min_close() -> float:
    now = datetime.now(timezone.utc)
    minute = now.minute
    if minute < 30:
        seconds = (30 - minute) * 60 - now.second
    else:
        seconds = (60 - minute) * 60 - now.second
    return max(seconds, 1)


def entry_scanner_loop():
    while True:
        try:
            wait = seconds_until_next_30min_close()
            logger.info(f"Bir sonraki tarama için {wait}s bekleniyor")
            time.sleep(wait + 5)
            scan_for_entries()
        except Exception as e:
            logger.exception(f"Entry scanner hata: {e}")
            tg.notify_error(f"Entry scanner: {e}")
            time.sleep(60)


def exit_tracker_loop():
    while True:
        try:
            track_open_positions()
        except Exception as e:
            logger.exception(f"Exit tracker hata: {e}")
            tg.notify_error(f"Exit tracker: {e}")
        time.sleep(EXIT_CHECK_INTERVAL)


# ============================================================
# BASLATMA
# ============================================================
def main():
    global INITIAL_BALANCE, FIXED_STAKE

    logger.info("=" * 50)
    logger.info(f"Bot başlatılıyor... TESTNET={BYBIT_TESTNET}")
    logger.info("=" * 50)

    # Bakiye oku
    balance = 0.0
    for attempt in range(5):
        balance = exchange.get_usdt_balance()
        if balance > 0:
            break
        logger.warning(f"Bakiye okunamadı, tekrar... ({attempt+1}/5)")
        time.sleep(3)

    if balance <= 0:
        msg = f"Bakiye okunamadı veya 0: {balance}"
        logger.error(msg)
        tg.notify_error(msg)
        return

    INITIAL_BALANCE = balance
    FIXED_STAKE = balance * STAKE_PERCENT / 100
    logger.info(f"Bakiye: {balance:.2f} USDT, Stake: {FIXED_STAKE:.2f} USDT")
    tg.notify_bot_started(balance, FIXED_STAKE, BYBIT_TESTNET)

    # Senkronizasyon
    open_positions = exchange.get_open_positions()
    sync_positions(open_positions)
    logger.info(f"Senkronizasyon: {len(position_manager.positions)} takipli pozisyon")

    # Paralel thread'ler
    t1 = threading.Thread(target=entry_scanner_loop, daemon=True, name="EntryScanner")
    t2 = threading.Thread(target=exit_tracker_loop, daemon=True, name="ExitTracker")
    t1.start()
    t2.start()

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Bot durduruluyor (Ctrl+C)")
        tg.notify_error("Bot manuel olarak durduruldu")


if __name__ == "__main__":
    main()
