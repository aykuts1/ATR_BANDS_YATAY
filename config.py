"""
Ema100-Ema21-Tunel Bot Configuration
"""
import os
# ============================================================
# API CREDENTIALS (from Railway environment variables)
# ============================================================
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
# Optional: testnet (default false)
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
# ============================================================
# COIN LIST (20 coins - orta volatilite, yüksek hacim)
# ============================================================
DEFAULT_SYMBOLS = [
    "SOLUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "NEARUSDT",
    "INJUSDT", "OPUSDT", "ARBUSDT", "SUIUSDT", "TONUSDT",
    "APTUSDT", "FTMUSDT", "TIAUSDT", "ENAUSDT", "JTOUSDT",
    "XRPUSDT", "TRXUSDT", "ATOMUSDT", "ADAUSDT", "ALGOUSDT",
]
# Allow override from environment (comma-separated)
_env_symbols = os.getenv("SYMBOLS", "").strip()
if _env_symbols:
    SYMBOLS = [s.strip().upper() for s in _env_symbols.split(",") if s.strip()]
else:
    SYMBOLS = DEFAULT_SYMBOLS
# ============================================================
# STRATEGY PARAMETERS
# ============================================================
# Tüneller
EMA_TUNNEL_PERIOD = 100  # EMA100 (filtre tüneli) - high & low
EMA_SIGNAL_PERIOD = 21   # EMA21 (sinyal tüneli) - high & low & close
# Armed state (2 adımlı giriş)
ARMED_TIMEOUT_SECONDS = 7200  # 2 saat - armed durumu bu sürede tetiklenmezse sıfırlanır
# Timeframe
TIMEFRAME = "5"          # 5 dakikalık mum
KLINE_LIMIT = 300        # EMA100 için yeterli mum sayısı
# Risk / Pozisyon
LEVERAGE = 50            # 50x isolated (zorunlu)
STAKE_PERCENT = 0.20     # Bakiyenin %20'si stake
MAX_POSITIONS = 5        # Aynı anda max 5 işlem
MARGIN_MODE = "ISOLATED" # Isolated margin
# Stop Loss
INITIAL_SL_PERCENT = 0.01  # %1 fiyat hareketi (kaldıraçsız)
# Chandelier Exit (CE) Trailing Stop
CE_ACTIVATION_PCT = 0.005  # Kar %0.5'i geçince CE aktif
CE_TRAIL_PCT = 0.005       # En iyi fiyattan %0.5 geri dönüş = çıkış
# Tarama ve emir zamanlamaları
SCAN_INTERVAL = 30           # Saniye - her 30sn tarama
KLINE_REFRESH_INTERVAL = 60  # Saniye - klines cache yenileme
REPORT_INTERVAL = 300        # Saniye - her 5dk genel rapor
# Emir parametreleri (yalnızca limit emir, market kullanılmaz)
LIMIT_ORDER_RETRY_INTERVAL = 3   # Saniye - her saniye yeni emir
LIMIT_ORDER_MAX_RETRIES = 20     # Max 20 deneme, dolmazsa sinyali atla
# ============================================================
# BYBIT API SETTINGS
# ============================================================
ACCOUNT_TYPE = "UNIFIED"   # Bybit Unified Trading Account
CATEGORY = "linear"        # USDT perpetuals
# ============================================================
# VALIDATION
# ============================================================
def validate_config():
    """Ensure required env vars exist."""
    missing = []
    if not BYBIT_API_KEY:
        missing.append("BYBIT_API_KEY")
    if not BYBIT_API_SECRET:
        missing.append("BYBIT_API_SECRET")
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        raise RuntimeError(f"Eksik environment variables: {', '.join(missing)}")
