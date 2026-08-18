"""Konuşmadan metne (faster-whisper).

Model ilk çalıştırmada indirilir ve models/ altında önbelleklenir. CUDA
kullanılıyorsa gerekli NVIDIA DLL yolları config.add_cuda_dll_dirs() ile
eklenir.

Dayanıklılık: GPU belleği iki ayrı anda tükenebilir — model yüklenirken ve
transkripsiyon sırasında. İkisi de yakalanır ve kalıcı olarak CPU'ya
düşülür; asistan çökmek yerine yavaşlayarak devam eder.
"""

from __future__ import annotations

import gc
from pathlib import Path

import numpy as np

from ..config import AudioConfig, add_cuda_dll_dirs

# Bu ifadeler geçen hatalar donanım kaynaklı sayılır ve CPU'ya düşmeyi tetikler.
_GPU_FAILURE_MARKERS = (
    "out of memory",
    "cudaerror",
    "cublas",
    "cudnn",
    "no kernel image",
    "cuda driver",
)


def _is_gpu_failure(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _GPU_FAILURE_MARKERS)


class Transcriber:
    def __init__(self, config: AudioConfig, model_dir: Path) -> None:
        # DLL yolları faster_whisper/ctranslate2 import edilmeden önce eklenmeli.
        add_cuda_dll_dirs()

        self.config = config
        self.model_dir = model_dir
        self.device = config.whisper_device
        self.compute_type = config.whisper_compute_type
        self.fallback_reason: str | None = None
        # CPU'ya düşerken daha küçük bir modele geçildiyse adı burada durur.
        self.downgraded_model: str | None = None

        try:
            self._model = self._load(self.device, self.compute_type)
        except Exception as exc:
            if self.device == "cpu":
                raise
            self._fall_back_to_cpu(exc)

    def _load(self, device: str, compute_type: str, model_name: str | None = None):  # noqa: ANN202
        from faster_whisper import WhisperModel

        return WhisperModel(
            model_name or self.config.whisper_model,
            device=device,
            compute_type=compute_type,
            download_root=str(self.model_dir),
        )

    def _fall_back_to_cpu(self, exc: Exception) -> None:
        """GPU'yu bırakır, modeli CPU'da yeniden yükler.

        Bir kez düşüldükten sonra GPU'ya geri dönülmez: bellek baskısı
        genellikle sürer ve her komutta yeniden denemek asistanı kilitler.

        İki incelik var:
          - Yeni model yüklenmeden ÖNCE eskisi serbest bırakılmalı. Aksi
            halde iki model bir arada bellekte durur ve CPU'ya düşüş,
            düşüşün sebebi olan bellek darlığı yüzünden patlar.
          - CPU'da RAM de yetmeyebilir. O yüzden yapılandırılan modelden
            başlayıp giderek küçülen bir zincir denenir; sesi hiç
            çevirememektense küçük modelle çevirmek yeğdir.
        """
        self.fallback_reason = str(exc)
        self.device = "cpu"
        self.compute_type = "int8"

        self._model = None
        gc.collect()

        errors: list[str] = []
        for name in self._cpu_model_chain():
            try:
                self._model = self._load("cpu", "int8", name)
            except Exception as load_error:
                errors.append(f"{name}: {load_error}")
                gc.collect()
                continue
            if name != self.config.whisper_model:
                self.downgraded_model = name
            return

        raise RuntimeError(
            "GPU başarısız oldu ve CPU'da hiçbir model yüklenemedi. "
            + " | ".join(errors)
        )

    def _cpu_model_chain(self) -> list[str]:
        """Yapılandırılan modelden başlayıp küçülen deneme sırası."""
        chain = [self.config.whisper_model]
        for name in ("medium", "small", "base"):
            if name not in chain:
                chain.append(name)
        return chain

    def transcribe(self, audio: np.ndarray) -> str:
        """int16 PCM dizisini metne çevirir."""
        try:
            return self._run(audio)
        except Exception as exc:
            if self.device == "cpu" or not _is_gpu_failure(exc):
                raise
            # GPU çalışma anında düştü (ör. VRAM doldu). CPU'ya geçip
            # aynı sesi bir kez daha dene; kullanıcı komutunu kaybetmesin.
            self._fall_back_to_cpu(exc)
            return self._run(audio)

    def _run(self, audio: np.ndarray) -> str:
        samples = audio.astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(
            samples,
            language=self.config.whisper_language or None,
            beam_size=5,
            vad_filter=False,  # Kayıt zaten VAD ile kırpıldı.
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
