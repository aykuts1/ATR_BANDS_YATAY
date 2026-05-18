"""
Strateji - Sinyal üretimi ve çıkış mantığı.

GIRIS (2 adımlı):
- LONG:
  1. ARM: Fiyat EMA21 alt çizgisinin altına iner (curr_price < EMA21 LOW)
  2. TRIGGER: Fiyat EMA21 CLOSE'u yukarı keser → LONG aç
- SHORT:
  1. ARM: Fiyat EMA21 üst çizgisinin üstüne çıkar (curr_price > EMA21 HIGH)
  2. TRIGGER: Fiyat EMA21 CLOSE'u aşağı keser → SHORT aç

FILTRELER:
- LONG: EMA21 tüneli EMA100 üstünde (EMA21_low > EMA100_high)
- SHORT: EMA21 tüneli EMA100 altında (EMA21_high < EMA100_low)

ARMED SIFIRLAMA:
- Pozisyon açılınca
- Tünel filtresi bozulunca
- 2 saat geçince (ARMED_TIMEOUT_SECONDS)

CIKIS:
1. Normal: Hedef çizgi MUM KAPANIŞINDA geçildi, sonra fiyat EMA21 CLOSE'u ters yönde kesti
2. Emniyet Kemeri: Fiyat EMA100 ters çizgisini ketti
3. Chandelier Exit (ATR): Kar 1 ATR'yi geçince aktif, en iyi fiyattan 1 ATR geri dönüş → çıkış
"""
from typing import Optional
import config


# ============================================================
# FILTRE - EMA21 tüneli EMA100 dışında mı?
# ============================================================
def is_long_tunnel_ok(t: dict) -> bool:
    return t["ema_signal_low"] > t["ema_tunnel_high"]


def is_short_tunnel_ok(t: dict) -> bool:
    return t["ema_signal_high"] < t["ema_tunnel_low"]


# ============================================================
# FILTRE STATUS (raporlama için)
# ============================================================
def filter_status(t: dict) -> str:
    if is_long_tunnel_ok(t):
        return "LONG"
    if is_short_tunnel_ok(t):
        return "SHORT"
    return "NONE"


# ============================================================
# ARM KONTROLÜ - Adım 1: Fiyat sınır çizgisini geçti mi?
# ============================================================
def should_arm_long(curr_price: float, t: dict) -> bool:
    return curr_price < t["ema_signal_low"]


def should_arm_short(curr_price: float, t: dict) -> bool:
    return curr_price > t["ema_signal_high"]


# ============================================================
# GIRIS TETIK - Adım 2: Armed durumda EMA21 CLOSE'u kesti mi?
# ============================================================
def detect_long_entry(prev_price: float, curr_price: float, t: dict, is_armed: bool) -> bool:
    if not is_long_tunnel_ok(t):
        return False
    if not is_armed:
        return False
    line = t["ema_signal_close"]
    return prev_price <= line and curr_price > line


def detect_short_entry(prev_price: float, curr_price: float, t: dict, is_armed: bool) -> bool:
    if not is_short_tunnel_ok(t):
        return False
    if not is_armed:
        return False
    line = t["ema_signal_close"]
    return prev_price >= line and curr_price < line


# ============================================================
# CIKIS DETEKSIYON
# ============================================================
def check_long_exits(pos, prev_price: float, curr_price: float,
                     last_closed_close: float, t: dict) -> Optional[str]:
    if pos.best_price is None or curr_price > pos.best_price:
        pos.best_price = curr_price

    target_high_line = t["ema_signal_high"]
    target_close_line = t["ema_signal_close"]
    safety_line = t["ema_tunnel_high"]
    atr_val = t["atr"]

    # 1. NORMAL CIKIS
    if not pos.crossed_target:
        if last_closed_close > target_high_line:
            pos.crossed_target = True
    else:
        if prev_price >= target_close_line and curr_price < target_close_line:
            return "Normal Çıkış"

    # 2. EMNIYET KEMERI
    if prev_price >= safety_line and curr_price < safety_line:
        return "Emniyet Kemeri (EMA100)"

    # 3. CHANDELIER EXIT (ATR)
    profit = curr_price - pos.entry_price
    atr_threshold = config.CE_ATR_MULTIPLIER * atr_val
    if profit >= atr_threshold or pos.ce_active:
        pos.ce_active = True
        ce_level = pos.best_price - atr_threshold
        if curr_price <= ce_level:
            return "Chandelier Exit (ATR)"

    return None


def check_short_exits(pos, prev_price: float, curr_price: float,
                      last_closed_close: float, t: dict) -> Optional[str]:
    if pos.best_price is None or curr_price < pos.best_price:
        pos.best_price = curr_price

    target_low_line = t["ema_signal_low"]
    target_close_line = t["ema_signal_close"]
    safety_line = t["ema_tunnel_low"]
    atr_val = t["atr"]

    # 1. NORMAL CIKIS
    if not pos.crossed_target:
        if last_closed_close < target_low_line:
            pos.crossed_target = True
    else:
        if prev_price <= target_close_line and curr_price > target_close_line:
            return "Normal Çıkış"

    # 2. EMNIYET KEMERI
    if prev_price <= safety_line and curr_price > safety_line:
        return "Emniyet Kemeri (EMA100)"

    # 3. CHANDELIER EXIT (ATR)
    profit = pos.entry_price - curr_price
    atr_threshold = config.CE_ATR_MULTIPLIER * atr_val
    if profit >= atr_threshold or pos.ce_active:
        pos.ce_active = True
        ce_level = pos.best_price + atr_threshold
        if curr_price >= ce_level:
            return "Chandelier Exit (ATR)"

    return None


def position_status(pos, curr_price: float) -> str:
    if pos.ce_active:
        return "🎯 CE aktif"
    if pos.crossed_target:
        return "✅ Hedef geçildi"
    return "⏳ Hedef bekleniyor"
