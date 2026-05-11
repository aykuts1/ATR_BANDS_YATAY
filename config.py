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
ADX_PERIOD = 14
RSI_FAST = 6
RSI_SLOW = 14
KDJ_PERIOD = 9
KDJ_K = 3
KDJ_D = 3
# Yatay piyasa filtreleri (30dk mumunda, sinyalle aynı mum)
ADX_MIN = 20.0         # ADX > 20 olmayan piyasada giriş yok (trendsiz/yatay eler)
ATR_MIN_PCT = 0.5      # ATR% > 0.5 olmayan piyasada giriş yok (ölü volatilite eler)
# ============================================================
# RİSK YONETIMI
# ============================================================
LEVERAGE = 10
STAKE_PERCENT = 20         # Bakiyenin %20'si stake olarak
SL_ATR_MULTIPLIER = 1.0    # Borsa SL: giriş ± (1.0 × ATR)
MAX_OPEN_POSITIONS = 5
# ============================================================
# CHANDELIER EXIT (ATR bazlı) - 2 KADEMELİ SISTEM
# ============================================================
# Giriş (Kademe 0): CE giriş anında AKTİF, 1.0 ATR geriden takip
CE_INITIAL_TRAIL_ATR = 1.0     # CE: giriş anında 1.0 ATR geriden takip başlar
# Birinci eşik: Kâr ≥ 0.5 ATR
SL_LOCK_TRIGGER_ATR = 0.5      # Borsa SL +0.2 ATR'ye çekilir (kâr kilidi)
SL_LOCK_OFFSET_ATR = 0.2       # Borsa SL: giriş ± (0.2 × ATR) — kâr kilidi
# Bu kademede CE değişmez (1.0 ATR trail aynen devam eder)
# İkinci eşik: Kâr ≥ 1.0 ATR
CE_TIGHT_TRIGGER_ATR = 1.0     # CE 0.5 ATR'ye sıkışır (son durak)
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
