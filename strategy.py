"""
Strateji - Sinyal üretimi ve çıkış mantığı.

GIRIS:
- LONG: EMA21 tüneli EMA100 üstünde (EMA21_low > EMA100_high)
        + EMA21 close yönü yukarı (close > 10mum ortalaması - tolerans)
        + Fiyat EMA21 alt çizgisini aşağıdan yukarıya keser
- SHORT: EMA21 tüneli EMA100 altında (EMA21_high < EMA100_low)
         + EMA21 close yönü aşağı (close < 10mum ortalaması + tolerans)
         + Fiyat EMA21 üst çizgisini yukarıdan aşağıya keser

CIKIS:
1. Normal: Hedef çizgi MUM KAPANISINDA EMA21_high (long) üstüne çıktı,
           sonra canlı fiyat EMA21_CLOSE'u aşağı kesti
2. Emniyet Kemeri: Fiyat EMA100 ters çizgisini kesti
3. Chandelier Exit: Kar %0.5 sonrası en iyi fiyattan %0.5 geri dönüş
"""
from typing import Optional
import config


# ============================================================
# FILTRE 1 - EMA21 tüneli EMA100 dışında mı?
# ============================================================
def is_long_tunnel_ok(t: dict) -> bool:
    """EMA21 tüneli tamamen EMA100 üstünde mi?"""
    return t["ema_signal_low"] > t["ema_tunnel_high"]


def is_short_tunnel_ok(t: dict) -> bool:
    """EMA21 tüneli tamamen EMA100 altında mi?"""
    return t["ema_signal_high"] < t["ema_tunnel_low"]


# ============================================================
# FILTRE 2 - EMA21 yön kontrolü (close vs 10 mum ortalaması)
# ============================================================
def is_long_direction_ok(t: dict) -> bool:
    """
    LONG yönü uygun mu?
    EMA21 close şu anki değeri, son 10 mum ortalamasının
    %0.05 tolerans altına düşmemeli (hafif aşağı bile olsa geçer).
    """
    sc = t["ema_signal_close"]
    sc_avg = t["ema_signal_close_avg"]
    if sc_avg == 0:
        return False
    diff_pct = (sc - sc_avg) / sc_avg
    return diff_pct > -config.EMA_DIRECTION_TOLERANCE


def is_short_direction_ok(t: dict) -> bool:
    """
    SHORT yönü uygun mu?
    EMA21 close şu anki değeri, son 10 mum ortalamasının
    %0.05 tolerans üstüne çıkmamalı (hafif yukarı bile olsa geçer).
    """
    sc = t["ema_signal_close"]
    sc_avg = t["ema_signal_close_avg"]
    if sc_avg == 0:
        return False
    diff_pct = (sc - sc_avg) / sc_avg
    return diff_pct < config.EMA_DIRECTION_TOLERANCE


# ============================================================
# FILTRE STATUS (raporlama için)
# ============================================================
def filter_status(t: dict) -> str:
    """Returns 'LONG', 'SHORT', or 'NONE' based on tunnel + direction."""
    if is_long_tunnel_ok(t) and is_long_direction_ok(t):
        return "LONG"
    if is_short_tunnel_ok(t) and is_short_direction_ok(t):
        return "SHORT"
    return "NONE"


# ============================================================
# GIRIS DETEKSIYON - Fiyat EMA21 çizgisini kesti mi?
# ============================================================
def detect_long_entry(prev_price: float, curr_price: float, t: dict) -> bool:
    """
    LONG girişi:
    - Filtre 1: EMA21 EMA100 üstünde
    - Filtre 2: EMA21 close yönü yukarı
    - Tetikleyici: Fiyat EMA21 alt çizgisini aşağıdan yukarıya kesti
    """
    if not is_long_tunnel_ok(t):
        return False
    if not is_long_direction_ok(t):
        return False
    line = t["ema_signal_low"]
    return prev_price <= line and curr_price > line


def detect_short_entry(prev_price: float, curr_price: float, t: dict) -> bool:
    """
    SHORT girişi:
    - Filtre 1: EMA21 EMA100 altında
    - Filtre 2: EMA21 close yönü aşağı
    - Tetikleyici: Fiyat EMA21 üst çizgisini yukarıdan aşağıya kesti
    """
    if not is_short_tunnel_ok(t):
        return False
    if not is_short_direction_ok(t):
        return False
    line = t["ema_signal_high"]
    return prev_price >= line and curr_price < line


# ============================================================
# CIKIS DETEKSIYON
# ============================================================
def check_long_exits(pos, prev_price: float, curr_price: float,
                     last_closed_close: float, t: dict) -> Optional[str]:
    """
    LONG pozisyon için çıkış kontrolü.
    Returns exit reason string or None.

    last_closed_close: Son KAPANAN mumun close değeri (iğne filtresi için).
    """
    # En iyi fiyatı güncelle (LONG için en yüksek)
    if pos.best_price is None or curr_price > pos.best_price:
        pos.best_price = curr_price

    target_high_line = t["ema_signal_high"]   # EMA21 üst (geçilmesi gereken)
    target_close_line = t["ema_signal_close"] # EMA21 close (kesilince çıkış)
    safety_line = t["ema_tunnel_high"]        # EMA100 üst (emniyet kemeri)

    # 1. NORMAL CIKIS
    # Step 1: Hedef üst çizgi MUM KAPANISINDA geçildi mi? (iğneleri yoksay)
    if not pos.crossed_target:
        if last_closed_close > target_high_line:
            pos.crossed_target = True
    else:
        # Step 2: Canlı fiyat EMA21 CLOSE çizgisini aşağı kesti mi? → çıkış
        if prev_price >= target_close_line and curr_price < target_close_line:
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


def check_short_exits(pos, prev_price: float, curr_price: float,
                      last_closed_close: float, t: dict) -> Optional[str]:
    """
    SHORT pozisyon için çıkış kontrolü.
    Returns exit reason string or None.

    last_closed_close: Son KAPANAN mumun close değeri (iğne filtresi için).
    """
    # En iyi fiyatı güncelle (SHORT için en düşük)
    if pos.best_price is None or curr_price < pos.best_price:
        pos.best_price = curr_price

    target_low_line = t["ema_signal_low"]     # EMA21 alt (geçilmesi gereken)
    target_close_line = t["ema_signal_close"] # EMA21 close (kesilince çıkış)
    safety_line = t["ema_tunnel_low"]         # EMA100 alt (emniyet kemeri)

    # 1. NORMAL CIKIS
    # Step 1: Hedef alt çizgi MUM KAPANISINDA geçildi mi? (iğneleri yoksay)
    if not pos.crossed_target:
        if last_closed_close < target_low_line:
            pos.crossed_target = True
    else:
        # Step 2: Canlı fiyat EMA21 CLOSE çizgisini yukarı kesti mi? → çıkış
        if prev_price <= target_close_line and curr_price > target_close_line:
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
