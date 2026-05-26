"""
Bybit API katmani.
Tum API cagrilari retry mekanizmasiyla yapilir.
Hatalar Telegram'a bildirilir.
"""
import time
import os
from pybit.unified_trading import HTTP


class BybitClient:
    def __init__(self, config, telegram_bildirim=None):
        """
        config: config.json dict
        telegram_bildirim: hata bildirimi icin callable (str alir) - opsiyonel
        """
        self.config = config
        self.telegram = telegram_bildirim
        self.max_retry = config['api_max_retry']
        self.retry_bekle = config['api_retry_bekleme_saniye']
        self.testnet = config['testnet']

        # API keyler environment variables'tan alinir
        api_key = os.getenv('BYBIT_API_KEY')
        api_secret = os.getenv('BYBIT_API_SECRET')

        if not api_key or not api_secret:
            raise ValueError("BYBIT_API_KEY ve BYBIT_API_SECRET environment variable olarak set edilmeli!")

        self.client = HTTP(
            testnet=self.testnet,
            api_key=api_key,
            api_secret=api_secret,
        )

        # Sembol bilgileri cache (qty_step, min_qty vs)
        self._sembol_bilgi_cache = {}

    def _retry(self, fonksiyon_adi, fonksiyon, *args, **kwargs):
        """
        Verilen fonksiyonu retry mekanizmasiyla calistirir.
        Hata olursa retry yapar, son denemede de hata varsa exception firlatir.
        """
        son_hata = None

        for deneme in range(1, self.max_retry + 1):
            try:
                sonuc = fonksiyon(*args, **kwargs)
                # Bybit'in donus formati: {'retCode': 0, 'retMsg': 'OK', 'result': {...}}
                if isinstance(sonuc, dict):
                    ret_code = sonuc.get('retCode', -1)
                    if ret_code != 0:
                        hata_msg = sonuc.get('retMsg', 'Bilinmeyen hata')
                        raise Exception(f"Bybit API hatasi (kod {ret_code}): {hata_msg}")
                return sonuc
            except Exception as e:
                son_hata = e
                if deneme < self.max_retry:
                    time.sleep(self.retry_bekle)
                else:
                    # son denemede hata varsa Telegram'a bildir
                    hata_metni = f"{fonksiyon_adi} HATASI ({self.max_retry} denemede): {e}"
                    if self.telegram:
                        try:
                            self.telegram(hata_metni)
                        except Exception:
                            pass  # telegram hatasi gizlenmeli, asil hata kaybolmasin
                    raise Exception(hata_metni) from son_hata

    # =========================================================================
    # PIYASA VERISI
    # =========================================================================

    def mum_cek(self, sembol, timeframe, limit=200):
        """
        Sembol icin mum verisi ceker.
        timeframe: '5', '15', '30', '60' (dakika cinsinden string)
        limit: kac mum
        return: [{open, high, low, close, volume, timestamp}, ...] (en eski -> en yeni)
        """
        def _cagri():
            return self.client.get_kline(
                category='linear',
                symbol=sembol,
                interval=timeframe,
                limit=limit,
            )

        sonuc = self._retry(f'mum_cek({sembol})', _cagri)
        ham_mumlar = sonuc['result']['list']

        # Bybit en yeni mumu basa koyar, biz tersine ceviriyoruz (en eski -> en yeni)
        mumlar = []
        for m in reversed(ham_mumlar):
            mumlar.append({
                'timestamp': int(m[0]),
                'open': float(m[1]),
                'high': float(m[2]),
                'low': float(m[3]),
                'close': float(m[4]),
                'volume': float(m[5]),
            })

        return mumlar

    def anlik_fiyat(self, sembol):
        """
        Sembolun anlik fiyatini doner.
        return: float
        """
        def _cagri():
            return self.client.get_tickers(category='linear', symbol=sembol)

        sonuc = self._retry(f'anlik_fiyat({sembol})', _cagri)
        ticker = sonuc['result']['list'][0]
        return float(ticker['lastPrice'])

    def sembol_bilgi(self, sembol):
        """
        Sembol icin instrument bilgilerini doner (qty_step, min_qty, tick_size).
        Cache'lenir, ilk cagrida API'den ceker.
        return: dict
        """
        if sembol in self._sembol_bilgi_cache:
            return self._sembol_bilgi_cache[sembol]

        def _cagri():
            return self.client.get_instruments_info(category='linear', symbol=sembol)

        sonuc = self._retry(f'sembol_bilgi({sembol})', _cagri)
        info = sonuc['result']['list'][0]

        bilgi = {
            'qty_step': float(info['lotSizeFilter']['qtyStep']),
            'min_qty': float(info['lotSizeFilter']['minOrderQty']),
            'max_qty': float(info['lotSizeFilter']['maxOrderQty']),
            'tick_size': float(info['priceFilter']['tickSize']),
        }

        self._sembol_bilgi_cache[sembol] = bilgi
        return bilgi

    # =========================================================================
    # HESAP
    # =========================================================================

    def bakiye_al(self):
        """
        USDT bakiyesini doner (Unified Account).
        return: float (kullanilabilir USDT)
        """
        def _cagri():
            return self.client.get_wallet_balance(accountType='UNIFIED', coin='USDT')

        sonuc = self._retry('bakiye_al', _cagri)
        listesi = sonuc['result']['list']
        if not listesi:
            return 0.0
        coinler = listesi[0].get('coin', [])
        for c in coinler:
            if c['coin'] == 'USDT':
                # availableToWithdraw veya walletBalance
                # walletBalance toplam, availableToWithdraw kullanilabilir
                bakiye = c.get('walletBalance', '0')
                return float(bakiye) if bakiye else 0.0
        return 0.0

    def pozisyonlari_al(self, sembol=None):
        """
        Acik pozisyonlari doner.
        sembol: None = tum sembol, string = belirli sembol
        return: pozisyon dict listesi
        """
        def _cagri():
            params = {'category': 'linear', 'settleCoin': 'USDT'}
            if sembol:
                params['symbol'] = sembol
            return self.client.get_positions(**params)

        sonuc = self._retry(f'pozisyonlari_al({sembol})', _cagri)
        return sonuc['result']['list']

    def kaldirac_ayarla(self, sembol, kaldirac):
        """
        Sembol icin kaldirac ayarlar.
        Zaten ayarliysa sessizce geri doner (Telegram bildirimi gitmez).
        """
        try:
            sonuc = self.client.set_leverage(
                category='linear',
                symbol=sembol,
                buyLeverage=str(kaldirac),
                sellLeverage=str(kaldirac),
            )
            if isinstance(sonuc, dict):
                ret_code = sonuc.get('retCode', 0)
                # 0 = basarili, 110043 = leverage not modified (zaten ayarli)
                if ret_code in (0, 110043):
                    return
                msg = sonuc.get('retMsg', 'Bilinmeyen hata')
                raise Exception(f"Bybit API hatasi (kod {ret_code}): {msg}")
        except Exception as e:
            err = str(e).lower()
            if '110043' in err or 'not modified' in err or 'leverage not modified' in err:
                return
            raise

    def hedge_mode_ayarla(self, sembol):
        """
        Sembol icin hedge mode ayarlar (pozisyon modu = both side).
        Zaten ayarliysa sessizce geri doner (Telegram bildirimi gitmez).
        """
        try:
            sonuc = self.client.switch_position_mode(
                category='linear',
                symbol=sembol,
                mode=3,
            )
            if isinstance(sonuc, dict):
                ret_code = sonuc.get('retCode', 0)
                # 0 = basarili, 110025 = position mode not modified (zaten hedge)
                if ret_code in (0, 110025):
                    return
                msg = sonuc.get('retMsg', 'Bilinmeyen hata')
                raise Exception(f"Bybit API hatasi (kod {ret_code}): {msg}")
        except Exception as e:
            err = str(e).lower()
            if '110025' in err or 'not modified' in err or 'position mode is not modified' in err:
                return
            raise

    # =========================================================================
    # EMIR
    # =========================================================================

    def market_emir(self, sembol, yon, miktar, sl_fiyat=None, position_idx=None):
        """
        Market emir gonderir.
        sembol: 'SOLUSDT'
        yon: 'LONG' veya 'SHORT'
        miktar: pozitif float (yon yon parametresinden anlasilir)
        sl_fiyat: stop loss fiyati (opsiyonel)
        position_idx: hedge mode'da: 1=Long taraf, 2=Short taraf
        return: order_id
        """
        side = 'Buy' if yon == 'LONG' else 'Sell'

        # hedge mode -> position_idx zorunlu
        if position_idx is None:
            position_idx = 1 if yon == 'LONG' else 2

        def _cagri():
            params = {
                'category': 'linear',
                'symbol': sembol,
                'side': side,
                'orderType': 'Market',
                'qty': str(miktar),
                'positionIdx': position_idx,
            }
            if sl_fiyat is not None:
                params['stopLoss'] = str(sl_fiyat)
                params['slTriggerBy'] = 'LastPrice'

            return self.client.place_order(**params)

        sonuc = self._retry(f'market_emir({sembol}, {yon})', _cagri)
        return sonuc['result']['orderId']

    def market_kapat(self, sembol, yon, miktar, position_idx=None):
        """
        Market emirle pozisyon kapatir.
        yon: kapatilan pozisyonun yonu (LONG/SHORT)
        miktar: kapatilacak miktar
        """
        # Long pozisyon kapatmak icin Sell, Short kapatmak icin Buy
        side = 'Sell' if yon == 'LONG' else 'Buy'

        if position_idx is None:
            position_idx = 1 if yon == 'LONG' else 2

        def _cagri():
            return self.client.place_order(
                category='linear',
                symbol=sembol,
                side=side,
                orderType='Market',
                qty=str(miktar),
                positionIdx=position_idx,
                reduceOnly=True,
            )

        sonuc = self._retry(f'market_kapat({sembol}, {yon})', _cagri)
        return sonuc['result']['orderId']
