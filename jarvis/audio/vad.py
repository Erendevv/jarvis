"""Konuşma etkinliği algılama (VAD).

Üç arka uç, .env içindeki VAD_ENGINE ile seçilir:

  silero (varsayılan)
      openWakeWord ile birlikte gelen Silero VAD ONNX modeli. Anahtar
      gerektirmez, CPU'da çok hafif, gürültüye dayanıklı.

  cobra
      Picovoice Cobra. Porcupine ile aynı AccessKey'i kullanır.

  rms
      Basit enerji eşiği. Hiçbir bağımlılık gerektirmez ama gürültülü
      ortamda güvenilmez; yalnızca son çare.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from ..config import AudioConfig


class VadError(RuntimeError):
    pass


class VoiceDetectorBase(ABC):
    backend: str

    @abstractmethod
    def probability(self, frame: np.ndarray) -> float:
        """Karenin konuşma içerme olasılığı (0.0 - 1.0)."""

    def reset(self) -> None:
        pass

    def close(self) -> None:
        pass


class SileroVad(VoiceDetectorBase):
    backend = "silero"

    # Silero modeli sabit uzunlukta alt-parçalarla beslenir. Kare uzunluğunu
    # tam bölen bir değer seçmeliyiz, yoksa son parça eksik kalır.
    CHUNK_CANDIDATES = (480, 320, 256, 160)

    def __init__(self) -> None:
        from openwakeword.vad import VAD

        self._vad = VAD()
        self._chunk_cache: dict[int, int] = {}

    def _chunk_for(self, length: int) -> int:
        if length not in self._chunk_cache:
            self._chunk_cache[length] = next(
                (c for c in self.CHUNK_CANDIDATES if length % c == 0),
                length,
            )
        return self._chunk_cache[length]

    def probability(self, frame: np.ndarray) -> float:
        return float(self._vad.predict(frame, frame_size=self._chunk_for(frame.shape[0])))

    def reset(self) -> None:
        self._vad.reset_states()


class CobraVad(VoiceDetectorBase):
    backend = "cobra"

    def __init__(self, access_key: str) -> None:
        import pvcobra

        if not access_key:
            raise VadError(
                "VAD_ENGINE=cobra seçildi ama PICOVOICE_ACCESS_KEY boş. "
                ".env içinde VAD_ENGINE=silero yap."
            )
        self._handle = pvcobra.create(access_key=access_key)

    def probability(self, frame: np.ndarray) -> float:
        return float(self._handle.process(frame))

    def close(self) -> None:
        if self._handle is not None:
            self._handle.delete()
            self._handle = None


class RmsVad(VoiceDetectorBase):
    backend = "rms"

    def probability(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        return min(1.0, rms / 1500.0)


def create(config: AudioConfig) -> VoiceDetectorBase:
    engine = (config.vad_engine or "silero").lower()
    if engine == "silero":
        return SileroVad()
    if engine == "cobra":
        return CobraVad(config.picovoice_key)
    if engine == "rms":
        return RmsVad()
    raise VadError(f"Bilinmeyen VAD_ENGINE='{engine}'. Geçerli değerler: silero, cobra, rms")
