# Trade Bot

Bybit Futures uzerinde calisan 4 thread'li (Kirmizi, Mavi, Mor, Sari) bant stratejisi botu.

## Klasor yapisi

```
bot/
├── config.json           # Tum parametreler (gruplandirilmis, aciklamali)
├── main.py               # Bot giris noktasi
├── requirements.txt
├── Procfile              # Render worker tanimi
├── shared/
│   ├── bybit_client.py   # Bybit API + retry mekanizmasi
│   ├── market_data.py    # Merkezi veri yoneticisi (mum + fiyat)
│   └── pozisyon_yoneticisi.py
├── threads/
│   ├── kirmizi_thread.py
│   ├── mavi_thread.py
│   ├── mor_thread.py
│   └── sari_thread.py
├── telegram_bot/
│   ├── notifier.py       # Anlik bildirimler
│   └── reporter.py       # Periyodik raporlar
└── utils/
    ├── indicators.py     # EMA, ATR
    ├── bands.py          # 37 bant cizgisi
    └── crossover.py      # Yukari/asagi kirilma tespiti
```

## Environment Variables (zorunlu)

- `BYBIT_API_KEY`
- `BYBIT_API_SECRET`
- `TELEGRAM_BOT_TOKEN`  (opsiyonel - eksikse Telegram pasif)
- `TELEGRAM_CHAT_ID`    (opsiyonel)

## Yerel calistirma

```bash
pip install -r requirements.txt
export BYBIT_API_KEY=xxxx
export BYBIT_API_SECRET=yyyy
export TELEGRAM_BOT_TOKEN=zzzz
export TELEGRAM_CHAT_ID=12345
python3 main.py
```

## Render deploy

1. Bu repoyu GitHub'a yukleyin
2. Render'da "Background Worker" servisi olarak baglayin
3. Environment variables'i Render dashboard'dan ekleyin
4. Build: `pip install -r requirements.txt`
5. Start: `python main.py` (zaten Procfile'da)

## Config

Tum parametreler `config.json` icinde, gruplandirilmis ve aciklamali. Her ayar yaninda `_aciklama` ile aciklamasi var.

Onemli ayarlar:
- `timeframe`: 5 / 15 / 30 / 60 (dakika, varsayilan 30)
- `stake_yuzde`: 5 (baslangic bakiyesinin yuzdesi)
- `kaldirac`: 20
- `sl_yuzde`: 1 (emniyet kemeri SL)
- `max_pozisyon`: 20
- `coinler`: 10 coin

## Threadler

Hepsi config'ten ayri ayri acilip kapatilabilir (`thread_*_aktif`).
