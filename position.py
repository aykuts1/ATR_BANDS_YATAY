"""
position.py - Pozisyon state'i, seviye gecisleri, cikis kontrolu.

YENI STRATEJI (mean reversion):

Seviyeler:
  LEVEL_ENTRY (0) : Giris yapildi. Lose Exit + borsa SL aktif.
  LEVEL_BE    (1) : Fiyat ic tamponu (kar tarafinda 0.3 ATR) gecti.
                    BE cikis cizgisi anlik disbantla beraber hareket eder
                    (DINAMIK). LONG icin alt_disbant, SHORT icin ust_disbant.
  LEVEL_CE1   (2) : Kar >= CE1_ATR (0.6). Chandelier CE1_TRAIL (0.3) ATR
                    geriden takip.
  LEVEL_CE2   (3) : Fiyat EMA cizgisini gecti.
                    Eski CE1 chandelier SILINIR.
                    Yeni chandelier CE2_TRAIL (0.5) ATR geriden takip.

  (WINRATE artik seviye degil, sadece bir cikis tipi)

Cikis tipleri:
  CE Exit (CE1/CE2)  : Chandelier seviyesine carpildi
  BE Exit            : Dinamik BE cizgisinin (= entry-tarafi disbant) disina cikildi
                       LONG: price < alt_disbant
                       SHORT: price > ust_disbant
  Lose Exit          : Entry-tarafi dis tamponuna geri donus
                       LONG: price < alt_dis_tampon  (fiyat asagiya kactiysa)
                       SHORT: price > ust_dis_tampon (fiyat yukariya kactiysa)
  Winrate Exit       : Karsi-taraf ic tamponunu kar yonunde KESERSE (crossover)
                       LONG: ust_ic_tampon yukari yonlu kesim
                       SHORT: alt_ic_tampon asagi yonlu kesim
  Stoploss Exit      : Borsa %1 SL emrini tetikledi (disardan tespit edilir)

Cikis oncelik sirasi (yuksekten dusuge):
  1. Winrate Exit (kar alimi, en yuksek oncelik)
  2. CE Exit (chandelier, CE seviyesindeyse)
  3. BE Exit (BE seviyesindeyse)
  4. Lose Exit (her zaman aktif)
"""

import time
from dataclasses import dataclass, field
from typing import Optional


# Seviye sabitleri
LEVEL_ENTRY = 0
LEVEL_BE    = 1
LEVEL_CE1   = 2
LEVEL_CE2   = 3

LEVEL_LABELS = {
    LEVEL_ENTRY: "Giris",
    LEVEL_BE:    "BE",
    LEVEL_CE1:   "CE1",
    LEVEL_CE2:   "CE2",
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

    # BE cikis cizgisi (dinamik - her tarama guncellenir, = entry tarafi disbant)
    be_exit_price:   Optional[float] = None

    # Cikis thread'inin onceki taramadaki fiyati (winrate exit crossover icin)
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
    """CE = best_price -/+ trail * ATR."""
    trail = trail_atr * pos.atr_at_entry
    if pos.side == "LONG":
        return pos.best_price - trail
    return pos.best_price + trail


def update_level_and_ce(
    pos: Position,
    price: float,
    ema: float,
    ust_ic_tampon: float,
    alt_ic_tampon: float,
    ust_disbant:   float,
    alt_disbant:   float,
    ce1_atr:   float,
    ce1_trail: float,
    ce2_trail: float,
) -> Optional[int]:
    """
    Her tarama dongusunde cagrilir. Su islemleri yapar:

    1. best_price'i gunceller
    2. BE seviyesini kontrol eder:
         LONG : price > alt_ic_tampon (0.3 ATR kar)
         SHORT: price < ust_ic_tampon (0.3 ATR kar)
    3. BE cikis cizgisini DINAMIK olarak entry-tarafi disbant'a esitler:
         LONG : be_exit_price = alt_disbant
         SHORT: be_exit_price = ust_disbant
    4. CE1 seviyesini kontrol eder: kar >= ce1_atr
    5. CE2 seviyesini kontrol eder: fiyat EMA'yi gecti mi
         LONG : price > ema
         SHORT: price < ema
    6. Yeni CE seviyesine gecildiyse eski chandelier SILINIR, yeni trail
       ile yeniden hesaplanir
    7. CE'yi gunceller (asla geri cekilmez - sadece kar yonunde ilerler)
    8. Yeni bir seviyeye geciliyorsa o seviyenin numarasini doner, yoksa None
    """
    pos.update_best(price)
    new_level = pos.level

    # --- BE seviye gecisi (kar tarafindaki ic tampon) ---
    if pos.level < LEVEL_BE:
        if pos.side == "LONG" and price > alt_ic_tampon:
            new_level = LEVEL_BE
        elif pos.side == "SHORT" and price < ust_ic_tampon:
            new_level = LEVEL_BE

    # --- BE cikis cizgisi (DINAMIK: entry-tarafi disbant) ---
    if new_level >= LEVEL_BE or pos.level >= LEVEL_BE:
        pos.be_exit_price = alt_disbant if pos.side == "LONG" else ust_disbant

    # --- CE2 seviye gecisi (EMA cross) ---
    if pos.level < LEVEL_CE2 and new_level < LEVEL_CE2:
        ce2_now = False
        if pos.side == "LONG" and price > ema:
            ce2_now = True
        elif pos.side == "SHORT" and price < ema:
            ce2_now = True
        if ce2_now:
            new_level = LEVEL_CE2

    # --- CE1 seviye gecisi (ATR bazli kar) ---
    # CE2 zaten tetiklendiyse CE1'i atla; level zaten >= CE2 olacak
    if new_level < LEVEL_CE2 and pos.level < LEVEL_CE1:
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

    # --- Chandelier RESET: yeni bir CE seviyesine geciliyor mu ---
    # Eski chandelier silinir, yenisi sifirdan hesaplanir
    if new_level != pos.level and new_level >= LEVEL_CE1:
        pos.ce_price = None

    # --- Aktif CE takip carpani ---
    if new_level >= LEVEL_CE2:
        trail = ce2_trail
    elif new_level >= LEVEL_CE1:
        trail = ce1_trail
    else:
        trail = None

    # --- CE guncelleme (asla geri cekilmez) ---
    if trail is not None:
        candidate = _compute_ce(pos, trail)
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
    ust_ic_tampon:  float,
    alt_ic_tampon:  float,
) -> Optional[str]:
    """
    Cikis tetikleyicilerini sirayla kontrol eder.

    Oncelik sirasi (yuksekten dusuge):
      1. Winrate Exit : Karsi-taraf ic tamponu kar yonunde kesildi (crossover)
      2. CE Exit      : Chandelier seviyesine carpildi (CE seviyesindeyse)
      3. BE Exit      : Entry-tarafi disbantin disina cikildi (BE seviyesindeyse)
      4. Lose Exit    : Entry-tarafi dis tamponuna geri donuldu

    Stoploss Exit borsa tarafindan tetiklenir, disardan tespit edilir.
    """

    # 1. Winrate Exit (karsi-taraf ic tampon CROSSOVER, her seviyede aktif)
    #    LONG:  prev <= ust_ic_tampon < price  (yukari kesti)
    #    SHORT: prev >= alt_ic_tampon > price  (asagi kesti)
    if pos.side == "LONG":
        if prev_price <= ust_ic_tampon and price > ust_ic_tampon:
            return "Winrate Exit"
    else:
        if prev_price >= alt_ic_tampon and price < alt_ic_tampon:
            return "Winrate Exit"

    # 2. CE Exit (varsa CE seviyesi)
    if pos.ce_price is not None and pos.level >= LEVEL_CE1:
        if pos.side == "LONG" and price <= pos.ce_price:
            return _ce_exit_name(pos.level)
        if pos.side == "SHORT" and price >= pos.ce_price:
            return _ce_exit_name(pos.level)

    # 3. BE Exit (BE seviyesindeysek)
    if pos.level >= LEVEL_BE and pos.be_exit_price is not None:
        # LONG: BE exit = entry tarafi disbant = alt_disbant
        #       price alt_disbant'in altina dustuyse (be_exit'in disina)
        if pos.side == "LONG" and price < pos.be_exit_price:
            return "BE Exit"
        # SHORT: BE exit = ust_disbant
        #        price ust_disbant'in ustune ciktiysa
        if pos.side == "SHORT" and price > pos.be_exit_price:
            return "BE Exit"

    # 4. Lose Exit (her zaman aktif, entry-tarafi dis tampon)
    if pos.side == "LONG" and price < alt_dis_tampon:
        return "Lose Exit"
    if pos.side == "SHORT" and price > ust_dis_tampon:
        return "Lose Exit"

    return None


def _ce_exit_name(level: int) -> str:
    if level == LEVEL_CE2:
        return "CE2 Exit"
    if level == LEVEL_CE1:
        return "CE1 Exit"
    return "Lose Exit"


def next_level_target(
    pos: Position,
    ce1_atr: float,
    ema: float,
) -> Optional[tuple]:
    """
    Bir sonraki seviyenin (label, hedef_fiyat). Telegram raporlarinda gosterilir.
    En son seviyedeyse None.
    """
    atr = pos.atr_at_entry
    if pos.level >= LEVEL_CE2:
        return None
    elif pos.level >= LEVEL_CE1:
        # CE2 hedefi: EMA cizgisi
        return ("CE2", ema)
    else:
        # ENTRY veya BE -> CE1 hedefi (ATR bazli)
        if pos.side == "LONG":
            target_price = pos.entry_price + ce1_atr * atr
        else:
            target_price = pos.entry_price - ce1_atr * atr
        return ("CE1", target_price)
