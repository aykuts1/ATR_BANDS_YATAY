"""
Trading Bot Configuration
Tüm ayarlar burada. API key'leri .env dosyasından okunur.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# BYBIT API
# ============================================================
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

# ============================================================
# TELEGRAM
# ============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# COIN LISTESI (26 adet, Bybit Futures sembolleri)
# ============================================================
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "SUIUSDT", "ZECUSDT", "DOGEUSDT", "TONUSDT", "LAYERUSDT",
    "TRXUSDT", "AAVEUSDT", "ADAUSDT", "LTCUSDT", "LINKUSDT",
    "APTUSDT", "INJUSDT", "AVAXUSDT", "NEARUSDT",
    "1000PEPEUSDT", "1000SHIBUSDT",
    "MEGAUSDT", "ONDOUSDT", "HYPEUSDT", "UNIUSDT", "ASTERUSDT",
]

# ============================================================
# STRATEJI PARAMETRELERI
# ============================================================
TIMEFRAME = "30"   # 30 dakikalık (KDJ + RSI bu mumlarda kontrol)
MTF_4H = "240"     # 4 saatlik (yön onayı için)

# Indikatör periyotları
ATR_PERIOD = 14
RSI_FAST = 6
RSI_SLOW = 14
KDJ_PERIOD = 9
KDJ_K = 3
KDJ_D = 3

# ============================================================
# RİSK YONETIMI
# ============================================================
LEVERAGE = 10
STAKE_PERCENT = 20         # Bakiyenin %20'si stake olarak
SL_ATR_MULTIPLIER = 1.5    # Borsa SL: giriş ± (1.5 × ATR)
MAX_OPEN_POSITIONS = 5

# ============================================================
# CHANDELIER EXIT (ATR bazlı) - 3 KADEMELİ YENİ SİSTEM
# ============================================================
# Birinci eşik: Kâr ≥ 0.5 ATR
SL_LOCK_TRIGGER_ATR = 0.5      # Borsa SL +0.1 ATR'ye çekilir + CE aktif olur
SL_LOCK_OFFSET_ATR = 0.1       # Borsa SL: giriş ± (0.1 × ATR) — kâr kilidi
CE_ACTIVATION_ATR = 0.5        # CE aktif olur (SL_LOCK_TRIGGER_ATR ile aynı)
CE_INITIAL_TRAIL_ATR = 1.0     # CE: 1.0 ATR geriden takip

# İkinci eşik: Kâr ≥ 1.5 ATR
CE_MID_TRIGGER_ATR = 1.5       # CE 0.75 ATR'ye sıkışır
CE_MID_TRAIL_ATR = 0.75

# Üçüncü eşik: Kâr ≥ 2.0 ATR
CE_TIGHT_TRIGGER_ATR = 2.0     # CE 0.5 ATR'ye sıkışır (son durak)
CE_TIGHT_TRAIL_ATR = 0.5

# ============================================================
# EMİR TİPİ (LIMIT - market gibi davranan)
# ============================================================
LIMIT_PRICE_OFFSET_PCT = 0.1   # Limit fiyat: anlık fiyat ± %0.1

# ============================================================
# DONGU AYARLARI
# ============================================================
EXIT_CHECK_INTERVAL = 60   # 60 saniye

# Logging
LOG_LEVEL = "INFO"
