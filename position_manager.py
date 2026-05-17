"""
Position Manager - açık pozisyonları hafızada tutar.
"""
from dataclasses import dataclass, field
from typing import Dict, Optional
import time


@dataclass
class Position:
    """Tek bir açık pozisyon."""
    symbol: str
    side: str               # "Buy" (long) veya "Sell" (short)
    entry_price: float      # Giriş fiyatı
    qty: float              # Miktar (coin)
    stake_usdt: float       # Stake (USDT)
    leverage: int           # Kaldıraç (50x)
    notional_usdt: float    # Hacim = stake * leverage
    sl_price: float         # Borsadaki %1 stop loss fiyatı
    open_time: float        # Açılış timestamp
    
    # Strategy state
    best_price: Optional[float] = None       # CE için en iyi fiyat
    ce_active: bool = False                  # CE aktif mi?
    crossed_target: bool = False             # Hedef çizgi geçildi mi?
    
    # Tunnel snapshot at entry (for reporting)
    entry_tunnel_high: float = 0.0
    entry_tunnel_low: float = 0.0
    entry_signal_high: float = 0.0
    entry_signal_low: float = 0.0


class PositionManager:
    """Açık pozisyonları yönetir."""
    
    def __init__(self):
        self._positions: Dict[str, Position] = {}
    
    def open(self, pos: Position) -> None:
        self._positions[pos.symbol] = pos
    
    def close(self, symbol: str) -> Optional[Position]:
        return self._positions.pop(symbol, None)
    
    def get(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)
    
    def has(self, symbol: str) -> bool:
        return symbol in self._positions
    
    def all(self) -> Dict[str, Position]:
        return dict(self._positions)
    
    def count(self) -> int:
        return len(self._positions)
    
    def symbols(self) -> list:
        return list(self._positions.keys())
