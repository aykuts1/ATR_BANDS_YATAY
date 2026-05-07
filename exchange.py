"""
Bybit Borsa İşlemleri
pybit kütüphanesi (Unified Trading API V5) kullanılır.
"""
import logging
import time
from decimal import Decimal, ROUND_DOWN
import pandas as pd
from pybit.unified_trading import HTTP

from config import BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_TESTNET, LEVERAGE

logger = logging.getLogger(__name__)


class BybitExchange:
    def __init__(self):
        self.session = HTTP(
            testnet=BYBIT_TESTNET,
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
        )
        # Symbol bilgilerini cache'le (qty step, min qty, fiyat hassasiyeti)
        self.symbol_info_cache: dict = {}
        self._load_symbol_info()

    # ============================================================
    # SYMBOL BİLGİSİ
    # ============================================================
    def _load_symbol_info(self):
        """Tüm linear (USDT perp) sembollerin filtrelerini yükler."""
        try:
            res = self.session.get_instruments_info(category="linear")
            if res.get("retCode") != 0:
                logger.error(f"Symbol info yüklenemedi: {res.get('retMsg')}")
                return
            for item in res["result"]["list"]:
                sym = item["symbol"]
                lot = item["lotSizeFilter"]
                price_filter = item["priceFilter"]
                self.symbol_info_cache[sym] = {
                    "qty_step": Decimal(lot["qtyStep"]),
                    "min_qty": Decimal(lot["minOrderQty"]),
                    "max_qty": Decimal(lot["maxOrderQty"]),
                    "tick_size": Decimal(price_filter["tickSize"]),
                }
            logger.info(f"Symbol info yüklendi: {len(self.symbol_info_cache)} adet")
        except Exception as e:
            logger.exception(f"Symbol info yükleme hatası: {e}")

    def round_qty(self, symbol: str, qty: float) -> float:
        """Miktarı borsanın izin verdiği step'e yuvarlar (aşağı)."""
        info = self.symbol_info_cache.get(symbol)
        if not info:
            return float(qty)
        step = info["qty_step"]
        q = Decimal(str(qty))
        rounded = (q // step) * step
        return float(rounded)

    def round_price(self, symbol: str, price: float) -> float:
        """Fiyatı tick size'a yuvarlar."""
        info = self.symbol_info_cache.get(symbol)
        if not info:
            return float(price)
        tick = info["tick_size"]
        p = Decimal(str(price))
        rounded = (p / tick).quantize(Decimal("1"), rounding=ROUND_DOWN) * tick
        return float(rounded)

    def get_min_qty(self, symbol: str) -> float:
        info = self.symbol_info_cache.get(symbol)
        return float(info["min_qty"]) if info else 0.0

    # ============================================================
    # MUM VERISI
    # ============================================================
    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
        """
        Mum verisi çeker.
        interval: '30', '120', '240' (dakika)
        Returns: DataFrame [open, high, low, close, volume]
        """
        try:
            res = self.session.get_kline(
                category="linear",
                symbol=symbol,
                interval=interval,
                limit=limit,
            )
            if res.get("retCode") != 0:
                logger.error(f"{symbol} {interval} kline hata: {res.get('retMsg')}")
                return pd.DataFrame()

            rows = res["result"]["list"]
            # Bybit en yenisi başta gelir, ters çeviriyoruz
            rows = list(reversed(rows))

            df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
            df["timestamp"] = pd.to_numeric(df["timestamp"])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col])
            df.set_index("timestamp", inplace=True)
            return df
        except Exception as e:
            logger.exception(f"{symbol} kline çekme hatası: {e}")
            return pd.DataFrame()

    # ============================================================
    # FIYAT
    # ============================================================
    def get_current_price(self, symbol: str) -> float:
        """Anlık son fiyatı döner."""
        try:
            res = self.session.get_tickers(category="linear", symbol=symbol)
            if res.get("retCode") != 0:
                return 0.0
            return float(res["result"]["list"][0]["lastPrice"])
        except Exception as e:
            logger.exception(f"{symbol} fiyat çekme hatası: {e}")
            return 0.0

    # ============================================================
    # BAKIYE
    # ============================================================
    def get_usdt_balance(self) -> float:
        """USDT bakiyesini (UNIFIED hesap) döner."""
        try:
            res = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
            if res.get("retCode") != 0:
                logger.error(f"Bakiye hata: {res.get('retMsg')}")
                return 0.0
            for item in res["result"]["list"]:
                for coin in item["coin"]:
                    if coin["coin"] == "USDT":
                        # walletBalance toplam, availableToWithdraw kullanılabilir
                        wb = coin.get("walletBalance", "0")
                        return float(wb) if wb else 0.0
            return 0.0
        except Exception as e:
            logger.exception(f"Bakiye çekme hatası: {e}")
            return 0.0

    # ============================================================
    # KALDIRAÇ
    # ============================================================
    def set_leverage(self, symbol: str, leverage: int = LEVERAGE) -> bool:
        """Sembol için kaldıraç ayarlar."""
        try:
            self.session.set_leverage(
                category="linear",
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
            return True
        except Exception as e:
            # 110043: leverage not modified — zaten ayarlı, hata değil
            err_str = str(e)
            if "110043" in err_str or "leverage not modified" in err_str.lower():
                return True
            logger.warning(f"{symbol} kaldıraç ayarı hata: {e}")
            return False

    # ============================================================
    # POZISYON AÇMA
    # ============================================================
    def open_position(self, symbol: str, side: str, qty: float, stop_loss_price: float) -> dict:
        """
        Market emir ile pozisyon açar ve borsa-tarafı stop loss kurar.
        side: 'Buy' (long) veya 'Sell' (short)
        Returns: {'success': bool, 'order_id': str, 'message': str}
        """
        try:
            # Kaldıracı ayarla (her seferinde, garanti için)
            self.set_leverage(symbol, LEVERAGE)

            qty_str = str(self.round_qty(symbol, qty))
            sl_str = str(self.round_price(symbol, stop_loss_price))

            res = self.session.place_order(
                category="linear",
                symbol=symbol,
                side=side,
                orderType="Market",
                qty=qty_str,
                stopLoss=sl_str,
                slTriggerBy="LastPrice",
                positionIdx=0,  # one-way mode
                reduceOnly=False,
            )
            if res.get("retCode") != 0:
                return {"success": False, "order_id": "", "message": res.get("retMsg", "Bilinmeyen hata")}

            order_id = res["result"].get("orderId", "")
            return {"success": True, "order_id": order_id, "message": "OK"}
        except Exception as e:
            logger.exception(f"{symbol} pozisyon açma hatası: {e}")
            return {"success": False, "order_id": "", "message": str(e)}

    # ============================================================
    # POZISYON KAPATMA
    # ============================================================
    def close_position(self, symbol: str, side: str, qty: float) -> dict:
        """
        Market emir ile pozisyonu kapatır (reduceOnly).
        side: kapatılacak pozisyonun YÖNÜ değil, KARŞIT yön.
              Long pozisyonu kapatmak için 'Sell', Short için 'Buy'.
        """
        try:
            qty_str = str(self.round_qty(symbol, qty))
            res = self.session.place_order(
                category="linear",
                symbol=symbol,
                side=side,
                orderType="Market",
                qty=qty_str,
                positionIdx=0,
                reduceOnly=True,
            )
            if res.get("retCode") != 0:
                return {"success": False, "message": res.get("retMsg", "Bilinmeyen hata")}
            return {"success": True, "message": "OK"}
        except Exception as e:
            logger.exception(f"{symbol} pozisyon kapatma hatası: {e}")
            return {"success": False, "message": str(e)}

    # ============================================================
    # STOP LOSS GÜNCELLEME (Breakeven)
    # ============================================================
    def update_stop_loss(self, symbol: str, new_sl: float) -> dict:
        """Açık pozisyonun SL emrini günceller (breakeven için)."""
        try:
            sl_str = str(self.round_price(symbol, new_sl))
            res = self.session.set_trading_stop(
                category="linear",
                symbol=symbol,
                stopLoss=sl_str,
                slTriggerBy="LastPrice",
                tpslMode="Full",
                positionIdx=0,
            )
            if res.get("retCode") != 0:
                # 34040: not modified, sorun değil
                msg = res.get("retMsg", "")
                if "not modified" in msg.lower():
                    return {"success": True, "message": "already set"}
                return {"success": False, "message": msg}
            return {"success": True, "message": "OK"}
        except Exception as e:
            logger.exception(f"{symbol} SL update hatası: {e}")
            return {"success": False, "message": str(e)}

    # ============================================================
    # AÇIK POZİSYONLAR
    # ============================================================
    def get_open_positions(self) -> list:
        """Tüm açık pozisyonları döner."""
        try:
            res = self.session.get_positions(category="linear", settleCoin="USDT")
            if res.get("retCode") != 0:
                return []
            positions = []
            for p in res["result"]["list"]:
                size = float(p.get("size", 0))
                if size > 0:
                    positions.append({
                        "symbol": p["symbol"],
                        "side": p["side"],  # 'Buy' or 'Sell'
                        "size": size,
                        "entry_price": float(p["avgPrice"]),
                        "unrealized_pnl": float(p.get("unrealisedPnl", 0)),
                    })
            return positions
        except Exception as e:
            logger.exception(f"Pozisyon listeleme hatası: {e}")
            return []

    def get_position(self, symbol: str) -> dict | None:
        """Belirli sembolün açık pozisyonunu döner, yoksa None."""
        for p in self.get_open_positions():
            if p["symbol"] == symbol:
                return p
        return None
