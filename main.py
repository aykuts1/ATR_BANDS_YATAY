def main():
    global INITIAL_BALANCE, FIXED_STAKE

    logger.info("=" * 50)
    logger.info(f"Bot başlatılıyor... TESTNET={BYBIT_TESTNET}")
    logger.info("=" * 50)

    # 1. Bakiye oku
    balance = 0.0
    for attempt in range(5):
        balance = exchange.get_usdt_balance()
        if balance > 0:
            break
        logger.warning(f"Bakiye okunamadı, tekrar deneniyor... ({attempt+1}/5)")
        time.sleep(3)

    if balance <= 0:
        msg = f"Bakiye okunamadı veya 0: {balance}"
        logger.error(msg)
        tg.notify_error(msg)
        return

    INITIAL_BALANCE = balance
    FIXED_STAKE = balance * STAKE_PERCENT / 100

    logger.info(f"Bakiye: {balance:.2f} USDT, Stake: {FIXED_STAKE:.2f} USDT")
    tg.notify_bot_started(balance, FIXED_STAKE, BYBIT_TESTNET)

    # 2. Mevcut açık pozisyonları senkronize et
    open_positions = exchange.get_open_positions()
    sync_positions(open_positions)
    logger.info(f"Senkronizasyon: {len(position_manager.positions)} takipli pozisyon")

    # 3. İki paralel thread
    t1 = threading.Thread(target=entry_scanner_loop, daemon=True, name="EntryScanner")
    t2 = threading.Thread(target=exit_tracker_loop, daemon=True, name="ExitTracker")
    t1.start()
    t2.start()

    # Ana thread canlı tut
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Bot durduruluyor (Ctrl+C)")
        tg.notify_error("Bot manuel olarak durduruldu")


if __name__ == "__main__":
    main()
