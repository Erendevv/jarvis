"""Ayarların tek kaynağı.

Tüm gizli bilgiler .env dosyasından okunur; hiçbir anahtar kaynak koda gömülü
değildir. Bu modül import edildiğinde .env otomatik yüklenir ve CUDA
kütüphanelerinin DLL yolları Windows'ta arama yoluna eklenir.
"""

from __future__ import annotations

import os
import site
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
MEMORY_DIR = ROOT / "memory"
MODEL_DIR = ROOT / "models"

load_dotenv(ROOT / ".env")

# Windows'ta sembolik bağlantı yoksa Hugging Face her model erişiminde uyarı
# basıyor; önbellek yine çalışıyor, uyarı sadece gürültü.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _str(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _int(key: str, default: int) -> int:
    raw = _str(key)
    return int(raw) if raw else default


def _float(key: str, default: float) -> float:
    raw = _str(key)
    return float(raw) if raw else default


def _bool(key: str, default: bool) -> bool:
    raw = _str(key).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "evet", "on")


def _optional_int(key: str) -> int | None:
    raw = _str(key)
    return int(raw) if raw else None


def add_cuda_dll_dirs() -> list[str]:
    """nvidia-* pip paketlerinin DLL klasörlerini Windows arama yoluna ekler.

    faster-whisper CUDA'da çalışırken cuBLAS ve cuDNN DLL'lerini bulamazsa
    "Library cublas64_12.dll is not found" gibi bir hata verir. pip ile kurulan
    nvidia paketleri bu DLL'leri site-packages/nvidia/*/bin altına koyar ama
    PATH'e eklemez; burada elle ekliyoruz.

    Hem os.add_dll_directory hem PATH gerekiyor: ctranslate2 bu kütüphaneleri
    düz LoadLibrary ile açar ve LoadLibrary, add_dll_directory ile eklenen
    klasörlere bakmaz, PATH'e bakar.
    """
    if sys.platform != "win32":
        return []

    candidates: list[Path] = []
    for base in {*site.getsitepackages(), site.getusersitepackages()}:
        nvidia = Path(base) / "nvidia"
        if nvidia.is_dir():
            candidates.extend(p for p in nvidia.glob("*/bin") if p.is_dir())

    added: list[str] = []
    for path in candidates:
        text = str(path)
        try:
            os.add_dll_directory(text)
        except OSError:
            continue
        if text not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = text + os.pathsep + os.environ.get("PATH", "")
        added.append(text)
    return added


@dataclass(frozen=True)
class AudioConfig:
    picovoice_key: str = field(default_factory=lambda: _str("PICOVOICE_ACCESS_KEY"))
    wake_engine: str = field(default_factory=lambda: _str("WAKE_ENGINE", "openwakeword"))
    # Boş bırakılırsa motorun kendi varsayılanı kullanılır (audio/wake.py).
    wake_word: str = field(default_factory=lambda: _str("WAKE_WORD"))
    wake_sensitivity: float = field(default_factory=lambda: _float("WAKE_SENSITIVITY", 0.6))
    input_device: int | None = field(default_factory=lambda: _optional_int("AUDIO_INPUT_DEVICE"))

    vad_engine: str = field(default_factory=lambda: _str("VAD_ENGINE", "silero"))

    whisper_model: str = field(default_factory=lambda: _str("WHISPER_MODEL", "large-v3-turbo"))
    whisper_device: str = field(default_factory=lambda: _str("WHISPER_DEVICE", "cuda"))
    whisper_compute_type: str = field(default_factory=lambda: _str("WHISPER_COMPUTE_TYPE", "float16"))
    whisper_language: str = field(default_factory=lambda: _str("WHISPER_LANGUAGE", "tr"))

    vad_threshold: float = field(default_factory=lambda: _float("VAD_THRESHOLD", 0.25))
    vad_silence_ms: int = field(default_factory=lambda: _int("VAD_SILENCE_MS", 900))
    vad_start_timeout_ms: int = field(default_factory=lambda: _int("VAD_START_TIMEOUT_MS", 6000))
    vad_max_utterance_ms: int = field(default_factory=lambda: _int("VAD_MAX_UTTERANCE_MS", 30000))

    # Yanıttan sonra uyandırma kelimesi beklemeden dinlenen süre.
    # 0 yaparsan her komut için tekrar "Hey Jarvis" demen gerekir.
    followup_window_ms: int = field(default_factory=lambda: _int("FOLLOWUP_WINDOW_MS", 7000))
    # Jarvis bir soru sorduysa daha uzun beklenir; yanıt bekleniyor demektir.
    followup_question_ms: int = field(default_factory=lambda: _int("FOLLOWUP_QUESTION_MS", 15000))

    tts_engine: str = field(default_factory=lambda: _str("TTS_ENGINE", "edge"))
    tts_voice: str = field(default_factory=lambda: _str("TTS_VOICE", "tr-TR-AhmetNeural"))

    sample_rate: int = 16000


@dataclass(frozen=True)
class BrainConfig:
    # Boş bırakılırsa Claude Code'un varsayılan modeli kullanılır.
    model: str = field(default_factory=lambda: _str("CLAUDE_MODEL"))
    # Tek bir komut için izin verilen azami araç turu (sonsuz döngü emniyeti).
    max_turns: int = field(default_factory=lambda: _int("CLAUDE_MAX_TURNS", 25))
    # Tek bir komuta ayrılan azami süre.
    timeout_sec: int = field(default_factory=lambda: _int("CLAUDE_TIMEOUT_SEC", 300))
    # SDK mesaj tamponu. Ekran görüntüleri base64 olarak buradan geçiyor.
    max_buffer_bytes: int = field(
        default_factory=lambda: _int("CLAUDE_MAX_BUFFER_MB", 32) * 1024 * 1024
    )


@dataclass(frozen=True)
class HudConfig:
    enabled: bool = field(default_factory=lambda: _bool("HUD_ENABLED", True))
    # Yalnızca yerel makineye bağlanır; ağdaki başka cihazlar erişemez.
    host: str = field(default_factory=lambda: _str("HUD_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("HUD_PORT", 8765))


def _approval_level() -> str:
    """Hangi risk seviyesinden itibaren onay isteneceğini belirler.

    medium   : yerel yazma ve üzeri sorulur (en sıkı, varsayılan)
    high     : kabuk komutu, pencere kapatma ve üzeri sorulur
    critical : yalnızca geri dönüşü olmayan işlemler sorulur
    none     : hiçbir şey sorulmaz (her şey yine de günlüğe yazılır)

    Eski AUTO_APPROVE=true ayarı 'high' anlamına gelir.
    """
    level = _str("APPROVAL_LEVEL").lower()
    if level in ("medium", "high", "critical", "none"):
        return level
    return "high" if _bool("AUTO_APPROVE", False) else "medium"


@dataclass(frozen=True)
class SecurityConfig:
    approval_level: str = field(default_factory=_approval_level)
    approval_timeout_sec: int = field(default_factory=lambda: _int("APPROVAL_TIMEOUT_SEC", 60))
    # Bu araçlar seviyeden bağımsız olarak hiç sormaz. Virgülle ayır.
    always_allow: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            t.strip() for t in _str("ALWAYS_ALLOW").split(",") if t.strip()
        )
    )
    # Bu araçlar seviyeden bağımsız olarak her zaman sorar.
    always_ask: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            t.strip() for t in _str("ALWAYS_ASK").split(",") if t.strip()
        )
    )


@dataclass(frozen=True)
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    brain: BrainConfig = field(default_factory=BrainConfig)
    hud: HudConfig = field(default_factory=HudConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    root: Path = ROOT
    log_dir: Path = LOG_DIR
    memory_dir: Path = MEMORY_DIR
    model_dir: Path = MODEL_DIR

    def ensure_dirs(self) -> None:
        for path in (self.log_dir, self.memory_dir, self.model_dir):
            path.mkdir(parents=True, exist_ok=True)


def load() -> Config:
    config = Config()
    config.ensure_dirs()
    return config
