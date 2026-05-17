# Ema100-Ema21-Tunel Bot

Bybit USDT perpetual futures üzerinde 5 dakikalık zaman diliminde **EMA100 tüneli filtre** + **EMA21 tüneli sinyal** stratejisi ile scalping yapan otomatik bot. Railway worker olarak çalışmak üzere tasarlanmıştır.

---

## 📊 Strateji Özeti

### Tüneller
- **EMA100 Tüneli** (FİLTRE): High'ların EMA100'ü (üst) + Low'ların EMA100'ü (alt)
- **EMA21 Tüneli** (SİNYAL): High'ların EMA21'i (üst) + Low'ların EMA21'i (alt)

### Filtre Onayı
- **LONG onayı**: EMA21 tüneli tamamen EMA100 üstünde (EMA21_low > EMA100_high)
- **SHORT onayı**: EMA21 tüneli tamamen EMA100 altında (EMA21_high < EMA100_low)
- **Onay yok**: EMA21'in herhangi bir çizgisi EMA100'e değiyorsa

### Giriş Tetikleyici
- **LONG**: Fiyat EMA21 alt çizgisini aşağıdan yukarıya keser → LONG aç
- **SHORT**: Fiyat EMA21 üst çizgisini yukarıdan aşağıya keser → SHORT aç

### Çıkış Mekanizmaları (her biri ayrı ayrı geçerli)

1. **Normal Çıkış**
   - SHORT: Fiyat EMA21 alt çizgiyi aşağı kesti, sonra yukarı kesti
   - LONG: Fiyat EMA21 üst çizgiyi yukarı kesti, sonra aşağı kesti

2. **Emniyet Kemeri (EMA100)**
   - SHORT: Fiyat EMA100 alt çizgisini yukarı keser
   - LONG: Fiyat EMA100 üst çizgisini aşağı keser

3. **Chandelier Exit (CE) - Trailing Stop**
   - Kar %0.5'i geçince aktif olur
   - En iyi fiyattan %0.5 geri dönüş → çıkış

### Risk Yönetimi
- **50x kaldıraç** (zorunlu - desteklemeyen coinler atlanır)
- **%20 stake** (bot başında bakiyenin %20'si, restart'a kadar sabit)
- **Maksimum 5** eş zamanlı işlem
- **Aynı coinde max 1** işlem
- **%1 SL** borsa tarafında (kaldıraçsız fiyat hareketi → 50x ile %50 stake kaybı)

### Emir Mantığı
- Önce **limit emir** (1 tick agresif, daha az komisyon)
- 5 saniye bekle, dolmadıysa iptal et
- 3 deneme sonrası **market emir**
- Her coinin tick size'ı otomatik çekilir

### Tarama
- Her **30 saniyede** tarama
- Canlı fiyat ile kesişim tespiti (mum kapanışı beklenmez)
- 5 dakikalık mum verisi EMA hesabı için kullanılır
- Klines cache 60 saniyede yenilenir

---

## 📁 Dosya Yapısı

```
.
├── config.py              # Environment variables ve parametreler
├── bybit_client.py        # Bybit v5 API wrapper (pybit)
├── indicators.py          # EMA hesaplamaları
├── strategy.py            # Giriş/çıkış sinyalleri
├── position_manager.py    # Açık pozisyon takibi
├── telegram_bot.py        # Telegram bildirimleri
├── main.py                # Ana giriş + tarama döngüsü
├── requirements.txt       # Python bağımlılıkları
├── Procfile               # Railway worker komutu
├── runtime.txt            # Python sürümü
├── .gitignore
└── README.md
```

---

## 🔧 Environment Variables

Railway'de aşağıdaki değişkenleri ayarla:

| Değişken | Zorunlu | Açıklama |
| --- | --- | --- |
| `BYBIT_API_KEY` | ✅ | Bybit API anahtarı |
| `BYBIT_API_SECRET` | ✅ | Bybit API gizli anahtarı |
| `TELEGRAM_BOT_TOKEN` | ✅ | Telegram bot token (BotFather'dan) |
| `TELEGRAM_CHAT_ID` | ✅ | Telegram chat ID (mesajların gönderileceği) |
| `SYMBOLS` | ❌ | Virgülle ayrılmış sembol listesi (boşsa default kullanılır) |
| `BYBIT_TESTNET` | ❌ | `true` ise testnet (default: `false`) |

---

## 📋 Default Coin Listesi (20 coin)

`SOLUSDT, AVAXUSDT, LINKUSDT, DOTUSDT, NEARUSDT, INJUSDT, OPUSDT, ARBUSDT, SUIUSDT, TONUSDT, APTUSDT, FTMUSDT, TIAUSDT, ENAUSDT, JTOUSDT, XRPUSDT, TRXUSDT, ATOMUSDT, ADAUSDT, ALGOUSDT`

50x desteklemeyen coinler bot başlangıcında otomatik atlanır ve Telegram'a bildirim atılır.

---

## 🔐 Bybit API İzinleri

API key'in şu izinlere sahip olmalı:
- ✅ **Contract / Unified Trading**: Orders + Positions
- ❌ **Withdraw**: KAPALI olmalı (güvenlik)

---

## 🚂 Railway Kurulumu

1. Bu repo'yu GitHub'a push et
2. Railway'de yeni proje → **"Deploy from GitHub repo"**
3. Repo'yu seç
4. **Settings → Variables** sekmesinden 4 zorunlu env var'ı ekle:
   - `BYBIT_API_KEY`
   - `BYBIT_API_SECRET`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. Deploy başlayacak; **Logs** sekmesinden takip et

Procfile sayesinde Railway otomatik olarak `worker: python main.py` komutunu çalıştırır.

---

## 💻 Lokal Test

```bash
# Python 3.11+
pip install -r requirements.txt

# .env oluştur
cat > .env <<EOF
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
BYBIT_TESTNET=true
EOF

# main.py üstüne ekle:
# from dotenv import load_dotenv; load_dotenv()

python main.py
```

---

## 📱 Telegram Bildirimleri

Bot şu durumlarda Telegram mesajı gönderir:

| Durum | Mesaj |
| --- | --- |
| 🚀 Bot başlangıcı | Bakiye, stake, kaldıraç, aktif coinler |
| ⚠️ 50x desteklenmiyor | Coin atlandı bildirimi |
| 🟢 İşlem açıldı | Coin, yön, fiyat, miktar, stake, hacim, SL |
| 🔴 İşlem kapandı | Giriş/çıkış, PNL, kapanış nedeni |
| ⚠️ İşlem açılamadı | Coin, neden (yetersiz bakiye, slot dolu, vs.) |
| 📊 5 dakikalık özet | Bakiye, açık işlemler, tarama, son işlemler, seans PNL |
| 🚨 Hata | Anomali / API hatası |

---

## ⚠️ Önemli Notlar

- **Bot Unified Trading hesabı kullanır.**
- **Pozisyon modu**: One-way (positionIdx=0) varsayılır.
- **Stake bot başlangıcında sabitlenir.** Bakiye değişse de stake değişmez. Stake'i güncellemek için botu restart et.
- **Bot restart sonrası** açık pozisyonlar EXTERNAL olarak işaretlenir, bot bunlara dokunmaz. Sadece slot sayısı için sayılır. Borsadaki %1 SL korumaya devam eder.
- **CE seviyesi sadece bot hafızasında tutulur** (Bybit'te değil). Bot restart olursa CE state kaybolur.
- **API rate limit**: Bot 30 saniyede 20 coin × 1-2 API çağrısı yapar; Bybit limitleri içinde rahat çalışır.

---

## 🎯 Hedef Karlılık Senaryosu ($1000 bakiye)

- Stake: $200 (bakiyenin %20'si)
- Hacim: $10,000 (50x kaldıraç)
- Karlı işlem ortalaması: %40 stake getiri ($80)
- Zararlı işlem (SL): %50 stake kayıp ($100)
- 5 işlem / gün senaryosu (3 kar + 2 zarar): $240 - $200 = **$40 günlük net**
- Aylık tahmini: **%80-120 net kar** (piyasa koşullarına göre)

---

## ⚠️ Risk Uyarısı

Bu bot finansal tavsiye değildir. Kripto vadeli işlemler **yüksek risklidir**, sermayenizin tamamını kaybedebilirsiniz. Önce **testnet'te** veya **düşük tutarlarla** test edin. Yazılım hataları, ağ kesintileri, borsa kesintileri vb. nedenlerle beklenmedik kayıplar oluşabilir. **Kullanım kendi sorumluluğunuzdadır.**
