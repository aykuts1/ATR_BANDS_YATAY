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
    SYMBOLS,
    TIMEFRAME,
    MTF_2H,
    MTF_4H,
    LEVERAGE,
    STAKE_PERCENT,
    STOP_LOSS_PERCENT,
    MAX_OPEN_POSITIONS,
    EXIT_CHECK_INTERVAL,
    BYBIT_TESTNET,
    LOG_LEVEL,
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
def calculate_position_qty(symbol: str, price: float, stake: float) -> float:
    if price <= 0:
        return 0.0
    notional = stake * LEVERAGE
    qty = notional / price
    qty = exchange.round_qty(symbol, qty)
    return qty


def stop_loss_price(side: str, entry: float) -> float:
    if side == "long":
        return entry * (1 - STOP_LOSS_PERCENT / 100)
    else:
        return entry * (1 + STOP_LOSS_PERCENT / 100)


# ============================================================
# GIRIS TARAMA
# ============================================================
def scan_for_entries():
    logger.info("=== Giriş taraması başladı ===")

    with state_lock:
        open_positions = exchange.get_open_positions()
        sync_positions(open_positions)

        if position_manager.count() >= MAX_OPEN_POSITIONS:
            logger.info(f"Max pozisyon ({MAX_OPEN_POSITIONS}) doldu, tarama atlandı")
            return

        current_balance = exchange.get_usdt_balance()

        for symbol in SYMBOLS:
            try:
                if position_manager.has_position(symbol):
                    continue
                if position_manager.count() >= MAX_OPEN_POSITIONS:
                    logger.info("Max pozisyon doldu, tarama bitti")
                    break

                df_30m = exchange.get_klines(symbol, TIMEFRAME, limit=200)
                df_2h = exchange.get_klines(symbol, MTF_2H, limit=50)
                df_4h = exchange.get_klines(symbol, MTF_4H, limit=50)

                if df_30m.empty or df_2h.empty or df_4h.empty:
                    logger.warning(f"{symbol} mum verisi eksik, atlandı")
                    continue
                if len(df_30m) < 30:
                    logger.warning(f"{symbol} yetersiz mum sayısı")
                    continue

                current_price = exchange.get_current_price(symbol)
                if current_price <= 0:
                    continue

                result = evaluate_signal(symbol, current_price, df_30m, df_2h, df_4h)
                logger.info(f"{symbol}: signal={result['signal']} reason={result['reason']} details={result['details']}")

                if result["signal"] == "none":
                    continue

                signal_dir = result["signal"]
                bybit_side = "Buy" if signal_dir == "long" else "Sell"

                if current_balance < FIXED_STAKE:
                    tg.notify_insufficient_balance(symbol, FIXED_STAKE, current_balance)
                    logger.warning(f"{symbol} için yetersiz bakiye: {current_balance:.2f} < {FIXED_STAKE:.2f}")
                    continue

                qty = calculate_position_qty(symbol, current_price, FIXED_STAKE)
                min_qty = exchange.get_min_qty(symbol)
                if qty < min_qty:
                    logger.warning(f"{symbol} qty={qty} < min_qty={min_qty}, atlandı")
                    continue

                sl_price = stop_loss_price(signal_dir, current_price)

                logger.info(f"{symbol} {bybit_side} açılıyor: qty={qty}, price~{current_price}, SL={sl_price}")
                order_result = exchange.open_position(symbol, bybit_side, qty, sl_price)

                if not order_result["success"]:
                    err = f"{symbol} pozisyon açılamadı: {order_result['message']}"
                    logger.error(err)
                    tg.notify_error(err)
                    continue

                time.sleep(1.5)
                live_pos = exchange.get_position(symbol)
                if not live_pos:
                    logger.warning(f"{symbol} pozisyon açıldı ama listede görünmüyor, tekrar deneme...")
                    time.sleep(2)
                    live_pos = exchange.get_position(symbol)

                if not live_pos:
                    err = f"{symbol} pozisyon açıldı ama doğrulanamadı, manuel kontrol gerek!"
                    logger.error(err)
                    tg.notify_error(err)
                    continue

                actual_entry = live_pos["entry_price"]
                actual_qty = live_pos["size"]

                atr_value = get_atr_value(df_30m, period=14)
                if atr_value <= 0:
                    atr_value = actual_entry * 0.01

                position_manager.add_position(
                    symbol=symbol,
                    side=signal_dir,
                    entry_price=actual_entry,
                    qty=actual_qty,
                    initial_sl=sl_price,
                    atr_value=atr_value,
                )

                pos = position_manager.get(symbol)
                tg.notify_position_opened(
                    symbol=symbol,
                    side=signal_dir,
                    entry=actual_entry,
                    qty=actual_qty,
                    sl=sl_price,
                    ce=pos.ce_price,
                )

                current_balance -= FIXED_STAKE / LEVERAGE

            except Exception as e:
                logger.exception(f"{symbol} işlenirken hata: {e}")
                tg.notify_error(f"{symbol} hata: {e}")

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

        for sym in list(position_manager.positions.keys()):
            if sym not in live_symbols:
                pos = position_manager.get(sym)
                if pos:
                    last_price = exchange.get_current_price(sym)
                    pnl_pct = position_manager.calculate_pnl_pct(pos.side, pos.entry_price, last_price)
                    pnl_usdt = (pnl_pct / 100) * (pos.qty * pos.entry_price)
                    tg.notify_position_closed(
                        symbol=sym,
                        side=pos.side,
                        entry=pos.entry_price,
                        exit_price=last_price,
                        pnl_usdt=pnl_usdt,
                        pnl_pct=pnl_pct,
                        reason="Borsa SL tetiklendi",
                    )
                    position_manager.remove_position(sym)
                    logger.info(f"{sym} borsada kapanmış, manager'dan silindi")

        for live_p in live_positions:
            symbol = live_p["symbol"]
            pos = position_manager.get(symbol)
            if not pos:
                continue

            current_price = exchange.get_current_price(symbol)
            if current_price <= 0:
                continue

            result = position_manager.update_position(symbol, current_price)

            if result["action"] == "breakeven":
                sl_update = exchange.update_stop_loss(symbol, pos.entry_price)
                if sl_update["success"]:
                    tg.notify_breakeven(symbol, pos.entry_price)
                    logger.info(f"{symbol} breakeven aktif")
                else:
                    logger.error(f"{symbol} breakeven güncellenemedi: {sl_update['message']}")

            elif result["action"] == "close":
                close_side = "Sell" if pos.side == "long" else "Buy"
                close_result = exchange.close_position(symbol, close_side, pos.qty)

                if close_result["success"]:
                    pnl_pct = position_manager.calculate_pnl_pct(pos.side, pos.entry_price, current_price)
                    pnl_usdt = (pnl_pct / 100) * (pos.qty * pos.entry_price)
                    tg.notify_position_closed(
                        symbol=symbol,
                        side=pos.side,
                        entry=pos.entry_price,
                        exit_price=current_price,
                        pnl_usdt=pnl_usdt,
                        pnl_pct=pnl_pct,
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
    next_close_minute = 30 if minute < 30 else 60
    if next_close_minute == 60:
        seconds = (60 - minute) * 60 - now.second
    else:
        seconds = (30 - minute) * 60 - now.second
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

    # 1. Bakiye oku
    balance = 0.0
    for attempt in range(5):
        balance = exchange.get_usdt_balance()
        if balance > 0:
            break
        logger.warning(f"Bakiye okunamadı, tekrar deneniyor... ({attempt+1}/5)")
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

    # 2. Mevcut açık pozisyonları senkronize et
    open_positions = exchange.get_open_positions()
    sync_positions(open_positions)
    logger.info(f"Senkronizasyon: {len(position_manager.positions)} takipli pozisyon")

    # 3. İki paralel thread
    t1 = threading.Thread(target=entry_scanner_loop, daemon=True, name="EntryScanner")
    t2 = threading.Thread(target=exit_tracker_loop, daemon=True, name="ExitTracker")
    t1.start()
    t2.start()

    # Ana thread canlı tut
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Bot durduruluyor (Ctrl+C)")
        tg.notify_error("Bot manuel olarak durduruldu")


if __name__ == "__main__":
    main()
