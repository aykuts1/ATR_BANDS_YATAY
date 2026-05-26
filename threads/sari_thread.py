"""
SARI THREAD - REVERSAL (disaridan iceriye)

YENI SARI NUMARALANDIRMA:
- Sari Ust Seviye 1 = Mavi Ust Dis + 0.4 ATR (DISARIDA, en ustte)
- Sari Ust Seviye 2 = Mavi Ust Dis + 0.2 ATR
- Sari Ust Seviye 4 = Mavi Ust Dis - 0.2 ATR
- Sari Ust Seviye 5 = Mavi Ust Dis - 0.4 ATR (ICERIDE, en altta)

UST BANT SIRALAMA (yukaridan asagiya, Short ilerleme yonu):
  Mavi Ust Dis Tampon -> Sari1 -> Sari2 -> Mavi Ust Dis -> Sari4 -> Sari5 -> Mavi Ust Ic Tampon

SHORT:
- Flag acilma normal: Mavi Ust Dis Tampon asagi kirilir
- Flag acilma stop sonrasi: Kayitli cikis cizgisi asagi kirilir
- Flag silme: Ayni cizgi yukari kirilir (ya da islem acilir)
- Giris haritasi (flag -> giris cizgisi):
    Mavi Ust Dis Tampon -> Sari 1
    Sari 1              -> Sari 2
    Sari 2              -> Mavi Ust Dis
    Mavi Ust Dis        -> Sari 4
    Sari 4              -> Sari 5

Seviye tespiti:
  ENTRY: Sari 2 < fiyat < Sari 1
  BE:    Mavi Ust Dis < fiyat <= Sari 2
  ST1:   Sari 4 < fiyat <= Mavi Ust Dis
  ST2:   Sari 5 < fiyat <= Sari 4
  ST3:   Mavi Ust Ic Tampon < fiyat <= Sari 5

Cikis Tip 1 STOP (mevcut seviyenin UST sinirini yukari kirma):
  ENTRY -> Sari 1
  BE    -> Sari 2
  ST1   -> Mavi Ust Dis
  ST2   -> Sari 4
  ST3   -> Sari 5
Kirilan cizgi saklanir.

Cikis Tip 2 HEDEF: Mavi Ust Ic Tampon'a ulasilirsa -> WINRATE EXIT (saklanmaz)

LONG: tam ters simetri
"""

import threading
from datetime import datetime
from utils.crossover import yukari_kirilma, asagi_kirilma


class SariThread:
    THREAD_ADI = 'SARI'

    SHORT_FLAG_GIRIS = [
        ('mavi_ust_dis_tampon', 'sari_ust_seviye1'),
        ('sari_ust_seviye1',    'sari_ust_seviye2'),
        ('sari_ust_seviye2',    'mavi_ust_dis'),
        ('mavi_ust_dis',        'sari_ust_seviye4'),
        ('sari_ust_seviye4',    'sari_ust_seviye5'),
    ]
    LONG_FLAG_GIRIS = [
        ('mavi_alt_dis_tampon', 'sari_alt_seviye1'),
        ('sari_alt_seviye1',    'sari_alt_seviye2'),
        ('sari_alt_seviye2',    'mavi_alt_dis'),
        ('mavi_alt_dis',        'sari_alt_seviye4'),
        ('sari_alt_seviye4',    'sari_alt_seviye5'),
    ]

    SHORT_SEVIYE_STOP_CIZGI = {
        'ENTRY': 'sari_ust_seviye1',
        'BE':    'sari_ust_seviye2',
        'ST1':   'mavi_ust_dis',
        'ST2':   'sari_ust_seviye4',
        'ST3':   'sari_ust_seviye5',
    }
    LONG_SEVIYE_STOP_CIZGI = {
        'ENTRY': 'sari_alt_seviye1',
        'BE':    'sari_alt_seviye2',
        'ST1':   'mavi_alt_dis',
        'ST2':   'sari_alt_seviye4',
        'ST3':   'sari_alt_seviye5',
    }

    def __init__(self, config, market_data, pozisyon_yon, bybit_client, telegram):
        self.config = config
        self.market = market_data
        self.poz = pozisyon_yon
        self.bybit = bybit_client
        self.telegram = telegram

        self.coinler = config['coinler']
        self.fiyat_yenileme = config['fiyat_yenileme_saniye']
        self.komisyon = config['komisyon_yuzde'] / 100.0

        self._state = {}
        for s in self.coinler:
            self._state[s] = {
                'long_flag_cizgi': None,
                'short_flag_cizgi': None,
                'long_kayitli_cikis': None,
                'short_kayitli_cikis': None,
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

    def _giris_cizgisi(self, yon, flag_cizgi):
        liste = self.SHORT_FLAG_GIRIS if yon == 'SHORT' else self.LONG_FLAG_GIRIS
        for fc, gc in liste:
            if fc == flag_cizgi:
                return gc
        return None

    # =========================================================================
    # SHORT
    # =========================================================================

    def _short_isle(self, sembol, buffer, anlik, cizgiler, atr):
        state = self._state[sembol]
        islem_id = state['short_islem_id']

        if islem_id is not None:
            self._short_islem_yonet(sembol, islem_id, buffer, anlik, cizgiler, atr)
            return

        if state['short_flag_cizgi'] is not None:
            flag_cizgi_adi = state['short_flag_cizgi']
            giris_cizgi_adi = self._giris_cizgisi('SHORT', flag_cizgi_adi)
            if giris_cizgi_adi is None:
                state['short_flag_cizgi'] = None
                return

            if asagi_kirilma(buffer, anlik, cizgiler[giris_cizgi_adi]):
                self._short_islem_ac(sembol, anlik, cizgiler, atr)
                state['short_flag_cizgi'] = None
                state['short_kayitli_cikis'] = None
                return
            if yukari_kirilma(buffer, anlik, cizgiler[flag_cizgi_adi]):
                state['short_flag_cizgi'] = None
            return

        # Normal flag
        if asagi_kirilma(buffer, anlik, cizgiler['mavi_ust_dis_tampon']):
            state['short_flag_cizgi'] = 'mavi_ust_dis_tampon'
            return

        # Stop sonrasi flag
        if state['short_kayitli_cikis'] is not None:
            cikis_ad = state['short_kayitli_cikis']
            if asagi_kirilma(buffer, anlik, cizgiler[cikis_ad]):
                state['short_flag_cizgi'] = cikis_ad
                return

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

        baslangic_seviye = self._short_seviye_belirle(anlik, cizgiler)
        islem_id = self.poz.islem_ekle(
            sembol=sembol, yon='SHORT', thread_adi=self.THREAD_ADI,
            giris_fiyat=anlik, miktar=miktar, sl_fiyat=sl_fiyat, atr=atr,
            baslangic_seviye=baslangic_seviye,
        )
        islem = self.poz.islem_al(islem_id)
        islem['acilis_zamani'] = datetime.now()

        self._state[sembol]['short_islem_id'] = islem_id
        self.telegram.islem_acildi(islem, self.poz.acik_sayisi(), self.poz.max_pozisyon)

    def _short_seviye_belirle(self, fiyat, cizgiler):
        s1 = cizgiler['sari_ust_seviye1']
        s2 = cizgiler['sari_ust_seviye2']
        s4 = cizgiler['sari_ust_seviye4']
        s5 = cizgiler['sari_ust_seviye5']
        mdis = cizgiler['mavi_ust_dis']
        mic = cizgiler['mavi_ust_ic_tampon']

        if s2 < fiyat < s1:
            return 'ENTRY'
        if mdis < fiyat <= s2:
            return 'BE'
        if s4 < fiyat <= mdis:
            return 'ST1'
        if s5 < fiyat <= s4:
            return 'ST2'
        if mic < fiyat <= s5:
            return 'ST3'
        return 'ENTRY'

    def _short_islem_yonet(self, sembol, islem_id, buffer, anlik, cizgiler, atr):
        islem = self.poz.islem_al(islem_id)
        if islem is None:
            self._state[sembol]['short_islem_id'] = None
            return

        # Hedef Tip 2 once: Mavi Ust Ic Tampon
        if anlik <= cizgiler['mavi_ust_ic_tampon']:
            self._short_islem_kapat(sembol, islem_id, anlik, 'WINRATE EXIT', kayitli_cikis=None)
            return

        yeni_seviye = self._short_seviye_belirle(anlik, cizgiler)
        eski_seviye = islem['seviye']
        if yeni_seviye != eski_seviye:
            self.poz.seviye_guncelle(islem_id, yeni_seviye)
            self.telegram.seviye_gecis(islem, eski_seviye, yeni_seviye, anlik, atr)

        # Stop Tip 1
        mevcut_seviye = islem['seviye']
        stop_cizgi_ad = self.SHORT_SEVIYE_STOP_CIZGI.get(mevcut_seviye)
        if stop_cizgi_ad is not None:
            stop_cizgi = cizgiler[stop_cizgi_ad]
            if yukari_kirilma(buffer, anlik, stop_cizgi):
                cikis_tipi = f'{mevcut_seviye} EXIT'
                self._short_islem_kapat(sembol, islem_id, anlik, cikis_tipi, kayitli_cikis=stop_cizgi_ad)
                return

    def _short_islem_kapat(self, sembol, islem_id, anlik, cikis_tipi, kayitli_cikis=None):
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
        self._state[sembol]['short_kayitli_cikis'] = kayitli_cikis

    # =========================================================================
    # LONG (tam ters simetri)
    # =========================================================================

    def _long_isle(self, sembol, buffer, anlik, cizgiler, atr):
        state = self._state[sembol]
        islem_id = state['long_islem_id']

        if islem_id is not None:
            self._long_islem_yonet(sembol, islem_id, buffer, anlik, cizgiler, atr)
            return

        if state['long_flag_cizgi'] is not None:
            flag_cizgi_adi = state['long_flag_cizgi']
            giris_cizgi_adi = self._giris_cizgisi('LONG', flag_cizgi_adi)
            if giris_cizgi_adi is None:
                state['long_flag_cizgi'] = None
                return

            if yukari_kirilma(buffer, anlik, cizgiler[giris_cizgi_adi]):
                self._long_islem_ac(sembol, anlik, cizgiler, atr)
                state['long_flag_cizgi'] = None
                state['long_kayitli_cikis'] = None
                return
            if asagi_kirilma(buffer, anlik, cizgiler[flag_cizgi_adi]):
                state['long_flag_cizgi'] = None
            return

        if yukari_kirilma(buffer, anlik, cizgiler['mavi_alt_dis_tampon']):
            state['long_flag_cizgi'] = 'mavi_alt_dis_tampon'
            return

        if state['long_kayitli_cikis'] is not None:
            cikis_ad = state['long_kayitli_cikis']
            if yukari_kirilma(buffer, anlik, cizgiler[cikis_ad]):
                state['long_flag_cizgi'] = cikis_ad
                return

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

        baslangic_seviye = self._long_seviye_belirle(anlik, cizgiler)
        islem_id = self.poz.islem_ekle(
            sembol=sembol, yon='LONG', thread_adi=self.THREAD_ADI,
            giris_fiyat=anlik, miktar=miktar, sl_fiyat=sl_fiyat, atr=atr,
            baslangic_seviye=baslangic_seviye,
        )
        islem = self.poz.islem_al(islem_id)
        islem['acilis_zamani'] = datetime.now()

        self._state[sembol]['long_islem_id'] = islem_id
        self.telegram.islem_acildi(islem, self.poz.acik_sayisi(), self.poz.max_pozisyon)

    def _long_seviye_belirle(self, fiyat, cizgiler):
        s1 = cizgiler['sari_alt_seviye1']  # Mavi Alt Dis - 0.4 (disarda/altta)
        s2 = cizgiler['sari_alt_seviye2']  # Mavi Alt Dis - 0.2
        s4 = cizgiler['sari_alt_seviye4']  # Mavi Alt Dis + 0.2
        s5 = cizgiler['sari_alt_seviye5']  # Mavi Alt Dis + 0.4 (iceride/ustte)
        madis = cizgiler['mavi_alt_dis']
        maic = cizgiler['mavi_alt_ic_tampon']

        if s1 < fiyat < s2:
            return 'ENTRY'
        if s2 <= fiyat < madis:
            return 'BE'
        if madis <= fiyat < s4:
            return 'ST1'
        if s4 <= fiyat < s5:
            return 'ST2'
        if s5 <= fiyat < maic:
            return 'ST3'
        return 'ENTRY'

    def _long_islem_yonet(self, sembol, islem_id, buffer, anlik, cizgiler, atr):
        islem = self.poz.islem_al(islem_id)
        if islem is None:
            self._state[sembol]['long_islem_id'] = None
            return

        # Hedef once: Mavi Alt Ic Tampon
        if anlik >= cizgiler['mavi_alt_ic_tampon']:
            self._long_islem_kapat(sembol, islem_id, anlik, 'WINRATE EXIT', kayitli_cikis=None)
            return

        yeni_seviye = self._long_seviye_belirle(anlik, cizgiler)
        eski_seviye = islem['seviye']
        if yeni_seviye != eski_seviye:
            self.poz.seviye_guncelle(islem_id, yeni_seviye)
            self.telegram.seviye_gecis(islem, eski_seviye, yeni_seviye, anlik, atr)

        # Stop
        mevcut_seviye = islem['seviye']
        stop_cizgi_ad = self.LONG_SEVIYE_STOP_CIZGI.get(mevcut_seviye)
        if stop_cizgi_ad is not None:
            stop_cizgi = cizgiler[stop_cizgi_ad]
            if asagi_kirilma(buffer, anlik, stop_cizgi):
                cikis_tipi = f'{mevcut_seviye} EXIT'
                self._long_islem_kapat(sembol, islem_id, anlik, cikis_tipi, kayitli_cikis=stop_cizgi_ad)
                return

    def _long_islem_kapat(self, sembol, islem_id, anlik, cikis_tipi, kayitli_cikis=None):
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
        self._state[sembol]['long_kayitli_cikis'] = kayitli_cikis

    def _hata(self, mesaj):
        try:
            self.telegram.hata(self.THREAD_ADI, mesaj)
        except Exception:
            pass
