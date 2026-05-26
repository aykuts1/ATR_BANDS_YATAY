"""
Crossover tespit modulu.

Tum threadlerde, tum flag/giris/cikis kontrollerinde KULLANILAN ortak mantik:
- Yukari kirilma: Son 3 fiyattan herhangi biri cizginin ALTINDA VE anlik fiyat USTUNDE
- Asagi kirilma: Son 3 fiyattan herhangi biri cizginin USTUNDE VE anlik fiyat ALTINDA
"""


def yukari_kirilma(buffer, anlik_fiyat, cizgi):
    """
    Yukari kirilma kontrolu.
    buffer: son 6 fiyat listesi (en eski -> en yeni)
    anlik_fiyat: su anki fiyat
    cizgi: kontrol edilecek cizgi degeri
    return: True/False
    """
    # buffer'dan son 3 fiyat alinir (anlik haric, ondan onceki 3 fiyat)
    # buffer en yeni anlik fiyati zaten icermeli, son 3 = buffer[-3:]
    son_3 = buffer[-3:] if len(buffer) >= 3 else buffer

    if len(son_3) == 0:
        return False

    # son 3 fiyattan herhangi biri cizginin ALTINDA mi?
    herhangi_altinda = any(f < cizgi for f in son_3)

    # anlik fiyat cizginin USTUNDE mi?
    anlik_ustunde = anlik_fiyat > cizgi

    return herhangi_altinda and anlik_ustunde


def asagi_kirilma(buffer, anlik_fiyat, cizgi):
    """
    Asagi kirilma kontrolu.
    buffer: son 6 fiyat listesi (en eski -> en yeni)
    anlik_fiyat: su anki fiyat
    cizgi: kontrol edilecek cizgi degeri
    return: True/False
    """
    son_3 = buffer[-3:] if len(buffer) >= 3 else buffer

    if len(son_3) == 0:
        return False

    # son 3 fiyattan herhangi biri cizginin USTUNDE mi?
    herhangi_ustunde = any(f > cizgi for f in son_3)

    # anlik fiyat cizginin ALTINDA mi?
    anlik_altinda = anlik_fiyat < cizgi

    return herhangi_ustunde and anlik_altinda
