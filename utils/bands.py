"""
Bant cizgileri hesaplama modulu.
EMA ve ATR degerlerinden tum bant cizgilerini hesaplar.
"""


def hesapla_tum_cizgiler(ema, atr, config):
    """
    EMA ve ATR'den tum bant cizgilerini hesaplar.
    return: tum cizgileri iceren dict
    """
    kirmizi_dis = config['kirmizi_dis_atr']
    mavi_dis = config['mavi_dis_atr']
    tampon = config['tampon_atr']
    ms_s1 = config['mor_sari_seviye1_atr']
    ms_s2 = config['mor_sari_seviye2_atr']
    k_s1 = config['kirmizi_seviye1_atr']
    k_s2 = config['kirmizi_seviye2_atr']
    m_s1 = config['mavi_seviye1_atr']
    m_s2 = config['mavi_seviye2_atr']

    cizgiler = {}

    # MERKEZ
    cizgiler['ema'] = ema

    # === KIRMIZI UST ===
    kud = ema + kirmizi_dis * atr  # Kirmizi Ust Dis Cizgi
    cizgiler['kirmizi_ust_dis'] = kud
    cizgiler['kirmizi_ust_ic_tampon'] = kud - tampon * atr
    cizgiler['kirmizi_ust_dis_tampon'] = kud + tampon * atr
    cizgiler['kirmizi_ust_seviye1'] = kud + k_s1 * atr  # disa dogru
    cizgiler['kirmizi_ust_seviye2'] = kud + k_s2 * atr  # disa dogru

    # === MAVI UST ===
    mud = ema + mavi_dis * atr  # Mavi Ust Dis Cizgi
    cizgiler['mavi_ust_dis'] = mud
    cizgiler['mavi_ust_ic_tampon'] = mud - tampon * atr
    cizgiler['mavi_ust_dis_tampon'] = mud + tampon * atr
    cizgiler['mavi_ust_seviye1'] = mud - m_s1 * atr  # ice dogru
    cizgiler['mavi_ust_seviye2'] = mud - m_s2 * atr  # ice dogru

    # === MOR UST (Kirmizi Dis'e gore) ===
    cizgiler['mor_ust_seviye1'] = kud - ms_s1 * atr
    cizgiler['mor_ust_seviye2'] = kud - ms_s2 * atr
    cizgiler['mor_ust_seviye4'] = kud + ms_s2 * atr
    cizgiler['mor_ust_seviye5'] = kud + ms_s1 * atr

    # === SARI UST (Mavi Dis'e gore) ===
    # Sari numaralandirma: 1=disarda (en ust), 5=iceride (en alt)
    # Bant sirasi (yukaridan asagiya): Mavi Dis Tampon -> Sari1 -> Sari2 -> Mavi Dis -> Sari4 -> Sari5 -> Mavi Ic Tampon
    cizgiler['sari_ust_seviye1'] = mud + ms_s1 * atr  # +0.4 ATR (disarda/ustte)
    cizgiler['sari_ust_seviye2'] = mud + ms_s2 * atr  # +0.2 ATR
    cizgiler['sari_ust_seviye4'] = mud - ms_s2 * atr  # -0.2 ATR
    cizgiler['sari_ust_seviye5'] = mud - ms_s1 * atr  # -0.4 ATR (iceride/altta)

    # === KIRMIZI ALT (tam simetri) ===
    kad = ema - kirmizi_dis * atr  # Kirmizi Alt Dis Cizgi
    cizgiler['kirmizi_alt_dis'] = kad
    cizgiler['kirmizi_alt_ic_tampon'] = kad + tampon * atr
    cizgiler['kirmizi_alt_dis_tampon'] = kad - tampon * atr
    cizgiler['kirmizi_alt_seviye1'] = kad - k_s1 * atr  # disa dogru
    cizgiler['kirmizi_alt_seviye2'] = kad - k_s2 * atr  # disa dogru

    # === MAVI ALT ===
    mad = ema - mavi_dis * atr  # Mavi Alt Dis Cizgi
    cizgiler['mavi_alt_dis'] = mad
    cizgiler['mavi_alt_ic_tampon'] = mad + tampon * atr
    cizgiler['mavi_alt_dis_tampon'] = mad - tampon * atr
    cizgiler['mavi_alt_seviye1'] = mad + m_s1 * atr  # ice dogru
    cizgiler['mavi_alt_seviye2'] = mad + m_s2 * atr  # ice dogru

    # === MOR ALT ===
    cizgiler['mor_alt_seviye1'] = kad + ms_s1 * atr
    cizgiler['mor_alt_seviye2'] = kad + ms_s2 * atr
    cizgiler['mor_alt_seviye4'] = kad - ms_s2 * atr
    cizgiler['mor_alt_seviye5'] = kad - ms_s1 * atr

    # === SARI ALT ===
    # === SARI ALT (Sari Ust simetrisi) ===
    # Bant sirasi (asagidan yukariya): Mavi Alt Dis Tampon -> Sari Alt 1 -> Sari Alt 2 -> Mavi Alt Dis -> Sari Alt 4 -> Sari Alt 5 -> Mavi Alt Ic Tampon
    cizgiler['sari_alt_seviye1'] = mad - ms_s1 * atr  # -0.4 ATR (disarda/altta)
    cizgiler['sari_alt_seviye2'] = mad - ms_s2 * atr  # -0.2 ATR
    cizgiler['sari_alt_seviye4'] = mad + ms_s2 * atr  # +0.2 ATR
    cizgiler['sari_alt_seviye5'] = mad + ms_s1 * atr  # +0.4 ATR (iceride/ustte)

    return cizgiler
