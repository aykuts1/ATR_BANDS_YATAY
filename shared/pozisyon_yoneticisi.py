"""
Pozisyon yoneticisi.

Gorevleri:
- Acik pozisyon sayisini takip eder (slot)
- Yeni islem icin slot uygunlugu kontrol eder
- Stake hesabini yapar (baslangic bakiyesinin %5'i)
- Miktar yuvarlama (qty_step'e gore)
- Toplam acik pozisyon listesi (rapor icin)

Thread-safe.
"""
import threading
import math


class PozisyonYoneticisi:
    def __init__(self, config, bybit_client):
        """
        config: config.json
        bybit_client: BybitClient
        """
        self.config = config
        self.bybit = bybit_client

        self.max_pozisyon = config['max_pozisyon']
        self.stake_yuzde = config['stake_yuzde']
        self.kaldirac = config['kaldirac']
        self.sl_yuzde = config['sl_yuzde']

        # Baslangic bakiyesi (bot baslayinca bir kere alinir, stake bunun uzerinden hesaplanir)
        self.baslangic_bakiyesi = None
        self.stake_usdt = None

        self._lock = threading.RLock()

        # Acik islemler: {(sembol, yon, thread_adi): islem_dict}
        # ayni sembol+yon+thread'de ayni anda 1 islem oldugu icin bu kombinasyon unique
        # Ama sen "ayni thread ayni coinde 2 pozisyon acabilir" demistin -> o yuzden unique id de ekleyelim
        self._acik_islemler = {}  # islem_id -> islem dict
        self._sonraki_id = 1

    def baslat(self, baslangic_bakiyesi):
        """Bot acilirken cagrilir, stake'i hesaplar."""
        with self._lock:
            self.baslangic_bakiyesi = baslangic_bakiyesi
            self.stake_usdt = baslangic_bakiyesi * (self.stake_yuzde / 100.0)

    def islem_hacmi(self):
        """Bir islemin toplam hacmi (stake * kaldirac)."""
        return self.stake_usdt * self.kaldirac

    def acik_sayisi(self):
        """Toplam acik pozisyon sayisi."""
        with self._lock:
            return len(self._acik_islemler)

    def slot_var_mi(self):
        """Yeni pozisyon icin slot uygun mu?"""
        return self.acik_sayisi() < self.max_pozisyon

    def miktar_hesapla(self, sembol, fiyat):
        """
        Stake'e gore miktar hesaplar.
        Sembol bilgilerine gore qty_step'e yuvarlar.
        """
        hacim = self.islem_hacmi()
        ham_miktar = hacim / fiyat

        # qty_step'e yuvarla (asagi yuvarlama)
        bilgi = self.bybit.sembol_bilgi(sembol)
        qty_step = bilgi['qty_step']
        min_qty = bilgi['min_qty']

        # qty_step'in ondalik basamak sayisini bul
        if qty_step >= 1:
            yuvarlanmis = math.floor(ham_miktar / qty_step) * qty_step
            yuvarlanmis = round(yuvarlanmis, 0)
        else:
            ondalik = max(0, -int(math.floor(math.log10(qty_step))))
            yuvarlanmis = math.floor(ham_miktar / qty_step) * qty_step
            yuvarlanmis = round(yuvarlanmis, ondalik + 2)

        if yuvarlanmis < min_qty:
            return 0.0

        return yuvarlanmis

    def fiyat_yuvarla(self, sembol, fiyat):
        """Fiyati tick_size'a yuvarlar (SL fiyati icin)."""
        bilgi = self.bybit.sembol_bilgi(sembol)
        tick_size = bilgi['tick_size']

        if tick_size >= 1:
            return round(fiyat / tick_size) * tick_size

        ondalik = max(0, -int(math.floor(math.log10(tick_size))))
        yuvarlanmis = round(fiyat / tick_size) * tick_size
        return round(yuvarlanmis, ondalik + 2)

    def sl_fiyati(self, yon, giris_fiyat, sembol):
        """
        Emniyet kemeri SL fiyatini hesaplar.
        Long icin giris - %sl, Short icin giris + %sl
        """
        if yon == 'LONG':
            ham = giris_fiyat * (1 - self.sl_yuzde / 100.0)
        else:
            ham = giris_fiyat * (1 + self.sl_yuzde / 100.0)
        return self.fiyat_yuvarla(sembol, ham)

    # =========================================================================
    # ISLEM TAKIBI
    # =========================================================================

    def islem_ekle(self, sembol, yon, thread_adi, giris_fiyat, miktar, sl_fiyat, atr, baslangic_seviye='ENTRY'):
        """
        Acik islem listesine ekler. Unique id doner.
        islem dict'i thread tarafindan kullanilir (seviye, chandelier vs takip icin).
        """
        with self._lock:
            islem_id = self._sonraki_id
            self._sonraki_id += 1

            islem = {
                'id': islem_id,
                'sembol': sembol,
                'yon': yon,  # LONG / SHORT
                'thread': thread_adi,  # KIRMIZI/MAVI/MOR/SARI
                'giris_fiyat': giris_fiyat,
                'miktar': miktar,
                'sl_fiyat': sl_fiyat,
                'atr': atr,  # giris zamani atr (rapor icin)
                'seviye': baslangic_seviye,
                'acilis_zamani': None,  # thread set eder
                # ek alanlar thread tarafindan eklenir (chandelier_seviye vs)
            }
            self._acik_islemler[islem_id] = islem
            return islem_id

    def islem_sil(self, islem_id):
        """Islem kapandiginda silinir."""
        with self._lock:
            return self._acik_islemler.pop(islem_id, None)

    def islem_al(self, islem_id):
        """Islem dict'ini doner (kopya degil, referans)."""
        with self._lock:
            return self._acik_islemler.get(islem_id)

    def tum_acik_islemler(self):
        """Tum acik islemleri liste olarak doner (rapor icin)."""
        with self._lock:
            return list(self._acik_islemler.values())

    def seviye_guncelle(self, islem_id, yeni_seviye):
        """Bir islemin seviyesini gunceller."""
        with self._lock:
            islem = self._acik_islemler.get(islem_id)
            if islem:
                eski = islem['seviye']
                islem['seviye'] = yeni_seviye
                return eski
        return None
