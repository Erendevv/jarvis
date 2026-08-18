"""Uygulama adını çalıştırılabilir bir hedefe çevirme.

Kullanıcı "Spotify aç" dediğinde elimizde sadece bir isim var. Üç yerde
sırayla aranır:

  1. Bilinen takma adlar (chrome, hesap makinesi, dosya gezgini ...)
  2. Başlat menüsündeki kısayollar (.lnk) — kurulu her uygulama burada
  3. PATH üzerindeki çalıştırılabilir dosyalar

Kısayol dosyaları os.startfile ile açılabildiği için ayrıca COM ile hedef
çözmeye gerek yok.
"""

from __future__ import annotations

import os
import shutil
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Türkçe adlar ve yaygın kısaltmalar için doğrudan eşlemeler.
ALIASES: dict[str, str] = {
    "hesap makinesi": "calc",
    "hesap makinasi": "calc",
    "not defteri": "notepad",
    "dosya gezgini": "explorer",
    "gezgin": "explorer",
    "denetim masasi": "control",
    "gorev yoneticisi": "taskmgr",
    "ayarlar": "ms-settings:",
    "komut istemi": "cmd",
    "terminal": "wt",
    "kod": "code",
    "vscode": "code",
    "vs code": "code",
    "tarayici": "chrome",
    "google chrome": "chrome",
    "muzik": "spotify",
}

# Başlat menüsü aranmadan doğrudan çalıştırılabilecek sistem komutları.
BUILTIN = {
    "calc", "notepad", "explorer", "control", "taskmgr", "cmd",
    "mspaint", "snippingtool", "charmap", "magnify", "osk", "wt",
}


def normalize(text: str) -> str:
    """Karşılaştırma için sadeleştirir: küçük harf, aksansız, tek boşluk."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Türkçe'ye özgü dönüşümler NFKD ile çözülmüyor.
    for src, dst in (("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ç", "c"), ("ö", "o"), ("ü", "u")):
        text = text.replace(src, dst)
    return " ".join(text.split())


def start_menu_dirs() -> list[Path]:
    dirs = []
    for env in ("APPDATA", "ProgramData"):
        base = os.environ.get(env)
        if base:
            path = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if path.is_dir():
                dirs.append(path)
    return dirs


@lru_cache(maxsize=1)
def shortcut_index() -> list[tuple[str, Path]]:
    """(normalize edilmiş ad, kısayol yolu) listesi. Süreç boyunca önbelleklenir."""
    index: list[tuple[str, Path]] = []
    for base in start_menu_dirs():
        for link in base.rglob("*.lnk"):
            index.append((normalize(link.stem), link))
    return index


@dataclass
class Resolved:
    target: str
    source: str  # nasıl bulundu: alias | builtin | shortcut | path


def resolve(name: str) -> Resolved | None:
    key = normalize(name)

    if key in ALIASES:
        key = normalize(ALIASES[key])

    if key in BUILTIN or key.endswith(":"):
        return Resolved(key, "builtin")

    index = shortcut_index()

    # Önce tam ad, sonra "ile başlayan", sonra "içeren".
    for match_name, path in index:
        if match_name == key:
            return Resolved(str(path), "shortcut")
    for match_name, path in index:
        if match_name.startswith(key):
            return Resolved(str(path), "shortcut")
    for match_name, path in index:
        if key in match_name:
            return Resolved(str(path), "shortcut")

    found = shutil.which(key)
    if found:
        return Resolved(found, "path")

    return None


def candidates(name: str, limit: int = 8) -> list[str]:
    """Bulunamadığında kullanıcıya önerilecek yakın adlar."""
    key = normalize(name)
    head = key.split()[0] if key.split() else key
    seen: list[str] = []
    for match_name, path in shortcut_index():
        if head and head in match_name and path.stem not in seen:
            seen.append(path.stem)
        if len(seen) >= limit:
            break
    return seen
