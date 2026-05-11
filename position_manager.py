"""
Pozisyon Yönetimi (ATR bazlı) - 2 KADEMELİ SİSTEM

KADEME 0 (Giriş anında):
  - Borsa SL: giriş ± 1.0 ATR (emniyet kemeri)
  - CE AKTİF: giriş ± 1.0 ATR (highest/lowest takibi başlar)

KADEME 1 (Kâr ≥ 0.5 ATR):
  - Borsa SL → giriş ± 0.2 ATR'ye çekilir (kâr kilidi)
  - CE değişmez (hâlâ 1.0 ATR trail)

KADEME 2 (Kâr ≥ 1.0 ATR):
  - CE 0.5 ATR trail'e sıkışır (son durak)
  - Borsa SL aynı (giriş ± 0.2 ATR)

CE asla geri çekilmez. CE'ye fiyat çarptıysa bot pozisyonu kapatır.
"""

import logging
import json
import os
import time
from dataclasses import dataclass, field, asdict, fields
from typing import Optional

from config import (
    SL_LOCK_TRIGGER_ATR,
    SL_LOCK_OFFSET_ATR,
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
    initial_sl: float       # Borsa'ya verilen ilk SL (-1.0 ATR)
    atr_at_entry: float     # Giriş anındaki ATR
    sl_locked: bool = False         # Borsa SL kâr kilidine (+0.2 ATR) çekildi mi
    locked_sl_price: float = 0.0    # Kâr kilidi fiyatı (giriş ± 0.2 ATR)
    ce_price: float = 0.0           # Mevcut CE seviyesi (kilitlenmiş, geri çekilmez)
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

            valid_fields = {f.name for f in fields(TrackedPosition)}

            for sym, p in data.items():
                # Eski state migration: breakeven_active → sl_locked
                if "breakeven_active" in p and "sl_locked" not in p:
                    p["sl_locked"] = p.pop("breakeven_active")
                # Eski sistemde locked_sl_price yoksa, giriş fiyatı kabul edilir
                if p.get("sl_locked") and "locked_sl_price" not in p:
                    p["locked_sl_price"] = p.get("entry_price", 0.0)
                # ce_active kaldırıldı (CE artık her zaman aktif), eski state'lerden temizle
                p.pop("ce_active", None)
                # Bilinmeyen alanları at, eksikleri default'la başlat
                filtered = {k: v for k, v in p.items() if k in valid_fields}
                self.positions[sym] = TrackedPosition(**filtered)

            logger.info(f"State yüklendi: {len(self.positions)} pozisyon")
        except Exception as e:
            logger.exception(f"State yükleme hatası: {e}")

    # ============================================================
    # POZISYON EKLEME / SILME
    # ============================================================
    def add_position(self, symbol: str, side: str, entry_price: float, qty: float,
                     initial_sl: float, atr_value: float):
        """
        Giriş anında CE aktif olarak başlatılır.
        CE seviyesi = giriş ± (CE_INITIAL_TRAIL_ATR × ATR) — yani 1.0 ATR geri
        """
        side_lower = side.lower()
        if side_lower == "long":
            ce_price = entry_price - (CE_INITIAL_TRAIL_ATR * atr_value)
        else:
            ce_price = entry_price + (CE_INITIAL_TRAIL_ATR * atr_value)

        pos = TrackedPosition(
            symbol=symbol,
            side=side_lower,
            entry_price=entry_price,
            qty=qty,
            initial_sl=initial_sl,
            atr_at_entry=atr_value,
            sl_locked=False,
            locked_sl_price=0.0,
            ce_price=ce_price,
            ce_trail_atr=CE_INITIAL_TRAIL_ATR,
            highest_price=entry_price,
            lowest_price=entry_price,
        )
        self.positions[symbol] = pos
        self._save_state()
        logger.info(
            f"Pozisyon eklendi: {symbol} {side_lower} entry={entry_price} "
            f"ATR={atr_value} CE={ce_price:.6f} (1.0 ATR geri)"
        )

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
            'events': ['sl_lock', 'ce_tightened'],
            'reason': str (close ise),
            'ce_price': float,
            'new_sl': float (sl_lock event'inde),
            'pnl_atr': float,
        }
        """
        pos = self.positions.get(symbol)
        if not pos:
            return {"action": "none", "events": []}

        events = []
        result_extra = {}

        # 1. Highest/lowest güncelle
        if pos.side == "long":
            if current_price > pos.highest_price:
                pos.highest_price = current_price
        else:
            if current_price < pos.lowest_price:
                pos.lowest_price = current_price

        pnl_atr = self.calculate_pnl_atr(pos.side, pos.entry_price, current_price, pos.atr_at_entry)

        # 2. KADEME 1: Kâr ≥ 0.5 ATR → Borsa SL +0.2 ATR'ye çekilir (CE değişmez)
        if not pos.sl_locked and pnl_atr >= SL_LOCK_TRIGGER_ATR:
            pos.sl_locked = True
            if pos.side == "long":
                pos.locked_sl_price = pos.entry_price + (SL_LOCK_OFFSET_ATR * pos.atr_at_entry)
            else:
                pos.locked_sl_price = pos.entry_price - (SL_LOCK_OFFSET_ATR * pos.atr_at_entry)

            events.append("sl_lock")
            result_extra["new_sl"] = pos.locked_sl_price
            logger.info(
                f"{symbol} kâr kilidi aktif "
                f"(PnL: {pnl_atr:.2f} ATR, SL: {pos.locked_sl_price:.6f}, "
                f"CE: {pos.ce_price:.6f} — değişmedi, hâlâ 1.0 ATR trail)"
            )

        # 3. KADEME 2: Kâr ≥ 1.0 ATR → CE 0.5 ATR trail'e sıkışır (son durak)
        if pos.ce_trail_atr > CE_TIGHT_TRAIL_ATR and pnl_atr >= CE_TIGHT_TRIGGER_ATR:
            pos.ce_trail_atr = CE_TIGHT_TRAIL_ATR  # 0.5
            if pos.side == "long":
                new_ce = pos.highest_price - (CE_TIGHT_TRAIL_ATR * pos.atr_at_entry)
                if new_ce > pos.ce_price:
                    pos.ce_price = new_ce
            else:
                new_ce = pos.lowest_price + (CE_TIGHT_TRAIL_ATR * pos.atr_at_entry)
                if new_ce < pos.ce_price:
                    pos.ce_price = new_ce
            events.append("ce_tightened")
            logger.info(f"{symbol} CE sıkıştı (0.5 ATR trail), CE: {pos.ce_price:.6f}")

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
                    **result_extra,
                }
        else:
            if current_price >= pos.ce_price:
                return {
                    "action": "close",
                    "events": events,
                    "reason": f"CE tetiklendi ({pos.ce_trail_atr} ATR geri, kâr {pnl_atr:.2f} ATR)",
                    "ce_price": pos.ce_price,
                    "pnl_atr": pnl_atr,
                    **result_extra,
                }

        return {
            "action": "none",
            "events": events,
            "ce_price": pos.ce_price,
            "pnl_atr": pnl_atr,
            **result_extra,
        }
