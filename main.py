"""
Ema100-Ema21-Tunel Bot - Ana Döngü

Her 30 saniyede tarama yapar:
- Açık pozisyonlar için çıkış kontrolü
- Açık olmayan coinler için armed durumu yönetimi ve giriş kontrolü
- 60 saniyede bir klines cache yenilenir
- 5 dakikada bir Telegram durum raporu

GIRIS MANTIGI (2 adımlı):
- ARM: Fiyat EMA21 sınır çizgisini geçer (LONG için low altı, SHORT için high üstü)
- TRIGGER: Armed durumdayken fiyat EMA21 CLOSE'u ters yönde keser → pozisyon aç
- ARM SIFIRLAMA: Pozisyon açılınca, tünel filtresi bozulunca, 2 saat geçince

EMIR MANTIGI:
- Yalnızca limit emir kullanılır. Market emir KULLANILMAZ.
- Giriş: her 3 saniyede 1 tick pasif (maker) limit emir, max 20 deneme.
  20 denemede dolmazsa sinyal atlanır.
- Çıkış: her 3 saniyede 1 tick pasif (maker) limit emir (reduce-only), max 20 deneme.
  20 denemede dolmazsa bir sonraki taramada tekrar denenir.
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
ACTIVE_SYMBOLS: List[str] = []
PREVIOUS_PRICES: Dict[str, float] = {}
TUNNEL_CACHE: Dict[str, dict] = {}
EXTERNAL_POSITIONS: set = set()
SESSION_START_BALANCE: float = 0.0
SESSION_PNL: float = 0.0
RECENT_TRADES: List[dict] = []

# Armed state: symbol → armed olduğu timestamp (saniye)
LONG_ARMED: Dict[str, float] = {}
SHORT_ARMED: Dict[str, float] = {}


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


def compute_aggressive_limit_price(side: str, current_price: float, tick_size: float) -> float:
    """
    Pasif (maker) limit fiyat hesabı - düşük komisyon için.
    Buy → price - 1 tick (bid tarafına, satıcılar gelir),
    Sell → price + 1 tick (ask tarafına, alıcılar gelir).
    """
    if side == "Buy":
        raw = current_price - tick_size
    else:
        raw = current_price + tick_size
    return BybitClient.round_tick(raw, tick_size)


# ============================================================
# ARMED STATE YÖNETİMİ
# ============================================================
def manage_armed_state(symbol: str, curr_price: float, t: dict) -> None:
    """
    Her tarama başında armed durumunu güncelle:
    1. Tünel filtresi bozulunca → sıfırla
    2. 2 saat (ARMED_TIMEOUT_SECONDS) geçince → sıfırla
    3. Fiyat sınır çizgisini geçtiyse → arm et (timestamp güncelle)
    """
    now = now_ts()
    timeout = config.ARMED_TIMEOUT_SECONDS

    # --- LONG armed yönetimi ---
    if not strategy.is_long_tunnel_ok(t):
        # Tünel artık LONG değil → armed sıfırla
        LONG_ARMED.pop(symbol, None)
    else:
        # Timeout kontrolü
        ts = LONG_ARMED.get(symbol)
        if ts is not None and (now - ts) > timeout:
            LONG_ARMED.pop(symbol, None)
        # Arm koşulu (fiyat EMA21 LOW altına indi)
        if strategy.should_arm_long(curr_price, t):
            LONG_ARMED[symbol] = now

    # --- SHORT armed yönetimi ---
    if not strategy.is_short_tunnel_ok(t):
        SHORT_ARMED.pop(symbol, None)
    else:
        ts = SHORT_ARMED.get(symbol)
        if ts is not None and (now - ts) > timeout:
            SHORT_ARMED.pop(symbol, None)
        if strategy.should_arm_short(curr_price, t):
            SHORT_ARMED[symbol] = now


def clear_armed(symbol: str) -> None:
    """Pozisyon açılınca armed state'i sıfırla."""
    LONG_ARMED.pop(symbol, None)
    SHORT_ARMED.pop(symbol, None)


# ============================================================
# LIMIT EMIR DENEME DÖNGÜSÜ (giriş ve çıkış için ortak)
# ============================================================
def try_fill_limit(client: BybitClient, symbol: str, side: str, qty: float,
                   tick_size: float, reduce_only: bool = False) -> Optional[str]:
    """
    Her saniye yeni fiyatla 1 tick pasif (maker) limit emir verir.
    Max LIMIT_ORDER_MAX_RETRIES (20) deneme.
    Doldurursa order_id döner, dolduramazsa None döner.
    Market emir asla kullanılmaz.
    """
    for attempt in range(1, config.LIMIT_ORDER_MAX_RETRIES + 1):
        try:
            current_price = client.get_last_price(symbol)
            limit_price = compute_aggressive_limit_price(side, current_price, tick_size)

            order_id = client.place_limit_order(
                symbol=symbol, side=side, qty=qty, price=limit_price,
                reduce_only=reduce_only,
            )

            time.sleep(config.LIMIT_ORDER_RETRY_INTERVAL)

            status = client.get_order_status(symbol, order_id)
            if status == "Filled":
                tag = "REDUCE" if reduce_only else "ENTRY"
                print(f"[LIMIT-FILL/{tag}] {symbol} attempt {attempt} @ {limit_price}")
                return order_id

            try:
                client.cancel_order(symbol, order_id)
            except Exception:
                pass

        except Exception as e:
            msg = str(e)
            if "110007" in msg:
                raise
            if "110013" in msg:
                raise
            print(f"[ERR] try_fill_limit attempt {attempt} {symbol}: {e}")
            time.sleep(config.LIMIT_ORDER_RETRY_INTERVAL)

    return None


# ============================================================
# STARTUP - 50x KALDIRAÇ KONTROLÜ
# ============================================================
def check_leverage_support(client: BybitClient) -> tuple:
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

            try:
                client.set_isolated_margin(symbol, config.LEVERAGE)
            except Exception as e:
                print(f"[WARN] set_isolated_margin {symbol}: {e}")

            try:
                client.set_leverage(symbol, config.LEVERAGE)
            except Exception as e:
                msg = str(e)
                if "110013" in msg:
                    skipped.append(symbol)
                    tg.send_leverage_unsupported(symbol, max_lev)
                    print(f"[SKIP] {symbol}: 110013 leverage exceeded")
                    continue
                print(f"[WARN] set_leverage {symbol}: {e}")

            active.append(symbol)
            print(f"[OK] {symbol} 50x ready")
            time.sleep(0.15)

        except Exception as e:
            print(f"[ERR] check_leverage {symbol}: {e}")
            skipped.append(symbol)

    return active, skipped


# ============================================================
# KLINES & TUNNEL CACHE
# ============================================================
def refresh_tunnels(client: BybitClient, symbol: str, force: bool = False) -> Optional[dict]:
    now = now_ts()
    cached = TUNNEL_CACHE.get(symbol)

    if not force and cached and (now - cached["ts"] < config.KLINE_REFRESH_INTERVAL):
        return cached["tunnels"]

    try:
        klines = client.get_klines(symbol, config.TIMEFRAME, config.KLINE_LIMIT)
        if len(klines) >= 2:
            klines = klines[:-1]
        tunnels = indicators.compute_tunnels(
            klines,
            config.EMA_TUNNEL_PERIOD,
            config.EMA_SIGNAL_PERIOD,
            config.ATR_PERIOD,
        )        
        if tunnels:
            TUNNEL_CACHE[symbol] = {"tunnels": tunnels, "ts": now}
            return tunnels
    except Exception as e:
        print(f"[ERR] refresh_tunnels {symbol}: {e}")
    return None


# ============================================================
# POZISYON AÇMA - Sadece limit emir, 20 deneme, market YOK
# ============================================================
def open_position(client: BybitClient, pm: PositionManager,
                  symbol: str, side: str, tunnels: dict) -> None:
    """Pozisyon aç: limit emir döngüsü, dolmazsa sinyal atlanır."""
    try:
        info = client.get_instrument_info(symbol)
        tick_size = info["tick_size"]
        qty_step = info["qty_step"]
        min_qty = info["min_qty"]

        current_price = client.get_last_price(symbol)
        notional = STAKE_USDT * config.LEVERAGE
        raw_qty = notional / current_price
        qty = BybitClient.round_step(raw_qty, qty_step)

        if qty < min_qty:
            tg.send_entry_failed(symbol, side,
                f"Hesaplanan miktar ({qty}) minimum altında ({min_qty})")
            return

        try:
            filled_order_id = try_fill_limit(
                client, symbol, side, qty, tick_size, reduce_only=False
            )
        except Exception as e:
            msg = str(e)
            if "110007" in msg:
                tg.send_entry_failed(symbol, side, "Yetersiz bakiye")
                return
            if "110013" in msg:
                tg.send_entry_failed(symbol, side, f"Kaldıraç {config.LEVERAGE}x desteklenmiyor")
                return
            tg.send_entry_failed(symbol, side, str(e)[:200])
            return

        if not filled_order_id:
            tg.send_entry_failed(symbol, side,
                f"{config.LIMIT_ORDER_MAX_RETRIES} denemede limit emir dolmadı, sinyal atlandı")
            print(f"[OPEN-SKIP] {symbol} {side} {config.LIMIT_ORDER_MAX_RETRIES} denemede dolmadı")
            return

        time.sleep(1.5)
        ex_pos = client.get_position(symbol)
        if ex_pos is None:
            tg.send_entry_failed(symbol, side, "Pozisyon borsada bulunamadı")
            return

        actual_entry = float(ex_pos.get("avgPrice", current_price) or current_price)
        actual_qty = float(ex_pos.get("size", qty) or qty)

        sl_price = compute_initial_sl(side, actual_entry)
        sl_price = BybitClient.round_tick(sl_price, tick_size)
        try:
            client.update_stop_loss(symbol, sl_price)
        except Exception as e:
            print(f"[WARN] SL set {symbol}: {e}")

        actual_notional = actual_entry * actual_qty
        pos = Position(
            symbol=symbol,
            side=side,
            entry_price=actual_entry,
            qty=actual_qty,
            stake_usdt=STAKE_USDT,
            leverage=config.LEVERAGE,
            notional_usdt=actual_notional,
            sl_price=sl_price,
            open_time=now_ts(),
            best_price=actual_entry,
            entry_tunnel_high=tunnels["ema_tunnel_high"],
            entry_tunnel_low=tunnels["ema_tunnel_low"],
            entry_signal_high=tunnels["ema_signal_high"],
            entry_signal_low=tunnels["ema_signal_low"],
        )
        pm.open(pos)

        # Pozisyon açıldı → armed sıfırla
        clear_armed(symbol)

        tg.send_entry(
            symbol=symbol, side=side,
            price=actual_entry, qty=actual_qty,
            stake=STAKE_USDT, notional=actual_notional,
            leverage=config.LEVERAGE, sl_price=sl_price,
        )
        print(f"[OPEN] {symbol} {side} @ {actual_entry} qty={actual_qty}")

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[ERR] open_position {symbol}: {e}\n{tb}")
        tg.send_entry_failed(symbol, side, str(e)[:200])


# ============================================================
# POZISYON KAPATMA - Sadece limit emir reduce-only, 20 deneme, market YOK
# ============================================================
def close_position(client: BybitClient, pm: PositionManager,
                   symbol: str, reason: str) -> None:
    """
    Pozisyonu reduce-only limit emir ile kapatmaya çalışır.
    20 denemede dolmazsa pozisyon kapanmaz, bir sonraki taramada tekrar denenir.
    """
    global SESSION_PNL, RECENT_TRADES

    pos = pm.get(symbol)
    if pos is None:
        return

    try:
        ex_pos = client.get_position(symbol)
        if ex_pos is None:
            exit_price, pnl = client.get_closed_pnl(symbol)
            if exit_price is None or exit_price == 0:
                exit_price = pos.entry_price
            pnl_pct = (pnl / pos.stake_usdt * 100) if pos.stake_usdt else 0
            _send_exit_and_record(pos, exit_price, pnl, pnl_pct, reason)
            pm.close(symbol)
            return

        actual_qty = float(ex_pos.get("size", pos.qty) or pos.qty)
        close_side = "Sell" if pos.side == "Buy" else "Buy"

        info = client.get_instrument_info(symbol)
        tick_size = info["tick_size"]

        filled_order_id = try_fill_limit(
            client, symbol, close_side, actual_qty, tick_size, reduce_only=True
        )

        if not filled_order_id:
            print(f"[CLOSE-RETRY] {symbol} {config.LIMIT_ORDER_MAX_RETRIES} denemede dolmadı, "
                  f"sonraki taramada tekrar denenecek (reason={reason})")
            return

        time.sleep(1.2)
        exit_price, pnl = client.get_closed_pnl(symbol)
        if exit_price is None or exit_price == 0:
            try:
                exit_price = client.get_last_price(symbol)
            except Exception:
                exit_price = pos.entry_price
        pnl_pct = (pnl / pos.stake_usdt * 100) if pos.stake_usdt else 0
        _send_exit_and_record(pos, exit_price, pnl, pnl_pct, reason)
        pm.close(symbol)
        print(f"[CLOSE] {symbol} reason={reason} pnl={pnl:.2f}")

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[ERR] close_position {symbol}: {e}\n{tb}")
        tg.send_error(f"İşlem kapatılamadı: {symbol}", str(e))


def _send_exit_and_record(pos: Position, exit_price: float, pnl: float,
                          pnl_pct: float, reason: str) -> None:
    """Çıkış mesajı + session stats güncelleme."""
    global SESSION_PNL, RECENT_TRADES
    tg.send_exit(
        symbol=pos.symbol, side=pos.side,
        entry_price=pos.entry_price, exit_price=exit_price,
        stake=pos.stake_usdt, notional=pos.notional_usdt,
        leverage=pos.leverage, pnl_usdt=pnl, pnl_pct=pnl_pct,
        reason=reason,
    )
    SESSION_PNL += pnl
    RECENT_TRADES.append({
        "symbol": pos.symbol, "side": pos.side,
        "reason": reason, "pnl_usdt": pnl, "ts": now_ts(),
    })


# ============================================================
# ANA TARAMA - her 30 saniyede
# ============================================================
def scan_tick(client: BybitClient, pm: PositionManager) -> dict:
    scan_summary = {"long": [], "short": [], "none": []}

    for symbol in ACTIVE_SYMBOLS:
        try:
            tunnels = refresh_tunnels(client, symbol)
            if tunnels is None:
                continue

            try:
                curr_price = client.get_last_price(symbol)
            except Exception as e:
                print(f"[ERR] get_last_price {symbol}: {e}")
                continue

            prev_price = PREVIOUS_PRICES.get(symbol)

            # Filtre durumu (raporlama için)
            f_status = strategy.filter_status(tunnels)
            if f_status == "LONG":
                scan_summary["long"].append(symbol)
            elif f_status == "SHORT":
                scan_summary["short"].append(symbol)
            else:
                scan_summary["none"].append(symbol)

            # AÇIK POZİSYON VARSA → çıkış kontrolü
            if pm.has(symbol):
                pos = pm.get(symbol)
                if prev_price is not None:
                    last_closed_close = tunnels.get("last_close", curr_price)
                    if pos.side == "Buy":
                        reason = strategy.check_long_exits(
                            pos, prev_price, curr_price, last_closed_close, tunnels
                        )
                    else:
                        reason = strategy.check_short_exits(
                            pos, prev_price, curr_price, last_closed_close, tunnels
                        )
                    if reason:
                        close_position(client, pm, symbol, reason)
                PREVIOUS_PRICES[symbol] = curr_price
                continue

            # EXTERNAL pozisyonlara dokunma
            if symbol in EXTERNAL_POSITIONS:
                ex_pos = client.get_position(symbol)
                if ex_pos is None:
                    EXTERNAL_POSITIONS.discard(symbol)
                    tg.send_info(f"📤 Manuel pozisyon kapandı: <code>{symbol}</code>. Slot serbest.")
                PREVIOUS_PRICES[symbol] = curr_price
                continue

            # ARMED STATE YÖNETİMİ (giriş öncesi)
            manage_armed_state(symbol, curr_price, tunnels)

            # GİRİŞ KONTROLÜ
            if prev_price is not None:
                long_armed = symbol in LONG_ARMED
                short_armed = symbol in SHORT_ARMED

                total_active = pm.count() + len(EXTERNAL_POSITIONS)
                if total_active >= config.MAX_POSITIONS:
                    if strategy.detect_long_entry(prev_price, curr_price, tunnels, long_armed):
                        tg.send_entry_failed(symbol, "Buy",
                            f"Maksimum {config.MAX_POSITIONS} işlem limitine ulaşıldı")
                    elif strategy.detect_short_entry(prev_price, curr_price, tunnels, short_armed):
                        tg.send_entry_failed(symbol, "Sell",
                            f"Maksimum {config.MAX_POSITIONS} işlem limitine ulaşıldı")
                else:
                    if strategy.detect_long_entry(prev_price, curr_price, tunnels, long_armed):
                        open_position(client, pm, symbol, "Buy", tunnels)
                    elif strategy.detect_short_entry(prev_price, curr_price, tunnels, short_armed):
                        open_position(client, pm, symbol, "Sell", tunnels)

            PREVIOUS_PRICES[symbol] = curr_price
            time.sleep(0.1)

        except Exception as e:
            tb = traceback.format_exc()
            print(f"[ERR] scan_tick {symbol}: {e}\n{tb}")
            continue

    return scan_summary


# ============================================================
# 5 DAKİKALIK DURUM RAPORU
# ============================================================
def send_status_report(client: BybitClient, pm: PositionManager,
                        scan_summary: dict) -> None:
    global RECENT_TRADES, SESSION_PNL

    try:
        balance = client.get_total_balance_usdt()
    except Exception:
        balance = SESSION_START_BALANCE

    free = balance - STAKE_USDT

    open_pos_list = []
    for symbol, pos in pm.all().items():
        try:
            curr = client.get_last_price(symbol)
        except Exception:
            curr = pos.entry_price
        if pos.side == "Buy":
            pnl_pct = (curr - pos.entry_price) / pos.entry_price * 100 * pos.leverage
        else:
            pnl_pct = (pos.entry_price - curr) / pos.entry_price * 100 * pos.leverage
        pnl_usdt = pos.stake_usdt * pnl_pct / 100
        status = strategy.position_status(pos, curr)
        open_pos_list.append({
            "symbol": symbol, "side": pos.side,
            "entry": pos.entry_price, "current": curr,
            "pnl_usdt": pnl_usdt, "pnl_pct": pnl_pct,
            "notional": pos.notional_usdt, "status": status,
        })

    cutoff = now_ts() - config.REPORT_INTERVAL
    recent = [t for t in RECENT_TRADES if t["ts"] >= cutoff]

    session_pnl_pct = (SESSION_PNL / SESSION_START_BALANCE * 100) if SESSION_START_BALANCE else 0

    tg.send_status_report(
        balance=balance, stake=STAKE_USDT, free=free,
        open_positions=open_pos_list, max_positions=config.MAX_POSITIONS,
        scan_summary=scan_summary, recent_trades=recent,
        session_pnl=SESSION_PNL, session_pnl_pct=session_pnl_pct,
    )

    RECENT_TRADES = recent


# ============================================================
# MEVCUT POZİSYONLARI GERİ YÜKLE
# ============================================================
def restore_open_positions(client: BybitClient) -> None:
    try:
        open_positions = client.get_open_positions()
    except Exception as e:
        print(f"[ERR] get_open_positions: {e}")
        tg.send_error("Açık pozisyonlar okunamadı", str(e))
        return

    if not open_positions:
        return

    detected = []
    for ex_pos in open_positions:
        symbol = ex_pos.get("symbol", "?")
        side = ex_pos.get("side", "?")
        try:
            qty = float(ex_pos.get("size", 0) or 0)
            entry_price = float(ex_pos.get("avgPrice", 0) or 0)
            if qty <= 0 or entry_price <= 0:
                continue
            EXTERNAL_POSITIONS.add(symbol)
            side_str = "LONG" if side == "Buy" else "SHORT"
            detected.append(f"{symbol} ({side_str}) @ {entry_price}")
        except Exception as e:
            print(f"[ERR] restore {symbol}: {e}")

    if detected:
        free_slots = config.MAX_POSITIONS - len(EXTERNAL_POSITIONS)
        lines = "\n".join(f"• <code>{d}</code>" for d in detected)
        tg.send_info(
            f"🔄 <b>{len(detected)} mevcut işlem tespit edildi</b>\n"
            f"(Bot bu işlemleri yönetmeyecek, manuel kapatılması gerekir)\n\n"
            f"{lines}\n\n"
            f"Boş slot: {free_slots}/{config.MAX_POSITIONS}"
        )


# ============================================================
# STARTUP
# ============================================================
def startup(client: BybitClient) -> None:
    global STAKE_USDT, ACTIVE_SYMBOLS, SESSION_START_BALANCE

    config.validate_config()

    balance = client.get_total_balance_usdt()
    if balance <= 0:
        raise RuntimeError(f"Bakiye sıfır veya negatif: {balance}")
    SESSION_START_BALANCE = balance
    STAKE_USDT = balance * config.STAKE_PERCENT
    print(f"[START] balance={balance:.2f} stake={STAKE_USDT:.2f}")

    active, skipped = check_leverage_support(client)
    ACTIVE_SYMBOLS.clear()
    ACTIVE_SYMBOLS.extend(active)

    if not ACTIVE_SYMBOLS:
        raise RuntimeError("Hiçbir coin 50x desteklemiyor!")

    tg.send_bot_start(
        balance=balance, stake=STAKE_USDT,
        leverage=config.LEVERAGE,
        active_symbols=ACTIVE_SYMBOLS, skipped_symbols=skipped,
    )


# ============================================================
# MAIN LOOP
# ============================================================
def main():
    client = BybitClient()
    pm = PositionManager()

    try:
        startup(client)
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[FATAL] startup: {e}\n{tb}")
        try:
            tg.send_error("Bot başlatılamadı", str(e))
        except Exception:
            pass
        return

    try:
        restore_open_positions(client)
    except Exception as e:
        print(f"[ERR] restore: {e}")

    last_scan_summary: dict = {"long": [], "short": [], "none": []}
    last_report_time = now_ts()
    last_scan_time = 0.0

    print("[LOOP] Ana döngü başlıyor")
    while True:
        try:
            now = now_ts()

            if now - last_scan_time >= config.SCAN_INTERVAL:
                last_scan_summary = scan_tick(client, pm)
                last_scan_time = now

            if now - last_report_time >= config.REPORT_INTERVAL:
                try:
                    send_status_report(client, pm, last_scan_summary)
                except Exception as e:
                    print(f"[ERR] send_status_report: {e}")
                last_report_time = now

            time.sleep(2)

        except KeyboardInterrupt:
            print("[STOP] Manuel durdurma")
            break
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[ERR] main loop: {e}\n{tb}")
            try:
                tg.send_error("Ana döngü hatası", str(e))
            except Exception:
                pass
            time.sleep(10)


if __name__ == "__main__":
    main()
