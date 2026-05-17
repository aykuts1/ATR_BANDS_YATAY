"""
Telegram bot - bildirim gönderimi.
"""
import html as html_escape
from datetime import datetime
from typing import List, Optional

import requests

import config


# ============================================================
# CORE SEND
# ============================================================
def _send(text: str) -> None:
    """Send raw text message to Telegram."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print(f"[TG-DISABLED] {text}")
        return
    try:
        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
        if resp.status_code != 200:
            print(f"[TG-ERR] {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[TG-ERR] {e}")


def _esc(s) -> str:
    """HTML escape."""
    return html_escape.escape(str(s))


def _now_str() -> str:
    return datetime.utcnow().strftime("%d.%m.%Y %H:%M:%S UTC")


def _fmt_price(price: float) -> str:
    """Format price with appropriate decimals."""
    if price >= 1000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:.4f}"
    return f"{price:.6f}"


def _fmt_usdt(value: float) -> str:
    return f"{value:+,.2f}" if value != 0 else "0.00"


# ============================================================
# 1. BOT BAŞLANGIÇ
# ============================================================
def send_bot_start(balance: float, stake: float, leverage: int,
                   active_symbols: List[str], skipped_symbols: List[str]) -> None:
    """Bot başladığında gönderilen mesaj."""
    active_str = ", ".join(active_symbols) if active_symbols else "—"
    skipped_str = ", ".join(skipped_symbols) if skipped_symbols else "—"
    
    text = (
        f"🚀 <b>BOT BAŞLADI</b>\n\n"
        f"💰 <b>HESAP</b>\n"
        f"├ Bakiye  : <code>{balance:.2f} USDT</code>\n"
        f"├ Stake   : <code>{stake:.2f} USDT</code>\n"
        f"├ Kaldıraç: <code>{leverage}x</code>\n"
        f"└ Hacim   : <code>{stake * leverage:.2f} USDT</code>\n\n"
        f"📋 <b>COİNLER ({len(active_symbols)} aktif)</b>\n"
        f"<code>{_esc(active_str)}</code>\n"
    )
    if skipped_symbols:
        text += (
            f"\n⚠️ <b>50x desteklemiyor (atlandı)</b>\n"
            f"<code>{_esc(skipped_str)}</code>\n"
        )
    text += f"\n⏰ {_now_str()}"
    _send(text)


def send_leverage_unsupported(symbol: str, max_lev: float) -> None:
    """Coin 50x desteklemiyor."""
    text = (
        f"⚠️ <b>KALDIRAÇ YETERSİZ</b>\n\n"
        f"📌 <code>{_esc(symbol)}</code> 50x desteklemiyor.\n"
        f"📊 Maksimum: <code>{max_lev}x</code>\n"
        f"❌ Listeden çıkarıldı.\n\n"
        f"⏰ {_now_str()}"
    )
    _send(text)


# ============================================================
# 2. POZİSYON AÇILDI
# ============================================================
def send_entry(symbol: str, side: str, price: float, qty: float,
               stake: float, notional: float, leverage: int, sl_price: float) -> None:
    """Pozisyon açıldı."""
    side_str = "🟢 LONG" if side == "Buy" else "🔴 SHORT"
    text = (
        f"🟢 <b>YENİ İŞLEM AÇILDI</b>\n\n"
        f"📌 <code>{_esc(symbol)}</code> | {side_str} | <code>{leverage}x</code>\n"
        f"💵 Giriş Fiyatı : <code>{_fmt_price(price)}</code>\n"
        f"📦 Miktar       : <code>{qty}</code>\n"
        f"💰 Stake        : <code>{stake:.2f} USDT</code>\n"
        f"📊 Hacim        : <code>{notional:.2f} USDT</code>\n"
        f"🛑 SL (%1)      : <code>{_fmt_price(sl_price)}</code>\n"
        f"⏰ {_now_str()}"
    )
    _send(text)


# ============================================================
# 3. POZİSYON KAPANDI
# ============================================================
def send_exit(symbol: str, side: str, entry_price: float, exit_price: float,
              stake: float, notional: float, leverage: int,
              pnl_usdt: float, pnl_pct: float, reason: str) -> None:
    """Pozisyon kapandı."""
    side_str = "🟢 LONG" if side == "Buy" else "🔴 SHORT"
    icon = "✅" if pnl_usdt >= 0 else "❌"
    text = (
        f"🔴 <b>İŞLEM KAPATILDI</b>\n\n"
        f"📌 <code>{_esc(symbol)}</code> | {side_str} | <code>{leverage}x</code>\n"
        f"💵 Giriş  : <code>{_fmt_price(entry_price)}</code>\n"
        f"💵 Çıkış  : <code>{_fmt_price(exit_price)}</code>\n"
        f"💰 Stake  : <code>{stake:.2f} USDT</code>\n"
        f"📊 Hacim  : <code>{notional:.2f} USDT</code>\n"
        f"{icon} PNL   : <code>{_fmt_usdt(pnl_usdt)} USDT ({pnl_pct:+.2f}%)</code>\n"
        f"🏁 Neden  : <i>{_esc(reason)}</i>\n"
        f"⏰ {_now_str()}"
    )
    _send(text)


# ============================================================
# 4. İŞLEM AÇILAMADI
# ============================================================
def send_entry_failed(symbol: str, side: str, reason: str) -> None:
    """İşlem açılamadı."""
    side_str = "LONG" if side == "Buy" else "SHORT"
    text = (
        f"⚠️ <b>İŞLEM AÇILAMADI</b>\n\n"
        f"📌 <code>{_esc(symbol)}</code> | {side_str} | <code>{config.LEVERAGE}x</code>\n"
        f"❌ Neden : <i>{_esc(reason)}</i>\n"
        f"⏰ {_now_str()}"
    )
    _send(text)


# ============================================================
# 5. 5 DAKİKALIK GENEL ÖZET RAPOR
# ============================================================
def send_status_report(balance: float, stake: float, free: float,
                       open_positions: List[dict], max_positions: int,
                       scan_summary: dict, recent_trades: List[dict],
                       session_pnl: float, session_pnl_pct: float) -> None:
    """5dk genel durum raporu.
    
    scan_summary: {
      "long": [symbols], "short": [symbols], "none": [symbols]
    }
    open_positions: [{
      "symbol", "side", "entry", "current", "pnl_usdt", "pnl_pct",
      "notional", "status"
    }]
    recent_trades: [{
      "symbol", "side", "reason", "pnl_usdt"
    }]
    """
    lines = []
    lines.append(f"📊 <b>DURUM RAPORU</b> | {_now_str()}\n")
    
    # Hesap
    lines.append(f"💰 <b>HESAP</b>")
    lines.append(f"├ Toplam Bakiye : <code>{balance:.2f} USDT</code>")
    lines.append(f"├ Stake         : <code>{stake:.2f} USDT</code>")
    lines.append(f"└ Serbest       : <code>{free:.2f} USDT</code>\n")
    
    # Açık işlemler
    lines.append(f"📈 <b>AÇIK İŞLEMLER ({len(open_positions)}/{max_positions})</b>")
    if open_positions:
        for p in open_positions:
            side_emoji = "🟢" if p["side"] == "Buy" else "🔴"
            side_str = "LONG" if p["side"] == "Buy" else "SHORT"
            lines.append(
                f"┌ <code>{_esc(p['symbol'])}</code> | {side_emoji} {side_str} | Hacim: <code>{p['notional']:.2f}</code>\n"
                f"├ Giriş : <code>{_fmt_price(p['entry'])}</code>\n"
                f"├ Şimdi : <code>{_fmt_price(p['current'])}</code>\n"
                f"├ PNL   : <code>{_fmt_usdt(p['pnl_usdt'])} USDT ({p['pnl_pct']:+.2f}%)</code>\n"
                f"└ Durum : {_esc(p['status'])}"
            )
    else:
        lines.append("└ <i>Açık işlem yok</i>")
    lines.append("")
    
    # Tarama özeti
    long_syms = scan_summary.get("long", [])
    short_syms = scan_summary.get("short", [])
    none_syms = scan_summary.get("none", [])
    total = len(long_syms) + len(short_syms) + len(none_syms)
    lines.append(f"📋 <b>TARAMA ({total} Coin)</b>")
    lines.append(f"├ ✅ LONG onayı  : <code>{', '.join(long_syms) if long_syms else '—'}</code>")
    lines.append(f"├ ✅ SHORT onayı : <code>{', '.join(short_syms) if short_syms else '—'}</code>")
    lines.append(f"└ ⛔ Onay yok   : <code>{', '.join(none_syms) if none_syms else '—'}</code>")
    lines.append("")
    
    # Son 5dk işlemleri
    if recent_trades:
        lines.append(f"🕯 <b>SON 5DK İŞLEMLERİ</b>")
        for t in recent_trades:
            side_str = "LONG" if t["side"] == "Buy" else "SHORT"
            icon = "✅" if t["pnl_usdt"] >= 0 else "❌"
            lines.append(
                f"├ <code>{_esc(t['symbol'])}</code> {side_str} → "
                f"{_esc(t['reason'])}, {icon} <code>{_fmt_usdt(t['pnl_usdt'])} USDT</code>"
            )
        lines.append("")
    
    # Seans PNL
    lines.append(f"📉 <b>SEANS PNL</b> : <code>{_fmt_usdt(session_pnl)} USDT ({session_pnl_pct:+.2f}%)</code>")
    
    _send("\n".join(lines))


# ============================================================
# 6. HATA
# ============================================================
def send_error(title: str, error: str) -> None:
    """Hata bildirimi."""
    text = (
        f"🚨 <b>HATA</b>\n\n"
        f"📝 {_esc(title)}\n"
        f"💬 <code>{_esc(error)[:300]}</code>\n"
        f"⏰ {_now_str()}"
    )
    _send(text)


# ============================================================
# 7. BILGI MESAJI (genel)
# ============================================================
def send_info(message: str) -> None:
    """Genel bilgi mesajı."""
    _send(message)
