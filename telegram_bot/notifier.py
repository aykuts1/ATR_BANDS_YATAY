"""
Telegram bildirim modulu.

Gorevleri:
- Anlik bildirimleri Telegram'a gonderir (islem acilis/kapanis, seviye gecisi, hata, yetersiz bakiye)
- Kapanan islemleri rapor icin tutar (Reporter erisir)
- Mesajlari ayri bir threadte gonderir (bot ana akisini bloklamasin)
- Telegram API hatasi olursa internal log'a yazar, bot durmaz

Thread emojileri:
- 🔴 KIRMIZI
- 🔵 MAVI
- 🟣 MOR
- 🟡 SARI
"""
import os
import threading
import queue
import time
import urllib.parse
import urllib.request
import json as jsonlib
from datetime import datetime


THREAD_EMOJI = {
    'KIRMIZI': '🔴',
    'MAVI': '🔵',
    'MOR': '🟣',
    'SARI': '🟡',
}


class TelegramNotifier:
    def __init__(self, config):
        """
        config: config.json dict
        Bot token ve chat_id environment variables'tan alinir:
          TELEGRAM_BOT_TOKEN
          TELEGRAM_CHAT_ID
        """
        self.config = config
        self.aktif = config.get('telegram_aktif', True)
        self.timeframe = config.get('timeframe', '30')

        self.token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')

        if self.aktif and (not self.token or not self.chat_id):
            print("UYARI: TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID eksik, Telegram pasif")
            self.aktif = False

        # Mesaj kuyrugu (thread-safe)
        self._kuyruk = queue.Queue()
        self._durdur = threading.Event()
        self._gonderici_thread = None

        # Kapanan islemler havuzu (rapor icin)
        # liste: [{islem dict, kapanis_zamani}, ...]
        self._kapanan_havuzu = []
        self._havuz_lock = threading.RLock()

    def baslat(self):
        """Mesaj gonderici thread'i baslat."""
        if not self.aktif:
            return
        self._gonderici_thread = threading.Thread(
            target=self._gonderici_dongu, name='TelegramGonderici', daemon=True
        )
        self._gonderici_thread.start()

    def durdur(self):
        self._durdur.set()
        # son mesajlari da yollamak icin biraz bekle
        self._kuyruk.put(None)

    def _gonderici_dongu(self):
        """Kuyruktan mesajlari alip Telegram'a yollar."""
        while not self._durdur.is_set():
            try:
                mesaj = self._kuyruk.get(timeout=1.0)
                if mesaj is None:
                    break
                self._gercek_gonder(mesaj)
                # Telegram rate limit: saniyede max 30 mesaj. Aramiza biraz mesafe.
                time.sleep(0.05)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Telegram gonderici hata: {e}")

    def _gercek_gonder(self, mesaj):
        """Telegram Bot API'ye HTTP POST."""
        if not self.aktif:
            return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            data = urllib.parse.urlencode({
                'chat_id': self.chat_id,
                'text': mesaj,
                'parse_mode': 'HTML',
            }).encode('utf-8')
            req = urllib.request.Request(url, data=data, method='POST')
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except Exception as e:
            # Telegram hatasi konsola yazilir, ana akis bloklanmaz
            print(f"Telegram gonderim hatasi: {e}")

    def _gonder(self, mesaj):
        """Mesaji kuyruga at."""
        if not self.aktif:
            return
        try:
            self._kuyruk.put(mesaj)
        except Exception:
            pass

    # =========================================================================
    # YARDIMCI FORMAT
    # =========================================================================

    @staticmethod
    def _emoji(thread_adi):
        return THREAD_EMOJI.get(thread_adi, '⚪')

    @staticmethod
    def _zaman_str(dt=None):
        if dt is None:
            dt = datetime.now()
        return dt.strftime('%d.%m.%Y %H:%M:%S')

    @staticmethod
    def _fiyat_fmt(fiyat, ondalik=6):
        if fiyat is None:
            return '-'
        return f"{fiyat:.{ondalik}f}"

    @staticmethod
    def _isaret_fiyat(deger, ondalik=6):
        if deger is None:
            return '-'
        isaret = '+' if deger >= 0 else ''
        return f"{isaret}{deger:.{ondalik}f}"

    @staticmethod
    def _isaret_yuzde(deger, ondalik=2):
        isaret = '+' if deger >= 0 else ''
        return f"{isaret}{deger:.{ondalik}f}"

    # =========================================================================
    # 1. ISLEM ACILDI
    # =========================================================================

    def islem_acildi(self, islem, acik_sayisi, max_pozisyon):
        thread = islem['thread']
        emoji = self._emoji(thread)
        yon = islem['yon']
        sembol = islem['sembol']
        giris = islem['giris_fiyat']
        sl = islem['sl_fiyat']
        miktar = islem['miktar']
        atr = islem.get('atr', 1.0)
        kaldirac = self.config['kaldirac']
        stake = islem.get('stake_usdt', self.config['stake_yuzde'])  # bu hesaplanmali

        # stake'i Pozisyon'dan alma gerekirken islem'e eklemedik
        # Hesabi burada yaparim: stake = baslangic_bakiyesi * %
        # Daha basit: stake_usdt zaten config'ten alinir, miktarla esit degil ama yakin
        # Aslinda gerekli: islem hacmi = miktar * giris
        islem_hacmi = miktar * giris

        # SL mesafesi
        sl_mesafe_usdt = abs(giris - sl)
        sl_mesafe_yuzde = (sl_mesafe_usdt / giris) * 100
        sl_mesafe_atr = sl_mesafe_usdt / atr if atr > 0 else 0

        # Stake (islem hacmi / kaldirac)
        stake_real = islem_hacmi / kaldirac if kaldirac > 0 else 0

        mesaj = (
            f"<pre>"
            f"{emoji} {thread} {yon} AÇILDI\n"
            f"─────────────────────\n"
            f"🕐 Zaman: {self._zaman_str()}\n"
            f"📌 Coin: {sembol}\n"
            f"📊 Zaman Dilimi: {self.timeframe}m\n"
            f"\n"
            f"💰 POZİSYON\n"
            f"├ Giriş Fiyatı: {self._fiyat_fmt(giris)} USDT\n"
            f"├ Stake: {stake_real:.2f} USDT\n"
            f"├ İşlem Hacmi: {islem_hacmi:.2f} USDT ({kaldirac}x)\n"
            f"└ Miktar: {miktar}\n"
            f"\n"
            f"🛡 STOP-LOSS\n"
            f"├ SL Fiyatı: {self._fiyat_fmt(sl)} USDT\n"
            f"└ SL Mesafesi: {sl_mesafe_usdt:.6f} USDT / %{sl_mesafe_yuzde:.2f} / {sl_mesafe_atr:.2f} ATR\n"
            f"\n"
            f"🎰 SLOT: {acik_sayisi}/{max_pozisyon}\n"
            f"─────────────────────"
            f"</pre>"
        )
        self._gonder(mesaj)

    # =========================================================================
    # 2. ISLEM KAPANDI
    # =========================================================================

    def islem_kapandi(self, islem, acik_sayisi, max_pozisyon):
        thread = islem['thread']
        emoji = self._emoji(thread)
        yon = islem['yon']
        sembol = islem['sembol']
        giris = islem['giris_fiyat']
        cikis = islem['cikis_fiyat']
        cikis_tipi = islem['cikis_tipi']
        seviye = islem['seviye']
        brut_pnl = islem['brut_pnl']
        komisyon = islem['komisyon']
        net_pnl = islem['net_pnl']
        atr = islem.get('atr', 1.0)
        miktar = islem['miktar']

        # USDT, %, ATR farki
        if yon == 'LONG':
            fark_usdt = (cikis - giris) * miktar
        else:
            fark_usdt = (giris - cikis) * miktar
        fark_yuzde = ((cikis - giris) / giris * 100) if giris != 0 else 0
        if yon == 'SHORT':
            fark_yuzde = -fark_yuzde
        fark_atr = abs(cikis - giris) / atr if atr > 0 else 0

        mesaj = (
            f"<pre>"
            f"{emoji} {thread} {yon} KAPANDI\n"
            f"─────────────────────\n"
            f"🕐 Zaman: {self._zaman_str()}\n"
            f"📌 Coin: {sembol}\n"
            f"📊 Zaman Dilimi: {self.timeframe}m\n"
            f"\n"
            f"📈 POZİSYON\n"
            f"├ Giriş Fiyatı: {self._fiyat_fmt(giris)} USDT\n"
            f"├ Çıkış Fiyatı: {self._fiyat_fmt(cikis)} USDT\n"
            f"├ Çıkış Tipi: {cikis_tipi}\n"
            f"└ Seviye: {seviye}\n"
            f"\n"
            f"💰 PNL\n"
            f"├ Kazanç: {self._isaret_fiyat(fark_usdt)} USDT / %{self._isaret_yuzde(fark_yuzde)} / {fark_atr:.2f} ATR\n"
            f"├ Komisyon: -{komisyon:.6f} USDT\n"
            f"└ Net PNL: {self._isaret_fiyat(net_pnl)} USDT\n"
            f"\n"
            f"🎰 SLOT: {acik_sayisi}/{max_pozisyon}\n"
            f"─────────────────────"
            f"</pre>"
        )
        self._gonder(mesaj)

    # =========================================================================
    # 3. SEVIYE GECISI
    # =========================================================================

    def seviye_gecis(self, islem, eski_seviye, yeni_seviye, anlik_fiyat, atr):
        thread = islem['thread']
        emoji = self._emoji(thread)
        yon = islem['yon']
        sembol = islem['sembol']
        giris = islem['giris_fiyat']
        miktar = islem['miktar']

        if yon == 'LONG':
            fark_usdt = (anlik_fiyat - giris) * miktar
        else:
            fark_usdt = (giris - anlik_fiyat) * miktar
        fark_yuzde = ((anlik_fiyat - giris) / giris * 100) if giris != 0 else 0
        if yon == 'SHORT':
            fark_yuzde = -fark_yuzde
        fark_atr = abs(anlik_fiyat - giris) / atr if atr > 0 else 0

        # acik_sayisi'ni almak icin pozisyon yoneticisinden gelmiyor burada,
        # bu bilgi parametre olarak gelmiyor. Telegram'a basitce bilgisiz gonderelim.
        # Aslinda pozisyon yoneticisinden alabiliriz - sonra ekleyelim. Su an basit gonderiyoruz.

        mesaj = (
            f"<pre>"
            f"{emoji} {thread} {yon} SEVİYE GEÇİŞİ\n"
            f"─────────────────────\n"
            f"🕐 Zaman: {self._zaman_str()}\n"
            f"📌 Coin: {sembol}\n"
            f"📊 Zaman Dilimi: {self.timeframe}m\n"
            f"\n"
            f"📊 SEVİYE\n"
            f"├ Önceki: {eski_seviye}\n"
            f"└ Yeni: {yeni_seviye}\n"
            f"\n"
            f"📈 DURUM\n"
            f"├ Giriş Fiyatı: {self._fiyat_fmt(giris)} USDT\n"
            f"├ Anlık Fiyat: {self._fiyat_fmt(anlik_fiyat)} USDT\n"
            f"└ Fark: {self._isaret_fiyat(fark_usdt)} USDT / %{self._isaret_yuzde(fark_yuzde)} / {fark_atr:.2f} ATR\n"
            f"─────────────────────"
            f"</pre>"
        )
        self._gonder(mesaj)

    # =========================================================================
    # 4. HATA
    # =========================================================================

    def hata(self, thread_adi, mesaj_detay):
        emoji = self._emoji(thread_adi)
        # Hata mesajinin uzunlugunu sinirla (Telegram 4096 karakter limiti)
        if len(mesaj_detay) > 500:
            mesaj_detay = mesaj_detay[:500] + '...'

        mesaj = (
            f"<pre>"
            f"⚠️ HATA\n"
            f"─────────────────────\n"
            f"🕐 Zaman: {self._zaman_str()}\n"
            f"{emoji} Thread: {thread_adi}\n"
            f"\n"
            f"❌ Hata: {mesaj_detay}\n"
            f"─────────────────────"
            f"</pre>"
        )
        self._gonder(mesaj)

    # =========================================================================
    # 5. YETERSIZ BAKIYE
    # =========================================================================

    def yetersiz_bakiye(self, thread_adi, sembol, yon, gerekli, mevcut, acik_sayisi, max_pozisyon):
        emoji = self._emoji(thread_adi)
        eksik = gerekli - mevcut

        mesaj = (
            f"<pre>"
            f"💸 YETERSİZ BAKİYE\n"
            f"─────────────────────\n"
            f"🕐 Zaman: {self._zaman_str()}\n"
            f"\n"
            f"❌ İşlem açılamadı\n"
            f"📌 Coin: {sembol}\n"
            f"{emoji} Thread: {thread_adi} {yon}\n"
            f"\n"
            f"💰 DURUM\n"
            f"├ Gerekli Stake: {gerekli:.2f} USDT\n"
            f"├ Mevcut Bakiye: {mevcut:.2f} USDT\n"
            f"└ Eksik: {eksik:.2f} USDT\n"
            f"\n"
            f"🎰 SLOT: {acik_sayisi}/{max_pozisyon}\n"
            f"─────────────────────"
            f"</pre>"
        )
        self._gonder(mesaj)

    # =========================================================================
    # GENEL MESAJ (bot baslangic vs)
    # =========================================================================

    def bilgi(self, mesaj):
        """Genel bilgi mesaji (bot baslangic, durum vs)."""
        tam_mesaj = f"<pre>ℹ️ {mesaj}</pre>"
        self._gonder(tam_mesaj)

    # =========================================================================
    # RAPOR ICIN: KAPANAN ISLEMLER HAVUZU
    # =========================================================================

    def kapanan_islem_kaydet(self, islem_dict):
        """Rapor icin kapanan islemleri tutar."""
        with self._havuz_lock:
            self._kapanan_havuzu.append({
                'islem': islem_dict,
                'zaman': datetime.now(),
            })

    def kapanan_islemler_al(self, baslangic_zamani):
        """
        baslangic_zamani'ndan beri kapanan islemleri doner (kopya).
        Eski olanlari da temizlemez - havuz daima son 24 saat tutulur (reporter temizler).
        """
        with self._havuz_lock:
            return [
                k for k in self._kapanan_havuzu
                if k['zaman'] >= baslangic_zamani
            ]

    def havuz_temizle(self, eski_olan_oncesi):
        """eski_olan_oncesi'nden eski kayitlari sil (rapor icin)."""
        with self._havuz_lock:
            self._kapanan_havuzu = [
                k for k in self._kapanan_havuzu
                if k['zaman'] >= eski_olan_oncesi
            ]

    # =========================================================================
    # HAM RAPOR GONDERIMI (Reporter cagirir)
    # =========================================================================

    def rapor_gonder(self, rapor_metni):
        """Reporter'dan gelen hazir raporu gonderir."""
        self._gonder(rapor_metni)
