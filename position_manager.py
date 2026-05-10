"""
Pozisyon Yönetimi (ATR bazlı)

- Borsa SL: 1.5 ATR uzakta (giriş anında)
- Kâr ≥ 1 ATR → Borsa SL giriş fiyatına çekilir + CE aktif olur (1 ATR trail)
- Kâr ≥ 2 ATR → CE 0.5 ATR trail'e sıkışır (kâr kilidi)
- CE asla geri çekilmez
- CE'ye fiyat çarptıysa bot pozisyonu kapatır
"""

import logging
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from config import (
    BREAKEVEN_TRIGGER_ATR,
    CE_ACTIVATION_ATR,
    CE_INITIAL_TRAIL_ATR,
    CE_TIGHT_TRIGGER_ATR,
    CE_TIGHT_TRAIL_ATR,
)

logger = logging.getLogger(__name__)

POSITIONS_FILE = "positions_state.json"


@dataclass
class TrackedPosition:
    symbol: str
    side: str               # 'long' veya 'short'
    entry_price: float
    qty: float
    initial_sl: float       # Borsa'ya verilen ilk SL (1.5 ATR)
    atr_at_entry: float     # Giriş anındaki ATR
    breakeven_active: bool = False  # Borsa SL giriş fiyatına çekildi mi
    ce_active: bool = False         # CE aktif mi (kâr 1 ATR'yi geçti mi)
    ce_price: float = 0.0           # Mevcut CE seviyesi (kilitlenmiş)
    ce_trail_atr: float = 1.0       # CE'nin kaç ATR geriden takip ettiği
    highest_price: float = 0.0
    lowest_price: float = 0.0
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
        pos = TrackedPosition(
            symbol=symbol,
            side=side.lower(),
            entry_price=entry_price,
            qty=qty,
            initial_sl=initial_sl,
            atr_at_entry=atr_value,
            breakeven_active=False,
            ce_active=False,
            ce_price=0.0,
            ce_trail_atr=CE_INITIAL_TRAIL_ATR,
            highest_price=entry_price,
            lowest_price=entry_price,
        )
        self.positions[symbol] = pos
        self._save_state()
        logger.info(f"Pozisyon eklendi: {symbol} {side} entry={entry_price} ATR={atr_value}")

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
        else:
            return ((entry - current) / entry) * 100

    @staticmethod
    def calculate_pnl_atr(side: str, entry: float, current: float, atr: float) -> float:
        """Kâr/zararı ATR cinsinden döner."""
        if atr == 0:
            return 0.0
        if side == "long":
            return (current - entry) / atr
        else:
            return (entry - current) / atr

    # ============================================================
    # POZISYON GÜNCELLEME (her 60 saniyede çağrılır)
    # ============================================================
    def update_position(self, symbol: str, current_price: float) -> dict:
        """
        Returns: {
            'action': 'none' | 'close',
            'events': ['breakeven_and_ce', 'ce_tightened'],
            'reason': str (close ise),
            'ce_price': float,
            'pnl_atr': float,
        }
        """
        pos = self.positions.get(symbol)
        if not pos:
            return {"action": "none", "events": []}

        events = []

        # 1. Highest/lowest güncelle
        if pos.side == "long":
            if current_price > pos.highest_price:
                pos.highest_price = current_price
        else:
            if current_price < pos.lowest_price:
                pos.lowest_price = current_price

        pnl_atr = self.calculate_pnl_atr(pos.side, pos.entry_price, current_price, pos.atr_at_entry)

        # 2. Breakeven + CE aktivasyonu (Kâr ≥ 1 ATR)
        if not pos.breakeven_active and pnl_atr >= BREAKEVEN_TRIGGER_ATR:
            pos.breakeven_active = True
            pos.ce_active = True
            pos.ce_trail_atr = CE_INITIAL_TRAIL_ATR  # 1.0
            if pos.side == "long":
                pos.ce_price = pos.highest_price - (CE_INITIAL_TRAIL_ATR * pos.atr_at_entry)
            else:
                pos.ce_price = pos.lowest_price + (CE_INITIAL_TRAIL_ATR * pos.atr_at_entry)
            events.append("breakeven_and_ce")
            logger.info(f"{symbol} breakeven + CE aktif (PnL: {pnl_atr:.2f} ATR, CE: {pos.ce_price})")

        # CE aktif değilse devam etme
        if not pos.ce_active:
            self._save_state()
            return {"action": "none", "events": events, "pnl_atr": pnl_atr}

        # 3. CE sıkışma (Kâr ≥ 2 ATR → 0.5 ATR trail)
        if pos.ce_trail_atr > CE_TIGHT_TRAIL_ATR and pnl_atr >= CE_TIGHT_TRIGGER_ATR:
            pos.ce_trail_atr = CE_TIGHT_TRAIL_ATR  # 0.5
            # CE'yi yeni trail ile yeniden hesapla
            if pos.side == "long":
                new_ce = pos.highest_price - (CE_TIGHT_TRAIL_ATR * pos.atr_at_entry)
                if new_ce > pos.ce_price:
                    pos.ce_price = new_ce
            else:
                new_ce = pos.lowest_price + (CE_TIGHT_TRAIL_ATR * pos.atr_at_entry)
                if new_ce < pos.ce_price:
                    pos.ce_price = new_ce
            events.append("ce_tightened")
            logger.info(f"{symbol} CE sıkıştı (0.5 ATR trail), CE: {pos.ce_price}")

        # 4. CE seviyesini takip et (asla geri çekilmez)
        if pos.side == "long":
            new_ce = pos.highest_price - (pos.ce_trail_atr * pos.atr_at_entry)
            if new_ce > pos.ce_price:
                pos.ce_price = new_ce
        else:
            new_ce = pos.lowest_price + (pos.ce_trail_atr * pos.atr_at_entry)
            if new_ce < pos.ce_price:
                pos.ce_price = new_ce

        self._save_state()

        # 5. CE tetiklendi mi?
        if pos.side == "long":
            if current_price <= pos.ce_price:
                return {
                    "action": "close",
                    "events": events,
                    "reason": f"CE tetiklendi ({pos.ce_trail_atr} ATR geri, kâr {pnl_atr:.2f} ATR)",
                    "ce_price": pos.ce_price,
                    "pnl_atr": pnl_atr,
                }
        else:
            if current_price >= pos.ce_price:
                return {
                    "action": "close",
                    "events": events,
                    "reason": f"CE tetiklendi ({pos.ce_trail_atr} ATR geri, kâr {pnl_atr:.2f} ATR)",
                    "ce_price": pos.ce_price,
                    "pnl_atr": pnl_atr,
                }

        return {"action": "none", "events": events, "ce_price": pos.ce_price, "pnl_atr": pnl_atr}
