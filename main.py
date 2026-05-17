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
        info = client.get_instrument_info(symbol)
        tick_size = info["tick_size"]
        qty_step = info["qty_step"]
        min_qty = info["min_qty"]

        current_price = client.get_last_price(symbol)

        # Miktar hesaplama
        notional = STAKE_USDT * config.LEVERAGE
        raw_qty = notional / current_price
        qty = BybitClient.round_step(raw_qty, qty_step)

        if qty < min_qty:
            tg.send_entry_failed(symbol, side,
                f"Hesaplanan miktar ({qty}) minimum altında ({min_qty})")
            return

        # Limit emir retry döngüsü
        filled_order_id = None
        for attempt in range(1, config.LIMIT_ORDER_MAX_RETRIES + 1):
            try:
                current_price = client.get_last_price(symbol)
                # Hızlı dolması için 1 tick agresif fiyat
                if side == "Buy":
                    limit_price = current_price + tick_size
                else:
                    limit_price = current_price - tick_size
                limit_price = BybitClient.round_tick(limit_price, tick_size)

                order_id = client.place_limit_order(
                    symbol=symbol, side=side, qty=qty, price=limit_price,
                )

                # 5 saniye bekle
                time.sleep(config.LIMIT_ORDER_TIMEOUT)

                # Status kontrolü
                status = client.get_order_status(symbol, order_id)
                if status == "Filled":
                    filled_order_id = order_id
                    print(f"[LIMIT-FILL] {symbol} attempt {attempt} @ {limit_price}")
                    break

                # Filled değilse iptal et
                client.cancel_order(symbol, order_id)
                print(f"[LIMIT-RETRY] {symbol} attempt {attempt} status={status}")

            except Exception as e:
                msg = str(e)
                if "110007" in msg:
                    tg.send_entry_failed(symbol, side, "Yetersiz bakiye")
                    return
                if "110013" in msg:
                    tg.send_entry_failed(symbol, side, f"Kaldıraç {config.LEVERAGE}x desteklenmiyor")
                    return
                print(f"[ERR] limit attempt {attempt} {symbol}: {e}")

        # Limit emirler doldurmadıysa market
        if not filled_order_id:
            try:
                print(f"[MARKET] {symbol} fallback")
                client.place_market_order(symbol=symbol, side=side, qty=qty)
            except Exception as e:
                msg = str(e)
                if "110007" in msg:
                    tg.send_entry_failed(symbol, side, "Yetersiz bakiye")
                    return
                if "110013" in msg:
                    tg.send_entry_failed(symbol, side, f"Kaldıraç {config.LEVERAGE}x desteklenmiyor")
                    return
                raise

        # Pozisyon onaylama
        time.sleep(1.5)
        ex_pos = client.get_position(symbol)
        if ex_pos is None:
            tg.send_entry_failed(symbol, side, "Pozisyon borsada bulunamadı")
            return

        actual_entry = float(ex_pos.get("avgPrice", current_price) or current_price)
        actual_qty = float(ex_pos.get("size", qty) or qty)

        # %1 SL borsada ayarla
        sl_price = compute_initial_sl(side, actual_entry)
        sl_price = BybitClient.round_tick(sl_price, tick_size)
        try:
            client.update_stop_loss(symbol, sl_price)
        except Exception as e:
            print(f"[WARN] SL set {symbol}: {e}")

        # Position kaydı
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
# POZISYON KAPATMA
# ============================================================
def close_position(client: BybitClient, pm: PositionManager,
                   symbol: str, reason: str) -> None:
    """Mevcut pozisyonu reduce-only market ile kapat."""
    global SESSION_PNL, RECENT_TRADES

    pos = pm.get(symbol)
    if pos is None:
        return

    try:
        ex_pos = client.get_position(symbol)
        if ex_pos is None:
            # Zaten kapalı (borsa SL tetiklemiş olabilir)
            exit_price, pnl = client.get_closed_pnl(symbol)
            if exit_price is None or exit_price == 0:
                exit_price = pos.entry_price
            pnl_pct = (pnl / pos.stake_usdt * 100) if pos.stake_usdt else 0
            _send_exit_and_record(pos, exit_price, pnl, pnl_pct, reason)
            pm.close(symbol)
            return

        actual_qty = float(ex_pos.get("size", pos.qty) or pos.qty)
        client.close_position(symbol, pos.side, actual_qty)

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
    """
    Tüm aktif coinleri tara.
    Returns scan_summary: {"long": [...], "short": [...], "none": [...]}
    """
    scan_summary = {"long": [], "short": [], "none": []}

    for symbol in ACTIVE_SYMBOLS:
        try:
            # Klines/tüneller cache'ten veya yenile
            tunnels = refresh_tunnels(client, symbol)
            if tunnels is None:
                continue

            # Canlı fiyat
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
                    if pos.side == "Buy":
                        reason = strategy.check_long_exits(pos, prev_price, curr_price, tunnels)
                    else:
                        reason = strategy.check_short_exits(pos, prev_price, curr_price, tunnels)
                    if reason:
                        close_position(client, pm, symbol, reason)
                # Pozisyon var → giriş kontrolüne girme
                PREVIOUS_PRICES[symbol] = curr_price
                continue

            # EXTERNAL pozisyonlara dokunma
            if symbol in EXTERNAL_POSITIONS:
                # Manuel pozisyon kapanmış mı kontrol et
                ex_pos = client.get_position(symbol)
                if ex_pos is None:
                    EXTERNAL_POSITIONS.discard(symbol)
                    tg.send_info(f"📤 Manuel pozisyon kapandı: <code>{symbol}</code>. Slot serbest.")
                PREVIOUS_PRICES[symbol] = curr_price
                continue

            # GİRİŞ KONTROLÜ
            if prev_price is not None:
                # Slot kontrolü
                total_active = pm.count() + len(EXTERNAL_POSITIONS)
                if total_active >= config.MAX_POSITIONS:
                    # Sinyal var mı? Varsa bildir
                    if strategy.detect_long_entry(prev_price, curr_price, tunnels):
                        tg.send_entry_failed(symbol, "Buy",
                            f"Maksimum {config.MAX_POSITIONS} işlem limitine ulaşıldı")
                    elif strategy.detect_short_entry(prev_price, curr_price, tunnels):
                        tg.send_entry_failed(symbol, "Sell",
                            f"Maksimum {config.MAX_POSITIONS} işlem limitine ulaşıldı")
                else:
                    if strategy.detect_long_entry(prev_price, curr_price, tunnels):
                        open_position(client, pm, symbol, "Buy", tunnels)
                    elif strategy.detect_short_entry(prev_price, curr_price, tunnels):
                        open_position(client, pm, symbol, "Sell", tunnels)

            PREVIOUS_PRICES[symbol] = curr_price
            time.sleep(0.1)  # rate limit

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
    """5dk genel rapor."""
    global RECENT_TRADES, SESSION_PNL

    try:
        balance = client.get_total_balance_usdt()
    except Exception:
        balance = SESSION_START_BALANCE

    free = balance - STAKE_USDT

    # Açık pozisyonların güncel durumu
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

    # Son 5dk işlemleri (5dk öncesinden eski olanları temizle)
    cutoff = now_ts() - config.REPORT_INTERVAL
    recent = [t for t in RECENT_TRADES if t["ts"] >= cutoff]

    # Seans yüzdesi
    session_pnl_pct = (SESSION_PNL / SESSION_START_BALANCE * 100) if SESSION_START_BALANCE else 0

    tg.send_status_report(
        balance=balance, stake=STAKE_USDT, free=free,
        open_positions=open_pos_list, max_positions=config.MAX_POSITIONS,
        scan_summary=scan_summary, recent_trades=recent,
        session_pnl=SESSION_PNL, session_pnl_pct=session_pnl_pct,
    )

    # Recent trades listesini temizle (rapor için kullandık)
    RECENT_TRADES = recent


# ============================================================
# MEVCUT POZİSYONLARI GERİ YÜKLE
# ============================================================
def restore_open_positions(client: BybitClient) -> None:
    """Bot başlangıcında borsadaki açık pozisyonları external olarak işaretle."""
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
    """Bot başlangıç sequence."""
    global STAKE_USDT, ACTIVE_SYMBOLS, SESSION_START_BALANCE

    config.validate_config()

    # Bakiye okuma
    balance = client.get_total_balance_usdt()
    if balance <= 0:
        raise RuntimeError(f"Bakiye sıfır veya negatif: {balance}")

    SESSION_START_BALANCE = balance
    STAKE_USDT = balance * config.STAKE_PERCENT

    print(f"[START] balance={balance:.2f} stake={STAKE_USDT:.2f}")

    # 50x kaldıraç kontrolü
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

    # Mevcut açık pozisyonları external olarak işaretle
    try:
        restore_open_positions(client)
    except Exception as e:
        print(f"[ERR] restore: {e}")

    # Son scan_summary'i sakla (rapor için)
    last_scan_summary: dict = {"long": [], "short": [], "none": []}
    last_report_time = now_ts()
    last_scan_time = 0.0

    print("[LOOP] Ana döngü başlıyor")
    while True:
        try:
            now = now_ts()

            # Her 30 saniyede tarama
            if now - last_scan_time >= config.SCAN_INTERVAL:
                last_scan_summary = scan_tick(client, pm)
                last_scan_time = now

            # Her 5 dakikada rapor
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
