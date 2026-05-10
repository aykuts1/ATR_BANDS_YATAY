"""
Telegram Bildirimleri
"""

import logging
import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram ayarları eksik")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            logger.error(f"Telegram HTTP {r.status_code}: {r.text}")
            return False
        return True
    except Exception as e:
        logger.exception(f"Telegram gönderim hatası: {e}")
        return False


def notify_bot_started(balance: float, stake: float, testnet: bool):
    mode = "TESTNET" if testnet else "MAINNET"
    msg = (
        f"🤖 <b>Bot Başlatıldı</b> [{mode}]\n\n"
        f"💰 Bakiye: <b>{balance:.2f} USDT</b>\n"
        f"📊 Stake (her işlem): <b>{stake:.2f} USDT</b>\n"
        f"⚡ Kaldıraç: 10x | Borsa SL: -1.5 ATR | Max 5 işlem\n"
        f"🔒 Kâr ≥ 0.5 ATR: SL → +0.1 ATR + CE 1.0 ATR\n"
        f"🎯 Kâr ≥ 1.5 ATR: CE → 0.75 ATR\n"
        f"💎 Kâr ≥ 2.0 ATR: CE → 0.5 ATR (son durak)"
    )
    send_telegram(msg)


def notify_position_opened(symbol: str, side: str, entry: float, qty: float, sl: float, atr: float):
    arrow = "🟢 LONG" if side.lower() == "long" else "🔴 SHORT"
    msg = (
        f"{arrow} <b>{symbol}</b> AÇILDI\n\n"
        f"📍 Giriş: <code>{entry:.6f}</code>\n"
        f"📦 Miktar: <code>{qty}</code>\n"
        f"🛡️ Borsa SL: <code>{sl:.6f}</code> (-1.5 ATR)\n"
        f"📏 ATR: <code>{atr:.6f}</code>\n"
        f"🕯️ CE: Pasif (kâr 0.5 ATR'yi geçince aktif olur)"
    )
    send_telegram(msg)


def notify_sl_lock_and_ce(symbol: str, sl_price: float, ce_price: float):
    """Birinci eşik: kâr ≥ 0.5 ATR → SL +0.1 ATR'ye çekilir + CE aktif"""
    msg = (
        f"🔒 <b>{symbol}</b> Kâr Kilidi + CE Aktif\n\n"
        f"📍 Borsa SL → <code>{sl_price:.6f}</code> (+0.1 ATR — kâr garantili)\n"
        f"🕯️ CE: <code>{ce_price:.6f}</code> (1.0 ATR geriden takip)"
    )
    send_telegram(msg)


def notify_ce_tightened(symbol: str, ce_price: float, trail_atr: float):
    """İkinci/üçüncü eşik: CE sıkışma bildirimi"""
    if trail_atr <= 0.5:
        title = "CE Son Sıkışma (0.5 ATR)"
        emoji = "💎"
        note = "Son durak — kâr kilidi sıkı"
    else:
        title = f"CE Orta Sıkışma ({trail_atr} ATR)"
        emoji = "🎯"
        note = "Kâr kilidi devrede"

    msg = (
        f"{emoji} <b>{symbol}</b> {title}\n\n"
        f"🕯️ CE: <code>{ce_price:.6f}</code> ({trail_atr} ATR geri)\n"
        f"💼 {note}"
    )
    send_telegram(msg)


def notify_position_closed(symbol: str, side: str, entry: float, exit_price: float,
                            pnl_usdt: float, pnl_pct: float, reason: str):
    emoji = "✅" if pnl_usdt >= 0 else "❌"
    msg = (
        f"{emoji} <b>{symbol}</b> KAPANDI ({side.upper()})\n\n"
        f"📍 Giriş: <code>{entry:.6f}</code>\n"
        f"🚪 Çıkış: <code>{exit_price:.6f}</code>\n"
        f"💵 PnL: <b>{pnl_usdt:+.2f} USDT</b> ({pnl_pct:+.2f}%)\n"
        f"📝 Sebep: {reason}"
    )
    send_telegram(msg)


def notify_insufficient_balance(symbol: str, needed: float, available: float):
    msg = (
        f"⚠️ <b>Yetersiz Bakiye</b>\n\n"
        f"Sembol: {symbol}\n"
        f"Gerekli: {needed:.2f} USDT\n"
        f"Mevcut: {available:.2f} USDT\n"
        f"İşlem atlandı."
    )
    send_telegram(msg)


def notify_error(message: str):
    msg = f"🚨 <b>HATA</b>\n\n{message[:500]}"
    send_telegram(msg)


def notify_scan_summary(scanned: list, errors: list, opened: list,
                         skipped_capacity: list, capacity_full: bool):
    """Her tarama sonunda gönderilir."""
    msg = "📊 <b>Tarama Tamamlandı</b>\n\n"

    # Açılan işlemler
    if opened:
        msg += "✅ <b>Açılan İşlemler:</b>\n"
        for sym, side in opened:
            arrow = "🟢" if side == "long" else "🔴"
            msg += f"  {arrow} {sym} {side.upper()}\n"
        msg += "\n"

    # Kapasite dolu, atlanan sinyaller
    if skipped_capacity:
        msg += "🚫 <b>Kapasite Dolu — Atlanan Sinyaller:</b>\n"
        for sym, side in skipped_capacity:
            arrow = "🟢" if side == "long" else "🔴"
            msg += f"  {arrow} {sym} {side.upper()}\n"
        msg += f"  <i>(Mevcut: 5/5 pozisyon dolu)</i>\n\n"
    elif capacity_full and not opened:
        msg += "⚠️ <b>Kapasite Dolu (5/5)</b>\n  <i>Hiçbir sinyal gelmedi.</i>\n\n"

    # Sebepler
    if scanned:
        if not opened and not skipped_capacity:
            msg += f"Toplam {len(scanned)} coin tarandı, sinyal yok.\n\n"

        reason_groups = {}
        for sym, reason in scanned:
            reason_groups.setdefault(reason, []).append(sym)

        msg += "<b>Tarama Detayı:</b>\n"
        for reason, syms in reason_groups.items():
            msg += f"  <i>{reason}</i> ({len(syms)})\n"
            display = syms[:6]
            msg += f"   • {', '.join(display)}"
            if len(syms) > 6:
                msg += f" +{len(syms)-6} diğer"
            msg += "\n"

    # Hatalar
    if errors:
        msg += "\n⚠️ <b>Hatalar:</b>\n"
        for sym, err in errors[:5]:
            msg += f"  • {sym}: {err}\n"
        if len(errors) > 5:
            msg += f"  • +{len(errors)-5} diğer\n"

    send_telegram(msg)
