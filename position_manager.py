"""
Pozisyon Yönetimi
- Açık pozisyonların kâr durumunu takip eder
- Chandelier Exit (CE) seviyesini hesaplar (asla geri çekilmez)
- Breakeven SL'yi tetikler
- CE'ye fiyat çarptıysa pozisyonu kapatır
"""
import logging
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from config import (
    BREAKEVEN_TRIGGER_PCT,
    CE_INITIAL_ATR,
    CE_AT_2PCT,
    CE_AT_3PCT,
    CE_AT_4PCT,
)

logger = logging.getLogger(__name__)

POSITIONS_FILE = "positions_state.json"


@dataclass
class TrackedPosition:
    symbol: str
    side: str  # 'long' veya 'short'
    entry_price: float
    qty: float
    initial_sl: float
    atr_at_entry: float
    ce_price: float            # Mevcut CE seviyesi (kilitlenmiş)
    highest_price: float       # LONG için en yüksek nokta
    lowest_price: float        # SHORT için en düşük nokta
    breakeven_active: bool = False
    current_atr_mult: float = field(default=CE_INITIAL_ATR)
    opened_at: float = field(default_factory=time.time)


class PositionManager:
    def __init__(self):
        self.positions: dict[str, TrackedPosition] = {}
        self._load_state()

    # ============================================================
    # STATE PERSISTENCE
    # ============================================================
    def _save_state(self):
        try:
            data = {sym: asdict(p) for sym, p in self.positions.items()}
            with open(POSITIONS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.exception(f"State kaydetme hatası: {e}")

    def _load_state(self):
        if not os.path.exists(POSITIONS_FILE):
            return
        try:
            with open(POSITIONS_FILE, "r") as f:
                data = json.load(f)
            for sym, p in data.items():
                self.positions[sym] = TrackedPosition(**p)
            logger.info(f"State yüklendi: {len(self.positions)} pozisyon")
        except Exception as e:
            logger.exception(f"State yükleme hatası: {e}")

    # ============================================================
    # POZISYON EKLEME / SILME
    # ============================================================
    def add_position(self, symbol: str, side: str, entry_price: float, qty: float,
                     initial_sl: float, atr_value: float):
        if side.lower() == "long":
            ce_price = entry_price - (CE_INITIAL_ATR * atr_value)
            highest = entry_price
            lowest = entry_price
        else:
            ce_price = entry_price + (CE_INITIAL_ATR * atr_value)
            highest = entry_price
            lowest = entry_price

        pos = TrackedPosition(
            symbol=symbol,
            side=side.lower(),
            entry_price=entry_price,
            qty=qty,
            initial_sl=initial_sl,
            atr_at_entry=atr_value,
            ce_price=ce_price,
            highest_price=highest,
            lowest_price=lowest,
            breakeven_active=False,
            current_atr_mult=CE_INITIAL_ATR,
        )
        self.positions[symbol] = pos
        self._save_state()
        logger.info(f"Pozisyon eklendi: {symbol} {side} entry={entry_price} CE={ce_price}")

    def remove_position(self, symbol: str):
        if symbol in self.positions:
            del self.positions[symbol]
            self._save_state()

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions

    def count(self) -> int:
        return len(self.positions)

    def get(self, symbol: str) -> Optional[TrackedPosition]:
        return self.positions.get(symbol)

    # ============================================================
    # KAR HESABI
    # ============================================================
    @staticmethod
    def calculate_pnl_pct(side: str, entry: float, current: float) -> float:
        """Fiyat hareketi yüzdesi (kaldıraçsız)."""
        if entry == 0:
            return 0.0
        if side == "long":
            return ((current - entry) / entry) * 100
        else:  # short
            return ((entry - current) / entry) * 100

    # ============================================================
    # CE GUNCELLEME MANTIGI
    # ============================================================
    def _get_ce_atr_mult(self, pnl_pct: float) -> float:
        """Kâr seviyesine göre CE ATR çarpanını döner."""
        if pnl_pct >= 4.0:
            return CE_AT_4PCT
        if pnl_pct >= 3.0:
            return CE_AT_3PCT
        if pnl_pct >= 2.0:
            return CE_AT_2PCT
        return CE_INITIAL_ATR

    def update_position(self, symbol: str, current_price: float) -> dict:
        """
        Her dakika çağrılır.
        Returns: {
            'action': 'none' | 'breakeven' | 'close',
            'reason': str,
            'new_sl': float (breakeven ise),
            'ce_price': float,
        }
        """
        pos = self.positions.get(symbol)
        if not pos:
            return {"action": "none", "reason": "no position"}

        # En yüksek/düşük noktayı güncelle
        if pos.side == "long":
            if current_price > pos.highest_price:
                pos.highest_price = current_price
        else:
            if current_price < pos.lowest_price:
                pos.lowest_price = current_price

        # Kâr yüzdesi
        pnl_pct = self.calculate_pnl_pct(pos.side, pos.entry_price, current_price)

        # 1) BREAKEVEN tetiklendi mi?
        if not pos.breakeven_active and pnl_pct >= BREAKEVEN_TRIGGER_PCT:
            pos.breakeven_active = True
            self._save_state()
            return {
                "action": "breakeven",
                "reason": f"Kâr +{pnl_pct:.2f}%, SL → giriş fiyatına",
                "new_sl": pos.entry_price,
                "ce_price": pos.ce_price,
            }

        # 2) CE seviyesini güncelle
        # Yeni ATR çarpanı
        new_mult = self._get_ce_atr_mult(pnl_pct)
        if new_mult < pos.current_atr_mult:
            pos.current_atr_mult = new_mult

        # CE'yi en yüksek/düşük noktaya göre hesapla
        if pos.side == "long":
            new_ce = pos.highest_price - (pos.current_atr_mult * pos.atr_at_entry)
            # CE asla geri çekilmez (yukarı gidebilir, aşağı inemez)
            if new_ce > pos.ce_price:
                pos.ce_price = new_ce
                self._save_state()
        else:
            new_ce = pos.lowest_price + (pos.current_atr_mult * pos.atr_at_entry)
            # SHORT'ta CE asla yukarı çekilmez (aşağı gidebilir, yukarı çıkamaz)
            if new_ce < pos.ce_price:
                pos.ce_price = new_ce
                self._save_state()

        # 3) Fiyat CE'ye çarptı mı?
        if pos.side == "long":
            if current_price <= pos.ce_price:
                return {
                    "action": "close",
                    "reason": f"CE tetiklendi @ {pos.ce_price:.6f} (kâr %{pnl_pct:.2f})",
                    "ce_price": pos.ce_price,
                }
        else:
            if current_price >= pos.ce_price:
                return {
                    "action": "close",
                    "reason": f"CE tetiklendi @ {pos.ce_price:.6f} (kâr %{pnl_pct:.2f})",
                    "ce_price": pos.ce_price,
                }

        return {"action": "none", "reason": "tracking", "ce_price": pos.ce_price, "pnl_pct": pnl_pct}
