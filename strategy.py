"""
Strateji - Sinyal üretimi ve çıkış mantığı.

GIRIS:
- LONG: EMA21 tüneli EMA100 üstünde (EMA21_low > EMA100_high)
        + Fiyat EMA21 alt çizgisini aşağıdan yukarıya keser
- SHORT: EMA21 tüneli EMA100 altında (EMA21_high < EMA100_low)
         + Fiyat EMA21 üst çizgisini yukarıdan aşağıya keser

CIKIS:
1. Normal: Fiyat hedef çizgiyi geçti, sonra ters yönde kesti
2. Emniyet Kemeri: Fiyat EMA100 ters çizgisini kesti
3. Chandelier Exit: Kar %0.5 sonrası en iyi fiyattan %0.5 geri dönüş
"""
from typing import Optional
import config


# ============================================================
# FILTRE - EMA21 tüneli EMA100 dışında mı?
# ============================================================
def is_long_filter_ok(t: dict) -> bool:
    """EMA21 tüneli tamamen EMA100 üstünde mi?"""
    return t["ema_signal_low"] > t["ema_tunnel_high"]


def is_short_filter_ok(t: dict) -> bool:
    """EMA21 tüneli tamamen EMA100 altında mı?"""
    return t["ema_signal_high"] < t["ema_tunnel_low"]


def filter_status(t: dict) -> str:
    """Returns 'LONG', 'SHORT', or 'NONE' based on tunnel position."""
    if is_long_filter_ok(t):
        return "LONG"
    if is_short_filter_ok(t):
        return "SHORT"
    return "NONE"


# ============================================================
# GIRIS DETEKSIYON - Fiyat EMA21 çizgisini kesti mi?
# ============================================================
def detect_long_entry(prev_price: float, curr_price: float, t: dict) -> bool:
    """
    LONG girişi:
    - Filtre: EMA21 EMA100 üstünde
    - Tetikleyici: Fiyat EMA21 alt çizgisini aşağıdan yukarıya kesti
    """
    if not is_long_filter_ok(t):
        return False
    line = t["ema_signal_low"]
    return prev_price <= line and curr_price > line


def detect_short_entry(prev_price: float, curr_price: float, t: dict) -> bool:
    """
    SHORT girişi:
    - Filtre: EMA21 EMA100 altında
    - Tetikleyici: Fiyat EMA21 üst çizgisini yukarıdan aşağıya kesti
    """
    if not is_short_filter_ok(t):
        return False
    line = t["ema_signal_high"]
    return prev_price >= line and curr_price < line


# ============================================================
# CIKIS DETEKSIYON
# ============================================================
def check_long_exits(pos, prev_price: float, curr_price: float, t: dict) -> Optional[str]:
    """
    LONG pozisyon için çıkış kontrolü.
    Returns exit reason string or None.
    Mutates pos.best_price, pos.ce_active, pos.crossed_target.
    """
    # En iyi fiyatı güncelle (LONG için en yüksek)
    if pos.best_price is None or curr_price > pos.best_price:
        pos.best_price = curr_price

    target_line = t["ema_signal_high"]   # EMA21 üst çizgi (hedef)
    safety_line = t["ema_tunnel_high"]   # EMA100 üst çizgi (emniyet kemeri)

    # 1. NORMAL CIKIS - hedef çizgi geçildi, sonra ters yönde kesildi
    if not pos.crossed_target:
        # Hedef çizgi aşağıdan yukarıya geçildi mi?
        if prev_price <= target_line and curr_price > target_line:
            pos.crossed_target = True
    else:
        # Hedef çizgi yukarıdan aşağıya kesildi mi? → Normal çıkış
        if prev_price >= target_line and curr_price < target_line:
            return "Normal Çıkış"

    # 2. EMNIYET KEMERI - EMA100 üst çizgisi aşağı kesildi
    if prev_price >= safety_line and curr_price < safety_line:
        return "Emniyet Kemeri (EMA100)"

    # 3. CHANDELIER EXIT - kar %0.5 sonrası %0.5 geri dönüş
    profit_pct = (curr_price - pos.entry_price) / pos.entry_price
    if profit_pct >= config.CE_ACTIVATION_PCT or pos.ce_active:
        pos.ce_active = True
        ce_level = pos.best_price * (1 - config.CE_TRAIL_PCT)
        if curr_price <= ce_level:
            return "Chandelier Exit (CE %0.5)"

    return None


def check_short_exits(pos, prev_price: float, curr_price: float, t: dict) -> Optional[str]:
    """
    SHORT pozisyon için çıkış kontrolü.
    Returns exit reason string or None.
    Mutates pos.best_price, pos.ce_active, pos.crossed_target.
    """
    # En iyi fiyatı güncelle (SHORT için en düşük)
    if pos.best_price is None or curr_price < pos.best_price:
        pos.best_price = curr_price

    target_line = t["ema_signal_low"]    # EMA21 alt çizgi (hedef)
    safety_line = t["ema_tunnel_low"]    # EMA100 alt çizgi (emniyet kemeri)

    # 1. NORMAL CIKIS - hedef çizgi geçildi, sonra ters yönde kesildi
    if not pos.crossed_target:
        # Hedef çizgi yukarıdan aşağıya geçildi mi?
        if prev_price >= target_line and curr_price < target_line:
            pos.crossed_target = True
    else:
        # Hedef çizgi aşağıdan yukarıya kesildi mi? → Normal çıkış
        if prev_price <= target_line and curr_price > target_line:
            return "Normal Çıkış"

    # 2. EMNIYET KEMERI - EMA100 alt çizgisi yukarı kesildi
    if prev_price <= safety_line and curr_price > safety_line:
        return "Emniyet Kemeri (EMA100)"

    # 3. CHANDELIER EXIT - kar %0.5 sonrası %0.5 geri dönüş
    profit_pct = (pos.entry_price - curr_price) / pos.entry_price
    if profit_pct >= config.CE_ACTIVATION_PCT or pos.ce_active:
        pos.ce_active = True
        ce_level = pos.best_price * (1 + config.CE_TRAIL_PCT)
        if curr_price >= ce_level:
            return "Chandelier Exit (CE %0.5)"

    return None


def position_status(pos, curr_price: float) -> str:
    """Returns short status string for reporting."""
    if pos.ce_active:
        return "🎯 CE aktif"
    if pos.crossed_target:
        return "✅ Hedef geçildi"
    return "⏳ Hedef bekleniyor"
