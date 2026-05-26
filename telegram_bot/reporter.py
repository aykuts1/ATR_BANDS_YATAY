"""
Periyodik rapor uretecisi.

15dk, 1 saat, 8 saat ve 24 saatlik raporlari Telegram'a gonderir.

Mimari:
- Her rapor turu icin ayri bir zamanlayici threadi
- Her rapor calistiginda:
  * Acik pozisyonlari pozisyon yoneticisinden alir
  * Kapanan islemleri Notifier havuzundan alir (son N dakika)
  * Format edip Notifier ile gonderir
- 24 saatlik raporda havuz temizlenir (eski kayitlar silinir)
"""
import threading
import time
from datetime import datetime, timedelta
from collections import defaultdict


THREAD_EMOJI = {
    'KIRMIZI': '🔴',
    'MAVI': '🔵',
    'MOR': '🟣',
    'SARI': '🟡',
}


class Reporter:
    def __init__(self, config, notifier, pozisyon_yon, bybit_client):
        self.config = config
        self.notifier = notifier
        self.poz = pozisyon_yon
        self.bybit = bybit_client

        # Bot baslangic bakiyesi (main set eder)
        self.baslangic_bakiyesi = None
        self.baslangic_zamani = None

        self._durdur = threading.Event()
        self._threadler = []

        # Saatlik PNL takip (24 saatlik rapor icin)
        # saat (datetime hour) -> net_pnl toplam
        self._saatlik_pnl = defaultdict(float)
        self._saatlik_lock = threading.RLock()

    def baslat(self, baslangic_bakiyesi):
        self.baslangic_bakiyesi = baslangic_bakiyesi
        self.baslangic_zamani = datetime.now()

        if self.config.get('rapor_15dk', True):
            t = threading.Thread(target=self._dongu, args=(15, '15 DAKİKALIK', self._rapor_15dk),
                                 name='Rapor15dk', daemon=True)
            t.start()
            self._threadler.append(t)

        if self.config.get('rapor_1saat', True):
            t = threading.Thread(target=self._dongu, args=(60, '1 SAATLİK', self._rapor_1saat),
                                 name='Rapor1saat', daemon=True)
            t.start()
            self._threadler.append(t)

        if self.config.get('rapor_8saat', True):
            t = threading.Thread(target=self._dongu, args=(60 * 8, '8 SAATLİK', self._rapor_8saat),
                                 name='Rapor8saat', daemon=True)
            t.start()
            self._threadler.append(t)

        if self.config.get('rapor_24saat', True):
            t = threading.Thread(target=self._dongu, args=(60 * 24, '24 SAATLİK', self._rapor_24saat),
                                 name='Rapor24saat', daemon=True)
            t.start()
            self._threadler.append(t)

    def durdur(self):
        self._durdur.set()

    def _dongu(self, dakika, etiket, fonksiyon):
        """Verilen dakikada bir, fonksiyonu calistir."""
        bekleme = dakika * 60
        while not self._durdur.is_set():
            self._durdur.wait(bekleme)
            if self._durdur.is_set():
                break
            try:
                fonksiyon()
            except Exception as e:
                print(f"Rapor {etiket} hatasi: {e}")

    # =========================================================================
    # YARDIMCI
    # =========================================================================

    def _bakiye_anlik(self):
        """Anlik bakiyeyi doner (Bybit API'den)."""
        try:
            return self.bybit.bakiye_al()
        except Exception:
            return None

    def _islem_thread_yon_sayisi(self, islemler):
        """Kapanan islemleri thread x yon olarak sayar."""
        # thread -> {long: count, short: count, kar: count, zarar: count, pnl: float}
        sayilar = defaultdict(lambda: {
            'long': 0, 'short': 0, 'kar': 0, 'zarar': 0, 'pnl': 0.0
        })
        for k in islemler:
            islem = k['islem']
            thread = islem['thread']
            yon = islem['yon']
            net_pnl = islem.get('net_pnl', 0.0)

            if yon == 'LONG':
                sayilar[thread]['long'] += 1
            else:
                sayilar[thread]['short'] += 1

            if net_pnl > 0:
                sayilar[thread]['kar'] += 1
            else:
                sayilar[thread]['zarar'] += 1

            sayilar[thread]['pnl'] += net_pnl
        return sayilar

    def _cikis_tur_sayilari(self, islemler):
        """Cikis turune gore sayar."""
        sayilar = defaultdict(int)
        for k in islemler:
            tip = k['islem'].get('cikis_tipi', 'BILINMEYEN')
            sayilar[tip] += 1
        return sayilar

    def _coin_sayilari(self, islemler):
        """Coin bazinda islem sayisi."""
        sayilar = defaultdict(int)
        for k in islemler:
            sembol = k['islem'].get('sembol', '?')
            sayilar[sembol] += 1
        return sayilar

    def _acik_pozisyon_kutusu(self, islem):
        """Tek bir acik pozisyon icin kutu metni."""
        thread = islem['thread']
        emoji = THREAD_EMOJI.get(thread, '⚪')
        yon = islem['yon']
        sembol = islem['sembol']
        seviye = islem.get('seviye', '?')
        giris = islem['giris_fiyat']
        # Anlik fiyat market data'dan gelmeli ama burada yok - 0 koyalim, sonra duzeltiriz
        anlik = islem.get('anlik_fiyat', giris)
        miktar = islem['miktar']

        if yon == 'LONG':
            pnl = (anlik - giris) * miktar
        else:
            pnl = (giris - anlik) * miktar

        isaret = '+' if pnl >= 0 else ''
        return (
            f"┌─────────────────────┐\n"
            f"│ {emoji} {thread} {yon}\n"
            f"│ Coin   : {sembol}\n"
            f"│ Seviye : {seviye}\n"
            f"│ Giriş  : {giris:.4f} USDT\n"
            f"│ Anlık  : {anlik:.4f} USDT\n"
            f"│ PNL    : {isaret}{pnl:.2f} USDT\n"
            f"└─────────────────────┘"
        )

    def _acik_pozisyonlar_blogu(self):
        """Acik pozisyonlar bolumunu uretir."""
        acik = self.poz.tum_acik_islemler()
        if not acik:
            return f"💼 AÇIK POZİSYONLAR (0)\n🎰 SLOT: 0/{self.poz.max_pozisyon}"

        satirlar = [f"💼 AÇIK POZİSYONLAR ({len(acik)})"]
        for islem in acik:
            # Anlik fiyati market'tan alabilirsek daha iyi olur, ama burada erisimimiz yok
            # main.py'de market data'yi referans eklemek lazim. Su an giris esit anlik kabul
            satirlar.append(self._acik_pozisyon_kutusu(islem))
        satirlar.append(f"🎰 SLOT: {len(acik)}/{self.poz.max_pozisyon}")
        return "\n".join(satirlar)

    def _kapanan_detayli_blogu(self, islemler):
        """Kapanan islemleri kutu kutu yazar (15dk raporu icin detayli)."""
        if not islemler:
            return "📉 KAPANAN POZİSYONLAR (0)"

        # Thread-yon sayilari ve cikis turleri ozetleri
        thread_sayilari = self._islem_thread_yon_sayisi(islemler)
        cikis_sayilari = self._cikis_tur_sayilari(islemler)

        satirlar = [f"📉 KAPANAN POZİSYONLAR ({len(islemler)})"]

        # Her islem icin kutu
        for k in islemler:
            islem = k['islem']
            thread = islem['thread']
            emoji = THREAD_EMOJI.get(thread, '⚪')
            yon = islem['yon']
            sembol = islem['sembol']
            giris = islem['giris_fiyat']
            cikis = islem['cikis_fiyat']
            cikis_tipi = islem.get('cikis_tipi', '?')
            seviye = islem.get('seviye', '?')
            net_pnl = islem['net_pnl']
            isaret = '+' if net_pnl >= 0 else ''
            satirlar.append(
                f"┌─────────────────────┐\n"
                f"│ {emoji} {thread} {yon}\n"
                f"│ Coin    : {sembol}\n"
                f"│ Giriş   : {giris:.4f} USDT\n"
                f"│ Çıkış   : {cikis:.4f} USDT\n"
                f"│ Çıkış T.: {cikis_tipi}\n"
                f"│ Seviye  : {seviye}\n"
                f"│ Net PNL : {isaret}{net_pnl:.2f} USDT\n"
                f"└─────────────────────┘"
            )

        return "\n".join(satirlar)

    def _z_raporu_thread_kutulari(self, islemler):
        """Thread bazinda istatistik kutulari (Z raporu format)."""
        sayilar = self._islem_thread_yon_sayisi(islemler)
        if not sayilar:
            return ""

        satirlar = []
        for thread in ['KIRMIZI', 'MAVI', 'MOR', 'SARI']:
            if thread not in sayilar:
                continue
            s = sayilar[thread]
            emoji = THREAD_EMOJI.get(thread, '⚪')
            pnl = s['pnl']
            isaret = '+' if pnl >= 0 else ''
            satirlar.append(
                f"┌─────────────────────┐\n"
                f"│ {emoji} {thread}\n"
                f"│ Long  : {s['long']} | Short: {s['short']}\n"
                f"│ Kâr   : {s['kar']} | Zarar: {s['zarar']}\n"
                f"│ PNL   : {isaret}{pnl:.2f} USDT\n"
                f"└─────────────────────┘"
            )
        return "\n".join(satirlar)

    def _cikis_turu_dagilimi_kutusu(self, islemler):
        """Cikis turu dagilimini kutuda gosterir."""
        sayilar = self._cikis_tur_sayilari(islemler)
        if not sayilar:
            return ""
        # Buyukten kuçuge sirala
        sirali = sorted(sayilar.items(), key=lambda x: -x[1])
        satirlar = ["📊 ÇIKIŞ TÜRÜ DAĞILIMI", "┌─────────────────────┐"]
        for tip, sayi in sirali:
            satirlar.append(f"│ {tip:<14}: {sayi}")
        satirlar.append("└─────────────────────┘")
        return "\n".join(satirlar)

    def _coin_dagilimi_kutusu(self, islemler):
        sayilar = self._coin_sayilari(islemler)
        if not sayilar:
            return ""
        sirali = sorted(sayilar.items(), key=lambda x: -x[1])
        satirlar = ["📊 COİN DAĞILIMI", "┌─────────────────────┐"]
        for c, s in sirali[:10]:  # ilk 10
            satirlar.append(f"│ {c:<10}: {s} işlem")
        satirlar.append("└─────────────────────┘")
        return "\n".join(satirlar)

    def _ozet_kutusu(self, baslik, islemler, dakika, brut_pnl, komisyon_toplam, net_pnl):
        """Genel ozet kutusu."""
        acilan = len(self.poz.tum_acik_islemler()) + len(islemler)  # toplam acilan ~ kapanan + acik
        kapanan = len(islemler)
        kar = sum(1 for k in islemler if k['islem'].get('net_pnl', 0) > 0)
        zarar = kapanan - kar
        winrate = (kar / kapanan * 100) if kapanan > 0 else 0
        brut_isaret = '+' if brut_pnl >= 0 else ''
        net_isaret = '+' if net_pnl >= 0 else ''
        satirlar = [
            f"📈 {baslik} ÖZETİ",
            "┌─────────────────────┐",
            f"│ Açılan   : {acilan} işlem",
            f"│ Kapanan  : {kapanan} işlem",
            f"│ Kâr      : {kar} işlem",
            f"│ Zarar    : {zarar} işlem",
            f"│ Winrate  : %{winrate:.1f}",
            f"│ Brüt PNL : {brut_isaret}{brut_pnl:.2f} USDT",
            f"│ Komisyon : -{komisyon_toplam:.2f} USDT",
            f"│ Net PNL  : {net_isaret}{net_pnl:.2f} USDT",
            "└─────────────────────┘",
        ]
        return "\n".join(satirlar)

    def _bakiye_kutusu(self, baslik, bakiye_basi, bakiye_sonu):
        if bakiye_basi is None or bakiye_basi == 0:
            degisim = 0
        else:
            degisim = (bakiye_sonu - bakiye_basi) / bakiye_basi * 100
        toplam_degisim = 0
        if self.baslangic_bakiyesi:
            toplam_degisim = (bakiye_sonu - self.baslangic_bakiyesi) / self.baslangic_bakiyesi * 100
        return "\n".join([
            "💰 BAKİYE",
            "┌─────────────────────┐",
            f"│ {baslik}    : {bakiye_basi:.2f} USDT" if bakiye_basi else f"│ {baslik}    : - USDT",
            f"│ Anlık       : {bakiye_sonu:.2f} USDT",
            f"│ Değişim     : %{degisim:+.2f}",
            f"│ Başlangıç   : {self.baslangic_bakiyesi:.2f} USDT" if self.baslangic_bakiyesi else "│ Başlangıç   : - USDT",
            f"│ Toplam      : %{toplam_degisim:+.2f}",
            "└─────────────────────┘",
        ])

    # =========================================================================
    # RAPOR TURLERI
    # =========================================================================

    def _rapor_15dk(self):
        simdi = datetime.now()
        baslangic = simdi - timedelta(minutes=15)

        kapananlar = self.notifier.kapanan_islemler_al(baslangic)
        bakiye_anlik = self._bakiye_anlik()
        bakiye_basi = bakiye_anlik  # 15dk oncesi bakiye yok, simdiki

        # PNL hesaplari
        brut_pnl = sum(k['islem'].get('brut_pnl', 0) for k in kapananlar)
        komisyon = sum(k['islem'].get('komisyon', 0) for k in kapananlar)
        net_pnl = sum(k['islem'].get('net_pnl', 0) for k in kapananlar)

        # Saatlik PNL guncelle
        with self._saatlik_lock:
            saat_anahtari = simdi.replace(minute=0, second=0, microsecond=0)
            self._saatlik_pnl[saat_anahtari] += net_pnl

        # Mesaj
        bloklar = [
            "📊 15 DAKİKALIK RAPOR",
            "─────────────────────",
            f"🕐 {simdi.strftime('%d.%m.%Y %H:%M')}",
            "",
            self._acik_pozisyonlar_blogu(),
            "",
            self._kapanan_detayli_blogu(kapananlar),
            "",
            f"📈 SON 15 DAKİKA",
            f"├ Açılan : - işlem",  # acilan sayisini ayri tutmuyoruz
            f"├ Kapanan: {len(kapananlar)} işlem",
            f"└ Net PNL: {net_pnl:+.2f} USDT",
            "",
        ]
        if bakiye_anlik is not None:
            bloklar.extend([
                "💰 BAKİYE",
                f"├ Başlangıç: {self.baslangic_bakiyesi:.2f} USDT" if self.baslangic_bakiyesi else "├ Başlangıç: - USDT",
                f"└ Anlık    : {bakiye_anlik:.2f} USDT",
            ])
        bloklar.append("─────────────────────")

        mesaj = "<pre>" + "\n".join(bloklar) + "</pre>"
        self.notifier.rapor_gonder(mesaj)

    def _rapor_1saat(self):
        simdi = datetime.now()
        baslangic = simdi - timedelta(hours=1)
        kapananlar = self.notifier.kapanan_islemler_al(baslangic)
        bakiye_anlik = self._bakiye_anlik() or 0

        brut_pnl = sum(k['islem'].get('brut_pnl', 0) for k in kapananlar)
        komisyon = sum(k['islem'].get('komisyon', 0) for k in kapananlar)
        net_pnl = sum(k['islem'].get('net_pnl', 0) for k in kapananlar)

        bloklar = [
            "📊 1 SAATLİK RAPOR",
            "─────────────────────",
            f"🕐 {baslangic.strftime('%d.%m.%Y %H:%M')} - {simdi.strftime('%H:%M')}",
            "",
            self._acik_pozisyonlar_blogu(),
            "",
            f"📉 KAPANAN POZİSYONLAR ({len(kapananlar)})",
            self._z_raporu_thread_kutulari(kapananlar),
            "",
            self._cikis_turu_dagilimi_kutusu(kapananlar),
            "",
            self._ozet_kutusu("SAAT", kapananlar, 60, brut_pnl, komisyon, net_pnl),
            "",
        ]
        if bakiye_anlik:
            bloklar.append(self._bakiye_kutusu("Saat Başı", self.baslangic_bakiyesi or 0, bakiye_anlik))
        bloklar.append("─────────────────────")

        mesaj = "<pre>" + "\n".join(bloklar) + "</pre>"
        self.notifier.rapor_gonder(mesaj)

    def _rapor_8saat(self):
        simdi = datetime.now()
        baslangic = simdi - timedelta(hours=8)
        kapananlar = self.notifier.kapanan_islemler_al(baslangic)
        bakiye_anlik = self._bakiye_anlik() or 0

        brut_pnl = sum(k['islem'].get('brut_pnl', 0) for k in kapananlar)
        komisyon = sum(k['islem'].get('komisyon', 0) for k in kapananlar)
        net_pnl = sum(k['islem'].get('net_pnl', 0) for k in kapananlar)

        # Saatlik performans (son 8 saat)
        with self._saatlik_lock:
            saatlik_satirlar = ["📊 SAATLİK PERFORMANS", "┌─────────────────────┐"]
            for i in range(8):
                saat = (simdi - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
                pnl = self._saatlik_pnl.get(saat, 0)
                isaret = '+' if pnl >= 0 else ''
                saatlik_satirlar.append(f"│ {saat.strftime('%H:%M')} : {isaret}{pnl:.2f} USDT")
            saatlik_satirlar.append("└─────────────────────┘")

        bloklar = [
            "📊 8 SAATLİK RAPOR",
            "─────────────────────",
            f"🕐 {baslangic.strftime('%d.%m.%Y %H:%M')} - {simdi.strftime('%H:%M')}",
            "",
            self._acik_pozisyonlar_blogu(),
            "",
            f"📉 KAPANAN POZİSYONLAR ({len(kapananlar)})",
            self._z_raporu_thread_kutulari(kapananlar),
            "",
            self._cikis_turu_dagilimi_kutusu(kapananlar),
            "",
            self._coin_dagilimi_kutusu(kapananlar),
            "",
            self._ozet_kutusu("8 SAAT", kapananlar, 60 * 8, brut_pnl, komisyon, net_pnl),
            "",
        ]
        if bakiye_anlik:
            bloklar.append(self._bakiye_kutusu("Dönem Başı", self.baslangic_bakiyesi or 0, bakiye_anlik))
        bloklar.extend([
            "",
            "\n".join(saatlik_satirlar),
            "─────────────────────",
        ])

        mesaj = "<pre>" + "\n".join(bloklar) + "</pre>"
        self.notifier.rapor_gonder(mesaj)

    def _rapor_24saat(self):
        simdi = datetime.now()
        baslangic = simdi - timedelta(hours=24)
        kapananlar = self.notifier.kapanan_islemler_al(baslangic)
        bakiye_anlik = self._bakiye_anlik() or 0

        brut_pnl = sum(k['islem'].get('brut_pnl', 0) for k in kapananlar)
        komisyon = sum(k['islem'].get('komisyon', 0) for k in kapananlar)
        net_pnl = sum(k['islem'].get('net_pnl', 0) for k in kapananlar)

        with self._saatlik_lock:
            # 24 saat tum saatler
            saatlik_satirlar = ["📊 SAATLİK PERFORMANS", "┌─────────────────────┐"]
            for i in range(24):
                saat = (simdi - timedelta(hours=23 - i)).replace(minute=0, second=0, microsecond=0)
                pnl = self._saatlik_pnl.get(saat, 0)
                isaret = '+' if pnl >= 0 else ''
                saatlik_satirlar.append(f"│ {saat.strftime('%H:%M')} : {isaret}{pnl:.2f} USDT")
            saatlik_satirlar.append("└─────────────────────┘")

            # 8 saatlik dilimler
            sekiz_satirlar = ["📊 8 SAATLİK PERFORMANS", "┌─────────────────────┐"]
            for dilim in range(3):
                dilim_baslangic = simdi - timedelta(hours=24 - dilim * 8)
                dilim_bitis = dilim_baslangic + timedelta(hours=8)
                pnl_toplam = 0
                for k in kapananlar:
                    if dilim_baslangic <= k['zaman'] < dilim_bitis:
                        pnl_toplam += k['islem'].get('net_pnl', 0)
                isaret = '+' if pnl_toplam >= 0 else ''
                sekiz_satirlar.append(
                    f"│ {dilim_baslangic.strftime('%H:%M')}-{dilim_bitis.strftime('%H:%M')}: {isaret}{pnl_toplam:.2f} USDT"
                )
            sekiz_satirlar.append("└─────────────────────┘")

        bloklar = [
            "📊 24 SAATLİK RAPOR",
            "─────────────────────",
            f"🕐 {baslangic.strftime('%d.%m.%Y %H:%M')} - {simdi.strftime('%d.%m.%Y %H:%M')}",
            "",
            self._acik_pozisyonlar_blogu(),
            "",
            f"📉 KAPANAN POZİSYONLAR ({len(kapananlar)})",
            self._z_raporu_thread_kutulari(kapananlar),
            "",
            self._cikis_turu_dagilimi_kutusu(kapananlar),
            "",
            self._coin_dagilimi_kutusu(kapananlar),
            "",
            self._ozet_kutusu("24 SAAT", kapananlar, 60 * 24, brut_pnl, komisyon, net_pnl),
            "",
        ]
        if bakiye_anlik:
            bloklar.append(self._bakiye_kutusu("Gün Başı", self.baslangic_bakiyesi or 0, bakiye_anlik))
        bloklar.extend([
            "",
            "\n".join(sekiz_satirlar),
            "",
            "\n".join(saatlik_satirlar),
            "─────────────────────",
        ])

        mesaj = "<pre>" + "\n".join(bloklar) + "</pre>"
        self.notifier.rapor_gonder(mesaj)

        # 24 saatlik rapordan sonra eski kayitlari temizle (24 saatten eski)
        self.notifier.havuz_temizle(simdi - timedelta(hours=24))
        # Saatlik PNL'den de 24 saatten eski olanlari temizle
        with self._saatlik_lock:
            esik = simdi - timedelta(hours=24)
            self._saatlik_pnl = defaultdict(float, {
                k: v for k, v in self._saatlik_pnl.items() if k >= esik
            })
