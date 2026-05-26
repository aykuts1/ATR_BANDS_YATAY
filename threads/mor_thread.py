"""
MOR THREAD

LONG:
- Flag acilma normal: Kirmizi Ust Ic Tampon yukari kirilir, flag_cizgi = bu cizgi
- Flag acilma stop sonrasi: Kayitli cikis cizgisi yukari kirilir, flag_cizgi = o cizgi
- Flag silme: Flag acilan ayni cizgi asagi kirilirsa, ya da islem acilirsa
- Giris: flag_cizgi'nin BIR UST SEVIYESI yukari kirilirsa
  flag_cizgi   ->  giris cizgisi
  K_Ust_Ic_Tampon -> Mor_Ust_Seviye1
  Mor_Ust_Seviye1 -> Mor_Ust_Seviye2
  Mor_Ust_Seviye2 -> Kirmizi_Ust_Dis
  Kirmizi_Ust_Dis -> Mor_Ust_Seviye4
  Mor_Ust_Seviye4 -> Mor_Ust_Seviye5

- Seviyeler (fiyatin bulundugu yerle belirlenir):
  Seviye 1 ENTRY: Mor Ust Seviye 1 ustunde, Mor Ust Seviye 2 altinda
  Seviye 2 BE: Mor Ust Seviye 2 ustunde, Kirmizi Ust Dis altinda
  Seviye 3 ST1: Kirmizi Ust Dis ustunde, Mor Ust Seviye 4 altinda
  Seviye 4 ST2: Mor Ust Seviye 4 ustunde, Mor Ust Seviye 5 altinda
  Seviye 5 ST3: Mor Ust Seviye 5 ustunde, Kirmizi Ust Dis Tampon altinda

- Cikis Tip 1 STOP: seviyenin alt cizgisi asagi kirilir -> kirilan cizgi SAKLANIR
- Cikis Tip 2 HEDEF: anlik fiyat Kirmizi Ust Dis Tampon'a degerse/gecerse -> WINRATE EXIT, saklanmaz

Cikis isimleri:
- ENTRY EXIT, BE EXIT, ST1 EXIT, ST2 EXIT, ST3 EXIT, WINRATE EXIT
"""

import threading
from datetime import datetime
from utils.crossover import yukari_kirilma, asagi_kirilma


class MorThread:
    THREAD_ADI = 'MOR'

    # Flag cizgi -> giris cizgisi haritalari
    LONG_FLAG_GIRIS = [
        ('kirmizi_ust_ic_tampon', 'mor_ust_seviye1'),
        ('mor_ust_seviye1', 'mor_ust_seviye2'),
        ('mor_ust_seviye2', 'kirmizi_ust_dis'),
        ('kirmizi_ust_dis', 'mor_ust_seviye4'),
        ('mor_ust_seviye4', 'mor_ust_seviye5'),
    ]
    SHORT_FLAG_GIRIS = [
        ('kirmizi_alt_ic_tampon', 'mor_alt_seviye1'),
        ('mor_alt_seviye1', 'mor_alt_seviye2'),
        ('mor_alt_seviye2', 'kirmizi_alt_dis'),
        ('kirmizi_alt_dis', 'mor_alt_seviye4'),
        ('mor_alt_seviye4', 'mor_alt_seviye5'),
    ]

    # Seviye -> alt cizgi (stop cizgisi) Long
    LONG_SEVIYE_ALT_CIZGI = {
        'ENTRY': 'mor_ust_seviye1',
        'BE': 'mor_ust_seviye2',
        'ST1': 'kirmizi_ust_dis',
        'ST2': 'mor_ust_seviye4',
        'ST3': 'mor_ust_seviye5',
    }
    SHORT_SEVIYE_UST_CIZGI = {
        'ENTRY': 'mor_alt_seviye1',
        'BE': 'mor_alt_seviye2',
        'ST1': 'kirmizi_alt_dis',
        'ST2': 'mor_alt_seviye4',
        'ST3': 'mor_alt_seviye5',
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

        # State: flag bilgileri + acik islem id'leri
        # flag_cizgi_long: hangi cizgide flag acildi (string anahtar veya None)
        # cikis_cizgi_long: stop sonrasi flag icin saklanan cikis cizgisi (string veya None)
        self._state = {}
        for s in self.coinler:
            self._state[s] = {
                'long_flag_cizgi': None,   # flag aktifken hangi cizgide acildi
                'short_flag_cizgi': None,
                'long_kayitli_cikis': None,  # stop sonrasi normal flag yaninda bu da aktif
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

        self._long_isle(sembol, buffer, anlik, cizgiler, atr)
        self._short_isle(sembol, buffer, anlik, cizgiler, atr)

    # =========================================================================
    # LONG
    # =========================================================================

    def _long_isle(self, sembol, buffer, anlik, cizgiler, atr):
        state = self._state[sembol]
        islem_id = state['long_islem_id']

        if islem_id is not None:
            self._long_islem_yonet(sembol, islem_id, buffer, anlik, cizgiler, atr)
            return

        # Acik islem yok
        # 1. Flag varsa giris kontrolu
        if state['long_flag_cizgi'] is not None:
            flag_cizgi_adi = state['long_flag_cizgi']
            # giris cizgisini bul
            giris_cizgi_adi = self._giris_cizgisi('LONG', flag_cizgi_adi)
            if giris_cizgi_adi is None:
                # boyle bir mapping yok, flag temizlenmeli
                state['long_flag_cizgi'] = None
                return

            # giris kontrolu
            if yukari_kirilma(buffer, anlik, cizgiler[giris_cizgi_adi]):
                self._long_islem_ac(sembol, anlik, cizgiler, atr)
                state['long_flag_cizgi'] = None
                # islem acilirsa kayitli cikis da silinir (zaten flag ile beraber)
                state['long_kayitli_cikis'] = None
                return
            # flag silme: ayni cizgi asagi kirilirsa
            if asagi_kirilma(buffer, anlik, cizgiler[flag_cizgi_adi]):
                state['long_flag_cizgi'] = None
                # kayitli cikis kalir mi? User dedi: "flag silmek 4 madde dahilinde"
                # Stop sonrasi flag silinince kayitli cikis da silinir mi netnedegil.
                # Mantikli olan: kayitli cikis kalir, bir sonraki yukari kirilima firsat verir.
                # Bu yuzden silmiyoruz.
            return

        # 2. Flag yoksa flag acma kontrolu
        # NORMAL flag: Kirmizi Ust Ic Tampon yukari kirilir
        if yukari_kirilma(buffer, anlik, cizgiler['kirmizi_ust_ic_tampon']):
            state['long_flag_cizgi'] = 'kirmizi_ust_ic_tampon'
            return

        # STOP SONRASI flag (varsa)
        if state['long_kayitli_cikis'] is not None:
            cikis_ad = state['long_kayitli_cikis']
            if yukari_kirilma(buffer, anlik, cizgiler[cikis_ad]):
                state['long_flag_cizgi'] = cikis_ad
                # NOT: kayitli cikis silinmez, islem aciliana kadar
                return

    def _giris_cizgisi(self, yon, flag_cizgi):
        """Verilen flag cizgisi icin giris cizgisini doner."""
        liste = self.LONG_FLAG_GIRIS if yon == 'LONG' else self.SHORT_FLAG_GIRIS
        for fc, gc in liste:
            if fc == flag_cizgi:
                return gc
        return None

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

        # Acilis seviyesi anlik fiyata gore belirlenir
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
        """Anlik fiyatin Mor bant Long seviyelerinde nerede oldugunu bulur."""
        if fiyat >= cizgiler['mor_ust_seviye5'] and fiyat < cizgiler['kirmizi_ust_dis_tampon']:
            return 'ST3'
        if fiyat >= cizgiler['mor_ust_seviye4'] and fiyat < cizgiler['mor_ust_seviye5']:
            return 'ST2'
        if fiyat >= cizgiler['kirmizi_ust_dis'] and fiyat < cizgiler['mor_ust_seviye4']:
            return 'ST1'
        if fiyat >= cizgiler['mor_ust_seviye2'] and fiyat < cizgiler['kirmizi_ust_dis']:
            return 'BE'
        if fiyat >= cizgiler['mor_ust_seviye1'] and fiyat < cizgiler['mor_ust_seviye2']:
            return 'ENTRY'
        # Bunlardan birinde olmali ama yine de default
        return 'ENTRY'

    def _long_islem_yonet(self, sembol, islem_id, buffer, anlik, cizgiler, atr):
        islem = self.poz.islem_al(islem_id)
        if islem is None:
            self._state[sembol]['long_islem_id'] = None
            return

        # 1. Once Hedef Tip 2 kontrolu (fiyat hedef bolgesindeyse seviye guncellenmesin)
        if anlik >= cizgiler['kirmizi_ust_dis_tampon']:
            self._long_islem_kapat(sembol, islem_id, anlik, 'WINRATE EXIT', kayitli_cikis=None)
            return

        # 2. Seviye guncelle
        yeni_seviye = self._long_seviye_belirle(anlik, cizgiler)
        eski_seviye = islem['seviye']
        if yeni_seviye != eski_seviye:
            self.poz.seviye_guncelle(islem_id, yeni_seviye)
            self.telegram.seviye_gecis(islem, eski_seviye, yeni_seviye, anlik, atr)

        # 3. Cikis Tip 1 STOP (mevcut seviyenin alt cizgisi asagi kirilirsa)
        mevcut_seviye = islem['seviye']
        alt_cizgi_ad = self.LONG_SEVIYE_ALT_CIZGI.get(mevcut_seviye)
        if alt_cizgi_ad is not None:
            alt_cizgi = cizgiler[alt_cizgi_ad]
            if asagi_kirilma(buffer, anlik, alt_cizgi):
                # cikis ismi seviyeye gore
                cikis_tipi = f'{mevcut_seviye} EXIT'
                # cikis cizgisini kayitli olarak sakla
                self._long_islem_kapat(sembol, islem_id, anlik, cikis_tipi, kayitli_cikis=alt_cizgi_ad)
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

        # Stop cikisi ise kayitli cikis cizgisini sakla, hedefte sakla
        self._state[sembol]['long_kayitli_cikis'] = kayitli_cikis

    # =========================================================================
    # SHORT (tam ters simetri)
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
        if asagi_kirilma(buffer, anlik, cizgiler['kirmizi_alt_ic_tampon']):
            state['short_flag_cizgi'] = 'kirmizi_alt_ic_tampon'
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
        if fiyat <= cizgiler['mor_alt_seviye5'] and fiyat > cizgiler['kirmizi_alt_dis_tampon']:
            return 'ST3'
        if fiyat <= cizgiler['mor_alt_seviye4'] and fiyat > cizgiler['mor_alt_seviye5']:
            return 'ST2'
        if fiyat <= cizgiler['kirmizi_alt_dis'] and fiyat > cizgiler['mor_alt_seviye4']:
            return 'ST1'
        if fiyat <= cizgiler['mor_alt_seviye2'] and fiyat > cizgiler['kirmizi_alt_dis']:
            return 'BE'
        if fiyat <= cizgiler['mor_alt_seviye1'] and fiyat > cizgiler['mor_alt_seviye2']:
            return 'ENTRY'
        return 'ENTRY'

    def _short_islem_yonet(self, sembol, islem_id, buffer, anlik, cizgiler, atr):
        islem = self.poz.islem_al(islem_id)
        if islem is None:
            self._state[sembol]['short_islem_id'] = None
            return

        # 1. Hedef Tip 2 kontrolu once
        if anlik <= cizgiler['kirmizi_alt_dis_tampon']:
            self._short_islem_kapat(sembol, islem_id, anlik, 'WINRATE EXIT', kayitli_cikis=None)
            return

        # 2. Seviye guncelle
        yeni_seviye = self._short_seviye_belirle(anlik, cizgiler)
        eski_seviye = islem['seviye']
        if yeni_seviye != eski_seviye:
            self.poz.seviye_guncelle(islem_id, yeni_seviye)
            self.telegram.seviye_gecis(islem, eski_seviye, yeni_seviye, anlik, atr)

        # 3. Stop
        mevcut_seviye = islem['seviye']
        ust_cizgi_ad = self.SHORT_SEVIYE_UST_CIZGI.get(mevcut_seviye)
        if ust_cizgi_ad is not None:
            ust_cizgi = cizgiler[ust_cizgi_ad]
            if yukari_kirilma(buffer, anlik, ust_cizgi):
                cikis_tipi = f'{mevcut_seviye} EXIT'
                self._short_islem_kapat(sembol, islem_id, anlik, cikis_tipi, kayitli_cikis=ust_cizgi_ad)
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

    def _hata(self, mesaj):
        try:
            self.telegram.hata(self.THREAD_ADI, mesaj)
        except Exception:
            pass
