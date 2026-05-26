"""
MAVI THREAD - MOMENTUM (banttan ileri dogru)

SHORT (Mavi Ust banti uzerinde, fiyat asagi yonlu hareket eder):
- Flag acilma: Mavi Ust Dis Tampon asagi yonlu kirilir
- Flag silme: Ayni cizgi yukari yonlu kirilir, ya da islem acilir
- Giris: Mavi Ust Dis Cizgi asagi yonlu kirilir

Seviyeler:
  Seviye 1 ENTRY: Acilis seviyesi
    Cikis: Mavi Ust Dis Tampon yukari yonlu kirilir -> ENTRY EXIT
  Seviye 2 BE: Mavi Ust Ic Tampon asagi yonlu kirilir
    Cikis: Mavi Ust Dis Tampon + komisyon yukari yonlu kirilir -> BE EXIT
  Seviye 3 ST1: Mavi Ust Seviye 1 asagi yonlu kirilir (chandelier baslar: en dusuk + 1 ATR)
    Cikis A: Chandelier'e degerse -> CHANDELIER EXIT
    Cikis B: Mavi Ust Dis - komisyon yukari yonlu kirilir -> ST1 EXIT
  Seviye 4 ST2: Mavi Ust Seviye 2 asagi yonlu kirilir (chandelier devam)
    Cikis A: Chandelier'e degerse -> CHANDELIER EXIT
    Cikis B: Mavi Ust Seviye 1 yukari yonlu kirilir -> ST2 EXIT

WINRATE EXIT (her seviyede aktif):
  Short: Fiyat Mavi ALT Seviye 1'e ulasir veya gecer -> WINRATE EXIT
    (Fiyat Mavi Ust'ten Mavi Alt'a kadar inerse buyuk kar al)

LONG: tam ters simetri (Mavi Alt bantta, WINRATE = Mavi Ust Seviye 1)

Cikis isimleri: ENTRY EXIT, BE EXIT, CHANDELIER EXIT, ST1 EXIT, ST2 EXIT, WINRATE EXIT
"""

import threading
from datetime import datetime
from utils.crossover import yukari_kirilma, asagi_kirilma


class MaviThread:
    THREAD_ADI = 'MAVI'

    def __init__(self, config, market_data, pozisyon_yon, bybit_client, telegram):
        self.config = config
        self.market = market_data
        self.poz = pozisyon_yon
        self.bybit = bybit_client
        self.telegram = telegram

        self.coinler = config['coinler']
        self.fiyat_yenileme = config['fiyat_yenileme_saniye']
        self.komisyon = config['komisyon_yuzde'] / 100.0
        self.chandelier_atr_carpan = config['chandelier_atr']

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
        self.market.hazir_bekle()
        while not self._durdur.is_set():
            for sembol in self.coinler:
                if self._durdur.is_set():
                    break
                try:
                    self._coin_isle(sembol)
                except Exception as e:
                    self._hata(f"{sembol}: {e}")
            self._durdur.wait(self.fiyat_yenileme)

    def _coin_isle(self, sembol):
        veri = self.market.coin_verisi_al(sembol)
        if veri is None:
            return
        buffer = veri['fiyat_buffer']
        anlik = veri['anlik_fiyat']
        cizgiler = veri['cizgiler']
        atr = veri['atr']
        if anlik is None or cizgiler is None:
            return

        self._short_isle(sembol, buffer, anlik, cizgiler, atr)
        self._long_isle(sembol, buffer, anlik, cizgiler, atr)

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
                if asagi_kirilma(buffer, anlik, cizgiler['mavi_ust_dis']):
                    self._short_islem_ac(sembol, anlik, cizgiler, atr)
                    state['short_flag'] = False
                elif yukari_kirilma(buffer, anlik, cizgiler['mavi_ust_dis_tampon']):
                    state['short_flag'] = False
            else:
                if asagi_kirilma(buffer, anlik, cizgiler['mavi_ust_dis_tampon']):
                    state['short_flag'] = True

    def _short_islem_ac(self, sembol, anlik, cizgiler, atr):
        if not self.poz.slot_var_mi():
            return

        try:
            bakiye = self.bybit.bakiye_al()
        except Exception as e:
            self._hata(f"{sembol} SHORT bakiye: {e}")
            return

        stake = self.poz.stake_usdt
        if bakiye < stake:
            self.telegram.yetersiz_bakiye(self.THREAD_ADI, sembol, 'SHORT', stake, bakiye,
                                          self.poz.acik_sayisi(), self.poz.max_pozisyon)
            return

        miktar = self.poz.miktar_hesapla(sembol, anlik)
        if miktar <= 0:
            return

        sl_fiyat = self.poz.sl_fiyati('SHORT', anlik, sembol)

        try:
            self.bybit.kaldirac_ayarla(sembol, self.poz.kaldirac)
            self.bybit.market_emir(sembol, 'SHORT', miktar, sl_fiyat=sl_fiyat)
        except Exception as e:
            self._hata(f"{sembol} SHORT emir: {e}")
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

        chandelier = islem['en_dusuk'] + self.chandelier_atr_carpan * atr

        seviye = islem['seviye']

        # Seviye gecisleri
        if seviye == 'ENTRY':
            if asagi_kirilma(buffer, anlik, cizgiler['mavi_ust_ic_tampon']):
                self.poz.seviye_guncelle(islem_id, 'BE')
                islem['chandelier_aktif'] = True  # BE'den itibaren chandelier aktif
                self.telegram.seviye_gecis(islem, 'ENTRY', 'BE', anlik, atr)
        elif seviye == 'BE':
            if asagi_kirilma(buffer, anlik, cizgiler['mavi_ust_seviye1']):
                self.poz.seviye_guncelle(islem_id, 'ST1')
                # chandelier zaten BE'den beri aktif
                self.telegram.seviye_gecis(islem, 'BE', 'ST1', anlik, atr)
        elif seviye == 'ST1':
            if asagi_kirilma(buffer, anlik, cizgiler['mavi_ust_seviye2']):
                self.poz.seviye_guncelle(islem_id, 'ST2')
                self.telegram.seviye_gecis(islem, 'ST1', 'ST2', anlik, atr)

        seviye = islem['seviye']
        cikis_yapildi = False
        cikis_tipi = None

        # WINRATE EXIT: Fiyat Mavi ALT Seviye 1'e ulasir veya gecerse (her seviyede aktif)
        if anlik <= cizgiler['mavi_alt_seviye1']:
            cikis_yapildi = True
            cikis_tipi = 'WINRATE EXIT'

        # Diger cikislar (WINRATE basta tetiklenmediyse)
        if not cikis_yapildi:
            if seviye == 'ENTRY':
                if yukari_kirilma(buffer, anlik, cizgiler['mavi_ust_dis_tampon']):
                    cikis_yapildi = True
                    cikis_tipi = 'ENTRY EXIT'
            elif seviye == 'BE':
                # Chandelier'e cikis (BE'den itibaren aktif)
                if anlik >= chandelier:
                    cikis_yapildi = True
                    cikis_tipi = 'CHANDELIER EXIT'
                else:
                    cikis_cizgi = cizgiler['mavi_ust_dis_tampon'] + (cizgiler['mavi_ust_dis_tampon'] * self.komisyon)
                    if yukari_kirilma(buffer, anlik, cikis_cizgi):
                        cikis_yapildi = True
                        cikis_tipi = 'BE EXIT'
            elif seviye == 'ST1':
                if anlik >= chandelier:
                    cikis_yapildi = True
                    cikis_tipi = 'CHANDELIER EXIT'
                else:
                    cikis_cizgi = cizgiler['mavi_ust_dis'] - (cizgiler['mavi_ust_dis'] * self.komisyon)
                    if yukari_kirilma(buffer, anlik, cikis_cizgi):
                        cikis_yapildi = True
                        cikis_tipi = 'ST1 EXIT'
            elif seviye == 'ST2':
                if anlik >= chandelier:
                    cikis_yapildi = True
                    cikis_tipi = 'CHANDELIER EXIT'
                else:
                    if yukari_kirilma(buffer, anlik, cizgiler['mavi_ust_seviye1']):
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
            self._hata(f"{sembol} SHORT kapat: {e}")
            return

        giris = islem['giris_fiyat']
        cikis = anlik
        miktar = islem['miktar']
        brut_pnl = (giris - cikis) * miktar
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
    # LONG (tam ters simetri)
    # =========================================================================

    def _long_isle(self, sembol, buffer, anlik, cizgiler, atr):
        state = self._state[sembol]
        islem_id = state['long_islem_id']

        if islem_id is not None:
            self._long_islem_yonet(sembol, islem_id, buffer, anlik, cizgiler, atr)
        else:
            if state['long_flag']:
                if yukari_kirilma(buffer, anlik, cizgiler['mavi_alt_dis']):
                    self._long_islem_ac(sembol, anlik, cizgiler, atr)
                    state['long_flag'] = False
                elif asagi_kirilma(buffer, anlik, cizgiler['mavi_alt_dis_tampon']):
                    state['long_flag'] = False
            else:
                if yukari_kirilma(buffer, anlik, cizgiler['mavi_alt_dis_tampon']):
                    state['long_flag'] = True

    def _long_islem_ac(self, sembol, anlik, cizgiler, atr):
        if not self.poz.slot_var_mi():
            return

        try:
            bakiye = self.bybit.bakiye_al()
        except Exception as e:
            self._hata(f"{sembol} LONG bakiye: {e}")
            return

        stake = self.poz.stake_usdt
        if bakiye < stake:
            self.telegram.yetersiz_bakiye(self.THREAD_ADI, sembol, 'LONG', stake, bakiye,
                                          self.poz.acik_sayisi(), self.poz.max_pozisyon)
            return

        miktar = self.poz.miktar_hesapla(sembol, anlik)
        if miktar <= 0:
            return

        sl_fiyat = self.poz.sl_fiyati('LONG', anlik, sembol)

        try:
            self.bybit.kaldirac_ayarla(sembol, self.poz.kaldirac)
            self.bybit.market_emir(sembol, 'LONG', miktar, sl_fiyat=sl_fiyat)
        except Exception as e:
            self._hata(f"{sembol} LONG emir: {e}")
            return

        islem_id = self.poz.islem_ekle(
            sembol=sembol, yon='LONG', thread_adi=self.THREAD_ADI,
            giris_fiyat=anlik, miktar=miktar, sl_fiyat=sl_fiyat, atr=atr,
            baslangic_seviye='ENTRY',
        )
        islem = self.poz.islem_al(islem_id)
        islem['acilis_zamani'] = datetime.now()
        islem['en_yuksek'] = anlik
        islem['chandelier_aktif'] = False

        self._state[sembol]['long_islem_id'] = islem_id
        self.telegram.islem_acildi(islem, self.poz.acik_sayisi(), self.poz.max_pozisyon)

    def _long_islem_yonet(self, sembol, islem_id, buffer, anlik, cizgiler, atr):
        islem = self.poz.islem_al(islem_id)
        if islem is None:
            self._state[sembol]['long_islem_id'] = None
            return

        if anlik > islem['en_yuksek']:
            islem['en_yuksek'] = anlik

        chandelier = islem['en_yuksek'] - self.chandelier_atr_carpan * atr

        seviye = islem['seviye']

        # Seviye gecisleri
        if seviye == 'ENTRY':
            if yukari_kirilma(buffer, anlik, cizgiler['mavi_alt_ic_tampon']):
                self.poz.seviye_guncelle(islem_id, 'BE')
                islem['chandelier_aktif'] = True  # BE'den itibaren chandelier aktif
                self.telegram.seviye_gecis(islem, 'ENTRY', 'BE', anlik, atr)
        elif seviye == 'BE':
            if yukari_kirilma(buffer, anlik, cizgiler['mavi_alt_seviye1']):
                self.poz.seviye_guncelle(islem_id, 'ST1')
                # chandelier zaten BE'den beri aktif
                self.telegram.seviye_gecis(islem, 'BE', 'ST1', anlik, atr)
        elif seviye == 'ST1':
            if yukari_kirilma(buffer, anlik, cizgiler['mavi_alt_seviye2']):
                self.poz.seviye_guncelle(islem_id, 'ST2')
                self.telegram.seviye_gecis(islem, 'ST1', 'ST2', anlik, atr)

        seviye = islem['seviye']
        cikis_yapildi = False
        cikis_tipi = None

        # WINRATE EXIT: Fiyat Mavi UST Seviye 1'e ulasir veya gecerse (her seviyede aktif)
        if anlik >= cizgiler['mavi_ust_seviye1']:
            cikis_yapildi = True
            cikis_tipi = 'WINRATE EXIT'

        if not cikis_yapildi:
            if seviye == 'ENTRY':
                if asagi_kirilma(buffer, anlik, cizgiler['mavi_alt_dis_tampon']):
                    cikis_yapildi = True
                    cikis_tipi = 'ENTRY EXIT'
            elif seviye == 'BE':
                # Chandelier'e cikis (BE'den itibaren aktif)
                if anlik <= chandelier:
                    cikis_yapildi = True
                    cikis_tipi = 'CHANDELIER EXIT'
                else:
                    cikis_cizgi = cizgiler['mavi_alt_dis_tampon'] - (cizgiler['mavi_alt_dis_tampon'] * self.komisyon)
                    if asagi_kirilma(buffer, anlik, cikis_cizgi):
                        cikis_yapildi = True
                        cikis_tipi = 'BE EXIT'
            elif seviye == 'ST1':
                if anlik <= chandelier:
                    cikis_yapildi = True
                    cikis_tipi = 'CHANDELIER EXIT'
                else:
                    cikis_cizgi = cizgiler['mavi_alt_dis'] + (cizgiler['mavi_alt_dis'] * self.komisyon)
                    if asagi_kirilma(buffer, anlik, cikis_cizgi):
                        cikis_yapildi = True
                        cikis_tipi = 'ST1 EXIT'
            elif seviye == 'ST2':
                if anlik <= chandelier:
                    cikis_yapildi = True
                    cikis_tipi = 'CHANDELIER EXIT'
                else:
                    if asagi_kirilma(buffer, anlik, cizgiler['mavi_alt_seviye1']):
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
            self._hata(f"{sembol} LONG kapat: {e}")
            return

        giris = islem['giris_fiyat']
        cikis = anlik
        miktar = islem['miktar']
        brut_pnl = (cikis - giris) * miktar
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
        self._state[sembol]['long_islem_id'] = None

    def _hata(self, mesaj):
        try:
            self.telegram.hata(self.THREAD_ADI, mesaj)
        except Exception:
            pass
