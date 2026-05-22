"""
strategy.py - Flag mantigi ve giris sinyali tespiti.

YENI STRATEJI (mean reversion):
  Fiyat asiri yukari/asagi gittikten sonra dis banttan ICERI dogru
  donerken giris yapilir.

SHORT akisi (fiyat asiriliklara cikti, dusus baslayacak):
  FLAG AC   : prev_price >= ust_dis_tampon && price < ust_dis_tampon
              (fiyat ust dis tamponu yukaridan asagi kesti, altina indi)
  FLAG SIL  : prev_price <= ust_dis_tampon && price > ust_dis_tampon
              (fiyat ust dis tamponu asagidan yukari kesti, ustune cikti - vazgec)
  GIRIS     : prev_price >= ust_disbant && price < ust_disbant && flag aktif
              (fiyat ust dis bandi asagi yonlu kesti, altina indi -> SHORT entry)

LONG akisi (fiyat asiri dustu, yukselis baslayacak):
  FLAG AC   : prev_price <= alt_dis_tampon && price > alt_dis_tampon
              (fiyat alt dis tamponu asagidan yukari kesti, ustune cikti)
  FLAG SIL  : prev_price >= alt_dis_tampon && price < alt_dis_tampon
              (fiyat alt dis tamponu yukaridan asagi kesti, altina indi - vazgec)
  GIRIS     : prev_price <= alt_disbant && price > alt_disbant && flag aktif
              (fiyat alt dis bandi yukari yonlu kesti, ustune cikti -> LONG entry)

Onemli: Tum kosullar CROSSOVER (kesim) gerektirir. Sadece "ustunde/altinda"
olmak yetmez. prev_price gereklidir; en az 2 fiyat kaydi olmalidir.
"""

from dataclasses import dataclass
from typing import Optional

from bands import Bands


# Flag aksiyon kodlari
FLAG_OPEN_LONG   = "FLAG_OPEN_LONG"
FLAG_OPEN_SHORT  = "FLAG_OPEN_SHORT"
FLAG_CLEAR_LONG  = "FLAG_CLEAR_LONG"
FLAG_CLEAR_SHORT = "FLAG_CLEAR_SHORT"


@dataclass
class EntrySignal:
    side: str   # "LONG" / "SHORT"
    price: float
    bands: Bands


def detect_flag_action(
    prev_price: Optional[float],
    price: float,
    bands: Bands,
    current_flag: Optional[str],
) -> Optional[str]:
    """
    Flag actma veya silme aksiyonu var mi? Yoksa None.

    Donus degeri:
      FLAG_OPEN_LONG   : Long flag acilmali  (alt_dis_tampon yukari kesildi)
      FLAG_OPEN_SHORT  : Short flag acilmali (ust_dis_tampon asagi kesildi)
      FLAG_CLEAR_LONG  : Mevcut long flag silinmeli (alt_dis_tampon asagi kesildi)
      FLAG_CLEAR_SHORT : Mevcut short flag silinmeli (ust_dis_tampon yukari kesildi)
      None             : aksiyon yok
    """
    if prev_price is None:
        return None  # prev yoksa crossover tespiti yapilamaz

    # --- SHORT flag acma ---
    # Fiyat ust dis tamponu yukaridan asagi kesti, altina indi
    if prev_price >= bands.ust_dis_tampon and price < bands.ust_dis_tampon:
        if current_flag != "SHORT":
            return FLAG_OPEN_SHORT

    # --- SHORT flag silme ---
    # Fiyat ust dis tamponu asagidan yukari kesti, ustune cikti (vazgec)
    if prev_price <= bands.ust_dis_tampon and price > bands.ust_dis_tampon:
        if current_flag == "SHORT":
            return FLAG_CLEAR_SHORT

    # --- LONG flag acma ---
    # Fiyat alt dis tamponu asagidan yukari kesti, ustune cikti
    if prev_price <= bands.alt_dis_tampon and price > bands.alt_dis_tampon:
        if current_flag != "LONG":
            return FLAG_OPEN_LONG

    # --- LONG flag silme ---
    # Fiyat alt dis tamponu yukaridan asagi kesti, altina indi (vazgec)
    if prev_price >= bands.alt_dis_tampon and price < bands.alt_dis_tampon:
        if current_flag == "LONG":
            return FLAG_CLEAR_LONG

    return None


def detect_entry(
    prev_price: Optional[float],
    price: float,
    bands: Bands,
    current_flag: Optional[str],
) -> Optional[EntrySignal]:
    """
    Giris sinyali var mi?

    SHORT giris: fiyat ust disbandi YUKARIDAN ASAGI kesti (crossover) +
                 short flag aktif
    LONG giris : fiyat alt disbandi ASAGIDAN YUKARI kesti (crossover) +
                 long flag aktif

    Flag yoksa veya prev_price yoksa sinyal de yok.
    """
    if prev_price is None:
        return None

    # --- SHORT giris ---
    if (current_flag == "SHORT"
            and prev_price >= bands.ust_disbant
            and price < bands.ust_disbant):
        return EntrySignal(side="SHORT", price=price, bands=bands)

    # --- LONG giris ---
    if (current_flag == "LONG"
            and prev_price <= bands.alt_disbant
            and price > bands.alt_disbant):
        return EntrySignal(side="LONG", price=price, bands=bands)

    return None
