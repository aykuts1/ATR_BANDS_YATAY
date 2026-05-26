"""
Merkezi veri yoneticisi.

Gorevleri:
- Her N saniyede tum coinlerin anlik fiyatini cekip buffer'a yazar
- Her N dakikada tum coinlerin mum verisini cekip EMA + ATR hesaplar
- Tum bant cizgilerini hesaplar ve shared bellege yazar
- 4 thread bu bellegi thread-safe sekilde okur

Thread-safe: tum yazma/okuma lock altinda yapilir.
"""
import threading
import time
import traceback
from utils.indicators import hesapla_ema, hesapla_atr_ortalama
from utils.bands import hesapla_tum_cizgiler


class MarketData:
    def __init__(self, config, bybit_client, telegram_bildirim=None):
        """
        config: config.json
        bybit_client: BybitClient instance
        telegram_bildirim: hata bildirimi icin callable
        """
        self.config = config
        self.bybit = bybit_client
        self.telegram = telegram_bildirim

        self.coinler = config['coinler']
        self.fiyat_yenileme = config['fiyat_yenileme_saniye']
        self.mum_yenileme = config['mum_yenileme_saniye']
        self.timeframe = config['timeframe']
        self.mum_sayisi = config['mum_sayisi']
        self.ema_periyot = config['ema_periyot']
        self.atr_periyot_ortalama = config['atr_periyot']
        self.buffer_boyut = config['fiyat_buffer_boyut']

        # Shared bellek: her coin icin ayri veri
        # yapi: {sembol: {fiyat_buffer: [...], ema: float, atr: float, cizgiler: {...}, son_guncelleme_fiyat: timestamp, son_guncelleme_mum: timestamp}}
        self._veri = {}
        self._lock = threading.RLock()

        # ilk veriyi cekene kadar threadler beklesin
        self._hazir = threading.Event()

        # durdurma sinyali
        self._durdur = threading.Event()

        # her coin icin baslangic dict
        for sembol in self.coinler:
            self._veri[sembol] = {
                'fiyat_buffer': [],
                'ema': None,
                'atr': None,
                'cizgiler': None,
                'son_fiyat_zamani': 0,
                'son_mum_zamani': 0,
            }

    def hazir_mi(self):
        """Threadler bu fonksiyonu cagirir, ilk veri gelene kadar bekler."""
        return self._hazir.is_set()

    def hazir_bekle(self, timeout=None):
        """Ilk veri hazir olana kadar bekler."""
        return self._hazir.wait(timeout)

    def coin_verisi_al(self, sembol):
        """
        Bir sembolun tum verisini doner (thread-safe).
        return: dict (fiyat_buffer kopyalanir, cizgiler aynen)
        """
        with self._lock:
            v = self._veri.get(sembol)
            if v is None or v['ema'] is None or v['atr'] is None:
                return None

            return {
                'fiyat_buffer': list(v['fiyat_buffer']),  # kopya
                'anlik_fiyat': v['fiyat_buffer'][-1] if v['fiyat_buffer'] else None,
                'ema': v['ema'],
                'atr': v['atr'],
                'cizgiler': dict(v['cizgiler']) if v['cizgiler'] else None,
            }

    # =========================================================================
    # ICERIK GUNCELLEME
    # =========================================================================

    def _mum_guncelle(self, sembol):
        """Bir sembol icin mum verisi ceker, EMA + ATR hesaplar, cizgileri gunceller."""
        try:
            mumlar = self.bybit.mum_cek(sembol, self.timeframe, self.mum_sayisi)

            kapanis_fiyatlari = [m['close'] for m in mumlar]
            ema = hesapla_ema(kapanis_fiyatlari, self.ema_periyot)
            atr = hesapla_atr_ortalama(mumlar, atr_periyot=14, ortalama_son=self.atr_periyot_ortalama)

            if ema is None or atr is None:
                return False

            cizgiler = hesapla_tum_cizgiler(ema, atr, self.config)

            with self._lock:
                self._veri[sembol]['ema'] = ema
                self._veri[sembol]['atr'] = atr
                self._veri[sembol]['cizgiler'] = cizgiler
                self._veri[sembol]['son_mum_zamani'] = time.time()

            return True
        except Exception as e:
            hata = f"MARKET DATA mum_guncelle({sembol}) hatasi: {e}"
            if self.telegram:
                try:
                    self.telegram(hata)
                except Exception:
                    pass
            return False

    def _fiyat_guncelle(self, sembol):
        """Bir sembol icin anlik fiyat ceker, buffer'a yazar."""
        try:
            fiyat = self.bybit.anlik_fiyat(sembol)

            with self._lock:
                buffer = self._veri[sembol]['fiyat_buffer']
                buffer.append(fiyat)
                if len(buffer) > self.buffer_boyut:
                    buffer.pop(0)  # en eskisini sil
                self._veri[sembol]['son_fiyat_zamani'] = time.time()

            return True
        except Exception as e:
            hata = f"MARKET DATA fiyat_guncelle({sembol}) hatasi: {e}"
            if self.telegram:
                try:
                    self.telegram(hata)
                except Exception:
                    pass
            return False

    # =========================================================================
    # ARKAPLAN DONGU
    # =========================================================================

    def _mum_dongu(self):
        """Her N saniyede tum coinlerin mum verisini gunceller."""
        # ilk seferde hemen calistir
        while not self._durdur.is_set():
            for sembol in self.coinler:
                if self._durdur.is_set():
                    break
                self._mum_guncelle(sembol)

            # ilk tur tamamlandi
            # mum verileri gelmeye basladi, ama hazir sinyali fiyat dongusunden gelir

            self._durdur.wait(self.mum_yenileme)

    def _fiyat_dongu(self):
        """Her N saniyede tum coinlerin anlik fiyatini gunceller."""
        ilk_tur = True
        while not self._durdur.is_set():
            for sembol in self.coinler:
                if self._durdur.is_set():
                    break
                self._fiyat_guncelle(sembol)

            # ilk tur bittiginde + mum verileri varsa -> hazir
            if ilk_tur:
                # Tum coinlerin mum verisi gelmis mi kontrol et
                with self._lock:
                    hepsi_hazir = all(
                        self._veri[s]['ema'] is not None and len(self._veri[s]['fiyat_buffer']) > 0
                        for s in self.coinler
                    )
                if hepsi_hazir:
                    self._hazir.set()
                    ilk_tur = False

            self._durdur.wait(self.fiyat_yenileme)

    def baslat(self):
        """Mum ve fiyat dongulerini ayri threadlerde baslat."""
        self._mum_thread = threading.Thread(target=self._mum_dongu, name='MumDongu', daemon=True)
        self._fiyat_thread = threading.Thread(target=self._fiyat_dongu, name='FiyatDongu', daemon=True)
        self._mum_thread.start()
        self._fiyat_thread.start()

    def durdur(self):
        """Tum dongulerini durdur."""
        self._durdur.set()
