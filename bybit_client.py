"""
Bybit v5 API client wrapper.
"""
import math
import time
from decimal import Decimal
from typing import List, Optional, Dict, Any

from pybit.unified_trading import HTTP

import config


class BybitClient:
    """Bybit Unified Trading hesabı için API wrapper."""

    def __init__(self):
        self.session = HTTP(
            testnet=config.BYBIT_TESTNET,
            api_key=config.BYBIT_API_KEY,
            api_secret=config.BYBIT_API_SECRET,
        )
        self._instrument_cache: Dict[str, dict] = {}

    # ============================================================
    # ACCOUNT
    # ============================================================
    def get_total_balance_usdt(self) -> float:
        """Total wallet balance in USDT (Unified Account)."""
        resp = self.session.get_wallet_balance(accountType=config.ACCOUNT_TYPE)
        if resp.get("retCode") != 0:
            raise RuntimeError(f"get_wallet_balance: {resp}")
        accounts = resp["result"]["list"]
        if not accounts:
            return 0.0
        # Unified account total in USDT
        acc = accounts[0]
        # Try totalWalletBalance first, fallback to USDT coin balance
        total = acc.get("totalWalletBalance")
        if total:
            return float(total)
        for coin in acc.get("coin", []):
            if coin.get("coin") == "USDT":
                return float(coin.get("walletBalance", 0) or 0)
        return 0.0

    # ============================================================
    # INSTRUMENT INFO (tick size, qty step, min qty)
    # ============================================================
    def get_instrument_info(self, symbol: str) -> dict:
        """Cached instrument info."""
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]

        resp = self.session.get_instruments_info(
            category=config.CATEGORY,
            symbol=symbol,
        )
        if resp.get("retCode") != 0:
            raise RuntimeError(f"get_instruments_info {symbol}: {resp}")
        items = resp["result"]["list"]
        if not items:
            raise RuntimeError(f"Instrument {symbol} bulunamadı")
        item = items[0]
        info = {
            "symbol": symbol,
            "tick_size": float(item["priceFilter"]["tickSize"]),
            "qty_step": float(item["lotSizeFilter"]["qtyStep"]),
            "min_qty": float(item["lotSizeFilter"]["minOrderQty"]),
            "max_leverage": float(item["leverageFilter"]["maxLeverage"]),
        }
        self._instrument_cache[symbol] = info
        return info

    # ============================================================
    # KLINES
    # ============================================================
    def get_klines(self, symbol: str, interval: str, limit: int) -> List[dict]:
        """
        Bybit klines. Returns list ordered OLDEST → NEWEST.
        Each kline: {start, open, high, low, close, volume}
        """
        resp = self.session.get_kline(
            category=config.CATEGORY,
            symbol=symbol,
            interval=interval,
            limit=limit,
        )
        if resp.get("retCode") != 0:
            raise RuntimeError(f"get_kline {symbol}: {resp}")
        raw = resp["result"]["list"]
        # Bybit returns newest first - reverse
        raw.reverse()
        result = []
        for k in raw:
            result.append({
                "start": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        return result

    # ============================================================
    # TICKER / PRICE
    # ============================================================
    def get_last_price(self, symbol: str) -> float:
        """Get current last traded price."""
        resp = self.session.get_tickers(
            category=config.CATEGORY,
            symbol=symbol,
        )
        if resp.get("retCode") != 0:
            raise RuntimeError(f"get_tickers {symbol}: {resp}")
        items = resp["result"]["list"]
        if not items:
            raise RuntimeError(f"Ticker {symbol} bulunamadı")
        return float(items[0]["lastPrice"])

    # ============================================================
    # LEVERAGE & MARGIN
    # ============================================================
    def set_leverage(self, symbol: str, leverage: int) -> None:
        """Set leverage. Raises on failure."""
        try:
            self.session.set_leverage(
                category=config.CATEGORY,
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
        except Exception as e:
            # 110043: leverage not modified (already set) - ignore
            if "110043" in str(e):
                return
            raise

    def set_isolated_margin(self, symbol: str, leverage: int) -> None:
        """Switch to isolated margin mode with given leverage."""
        try:
            self.session.switch_margin_mode(
                category=config.CATEGORY,
                symbol=symbol,
                tradeMode=1,  # 1 = isolated, 0 = cross
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
        except Exception as e:
            # 110026: margin mode not modified - ignore
            if "110026" in str(e):
                return
            raise

    # ============================================================
    # ORDERS
    # ============================================================
    def place_limit_order(self, symbol: str, side: str, qty: float, price: float,
                          stop_loss_price: Optional[float] = None,
                          reduce_only: bool = False) -> str:
        """
        Place a Post-Only limit order. Returns orderId.

        PostOnly: emir kesinlikle MAKER olarak işlenir.
        Eğer taker olacaksa borsa emri otomatik iptal eder (komisyon yok).
        """
        params = {
            "category": config.CATEGORY,
            "symbol": symbol,
            "side": side,
            "orderType": "Limit",
            "qty": str(qty),
            "price": str(price),
            "timeInForce": "PostOnly",
            "reduceOnly": reduce_only,
        }
        if stop_loss_price is not None:
            params["stopLoss"] = str(stop_loss_price)
        resp = self.session.place_order(**params)
        if resp.get("retCode") != 0:
            raise RuntimeError(f"place_limit_order {symbol}: {resp}")
        return resp["result"]["orderId"]

    def place_market_order(self, symbol: str, side: str, qty: float,
                           stop_loss_price: Optional[float] = None,
                           reduce_only: bool = False) -> str:
        """Place a market order. Returns orderId."""
        params = {
            "category": config.CATEGORY,
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": str(qty),
            "reduceOnly": reduce_only,
        }
        if stop_loss_price is not None:
            params["stopLoss"] = str(stop_loss_price)
        resp = self.session.place_order(**params)
        if resp.get("retCode") != 0:
            raise RuntimeError(f"place_market_order {symbol}: {resp}")
        return resp["result"]["orderId"]

    def cancel_order(self, symbol: str, order_id: str) -> None:
        """Cancel an open order."""
        try:
            self.session.cancel_order(
                category=config.CATEGORY,
                symbol=symbol,
                orderId=order_id,
            )
        except Exception as e:
            # Already cancelled / filled - ignore
            if "110001" in str(e) or "30032" in str(e):
                return
            raise

    def get_order_status(self, symbol: str, order_id: str) -> str:
        """Get order status string."""
        resp = self.session.get_open_orders(
            category=config.CATEGORY,
            symbol=symbol,
            orderId=order_id,
        )
        if resp.get("retCode") == 0:
            items = resp["result"]["list"]
            if items:
                return items[0].get("orderStatus", "Unknown")
        # Order not in open list - check history
        resp = self.session.get_order_history(
            category=config.CATEGORY,
            symbol=symbol,
            orderId=order_id,
        )
        if resp.get("retCode") == 0:
            items = resp["result"]["list"]
            if items:
                return items[0].get("orderStatus", "Unknown")
        return "Unknown"

    def update_stop_loss(self, symbol: str, sl_price: float) -> None:
        """Update SL of current position."""
        self.session.set_trading_stop(
            category=config.CATEGORY,
            symbol=symbol,
            stopLoss=str(sl_price),
            positionIdx=0,  # one-way mode
        )

    # ============================================================
    # POSITIONS
    # ============================================================
    def get_position(self, symbol: str) -> Optional[dict]:
        """Get current open position for symbol or None."""
        resp = self.session.get_positions(
            category=config.CATEGORY,
            symbol=symbol,
        )
        if resp.get("retCode") != 0:
            return None
        for pos in resp["result"]["list"]:
            size = float(pos.get("size", 0) or 0)
            if size > 0:
                return pos
        return None

    def get_open_positions(self) -> List[dict]:
        """Get all open positions across all symbols."""
        resp = self.session.get_positions(
            category=config.CATEGORY,
            settleCoin="USDT",
        )
        if resp.get("retCode") != 0:
            return []
        result = []
        for pos in resp["result"]["list"]:
            size = float(pos.get("size", 0) or 0)
            if size > 0:
                result.append(pos)
        return result

    def close_position(self, symbol: str, side: str, qty: float) -> str:
        """Close position by placing opposite market order."""
        opposite = "Sell" if side == "Buy" else "Buy"
        return self.place_market_order(
            symbol=symbol,
            side=opposite,
            qty=qty,
            reduce_only=True,
        )

    # ============================================================
    # CLOSED PNL
    # ============================================================
    def get_closed_pnl(self, symbol: str) -> tuple:
        """Get last closed PnL for symbol. Returns (exit_price, pnl_usdt)."""
        try:
            resp = self.session.get_closed_pnl(
                category=config.CATEGORY,
                symbol=symbol,
                limit=1,
            )
            if resp.get("retCode") == 0:
                items = resp["result"]["list"]
                if items:
                    last = items[0]
                    exit_price = float(last.get("avgExitPrice", 0) or 0)
                    pnl = float(last.get("closedPnl", 0) or 0)
                    return exit_price, pnl
        except Exception as e:
            print(f"[WARN] get_closed_pnl {symbol}: {e}")
        return None, 0.0

    # ============================================================
    # ROUNDING HELPERS (Decimal precision)
    # ============================================================
    @staticmethod
    def round_step(value: float, step: float) -> float:
        """Round value DOWN to nearest step (for qty)."""
        if step <= 0:
            return value
        d_value = Decimal(str(value))
        d_step = Decimal(str(step))
        rounded = (d_value // d_step) * d_step
        return float(rounded)

    @staticmethod
    def round_tick(value: float, tick: float) -> float:
        """Round value to nearest tick (for price)."""
        if tick <= 0:
            return value
        d_value = Decimal(str(value))
        d_tick = Decimal(str(tick))
        n = (d_value / d_tick).quantize(Decimal("1"))
        rounded = n * d_tick
        return float(rounded)
