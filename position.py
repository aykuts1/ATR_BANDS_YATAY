"""
position.py - Pozisyon state'i, seviye gecisleri, cikis kontrolu.

YENI STRATEJI v2 (mean reversion, sadelestirilmis seviyeler):

Seviyeler:
  LEVEL_ENTRY (0) : Giris yapildi. Sadece ENTRY exit + borsa SL aktif.
  LEVEL_BE    (1) : Fiyat entry-tarafi ic tamponu kar yonunde kesti.
                    LONG: price > alt_ic_tampon
                    SHORT: price < ust_ic_tampon
  LEVEL_CE1   (2) : Kar >= CE1_ATR (1.0). Chandelier CE1_TRAIL (1.0) ATR
                    geriden takip eder, asla geri cekilmez.

  (CE2 ve WINRATE artik seviye degil)

Cikis tipleri (sirasiyla kontrol):
  Winrate Exit (her zaman aktif, en yuksek oncelik):
    SHORT: prev_price >= alt_ic_tampon && price < alt_ic_tampon  (kesim)
    LONG : prev_price <= ust_ic_tampon && price > ust_ic_tampon  (kesim)

  CE1 Exit (sadece CE1 seviyesinde):
    SHORT: price >= ce_price (chandelier)
    LONG : price <= ce_price (chandelier)

  BE Exit (sadece BE seviyesinde, crossover entry-tarafi disbantta):
    SHORT: prev_price <= ust_disbant && price > ust_disbant  (yukari kesim)
    LONG : prev_price >= alt_disbant && price < alt_disbant  (asagi kesim)

  Lose Exit (sadece ENTRY seviyesinde, crossover entry-tarafi dis tamponunda):
    SHORT: prev_price <= ust_dis_tampon && price > ust_dis_tampon  (yukari kesim)
    LONG : prev_price >= alt_dis_tampon && price < alt_dis_tampon  (asagi kesim)

  Stoploss Exit: borsa %1 SL emrini tetikledi (disardan tespit edilir)
"""

import time
from dataclasses import dataclass, field
from typing import Optional


# Seviye sabitleri
LEVEL_ENTRY = 0
LEVEL_BE    = 1
LEVEL_CE1   = 2

LEVEL_LABELS = {
    LEVEL_ENTRY: "Giris",
    LEVEL_BE:    "BE",
    LEVEL_CE1:   "CE1",
}


@dataclass
class Position:
    """Bot tarafindan acilan ve takip edilen pozisyon."""
    symbol:          str
    side:            str       # "LONG" / "SHORT"
    entry_price:     float
    qty:             float
    stake:           float     # USDT teminat
    notional:        float     # entry_price * qty
    leverage:        int
    atr_at_entry:    float
    stop_loss_price: float

    open_time:       float = field(default_factory=time.time)

    # Seviye state
    level:           int = LEVEL_ENTRY

    # CE takibi
    best_price:      float = 0.0       # long: max gorulen, short: min gorulen
    ce_price:        Optional[float] = None   # mevcut chandelier cikis seviyesi

    # Cikis thread'inin onceki taramadaki fiyati (crossover tespiti icin)
    prev_check_price: float = 0.0

    def __post_init__(self):
        if self.best_price == 0.0:
            self.best_price = self.entry_price
        if self.prev_check_price == 0.0:
            self.prev_check_price = self.entry_price

    # --- Kar/zarar hesaplari -------------------------------------------------

    def update_best(self, price: float) -> None:
        if self.side == "LONG":
            if price > self.best_price:
                self.best_price = price
        else:
            if price < self.best_price:
                self.best_price = price

    def profit_in_atr(self, price: float) -> float:
        if self.atr_at_entry <= 0:
            return 0.0
        if self.side == "LONG":
            diff = price - self.entry_price
        else:
            diff = self.entry_price - price
        return diff / self.atr_at_entry

    def profit_pct(self, price: float) -> float:
        """Kaldirassiz yuzdesel kar."""
        if self.side == "LONG":
            return (price - self.entry_price) / self.entry_price * 100.0
        else:
            return (self.entry_price - price) / self.entry_price * 100.0

    def profit_pct_leveraged(self, price: float) -> float:
        """Kaldiracli yuzdesel kar (stake'e gore)."""
        return self.profit_pct(price) * self.leverage

    def profit_usdt(self, price: float) -> float:
        """USDT cinsinden net kar/zarar."""
        if self.side == "LONG":
            return (price - self.entry_price) * self.qty
        else:
            return (self.entry_price - price) * self.qty


def _compute_ce(pos: Position, trail_atr: float) -> float:
    """Chandelier = best_price -/+ trail * ATR."""
    trail = trail_atr * pos.atr_at_entry
    if pos.side == "LONG":
        return pos.best_price - trail
    return pos.best_price + trail


def update_level_and_ce(
    pos: Position,
    price: float,
    ust_ic_tampon: float,
    alt_ic_tampon: float,
    ce1_atr:   float,
    ce1_trail: float,
) -> Optional[int]:
    """
    Her tarama dongusunde cagrilir. Su islemleri yapar:

    1. best_price'i gunceller
    2. BE seviyesini kontrol eder:
         LONG : price > alt_ic_tampon (kar yonunde ic tampon gecisi)
         SHORT: price < ust_ic_tampon
    3. CE1 seviyesini kontrol eder: kar (ATR) >= ce1_atr
    4. CE1 seviyesindeyse chandelier'i gunceller (asla geri cekilmez)
    5. Yeni bir seviyeye geciliyorsa o seviyenin numarasini doner, yoksa None
    """
    pos.update_best(price)
    new_level = pos.level

    # --- BE seviye gecisi ---
    if pos.level < LEVEL_BE:
        if pos.side == "LONG" and price > alt_ic_tampon:
            new_level = LEVEL_BE
        elif pos.side == "SHORT" and price < ust_ic_tampon:
            new_level = LEVEL_BE

    # --- CE1 seviye gecisi (ATR bazli kar) ---
    if pos.level < LEVEL_CE1:
        # Floating-point kararliligi icin profit_atr yerine dogrudan
        # fiyat esigi karsilastir.
        ce1_offset = ce1_atr * pos.atr_at_entry
        if pos.side == "LONG":
            ce1_threshold = pos.entry_price + ce1_offset
            if pos.best_price >= ce1_threshold:
                new_level = LEVEL_CE1
        else:
            ce1_threshold = pos.entry_price - ce1_offset
            if pos.best_price <= ce1_threshold:
                new_level = LEVEL_CE1

    # --- Chandelier guncelleme (CE1 seviyesindeyse, asla geri cekilmez) ---
    if new_level >= LEVEL_CE1:
        candidate = _compute_ce(pos, ce1_trail)
        if pos.ce_price is None:
            pos.ce_price = candidate
        else:
            if pos.side == "LONG":
                pos.ce_price = max(pos.ce_price, candidate)
            else:
                pos.ce_price = min(pos.ce_price, candidate)

    if new_level != pos.level:
        pos.level = new_level
        return new_level
    return None


def check_exit(
    pos: Position,
    price: float,
    prev_price: float,
    ust_dis_tampon: float,
    alt_dis_tampon: float,
    ust_disbant:    float,
    alt_disbant:    float,
    ust_ic_tampon:  float,
    alt_ic_tampon:  float,
) -> Optional[str]:
    """
    Cikis tetikleyicilerini sirayla kontrol eder.

    Oncelik:
      1. Winrate Exit   (her zaman aktif, karsi taraf ic tampon kesimi)
      2. CE1 Exit       (sadece CE1 seviyesinde, chandelier)
      3. BE Exit        (sadece BE seviyesinde, entry-tarafi disbant kesimi)
      4. Lose Exit      (sadece ENTRY seviyesinde, entry-tarafi dis tampon kesimi)
    """

    # 1. Winrate Exit (karsi-taraf ic tampon CROSSOVER, her seviyede aktif)
    if pos.side == "LONG":
        if prev_price <= ust_ic_tampon and price > ust_ic_tampon:
            return "Winrate Exit"
    else:  # SHORT
        if prev_price >= alt_ic_tampon and price < alt_ic_tampon:
            return "Winrate Exit"

    # 2. CE1 Exit (chandelier'a deyince)
    if pos.level >= LEVEL_CE1 and pos.ce_price is not None:
        if pos.side == "LONG" and price <= pos.ce_price:
            return "CE1 Exit"
        if pos.side == "SHORT" and price >= pos.ce_price:
            return "CE1 Exit"

    # 3. BE Exit (BE seviyesinde, entry-tarafi disbantin disinda)
    if pos.level == LEVEL_BE:
        if pos.side == "LONG":
            # alt_disbant altinda
            if price < alt_disbant:
                return "BE Exit"
        else:  # SHORT
            # ust_disbant ustunde
            if price > ust_disbant:
                return "BE Exit"

    # 4. Lose Exit (ENTRY seviyesinde, entry-tarafi dis tamponun disinda)
    if pos.level == LEVEL_ENTRY:
        if pos.side == "LONG":
            # alt_dis_tampon altinda
            if price < alt_dis_tampon:
                return "Lose Exit"
        else:  # SHORT
            # ust_dis_tampon ustunde
            if price > ust_dis_tampon:
                return "Lose Exit"

    return None


def next_level_target(
    pos: Position,
    ce1_atr: float,
) -> Optional[tuple]:
    """
    Bir sonraki seviyenin (label, hedef_fiyat). Telegram raporlarinda gosterilir.
    En son seviyedeyse None.
    """
    if pos.level >= LEVEL_CE1:
        return None

    # ENTRY veya BE -> CE1 hedefi (ATR bazli)
    atr = pos.atr_at_entry
    if pos.side == "LONG":
        target_price = pos.entry_price + ce1_atr * atr
    else:
        target_price = pos.entry_price - ce1_atr * atr
    return ("CE1", target_price)
