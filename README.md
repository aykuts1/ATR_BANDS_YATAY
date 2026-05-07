# Trading Bot - Bybit Futures

5 filtre + Chandelier Exit stratejisi ile çalışan otomatik bot.

## Strateji Özeti

**Giriş Filtreleri (her 30 dakikalık mum kapanışında):**
- ATR% ≥ 0.7
- ADX > 25
- 2H ve 4H aktif mum açılışına göre yön uyumu
- KDJ kesişimi (J, K ve D'yi keser)
- RSI(6) RSI(14) kesişimi

**Risk:**
- Bakiyenin %20'si stake (bot başlangıcında sabitlenir)
- 5x kaldıraç
- %3 stop loss (borsada)
- Max 5 açık pozisyon
- Aynı coinde tek pozisyon

**Çıkış (Chandelier Exit):**
- Başlangıç: 3 ATR mesafe
- +%1 kâr: SL → giriş (breakeven)
- +%2 kâr: CE = 2 ATR
- +%3 kâr: CE = 1 ATR
- +%4+ kâr: CE = 0.5 ATR
- CE asla geri çekilmez

## Coinler
SOL, XRP, DOGE, TRX, LTC, ADA, LINK, AVAX, SHIB, SUI, PEPE, TON, NEAR, APT, INJ

## Kurulum

### 1. Bybit API Key
- bybit.com → API Management
- Read + Trade izinleri açık, Withdraw KAPALI
- IP whitelist (Railway IP'sini ekle)

### 2. Telegram
- @BotFather'dan bot oluştur, token al
- Bot'a bir mesaj gönder, sonra @userinfobot ile chat ID al

### 3. Lokal Test (isteğe bağlı)
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# .env'yi düzenle
python main.py
```

### 4. Railway Deploy
1. GitHub'a repo olarak push et (`.env` dahil etme!)
2. Railway → New Project → Deploy from GitHub
3. Variables sekmesinde environment değişkenlerini ekle:
   - `BYBIT_API_KEY`
   - `BYBIT_API_SECRET`
   - `BYBIT_TESTNET` (false)
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Deploy

## Bybit Hesap Ayarları (Önemli!)

Bot çalışmadan önce Bybit'te:
1. **Unified Trading Account** aktif olmalı (varsayılan zaten aktif)
2. **Position Mode: One-Way** (Hedge mode değil)
3. **Margin Mode: Cross veya Isolated** (Bot her ikisinde de çalışır, Cross önerilir)
4. Her sembol için kaldıraç bot tarafından otomatik 5x'e ayarlanacak

## Dosya Yapısı

```
trading_bot/
├── config.py            # Tüm ayarlar
├── indicators.py        # ATR, ADX, RSI, KDJ
├── filters.py           # 6 giriş filtresi
├── exchange.py          # Bybit API wrapper
├── telegram_bot.py      # Telegram bildirimleri
├── position_manager.py  # CE takibi, breakeven, state
├── main.py              # Ana döngü
├── requirements.txt
├── Procfile             # Railway worker config
├── runtime.txt          # Python sürümü
├── .env.example
└── .gitignore
```

## Önemli Notlar

- Bot her başlatıldığında bakiye yeniden okunur, stake yeniden sabitlenir.
- Pozisyon state'i `positions_state.json` dosyasında tutulur, bot restart'ta kaldığı yerden devam eder.
- CE seviyesi asla geri çekilmez (bir kez yukarı/aşağı gitti mi kalır).
- Stop loss borsada, CE botta yönetilir. İkisi paralel çalışır.

## Telegram Bildirimleri

Bot şunları bildirir:
- 🤖 Başlangıç (bakiye + stake)
- 🟢/🔴 Pozisyon açıldı
- 🔒 Breakeven aktifleşti
- ✅/❌ Pozisyon kapandı (PnL ile)
- ⚠️ Yetersiz bakiye
- 🚨 Hatalar
