"""Kurulum doğrulama: `python -m jarvis.selftest`

Hiçbir şeyi değiştirmez, sadece kontrol eder ve neyin eksik olduğunu söyler.
"""

from __future__ import annotations

import importlib
import shutil
import socket
import subprocess
import sys

from rich.console import Console
from rich.table import Table

from . import config as config_module
from .config import ROOT

console = Console()

REQUIRED_MODULES = [
    ("dotenv", "python-dotenv"),
    ("numpy", "numpy"),
    ("sounddevice", "sounddevice"),
    ("claude_agent_sdk", "claude-agent-sdk"),
    ("openwakeword", "openwakeword"),
    ("onnxruntime", "onnxruntime"),
    ("faster_whisper", "faster-whisper"),
    ("soundfile", "soundfile"),
    ("edge_tts", "edge-tts"),
    ("pyttsx3", "pyttsx3"),
    ("rich", "rich"),
    ("pygetwindow", "pygetwindow"),
    ("pycaw", "pycaw"),
    ("mss", "mss"),
    ("psutil", "psutil"),
    ("pyautogui", "pyautogui"),
    ("pyperclip", "pyperclip"),
    ("PIL", "pillow"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
]


def check(table: Table, name: str, ok: bool, detail: str = "") -> bool:
    table.add_row(name, "[green]TAMAM[/green]" if ok else "[red]EKSİK[/red]", detail)
    return ok


def main() -> int:
    table = Table(title="Jarvis kurulum kontrolü", show_lines=False)
    table.add_column("Kontrol", style="bold")
    table.add_column("Durum", justify="center")
    table.add_column("Ayrıntı", overflow="fold")

    ok = True

    table.add_row("Python", "[green]TAMAM[/green]", sys.version.split()[0])

    for module_name, package in REQUIRED_MODULES:
        try:
            importlib.import_module(module_name)
            found = True
            detail = ""
        except ImportError as exc:
            found = False
            detail = f"pip install {package}  ({exc})"
        ok &= check(table, f"paket: {package}", found, detail)

    env_file = ROOT / ".env"
    ok &= check(
        table,
        ".env dosyası",
        env_file.exists(),
        "" if env_file.exists() else "Copy-Item .env.example .env  ve doldur",
    )

    cfg = config_module.load()

    # Uyandırma motoru: gerçekten kurulabiliyor mu?
    try:
        from .audio import wake as wake_module

        detector = wake_module.create(cfg.audio)
        ok &= check(
            table,
            "uyandırma motoru",
            True,
            f"{detector.engine} / '{detector.keyword}' / kare {detector.frame_length}",
        )
        detector.close()
    except Exception as exc:
        ok &= check(table, "uyandırma motoru", False, str(exc))

    # VAD motoru
    try:
        from .audio import vad as vad_module

        vad = vad_module.create(cfg.audio)
        ok &= check(table, "VAD motoru", True, vad.backend)
        vad.close()
    except Exception as exc:
        ok &= check(table, "VAD motoru", False, str(exc))

    # Picovoice anahtarı yalnızca ilgili motorlar seçilmişse gerekli.
    needs_key = cfg.audio.wake_engine == "porcupine" or cfg.audio.vad_engine == "cobra"
    if needs_key:
        ok &= check(
            table,
            "PICOVOICE_ACCESS_KEY",
            bool(cfg.audio.picovoice_key),
            "" if cfg.audio.picovoice_key else "https://console.picovoice.ai/ adresinden ücretsiz al",
        )
    else:
        table.add_row(
            "PICOVOICE_ACCESS_KEY",
            "[dim]GEREKSİZ[/dim]",
            f"WAKE_ENGINE={cfg.audio.wake_engine}, VAD_ENGINE={cfg.audio.vad_engine} anahtar istemiyor",
        )

    # Mikrofonlar
    try:
        from .audio.mic import list_input_devices

        devices = list_input_devices()
        detail = "; ".join(
            f"[{d.index}] {d.name}{' (varsayılan)' if d.default else ''}" for d in devices
        )
        ok &= check(table, "giriş cihazları", bool(devices), detail or "mikrofon bulunamadı")
    except Exception as exc:
        ok &= check(table, "giriş cihazları", False, str(exc))

    # GPU belleği. Whisper large-v3-turbo float16 yaklaşık 2 GB ister;
    # boşta kalan bundan azsa transkripsiyon sırasında CPU'ya düşer.
    if cfg.audio.whisper_device != "cpu":
        try:
            output = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            used, total = (int(v) for v in output.splitlines()[0].split(","))
            free = total - used
            table.add_row(
                "GPU belleği",
                "[green]TAMAM[/green]" if free >= 2500 else "[yellow]DAR[/yellow]",
                f"{free} MB boş / {total} MB toplam"
                + ("" if free >= 2500 else " — WHISPER_MODEL=medium dene veya GPU kullanan uygulamaları kapat"),
            )
        except Exception as exc:
            table.add_row("GPU belleği", "[dim]OKUNAMADI[/dim]", str(exc)[:80])

    # CUDA DLL'leri
    dll_dirs = config_module.add_cuda_dll_dirs()
    table.add_row(
        "CUDA DLL yolları",
        "[green]TAMAM[/green]" if dll_dirs else "[yellow]YOK[/yellow]",
        "; ".join(dll_dirs) if dll_dirs else "CPU'ya düşülecek (yavaş). pip install nvidia-cublas-cu12 nvidia-cudnn-cu12",
    )

    # Claude Code CLI (SDK bunu alt süreç olarak çalıştırır)
    cli = shutil.which("claude")
    ok &= check(
        table,
        "claude CLI",
        bool(cli),
        cli or "npm install -g @anthropic-ai/claude-code",
    )

    # Masaüstü kontrolü: Başlat menüsü kısayolları bulunuyor mu?
    try:
        from .desktop import apps

        count = len(apps.shortcut_index())
        ok &= check(
            table,
            "uygulama dizini",
            count > 0,
            f"{count} kısayol bulundu" if count else "Başlat menüsü okunamadı",
        )
    except Exception as exc:
        ok &= check(table, "uygulama dizini", False, str(exc)[:100])

    # HUD portu boş mu?
    if cfg.hud.enabled:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.4)
            busy = probe.connect_ex((cfg.hud.host, cfg.hud.port)) == 0
        table.add_row(
            "HUD portu",
            "[green]BOŞ[/green]" if not busy else "[yellow]DOLU[/yellow]",
            f"{cfg.hud.host}:{cfg.hud.port}"
            + ("" if not busy else " — başka bir Jarvis çalışıyor olabilir, ya da HUD_PORT değiştir"),
        )

    # Yazılabilir klasörler
    for label, path in (("logs/", cfg.log_dir), ("memory/", cfg.memory_dir), ("models/", cfg.model_dir)):
        writable = path.is_dir()
        ok &= check(table, f"klasör {label}", writable, str(path))

    # Güvenlik ayarları
    level = cfg.security.approval_level
    descriptions = {
        "medium": "Dosya/uygulama açma, yazma ve üzeri onay ister (en sıkı)",
        "high": "Kabuk komutu, pencere kapatma ve üzeri onay ister",
        "critical": "Yalnızca geri dönüşsüz işlemler onay ister (mail, silme, ödeme)",
        "none": "Hiçbir şey onay istemez — her eylem yine de günlüğe yazılır",
    }
    colors = {"medium": "green", "high": "green", "critical": "yellow", "none": "red"}
    table.add_row(
        "APPROVAL_LEVEL",
        f"[{colors.get(level, 'yellow')}]{level.upper()}[/{colors.get(level, 'yellow')}]",
        descriptions.get(level, "bilinmeyen seviye, medium'a düşülür"),
    )
    if cfg.security.always_allow:
        table.add_row("ALWAYS_ALLOW", "[dim]—[/dim]", ", ".join(cfg.security.always_allow))
    if cfg.security.always_ask:
        table.add_row("ALWAYS_ASK", "[dim]—[/dim]", ", ".join(cfg.security.always_ask))
    table.add_row("Onay zaman aşımı", "[green]TAMAM[/green]", f"{cfg.security.approval_timeout_sec} sn (dolarsa reddedilir)")

    console.print(table)

    if ok:
        console.print("\n[bold green]Hazır.[/bold green] Ses testini çalıştır: [bold]python -m jarvis[/bold]")
    else:
        console.print("\n[bold red]Eksikler var.[/bold red] Yukarıdaki 'Ayrıntı' sütununu takip et.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
