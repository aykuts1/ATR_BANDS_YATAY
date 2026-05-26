"""
Bot ana giris noktasi.

Calistirma:
  python3 main.py

Gerekli environment variables:
  BYBIT_API_KEY
  BYBIT_API_SECRET
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""
import json
import os
import sys
import time
import signal
import traceback
from datetime import datetime

# Modulleri import et
from shared.bybit_client import BybitClient
from shared.market_data import MarketData
from shared.pozisyon_yoneticisi import PozisyonYoneticisi
from telegram_bot.notifier import TelegramNotifier
from telegram_bot.reporter import Reporter
from threads.kirmizi_thread import KirmiziThread
from threads.mavi_thread import MaviThread
from threads.mor_thread import MorThread
from threads.sari_thread import SariThread


def config_yukle(yol='config.json'):
    with open(yol, encoding='utf-8') as f:
        return json.load(f)


def env_kontrol():
    """Environment variables kontrol."""
    eksik = []
    for ad in ['BYBIT_API_KEY', 'BYBIT_API_SECRET']:
        if not os.getenv(ad):
            eksik.append(ad)
    if eksik:
        print(f"HATA: Eksik environment variables: {', '.join(eksik)}")
        sys.exit(1)

    # Telegram opsiyonel - eksikse pasif olur, ama uyari ver
    if not os.getenv('TELEGRAM_BOT_TOKEN') or not os.getenv('TELEGRAM_CHAT_ID'):
        print("UYARI: TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID yok, Telegram pasif olacak")


def main():
    print(f"[{datetime.now()}] Bot baslatiliyor...")
    env_kontrol()

    # 1. Config yukle
    config = config_yukle()
    print(f"[{datetime.now()}] Config yuklendi. {len(config.get('coinler', []))} coin, timeframe={config['timeframe']}m")

    # 2. Telegram baslat (diger modullere referans verecek)
    telegram = TelegramNotifier(config)
    telegram.baslat()

    # 3. Bybit client (telegram referansiyla, hata gondersin)
    bybit = BybitClient(config, telegram_bildirim=lambda m: telegram.hata('SISTEM', m))

    # 4. Baslangicta hesap durumunu al
    try:
        bakiye_baslangic = bybit.bakiye_al()
        print(f"[{datetime.now()}] Baslangic bakiyesi: {bakiye_baslangic:.2f} USDT")
    except Exception as e:
        print(f"HATA: Baslangic bakiyesi alinamadi: {e}")
        telegram.hata('SISTEM', f"Bot baslangicta bakiye alinamadi: {e}")
        sys.exit(1)

    if bakiye_baslangic <= 0:
        print("HATA: Bakiye 0 veya negatif, bot durduruluyor")
        telegram.hata('SISTEM', f"Bakiye yetersiz: {bakiye_baslangic}")
        sys.exit(1)

    # 5. Coinler icin kaldirac + hedge mode ayarla (idempotent)
    print(f"[{datetime.now()}] Coinler icin kaldirac ve hedge mode ayarlaniyor...")
    for sembol in config['coinler']:
        try:
            if config.get('hedge_mode', True):
                bybit.hedge_mode_ayarla(sembol)
            bybit.kaldirac_ayarla(sembol, config['kaldirac'])
        except Exception as e:
            print(f"  UYARI: {sembol} ayar hatasi (devam ediliyor): {e}")

    # 6. Pozisyon yoneticisi
    poz = PozisyonYoneticisi(config, bybit)
    poz.baslat(bakiye_baslangic)
    print(f"[{datetime.now()}] Pozisyon yoneticisi hazir. Stake: {poz.stake_usdt:.2f} USDT")

    # 7. Market data (mum + fiyat dongusu)
    market = MarketData(config, bybit, telegram_bildirim=lambda m: telegram.hata('SISTEM', m))
    market.baslat()
    print(f"[{datetime.now()}] Market data baslatildi, ilk veriler bekleniyor...")

    # 8. Ilk veriler hazir olana kadar bekle
    if not market.hazir_bekle(timeout=180):
        print("HATA: 3 dakika icinde market data hazir olmadi, bot durduruluyor")
        telegram.hata('SISTEM', 'Market data 3 dk icinde hazir olmadi')
        sys.exit(1)
    print(f"[{datetime.now()}] Market data hazir.")

    # 9. Telegram bot baslangic mesaji
    telegram.bilgi(
        f"Bot başladı\n"
        f"Bakiye: {bakiye_baslangic:.2f} USDT\n"
        f"Stake: {poz.stake_usdt:.2f} USDT/işlem\n"
        f"Kaldıraç: {config['kaldirac']}x\n"
        f"Timeframe: {config['timeframe']}m\n"
        f"Coin sayısı: {len(config['coinler'])}\n"
        f"Max pozisyon: {config['max_pozisyon']}"
    )

    # 10. 4 thread'i baslat (config'te aktif olanlari)
    threadler = []
    if config.get('thread_kirmizi_aktif', True):
        t = KirmiziThread(config, market, poz, bybit, telegram)
        t.baslat()
        threadler.append(t)
        print(f"[{datetime.now()}] KIRMIZI thread baslatildi")

    if config.get('thread_mavi_aktif', True):
        t = MaviThread(config, market, poz, bybit, telegram)
        t.baslat()
        threadler.append(t)
        print(f"[{datetime.now()}] MAVI thread baslatildi")

    if config.get('thread_mor_aktif', True):
        t = MorThread(config, market, poz, bybit, telegram)
        t.baslat()
        threadler.append(t)
        print(f"[{datetime.now()}] MOR thread baslatildi")

    if config.get('thread_sari_aktif', True):
        t = SariThread(config, market, poz, bybit, telegram)
        t.baslat()
        threadler.append(t)
        print(f"[{datetime.now()}] SARI thread baslatildi")

    # 11. Reporter
    reporter = Reporter(config, telegram, poz, bybit)
    reporter.baslat(bakiye_baslangic)
    print(f"[{datetime.now()}] Reporter baslatildi (15dk/1s/8s/24s)")

    # 12. Ana dongu - tum threadler arkaplanda, biz sadece signal bekleyelim
    print(f"[{datetime.now()}] Bot tam aktif. Ctrl+C ile durdurabilirsiniz.")

    durdur_istegi = False

    def sigint_handler(sig, frame):
        nonlocal durdur_istegi
        print(f"\n[{datetime.now()}] Durdurma istegi alindi...")
        durdur_istegi = True

    signal.signal(signal.SIGINT, sigint_handler)
    signal.signal(signal.SIGTERM, sigint_handler)

    try:
        while not durdur_istegi:
            time.sleep(1)
    except Exception as e:
        print(f"Ana dongu hatasi: {e}")
        traceback.print_exc()

    # 13. Temiz kapanis
    print(f"[{datetime.now()}] Threadler durduruluyor...")
    for t in threadler:
        t.durdur()
    market.durdur()
    reporter.durdur()
    telegram.bilgi("Bot durduruluyor")
    time.sleep(2)
    telegram.durdur()
    print(f"[{datetime.now()}] Bot durduruldu.")


if __name__ == '__main__':
    main()
