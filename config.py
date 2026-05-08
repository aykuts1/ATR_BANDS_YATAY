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
# COIN LISTESI
# ============================================================
SYMBOLS = [
    "SOLUSDT", "XRPUSDT", "DOGEUSDT", "TRXUSDT", "LTCUSDT",
    "ADAUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT",
    "1000PEPEUSDT", "TONUSDT", "NEARUSDT", "APTUSDT", "INJUSDT",
]

# ============================================================
# STRATEJI PARAMETRELERI
# ============================================================
TIMEFRAME = "30"  # Bybit format: 30 dakika
MTF_2H = "120"    # 2 saatlik
MTF_4H = "240"    # 4 saatlik

# Indikatör periyotları
ATR_PERIOD = 14
ADX_PERIOD = 14
RSI_FAST = 6
RSI_SLOW = 14
KDJ_PERIOD = 9
KDJ_K = 3
KDJ_D = 3

# Filtre eşikleri
ATR_MIN_PCT = 0.5   # ATR% minimum (örn: %0.5)
ADX_MIN = 20        # ADX minimum

# ============================================================
# RİSK YONETIMI
# ============================================================
LEVERAGE = 5
STAKE_PERCENT = 20      # Bakiyenin %20'si stake olarak
STOP_LOSS_PERCENT = 3   # %3 stop loss (borsada)
MAX_OPEN_POSITIONS = 5

# ============================================================
# CHANDELIER EXIT
# ============================================================
CE_INITIAL_ATR = 3.0    # Başlangıç: 3 ATR
CE_AT_2PCT = 2.0        # +%2 kârda: 2 ATR
CE_AT_3PCT = 1.0        # +%3 kârda: 1 ATR
CE_AT_4PCT = 0.5        # +%4+ kârda: 0.5 ATR

# Breakeven (SL'yi giriş fiyatına çek)
BREAKEVEN_TRIGGER_PCT = 1.0  # +%1 kârda

# ============================================================
# DONGU AYARLARI
# ============================================================
ENTRY_SCAN_INTERVAL = 30  # 30 dakika (mum kapanışında)
EXIT_CHECK_INTERVAL = 60  # 60 saniye (her dakika)

# Logging
LOG_LEVEL = "INFO"
