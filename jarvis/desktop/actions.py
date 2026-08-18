"""Windows masaüstü eylemleri.

Bu modül saf Python; Claude'a açılan araç yüzeyi server.py içinde. Böylece
her eylem ayrıca doğrudan test edilebiliyor.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import apps

# --- sanal tuş kodları (medya) ---
VK = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
    "mute": 0xAD,
    "volume_up": 0xAF,
    "volume_down": 0xAE,
}
KEYEVENTF_KEYUP = 0x0002


class DesktopError(RuntimeError):
    pass


# --- uygulama ve dosya açma ---


def open_app(name: str) -> str:
    resolved = apps.resolve(name)
    if resolved is None:
        near = apps.candidates(name)
        hint = f" Yakın olanlar: {', '.join(near)}" if near else ""
        raise DesktopError(f"'{name}' adlı uygulama bulunamadı.{hint}")

    if resolved.source == "builtin":
        os.startfile(resolved.target)  # noqa: S606
    else:
        os.startfile(resolved.target)  # noqa: S606
    return f"'{name}' açıldı ({resolved.source}: {resolved.target})"


def open_path(target: str) -> str:
    key = apps.normalize(target)
    if key in FOLDER_NAMES:
        path = resolve_root(target)
    else:
        path = Path(os.path.expandvars(target)).expanduser()
    if not path.exists():
        raise DesktopError(f"Yol bulunamadı: {path}")
    os.startfile(str(path))  # noqa: S606
    kind = "klasör" if path.is_dir() else "dosya"
    return f"{kind} açıldı: {path}"


def open_url(url: str) -> str:
    if not url.lower().startswith(("http://", "https://")):
        raise DesktopError("Yalnızca http ve https adresleri açılır.")
    os.startfile(url)  # noqa: S606
    return f"Tarayıcıda açıldı: {url}"


# --- dosya arama ---
# (FOLDER_NAMES ve resolve_root aşağıda tanımlı; open_path da onları kullanır.)

DEFAULT_SEARCH_ROOTS = ["~/Desktop", "~/Documents", "~/Downloads", "~/Pictures"]

# Kullanıcının aradığı şey bunların içinde olmaz; taramak hem yavaş hem de
# sonuçları kütüphane dosyalarıyla doldurur.
SKIP_DIRS = {
    ".venv", "venv", "env", "node_modules", "site-packages", "__pycache__",
    ".git", ".svn", ".cache", "AppData", "$RECYCLE.BIN", "dist-info",
}


@dataclass
class FoundFile:
    path: str
    kind: str  # "dosya" veya "klasör"
    size_kb: int
    modified: str


def _walk(base: Path):  # noqa: ANN202
    """SKIP_DIRS ve gizli klasörleri atlayarak dosya ve klasörleri dolaşır.

    Klasörler de sonuca giriyor: kullanıcı "proje klasörümü aç" dediğinde
    aranan şey bir dosya değil.
    """
    for root_dir, dir_names, file_names in os.walk(base, topdown=True):
        dir_names[:] = [
            d for d in dir_names
            if d not in SKIP_DIRS and not d.startswith(".") and not d.endswith(".dist-info")
        ]
        for name in dir_names:
            yield Path(root_dir) / name
        for name in file_names:
            yield Path(root_dir) / name


# Kullanıcı ve model klasörleri adıyla anıyor ("Masaüstü", "Downloads").
# Bunları gerçek yollara çeviriyoruz; yoksa arama sessizce boş dönüyor.
FOLDER_NAMES: dict[str, str] = {
    "masaustu": "Desktop", "desktop": "Desktop",
    "belgeler": "Documents", "dokumanlar": "Documents", "documents": "Documents",
    "indirilenler": "Downloads", "downloads": "Downloads", "indirmeler": "Downloads",
    "resimler": "Pictures", "pictures": "Pictures", "fotograflar": "Pictures",
    "muzik": "Music", "music": "Music",
    "videolar": "Videos", "videos": "Videos",
}


def resolve_root(root: str) -> Path:
    """Bir kök ifadesini gerçek klasör yoluna çevirir.

    Kabul edilenler: mutlak yol, ~ ile başlayan yol, ortam değişkeni ve
    "Masaüstü" / "Downloads" gibi klasör adları. OneDrive'a yönlendirilmiş
    kullanıcı klasörleri de denenir.
    """
    key = apps.normalize(root)
    if key in FOLDER_NAMES:
        folder = FOLDER_NAMES[key]
        for candidate in (Path.home() / folder, Path.home() / "OneDrive" / folder):
            if candidate.is_dir():
                return candidate
        return Path.home() / folder

    path = Path(os.path.expandvars(root)).expanduser()
    if not path.is_dir():
        raise DesktopError(
            f"'{root}' diye bir klasör yok. Tam yol ver ya da şu adlardan birini kullan: "
            + ", ".join(sorted(set(FOLDER_NAMES.values())))
        )
    return path


def search_files(query: str, root: str | None = None, limit: int = 20) -> list[FoundFile]:
    """Ada göre dosya ve klasör arar. Kök verilmezse tipik kullanıcı klasörlerine bakar."""
    roots = [resolve_root(root)] if root else [Path(r).expanduser() for r in DEFAULT_SEARCH_ROOTS]
    needle = apps.normalize(query)
    results: list[FoundFile] = []

    for base in roots:
        if not base.is_dir():
            continue
        for path in _walk(base):
            if len(results) >= limit:
                return results
            try:
                if needle not in apps.normalize(path.name):
                    continue
                stat = path.stat()
            except OSError:
                continue
            is_dir = path.is_dir()
            results.append(
                FoundFile(
                    path=str(path),
                    kind="klasör" if is_dir else "dosya",
                    size_kb=0 if is_dir else max(1, stat.st_size // 1024),
                    modified=datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M"),
                )
            )
    return results


# --- pencere yönetimi ---


def _windows():  # noqa: ANN202
    import pygetwindow as gw

    return [w for w in gw.getAllWindows() if w.title.strip() and w.visible]


def list_windows() -> list[dict]:
    return [
        {
            "title": w.title,
            "size": f"{w.width}x{w.height}",
            "position": f"{w.left},{w.top}",
            "active": bool(w.isActive),
            "minimized": bool(w.isMinimized),
        }
        for w in _windows()
    ]


def _find_window(title: str):  # noqa: ANN202
    needle = apps.normalize(title)
    matches = [w for w in _windows() if needle in apps.normalize(w.title)]
    if not matches:
        available = ", ".join(w.title for w in _windows()[:10])
        raise DesktopError(f"'{title}' başlıklı pencere yok. Açık olanlar: {available}")
    # En iyi eşleşme: başlığı en kısa olan (en az fazlalık içeren).
    return min(matches, key=lambda w: len(w.title))


SW_RESTORE = 9


def _force_foreground(hwnd: int) -> bool:
    """Pencereyi gerçekten öne getirir.

    İki sorun var:
      - pygetwindow'un activate()'i, işlem başarılı olduğunda bile
        "Error code from Windows: 0" diye istisna fırlatıyor.
      - Windows, arka plandaki bir sürecin başka pencereyi öne almasını
        engelliyor. Bunu aşmanın desteklenen yolu, hedef pencerenin ve o an
        önde olan pencerenin giriş kuyruklarını geçici olarak kendimize
        bağlamak (AttachThreadInput).
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    user32.ShowWindow(hwnd, SW_RESTORE)

    current = kernel32.GetCurrentThreadId()
    foreground = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    target = user32.GetWindowThreadProcessId(hwnd, None)

    attached = []
    for thread in {foreground, target}:
        if thread and thread != current and user32.AttachThreadInput(current, thread, True):
            attached.append(thread)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        for thread in attached:
            user32.AttachThreadInput(current, thread, False)

    return user32.GetForegroundWindow() == hwnd


def focus_window(title: str) -> str:
    window = _find_window(title)
    if window.isMinimized:
        window.restore()
    if not _force_foreground(window._hWnd):
        # Nadiren Windows öne almayı reddeder (tam ekran oyun gibi).
        # Sessizce başarılı sayma; çağıran bunu bilsin.
        raise DesktopError(
            f"'{window.title}' öne getirilemedi. Tam ekran bir uygulama "
            "engelliyor olabilir; önce onu küçült."
        )
    return f"Öne getirildi: {window.title}"


def window_action(title: str, action: str) -> str:
    window = _find_window(title)
    if action == "minimize":
        window.minimize()
    elif action == "maximize":
        window.maximize()
    elif action == "restore":
        window.restore()
    elif action == "close":
        window.close()
    else:
        raise DesktopError(f"Bilinmeyen pencere eylemi: {action}")
    return f"{window.title} -> {action}"


def snap_window(title: str, position: str) -> str:
    """Pencereyi ekranın bir bölgesine yerleştirir."""
    window = _find_window(title)
    user32 = ctypes.windll.user32
    screen_width = user32.GetSystemMetrics(0)
    screen_height = user32.GetSystemMetrics(1)

    layouts = {
        "left": (0, 0, screen_width // 2, screen_height),
        "right": (screen_width // 2, 0, screen_width // 2, screen_height),
        "top": (0, 0, screen_width, screen_height // 2),
        "bottom": (0, screen_height // 2, screen_width, screen_height // 2),
        "full": (0, 0, screen_width, screen_height),
    }
    if position not in layouts:
        raise DesktopError(f"Bilinmeyen konum: {position}. Seçenekler: {', '.join(layouts)}")

    if window.isMinimized:
        window.restore()
    left, top, width, height = layouts[position]
    window.moveTo(left, top)
    window.resizeTo(width, height)
    return f"{window.title} -> {position}"


def minimize_all() -> str:
    # Win+D ile masaüstünü göster.
    user32 = ctypes.windll.user32
    user32.keybd_event(0x5B, 0, 0, 0)  # Win basılı
    user32.keybd_event(0x44, 0, 0, 0)  # D
    user32.keybd_event(0x44, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(0x5B, 0, KEYEVENTF_KEYUP, 0)
    return "Tüm pencereler küçültüldü"


# --- ses ve medya ---


def media_key(key: str) -> str:
    if key not in VK:
        raise DesktopError(f"Bilinmeyen medya tuşu: {key}. Seçenekler: {', '.join(VK)}")
    user32 = ctypes.windll.user32
    user32.keybd_event(VK[key], 0, 0, 0)
    user32.keybd_event(VK[key], 0, KEYEVENTF_KEYUP, 0)
    return f"Medya tuşu gönderildi: {key}"


def _volume_interface():  # noqa: ANN202
    import comtypes

    from pycaw.utils import AudioUtilities

    # Bu çağrı SDK'nın olay döngüsü iş parçacığından gelebiliyor; COM her
    # iş parçacığında ayrıca başlatılmalı.
    try:
        comtypes.CoInitialize()
    except OSError:
        pass
    return AudioUtilities.GetSpeakers().EndpointVolume


def get_volume() -> dict:
    volume = _volume_interface()
    return {
        "percent": round(volume.GetMasterVolumeLevelScalar() * 100),
        "muted": bool(volume.GetMute()),
    }


def set_volume(percent: int) -> str:
    if not 0 <= percent <= 100:
        raise DesktopError("Ses seviyesi 0 ile 100 arasında olmalı.")
    volume = _volume_interface()
    volume.SetMasterVolumeLevelScalar(percent / 100.0, None)
    return f"Ses seviyesi %{percent}"


def set_mute(muted: bool) -> str:
    volume = _volume_interface()
    volume.SetMute(1 if muted else 0, None)
    return "Ses kapatıldı" if muted else "Ses açıldı"


# --- ekran görüntüsü ---


def screenshot(directory: Path, monitor: int = 0) -> dict:
    """Ekran görüntüsü alır ve dosya yolunu döndürür.

    monitor=0 tüm ekranları birleştirir; 1, 2, ... tek tek ekranlar.
    """
    import mss

    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"ekran-{stamp}.png"

    with mss.mss() as sct:
        if monitor >= len(sct.monitors):
            raise DesktopError(
                f"{monitor} numaralı ekran yok. Mevcut: 0 (hepsi) - {len(sct.monitors) - 1}"
            )
        shot = sct.grab(sct.monitors[monitor])
        mss.tools.to_png(shot.rgb, shot.size, output=str(path))

    return {"path": str(path), "size": f"{shot.width}x{shot.height}", "monitors": len(sct.monitors) - 1}


# --- sistem durumu ---


def system_status() -> dict:
    import psutil

    memory = psutil.virtual_memory()
    status = {
        "cpu_percent": psutil.cpu_percent(interval=0.3),
        "ram_percent": memory.percent,
        "ram_free_mb": memory.available // (1024 * 1024),
    }
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        used_percent, used, total = (int(v) for v in output.splitlines()[0].split(","))
        status |= {"gpu_percent": used_percent, "vram_used_mb": used, "vram_total_mb": total}
    except Exception:
        pass
    return status
