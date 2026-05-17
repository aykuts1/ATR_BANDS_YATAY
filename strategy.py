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
2. Emniyet Kemeri: Fiyat EMA100 ters çizgisini kesti
3. Chandelier Exit (ATR): Kar 1 ATR'yi geçince aktif, en iyi fiyattan 1 ATR geri dönüş → çıkış
"""
from typing import Optional
import config


# ============================================================
# FILTRE - EMA21 tüneli EMA100 dışında mı?
# ============================================================
def is_long_tunnel_ok(t: dict) -> bool:
    """EMA21 tüneli tamamen EMA100 üstünde mi?"""
    return t["ema_signal_low"] > t["ema_tunnel_high"]


def is_short_tunnel_ok(t: dict) -> bool:
    """EMA21 tüneli tamamen EMA100 altında mı?"""
    return t["ema_signal_high"] < t["ema_tunnel_low"]


# ============================================================
# FILTRE STATUS (raporlama için)
# ============================================================
def filter_status(t: dict) -> str:
    """Returns 'LONG', 'SHORT', or 'NONE' based on tunnel."""
    if is_long_tunnel_ok(t):
        return "LONG"
    if is_short_tunnel_ok(t):
        return "SHORT"
    return "NONE"


# ============================================================
# ARM KONTROLÜ - Adım 1: Fiyat sınır çizgisini geçti mi?
# ============================================================
def should_arm_long(curr_price: float, t: dict) -> bool:
    """
    LONG arm koşulu: fiyat EMA21 alt çizgisinin altına indi.
    (Sadece tünel filtresi LONG ise anlamlıdır.)
    """
    return curr_price < t["ema_signal_low"]


def should_arm_short(curr_price: float, t: dict) -> bool:
    """
    SHORT arm koşulu: fiyat EMA21 üst çizgisinin üstüne çıktı.
    (Sadece tünel filtresi SHORT ise anlamlıdır.)
    """
    return curr_price > t["ema_signal_high"]


# ============================================================
# GIRIS TETIK - Adım 2: Armed durumda EMA21 CLOSE'u kesti mi?
# ============================================================
def detect_long_entry(prev_price: float, curr_price: float, t: dict, is_armed: bool) -> bool:
    """
    LONG girişi:
    - Filtre: EMA21 tüneli EMA100 üstünde
    - Armed: Fiyat daha önce EMA21 LOW altına indi
    - Tetik: Fiyat EMA21 CLOSE'u aşağıdan yukarıya kesti
    """
    if not is_long_tunnel_ok(t):
        return False
    if not is_armed:
        return False
    line = t["ema_signal_close"]
    return prev_price <= line and curr_price > line


def detect_short_entry(prev_price: float, curr_price: float, t: dict, is_armed: bool) -> bool:
    """
    SHORT girişi:
    - Filtre: EMA21 tüneli EMA100 altında
    - Armed: Fiyat daha önce EMA21 HIGH üstüne çıktı
    - Tetik: Fiyat EMA21 CLOSE'u yukarıdan aşağıya kesti
    """
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
    """
    LONG pozisyon için çıkış kontrolü.
    Returns exit reason string or None.
    last_closed_close: Son KAPANAN mumun close değeri (iğne filtresi için).
    """
    # En iyi fiyatı güncelle (LONG için en yüksek)
    if pos.best_price is None or curr_price > pos.best_price:
        pos.best_price = curr_price
