"""
KIRMIZI THREAD

LONG:
- Flag acilma: Kirmizi Ust Ic Tampon yukari kirilir
- Flag silme: Ayni cizgi asagi kirilirsa, ya da islem acilirsa
- Giris: Kirmizi Ust Dis Cizgi yukari kirilir
- Seviye 1 ENTRY: girisin oldugu seviye, cikis = Kirmizi Ust Ic Tampon asagi
- Seviye 2 BE: Kirmizi Ust Dis Tampon yukari kirilir, cikis = Kirmizi Ust Ic Tampon + komisyon asagi
- Seviye 3 ST1: Kirmizi Ust Seviye 1 yukari kirilir, chandelier baslar (en yuksek - 1 ATR)
    cikis: chandelier'e degme VEYA Kirmizi Ust Dis Cizgi + komisyon asagi kirilir
- Seviye 4 ST2: Kirmizi Ust Seviye 2 yukari kirilir, chandelier devam
    cikis: chandelier VEYA Kirmizi Ust Seviye 1 asagi kirilir

SHORT: tam ters simetri

Cikis isimleri:
- ENTRY EXIT, BE EXIT, CHANDELIER EXIT, ST1 EXIT, ST2 EXIT
"""
import threading
import time
from datetime import datetime
from utils.crossover import yukari_kirilma, asagi_kirilma


class KirmiziThread:
    THREAD_ADI = 'KIRMIZI'

    def __init__(self, config, market_data, pozisyon_yon, bybit_client, telegram):
        self.config = config
        self.market = market_data
        self.poz = pozisyon_yon
        self.bybit = bybit_client
        self.telegram = telegram

        self.coinler = config['coinler']
        self.fiyat_yenileme = config['fiyat_yenileme_saniye']
        self.komisyon = config['komisyon_yuzde'] / 100.0  # yuzde -> oran
        self.chandelier_atr_carpan = config['chandelier_atr']

        # Her coin icin state: flag durumu + acik islem id
        # {sembol: {'long_flag': bool, 'short_flag': bool, 'long_islem_id': int|None, 'short_islem_id': int|None}}
        self._state = {}
        for s in self.coinler:
            self._state[s] = {
                'long_flag': False,
                'short_flag': False,
                'long_islem_id': None,
                'short_islem_id': None,
            }

        self._durdur = threading.Event()
        self._thread = None

    def baslat(self):
        self._thread = threading.Thread(target=self._dongu, name=f'{self.THREAD_ADI}_Thread', daemon=True)
        self._thread.start()

    def durdur(self):
        self._durdur.set()

    def _dongu(self):
        """Ana dongu - her N saniyede her coini isler."""
        # market data hazir olana kadar bekle
        self.market.hazir_bekle()

        while not self._durdur.is_set():
            for sembol in self.coinler:
                if self._durdur.is_set():
                    break
                try:
                    self._coin_isle(sembol)
                except Exception as e:
                    self._hata(f"{sembol} islerken hata: {e}")

            self._durdur.wait(self.fiyat_yenileme)

    def _coin_isle(self, sembol):
        """Bir coin icin tam mantik (long ve short ayri ayri)."""
        veri = self.market.coin_verisi_al(sembol)
        if veri is None:
            return

        buffer = veri['fiyat_buffer']
        anlik = veri['anlik_fiyat']
        cizgiler = veri['cizgiler']
        atr = veri['atr']

        if anlik is None or cizgiler is None:
            return

        # LONG taraf
        self._long_isle(sembol, buffer, anlik, cizgiler, atr)

        # SHORT taraf
        self._short_isle(sembol, buffer, anlik, cizgiler, atr)

    # =========================================================================
    # LONG
    # =========================================================================

    def _long_isle(self, sembol, buffer, anlik, cizgiler, atr):
        state = self._state[sembol]
        islem_id = state['long_islem_id']

        if islem_id is not None:
            # acik islem var -> yonet
            self._long_islem_yonet(sembol, islem_id, buffer, anlik, cizgiler, atr)
        else:
            # acik islem yok -> flag mantigi
            if state['long_flag']:
                # flag var -> giris kontrolu
                if yukari_kirilma(buffer, anlik, cizgiler['kirmizi_ust_dis']):
                    self._long_islem_ac(sembol, anlik, cizgiler, atr)
                    state['long_flag'] = False
                elif asagi_kirilma(buffer, anlik, cizgiler['kirmizi_ust_ic_tampon']):
                    # flag silme: ayni cizgiyi asagi keserse
                    state['long_flag'] = False
            else:
                # flag yok -> acma kontrolu
                if yukari_kirilma(buffer, anlik, cizgiler['kirmizi_ust_ic_tampon']):
                    state['long_flag'] = True

    def _long_islem_ac(self, sembol, anlik, cizgiler, atr):
        if not self.poz.slot_var_mi():
            self._yetersiz_slot(sembol, 'LONG')
            return

        # bakiye yeterli mi?
        try:
            bakiye = self.bybit.bakiye_al()
        except Exception as e:
            self._hata(f"{sembol} LONG bakiye alinamadi: {e}")
            return

        stake = self.poz.stake_usdt
        if bakiye < stake:
            self._yetersiz_bakiye(sembol, 'LONG', stake, bakiye)
            return

        miktar = self.poz.miktar_hesapla(sembol, anlik)
        if miktar <= 0:
            self._hata(f"{sembol} LONG miktar 0 ciktigi icin acilamadi")
            return

        sl_fiyat = self.poz.sl_fiyati('LONG', anlik, sembol)

        # Kaldirac ve hedge mod ayarla (idempotent)
        try:
            self.bybit.kaldirac_ayarla(sembol, self.poz.kaldirac)
        except Exception as e:
            self._hata(f"{sembol} kaldirac ayarlanamadi: {e}")
            return

        # Market emir
        try:
            self.bybit.market_emir(sembol, 'LONG', miktar, sl_fiyat=sl_fiyat)
        except Exception as e:
            self._hata(f"{sembol} LONG market emir hatasi: {e}")
            return

        # Islem kayit
        islem_id = self.poz.islem_ekle(
            sembol=sembol, yon='LONG', thread_adi=self.THREAD_ADI,
            giris_fiyat=anlik, miktar=miktar, sl_fiyat=sl_fiyat, atr=atr,
            baslangic_seviye='ENTRY',
        )
        islem = self.poz.islem_al(islem_id)
        islem['acilis_zamani'] = datetime.now()
        islem['en_yuksek'] = anlik  # chandelier icin
        islem['chandelier_aktif'] = False

        self._state[sembol]['long_islem_id'] = islem_id

        # Telegram
        self.telegram.islem_acildi(islem, self.poz.acik_sayisi(), self.poz.max_pozisyon)

    def _long_islem_yonet(self, sembol, islem_id, buffer, anlik, cizgiler, atr):
        islem = self.poz.islem_al(islem_id)
        if islem is None:
            self._state[sembol]['long_islem_id'] = None
            return

        # Chandelier guncelle (her zaman, ama sadece ST1 ve ST2'de cikis icin kullanilir)
        if anlik > islem['en_yuksek']:
            islem['en_yuksek'] = anlik

        chandelier_seviye = islem['en_yuksek'] - self.chandelier_atr_carpan * atr

        seviye = islem['seviye']

        # ====== SEVIYE GECIS KONTROLLERI ======
        # Seviye geçişi: bir üst çizgiyi yukarı keser ve üstündedir
        if seviye == 'ENTRY':
            # BE'ye gecis: Kirmizi Ust Dis Tampon yukari kirilma
            if yukari_kirilma(buffer, anlik, cizgiler['kirmizi_ust_dis_tampon']):
                self.poz.seviye_guncelle(islem_id, 'BE')
                islem['chandelier_aktif'] = True  # BE'den itibaren chandelier takip + cikis aktif
                self.telegram.seviye_gecis(islem, 'ENTRY', 'BE', anlik, atr)
        elif seviye == 'BE':
            # ST1'e gecis: Kirmizi Ust Seviye 1 yukari kirilma
            if yukari_kirilma(buffer, anlik, cizgiler['kirmizi_ust_seviye1']):
                self.poz.seviye_guncelle(islem_id, 'ST1')
                # chandelier zaten BE'den beri aktif
                self.telegram.seviye_gecis(islem, 'BE', 'ST1', anlik, atr)
        elif seviye == 'ST1':
            # ST2'ye gecis: Kirmizi Ust Seviye 2 yukari kirilma
            if yukari_kirilma(buffer, anlik, cizgiler['kirmizi_ust_seviye2']):
                self.poz.seviye_guncelle(islem_id, 'ST2')
                self.telegram.seviye_gecis(islem, 'ST1', 'ST2', anlik, atr)

        # ====== CIKIS KONTROLLERI ======
        # state degismis olabilir, yeniden al
        seviye = islem['seviye']

        cikis_yapildi = False
        cikis_tipi = None

        if seviye == 'ENTRY':
            # Kirmizi Ust Ic Tampon asagi kirilma
            if asagi_kirilma(buffer, anlik, cizgiler['kirmizi_ust_ic_tampon']):
                cikis_yapildi = True
                cikis_tipi = 'ENTRY EXIT'
        elif seviye == 'BE':
            # Chandelier'e cikis (BE'den itibaren aktif)
            if anlik <= chandelier_seviye:
                cikis_yapildi = True
                cikis_tipi = 'CHANDELIER EXIT'
            else:
                # VEYA Kirmizi Ust Ic Tampon + komisyon asagi kirilma
                cikis_cizgi = cizgiler['kirmizi_ust_ic_tampon'] + (cizgiler['kirmizi_ust_ic_tampon'] * self.komisyon)
                if asagi_kirilma(buffer, anlik, cikis_cizgi):
                    cikis_yapildi = True
                    cikis_tipi = 'BE EXIT'
        elif seviye == 'ST1':
            # Chandelier'e cikis (anlik fiyat chandelier'a degerse veya altina inerse)
            if anlik <= chandelier_seviye:
                cikis_yapildi = True
                cikis_tipi = 'CHANDELIER EXIT'
            else:
                # VEYA Kirmizi Ust Dis Cizgi + komisyon asagi kirilma
                cikis_cizgi = cizgiler['kirmizi_ust_dis'] + (cizgiler['kirmizi_ust_dis'] * self.komisyon)
                if asagi_kirilma(buffer, anlik, cikis_cizgi):
                    cikis_yapildi = True
                    cikis_tipi = 'ST1 EXIT'
        elif seviye == 'ST2':
            if anlik <= chandelier_seviye:
                cikis_yapildi = True
                cikis_tipi = 'CHANDELIER EXIT'
            else:
                # VEYA Kirmizi Ust Seviye 1 asagi kirilma
                if asagi_kirilma(buffer, anlik, cizgiler['kirmizi_ust_seviye1']):
                    cikis_yapildi = True
                    cikis_tipi = 'ST2 EXIT'

        if cikis_yapildi:
            self._long_islem_kapat(sembol, islem_id, anlik, cikis_tipi)

    def _long_islem_kapat(self, sembol, islem_id, anlik, cikis_tipi):
        islem = self.poz.islem_al(islem_id)
        if islem is None:
            self._state[sembol]['long_islem_id'] = None
            return

        try:
            self.bybit.market_kapat(sembol, 'LONG', islem['miktar'])
        except Exception as e:
            self._hata(f"{sembol} LONG kapatma hatasi: {e}")
            return

        # PNL hesabi
        giris = islem['giris_fiyat']
        cikis = anlik
        miktar = islem['miktar']
        brut_pnl = (cikis - giris) * miktar  # LONG: cikis - giris
        komisyon_tutar = (giris * miktar + cikis * miktar) * self.komisyon
        net_pnl = brut_pnl - komisyon_tutar

        islem['cikis_fiyat'] = cikis
        islem['cikis_tipi'] = cikis_tipi
        islem['brut_pnl'] = brut_pnl
        islem['komisyon'] = komisyon_tutar
        islem['net_pnl'] = net_pnl
        islem['kapanis_zamani'] = datetime.now()

        # Telegram
        self.telegram.islem_kapandi(islem, self.poz.acik_sayisi() - 1, self.poz.max_pozisyon)

        # Rapor icin sakla
        self.telegram.kapanan_islem_kaydet(dict(islem))

        # Sil
        self.poz.islem_sil(islem_id)
        self._state[sembol]['long_islem_id'] = None

    # =========================================================================
    # SHORT
    # =========================================================================

    def _short_isle(self, sembol, buffer, anlik, cizgiler, atr):
        state = self._state[sembol]
        islem_id = state['short_islem_id']

        if islem_id is not None:
            self._short_islem_yonet(sembol, islem_id, buffer, anlik, cizgiler, atr)
        else:
            if state['short_flag']:
                if asagi_kirilma(buffer, anlik, cizgiler['kirmizi_alt_dis']):
                    self._short_islem_ac(sembol, anlik, cizgiler, atr)
                    state['short_flag'] = False
                elif yukari_kirilma(buffer, anlik, cizgiler['kirmizi_alt_ic_tampon']):
                    state['short_flag'] = False
            else:
                if asagi_kirilma(buffer, anlik, cizgiler['kirmizi_alt_ic_tampon']):
                    state['short_flag'] = True

    def _short_islem_ac(self, sembol, anlik, cizgiler, atr):
        if not self.poz.slot_var_mi():
            self._yetersiz_slot(sembol, 'SHORT')
            return

        try:
            bakiye = self.bybit.bakiye_al()
        except Exception as e:
            self._hata(f"{sembol} SHORT bakiye alinamadi: {e}")
            return

        stake = self.poz.stake_usdt
        if bakiye < stake:
            self._yetersiz_bakiye(sembol, 'SHORT', stake, bakiye)
            return

        miktar = self.poz.miktar_hesapla(sembol, anlik)
        if miktar <= 0:
            self._hata(f"{sembol} SHORT miktar 0 ciktigi icin acilamadi")
            return

        sl_fiyat = self.poz.sl_fiyati('SHORT', anlik, sembol)

        try:
            self.bybit.kaldirac_ayarla(sembol, self.poz.kaldirac)
        except Exception as e:
            self._hata(f"{sembol} kaldirac ayarlanamadi: {e}")
            return

        try:
            self.bybit.market_emir(sembol, 'SHORT', miktar, sl_fiyat=sl_fiyat)
        except Exception as e:
            self._hata(f"{sembol} SHORT market emir hatasi: {e}")
            return

        islem_id = self.poz.islem_ekle(
            sembol=sembol, yon='SHORT', thread_adi=self.THREAD_ADI,
            giris_fiyat=anlik, miktar=miktar, sl_fiyat=sl_fiyat, atr=atr,
            baslangic_seviye='ENTRY',
        )
        islem = self.poz.islem_al(islem_id)
        islem['acilis_zamani'] = datetime.now()
        islem['en_dusuk'] = anlik
        islem['chandelier_aktif'] = False

        self._state[sembol]['short_islem_id'] = islem_id
        self.telegram.islem_acildi(islem, self.poz.acik_sayisi(), self.poz.max_pozisyon)

    def _short_islem_yonet(self, sembol, islem_id, buffer, anlik, cizgiler, atr):
        islem = self.poz.islem_al(islem_id)
        if islem is None:
            self._state[sembol]['short_islem_id'] = None
            return

        if anlik < islem['en_dusuk']:
            islem['en_dusuk'] = anlik

        chandelier_seviye = islem['en_dusuk'] + self.chandelier_atr_carpan * atr

        seviye = islem['seviye']

        # Seviye gecisleri
        if seviye == 'ENTRY':
            if asagi_kirilma(buffer, anlik, cizgiler['kirmizi_alt_dis_tampon']):
                self.poz.seviye_guncelle(islem_id, 'BE')
                islem['chandelier_aktif'] = True  # BE'den itibaren chandelier aktif
                self.telegram.seviye_gecis(islem, 'ENTRY', 'BE', anlik, atr)
        elif seviye == 'BE':
            if asagi_kirilma(buffer, anlik, cizgiler['kirmizi_alt_seviye1']):
                self.poz.seviye_guncelle(islem_id, 'ST1')
                # chandelier zaten BE'den beri aktif
                self.telegram.seviye_gecis(islem, 'BE', 'ST1', anlik, atr)
        elif seviye == 'ST1':
            if asagi_kirilma(buffer, anlik, cizgiler['kirmizi_alt_seviye2']):
                self.poz.seviye_guncelle(islem_id, 'ST2')
                self.telegram.seviye_gecis(islem, 'ST1', 'ST2', anlik, atr)

        seviye = islem['seviye']

        cikis_yapildi = False
        cikis_tipi = None

        if seviye == 'ENTRY':
            if yukari_kirilma(buffer, anlik, cizgiler['kirmizi_alt_ic_tampon']):
                cikis_yapildi = True
                cikis_tipi = 'ENTRY EXIT'
        elif seviye == 'BE':
            # Chandelier'e cikis (BE'den itibaren aktif)
            if anlik >= chandelier_seviye:
                cikis_yapildi = True
                cikis_tipi = 'CHANDELIER EXIT'
            else:
                # VEYA Kirmizi Alt Ic Tampon - komisyon yukari kirilma
                cikis_cizgi = cizgiler['kirmizi_alt_ic_tampon'] - (cizgiler['kirmizi_alt_ic_tampon'] * self.komisyon)
                if yukari_kirilma(buffer, anlik, cikis_cizgi):
                    cikis_yapildi = True
                    cikis_tipi = 'BE EXIT'
        elif seviye == 'ST1':
            if anlik >= chandelier_seviye:
                cikis_yapildi = True
                cikis_tipi = 'CHANDELIER EXIT'
            else:
                cikis_cizgi = cizgiler['kirmizi_alt_dis'] - (cizgiler['kirmizi_alt_dis'] * self.komisyon)
                if yukari_kirilma(buffer, anlik, cikis_cizgi):
                    cikis_yapildi = True
                    cikis_tipi = 'ST1 EXIT'
        elif seviye == 'ST2':
            if anlik >= chandelier_seviye:
                cikis_yapildi = True
                cikis_tipi = 'CHANDELIER EXIT'
            else:
                if yukari_kirilma(buffer, anlik, cizgiler['kirmizi_alt_seviye1']):
                    cikis_yapildi = True
                    cikis_tipi = 'ST2 EXIT'

        if cikis_yapildi:
            self._short_islem_kapat(sembol, islem_id, anlik, cikis_tipi)

    def _short_islem_kapat(self, sembol, islem_id, anlik, cikis_tipi):
        islem = self.poz.islem_al(islem_id)
        if islem is None:
            self._state[sembol]['short_islem_id'] = None
            return

        try:
            self.bybit.market_kapat(sembol, 'SHORT', islem['miktar'])
        except Exception as e:
            self._hata(f"{sembol} SHORT kapatma hatasi: {e}")
            return

        giris = islem['giris_fiyat']
        cikis = anlik
        miktar = islem['miktar']
        brut_pnl = (giris - cikis) * miktar  # SHORT
        komisyon_tutar = (giris * miktar + cikis * miktar) * self.komisyon
        net_pnl = brut_pnl - komisyon_tutar

        islem['cikis_fiyat'] = cikis
        islem['cikis_tipi'] = cikis_tipi
        islem['brut_pnl'] = brut_pnl
        islem['komisyon'] = komisyon_tutar
        islem['net_pnl'] = net_pnl
        islem['kapanis_zamani'] = datetime.now()

        self.telegram.islem_kapandi(islem, self.poz.acik_sayisi() - 1, self.poz.max_pozisyon)
        self.telegram.kapanan_islem_kaydet(dict(islem))

        self.poz.islem_sil(islem_id)
        self._state[sembol]['short_islem_id'] = None

    # =========================================================================
    # YARDIMCI
    # =========================================================================

    def _hata(self, mesaj):
        try:
            self.telegram.hata(self.THREAD_ADI, mesaj)
        except Exception:
            pass

    def _yetersiz_slot(self, sembol, yon):
        # bilgi amaçlı, sürekli spam yapmasın diye sadece sessizce geç
        pass

    def _yetersiz_bakiye(self, sembol, yon, gerekli, mevcut):
        try:
            self.telegram.yetersiz_bakiye(self.THREAD_ADI, sembol, yon, gerekli, mevcut,
                                          self.poz.acik_sayisi(), self.poz.max_pozisyon)
        except Exception:
            pass
