"""Ekran otomasyonu: görüntü alma, tıklama, yazma, tuş gönderme.

Koordinat sorunu ve çözümü
--------------------------
Claude ekran görüntüsünü bir resim olarak görür ve "şuraya tıkla" derken
gördüğü resmin koordinatını verir. Ama resim, ekranın küçültülmüş hali
olabilir; ham koordinatla tıklamak yanlış yere basar.

Bu yüzden son alınan görüntünün ölçeği ve ekran ofseti burada saklanır.
Tıklama araçları varsayılan olarak "görüntü koordinatı" kabul eder ve
gerçek ekran koordinatına çevirir. Gerçek koordinat vermek istersen
raw=True kullanılır.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Görüntüyü bu genişliğin altına indiriyoruz: model için hem daha ucuz hem
# de ayrıntı kaybı tıklama isabetini bozacak kadar büyük değil.
MAX_WIDTH = 1280

# Görüntü modele base64 olarak gidiyor; renk sayısını azaltmak dosyayı
# 3-4 kat küçültüyor. Arayüz görüntülerinde renk zaten az olduğu için
# metin okunabilirliği bozulmuyor.
PALETTE_COLORS = 256

# Fare hareketlerinin süresi. Anlık sıçrama bazı uygulamalarda hover
# durumunu tetiklemiyor; kısa bir animasyon daha güvenilir.
MOVE_DURATION = 0.25


class ScreenError(RuntimeError):
    pass


@dataclass
class Shot:
    path: str
    monitor: int
    scale: float          # görüntü pikseli -> ekran pikseli çarpanı
    offset_x: int         # ekranın sanal masaüstündeki sol kenarı
    offset_y: int
    image_size: tuple[int, int]
    screen_size: tuple[int, int]


# Son alınan görüntü. Tıklama koordinatları buna göre çevrilir.
_last: Shot | None = None


def _pyautogui():  # noqa: ANN202
    import pyautogui

    # Fare sol üst köşeye giderse her şeyi durdur: kaçak otomasyona
    # karşı acil fren. Kullanıcı fareyi köşeye atarak kesebilir.
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    return pyautogui


def capture(directory: Path, monitor: int = 0) -> Shot:
    """Ekran görüntüsü alır, küçültür, ölçeğini kaydeder."""
    global _last
    import mss
    from PIL import Image

    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"ekran-{stamp}.png"

    with mss.mss() as sct:
        if monitor >= len(sct.monitors):
            raise ScreenError(
                f"{monitor} numaralı ekran yok. Mevcut: 0 (hepsi) - {len(sct.monitors) - 1}"
            )
        area = sct.monitors[monitor]
        grab = sct.grab(area)
        image = Image.frombytes("RGB", grab.size, grab.rgb)

    screen_size = (image.width, image.height)
    scale = 1.0
    if image.width > MAX_WIDTH:
        scale = image.width / MAX_WIDTH
        image = image.resize(
            (MAX_WIDTH, round(image.height / scale)), Image.LANCZOS
        )
    image.quantize(colors=PALETTE_COLORS, method=Image.MEDIANCUT).save(path, optimize=True)

    _last = Shot(
        path=str(path),
        monitor=monitor,
        scale=scale,
        offset_x=area["left"],
        offset_y=area["top"],
        image_size=(image.width, image.height),
        screen_size=screen_size,
    )
    return _last


def to_screen(x: int, y: int, raw: bool = False) -> tuple[int, int]:
    """Görüntü koordinatını gerçek ekran koordinatına çevirir."""
    if raw:
        return int(x), int(y)
    if _last is None:
        raise ScreenError(
            "Önce ekran görüntüsü al (screenshot), sonra o görüntüdeki "
            "koordinatlara tıkla. Gerçek ekran koordinatı vereceksen raw=true kullan."
        )
    return (
        int(x * _last.scale) + _last.offset_x,
        int(y * _last.scale) + _last.offset_y,
    )


def last_shot() -> Shot | None:
    return _last


# --- fare ---


def move(x: int, y: int, raw: bool = False) -> str:
    gui = _pyautogui()
    sx, sy = to_screen(x, y, raw)
    gui.moveTo(sx, sy, duration=MOVE_DURATION)
    return f"Fare {sx},{sy} konumuna götürüldü"


def click(x: int, y: int, button: str = "left", clicks: int = 1, raw: bool = False) -> str:
    if button not in ("left", "right", "middle"):
        raise ScreenError(f"Bilinmeyen düğme: {button}. left, right veya middle olmalı.")
    gui = _pyautogui()
    sx, sy = to_screen(x, y, raw)
    gui.moveTo(sx, sy, duration=MOVE_DURATION)
    gui.click(x=sx, y=sy, button=button, clicks=clicks, interval=0.12)
    return f"{sx},{sy} konumuna {button} tıklandı ({clicks}x)"


def drag(x1: int, y1: int, x2: int, y2: int, raw: bool = False) -> str:
    gui = _pyautogui()
    sx1, sy1 = to_screen(x1, y1, raw)
    sx2, sy2 = to_screen(x2, y2, raw)
    gui.moveTo(sx1, sy1, duration=MOVE_DURATION)
    gui.dragTo(sx2, sy2, duration=0.4, button="left")
    return f"{sx1},{sy1} -> {sx2},{sy2} sürüklendi"


def scroll(amount: int, x: int | None = None, y: int | None = None, raw: bool = False) -> str:
    """amount pozitifse yukarı, negatifse aşağı kaydırır."""
    gui = _pyautogui()
    if x is not None and y is not None:
        sx, sy = to_screen(x, y, raw)
        gui.moveTo(sx, sy, duration=MOVE_DURATION)
    gui.scroll(int(amount))
    return f"{amount} birim kaydırıldı"


def cursor_position() -> dict:
    gui = _pyautogui()
    point = gui.position()
    return {"x": point.x, "y": point.y}


# --- klavye ---


def type_text(text: str) -> str:
    """Metni yazar.

    pyautogui.write yalnızca ASCII yazabiliyor; Türkçe karakterler düşüyor.
    Bu yüzden metin panoya konup Ctrl+V ile yapıştırılıyor. Kullanıcının
    panosu sonradan geri yükleniyor.
    """
    import pyperclip

    gui = _pyautogui()
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None

    pyperclip.copy(text)
    time.sleep(0.08)
    gui.hotkey("ctrl", "v")
    time.sleep(0.15)

    if previous is not None:
        try:
            pyperclip.copy(previous)
        except Exception:
            pass
    return f"{len(text)} karakter yazıldı"


# Kullanıcıyı oturumdan atacak veya sistemi kilitleyecek kombinasyonlar.
FORBIDDEN_KEYS = {
    ("ctrl", "alt", "delete"),
    ("win", "l"),
}


def press_keys(keys: str) -> str:
    """Tuş ya da tuş kombinasyonu gönderir. Örnek: 'enter', 'ctrl+shift+m'."""
    gui = _pyautogui()
    parts = [k.strip().lower() for k in keys.replace(" ", "+").split("+") if k.strip()]
    if not parts:
        raise ScreenError("Boş tuş kombinasyonu.")
    if tuple(parts) in FORBIDDEN_KEYS:
        raise ScreenError(
            f"'{keys}' gönderilmiyor: bu kombinasyon oturumu kilitler ya da "
            "kullanıcıyı sistemden atar."
        )

    valid = set(gui.KEYBOARD_KEYS)
    unknown = [p for p in parts if p not in valid]
    if unknown:
        raise ScreenError(f"Tanınmayan tuş: {', '.join(unknown)}")

    if len(parts) == 1:
        gui.press(parts[0])
    else:
        gui.hotkey(*parts)
    return f"Tuş gönderildi: {'+'.join(parts)}"
