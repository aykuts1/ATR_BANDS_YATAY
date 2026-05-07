"""
Telegram Bildirimleri
"""
import logging
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def send_telegram(message: str) -> bool:
    """Telegram'a mesaj gönderir."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram ayarları eksik, mesaj gönderilmedi")
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
        f"⚡ Kaldıraç: 5x | SL: %3 | Max 5 işlem"
    )
    send_telegram(msg)


def notify_position_opened(symbol: str, side: str, entry: float, qty: float, sl: float, ce: float):
    arrow = "🟢 LONG" if side.lower() == "long" else "🔴 SHORT"
    msg = (
        f"{arrow} <b>{symbol}</b> AÇILDI\n\n"
        f"📍 Giriş: <code>{entry:.6f}</code>\n"
        f"📦 Miktar: <code>{qty}</code>\n"
        f"🛑 Stop Loss: <code>{sl:.6f}</code>\n"
        f"🕯️ Chandelier: <code>{ce:.6f}</code>"
    )
    send_telegram(msg)


def notify_position_closed(symbol: str, side: str, entry: float, exit_price: float, pnl_usdt: float, pnl_pct: float, reason: str):
    emoji = "✅" if pnl_usdt >= 0 else "❌"
    msg = (
        f"{emoji} <b>{symbol}</b> KAPANDI ({side.upper()})\n\n"
        f"📍 Giriş: <code>{entry:.6f}</code>\n"
        f"🚪 Çıkış: <code>{exit_price:.6f}</code>\n"
        f"💵 PnL: <b>{pnl_usdt:+.2f} USDT</b> ({pnl_pct:+.2f}%)\n"
        f"📝 Sebep: {reason}"
    )
    send_telegram(msg)


def notify_breakeven(symbol: str, entry: float):
    msg = f"🔒 <b>{symbol}</b> Breakeven aktifleşti\nSL → Giriş ({entry:.6f})"
    send_telegram(msg)


def notify_ce_update(symbol: str, ce_price: float, atr_mult: float):
    msg = f"🕯️ <b>{symbol}</b> CE güncellendi: <code>{ce_price:.6f}</code> ({atr_mult}× ATR)"
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
    msg = f"🚨 <b>HATA</b>\n\n{message}"
    send_telegram(msg)


def notify_signal_skipped(symbol: str, reason: str):
    """Opsiyonel: çok fazla mesaj olur, kullanılırsa filtrelenmeli."""
    msg = f"⏭️ {symbol} atlandı: {reason}"
    send_telegram(msg)
