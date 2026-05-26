"""
Indikator hesaplama modulu.
EMA ve ATR hesaplamalarini icerir.
"""


def hesapla_ema(fiyatlar, periyot):
    """
    EMA (Exponential Moving Average) hesaplar.
    fiyatlar: kapanis fiyatlari listesi (en eski -> en yeni)
    periyot: EMA periyodu (orn. 100)
    return: en son EMA degeri (float)
    """
    if len(fiyatlar) < periyot:
        return None

    # ilk EMA, ilk 'periyot' fiyatin basit ortalamasi
    sma = sum(fiyatlar[:periyot]) / periyot
    ema = sma

    # smoothing factor
    k = 2 / (periyot + 1)

    # periyottan sonraki tum fiyatlar icin EMA guncelle
    for fiyat in fiyatlar[periyot:]:
        ema = (fiyat - ema) * k + ema

    return ema


def hesapla_tr_listesi(mumlar):
    """
    True Range listesi hesaplar.
    mumlar: [{open, high, low, close}, ...] formatinda (en eski -> en yeni)
    return: TR degerleri listesi
    """
    tr_listesi = []

    for i in range(len(mumlar)):
        high = mumlar[i]['high']
        low = mumlar[i]['low']

        if i == 0:
            # ilk mumda onceki kapanis yok, sadece high-low
            tr = high - low
        else:
            onceki_close = mumlar[i - 1]['close']
            tr = max(
                high - low,
                abs(high - onceki_close),
                abs(low - onceki_close)
            )

        tr_listesi.append(tr)

    return tr_listesi


def hesapla_atr_listesi(mumlar, periyot=14):
    """
    Wilder's smoothing yontemiyle ATR listesi hesaplar.
    mumlar: mum verisi (en eski -> en yeni)
    periyot: ATR periyodu (klasik 14)
    return: ATR degerleri listesi (ilk periyot kadar None doner)
    """
    tr_listesi = hesapla_tr_listesi(mumlar)

    if len(tr_listesi) < periyot:
        return []

    atr_listesi = []

    # ilk ATR: ilk 'periyot' TR'nin basit ortalamasi
    ilk_atr = sum(tr_listesi[:periyot]) / periyot
    atr_listesi.append(ilk_atr)

    # sonrakiler: Wilder's smoothing
    for i in range(periyot, len(tr_listesi)):
        onceki_atr = atr_listesi[-1]
        yeni_atr = (onceki_atr * (periyot - 1) + tr_listesi[i]) / periyot
        atr_listesi.append(yeni_atr)

    return atr_listesi


def hesapla_atr_ortalama(mumlar, atr_periyot=14, ortalama_son=100):
    """
    Son N ATR'nin ortalamasini hesaplar.
    Doc'a gore: "ATR degeri = son 100 ATR'nin ortalamasi"
    mumlar: mum verisi (en eski -> en yeni)
    atr_periyot: ATR periyodu (klasik 14)
    ortalama_son: son kac ATR ortalanir (varsayilan 100)
    return: ATR ortalama degeri (float) veya None
    """
    atr_listesi = hesapla_atr_listesi(mumlar, atr_periyot)

    if len(atr_listesi) < ortalama_son:
        # yeterli atr yoksa mevcut tumunun ortalamasini al
        if len(atr_listesi) == 0:
            return None
        return sum(atr_listesi) / len(atr_listesi)

    son_atr_lar = atr_listesi[-ortalama_son:]
    return sum(son_atr_lar) / len(son_atr_lar)
