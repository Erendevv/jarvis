"""Uyandırma kelimesi algılama.

İki motor desteklenir, .env içindeki WAKE_ENGINE ile seçilir:

  openwakeword (varsayılan)
      Tamamen açık kaynak, kayıt/anahtar gerektirmez, onnxruntime ile CPU'da
      çalışır. Hazır model: "hey_jarvis" — yani "Hey Jarvis" demen gerekir.

  porcupine
      Picovoice Porcupine. Daha düşük CPU ve daha az yanlış tetikleme, ama
      ücretsiz bir AccessKey almak için kayıt gerekir.

İkisi de tamamen yerel çalışır; ses hiçbir sunucuya gitmez.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from ..config import AudioConfig


class WakeWordError(RuntimeError):
    pass


class WakeWordBase(ABC):
    """Ortak arayüz: mikrofon akışı frame_length'e göre yapılandırılır."""

    keyword: str
    engine: str

    @property
    @abstractmethod
    def frame_length(self) -> int:
        """Bu motorun beklediği kare uzunluğu (16 kHz'de örnek sayısı)."""

    @abstractmethod
    def process(self, frame: np.ndarray) -> bool:
        """Kare uyandırma kelimesini içeriyorsa True."""

    def reset(self) -> None:
        """Tetiklemeden sonra iç durumu temizler."""

    def close(self) -> None:
        pass


class OpenWakeWord(WakeWordBase):
    engine = "openwakeword"

    # openWakeWord 80 ms'lik karelerle çalışacak şekilde eğitildi.
    FRAME_LENGTH = 1280

    def __init__(self, keyword: str = "hey_jarvis", sensitivity: float = 0.6) -> None:
        import openwakeword
        from openwakeword.model import Model
        from openwakeword.utils import download_models

        available = set(openwakeword.MODELS)
        if keyword not in available:
            raise WakeWordError(
                f"'{keyword}' openWakeWord hazır modelleri arasında yok. "
                f"Seçenekler: {', '.join(sorted(available))}"
            )

        # Modeller yoksa indirilir; varsa bu çağrı hızlıca geçer.
        download_models([keyword])

        self.keyword = keyword
        # openWakeWord bir *eşik* ile çalışır: yüksek eşik = az tetikleme.
        # Kullanıcıya sunulan WAKE_SENSITIVITY ise "yüksek = kolay tetiklenir"
        # anlamında, bu yüzden ters çeviriyoruz.
        self.threshold = max(0.05, min(0.95, 1.0 - sensitivity))
        self._model = Model(wakeword_models=[keyword], inference_framework="onnx")

    @property
    def frame_length(self) -> int:
        return self.FRAME_LENGTH

    def process(self, frame: np.ndarray) -> bool:
        scores = self._model.predict(frame)
        return any(score >= self.threshold for score in scores.values())

    def reset(self) -> None:
        self._model.reset()


class Porcupine(WakeWordBase):
    engine = "porcupine"

    def __init__(self, access_key: str, keyword: str = "jarvis", sensitivity: float = 0.6) -> None:
        import pvporcupine

        if not access_key:
            raise WakeWordError(
                "WAKE_ENGINE=porcupine seçildi ama PICOVOICE_ACCESS_KEY boş. "
                "Anahtar almak istemiyorsan .env içinde WAKE_ENGINE=openwakeword yap."
            )
        if keyword not in pvporcupine.KEYWORDS:
            raise WakeWordError(
                f"'{keyword}' Porcupine hazır anahtar kelimeleri arasında yok. "
                f"Seçenekler: {', '.join(sorted(pvporcupine.KEYWORDS))}"
            )
        self._handle = pvporcupine.create(
            access_key=access_key,
            keywords=[keyword],
            sensitivities=[sensitivity],
        )
        self.keyword = keyword

    @property
    def frame_length(self) -> int:
        return self._handle.frame_length

    def process(self, frame: np.ndarray) -> bool:
        return self._handle.process(frame) >= 0

    def close(self) -> None:
        self._handle.delete()


DEFAULT_KEYWORD = {"openwakeword": "hey_jarvis", "porcupine": "jarvis"}


def create(config: AudioConfig) -> WakeWordBase:
    engine = (config.wake_engine or "openwakeword").lower()
    keyword = config.wake_word or DEFAULT_KEYWORD.get(engine, "")
    if engine == "porcupine":
        return Porcupine(config.picovoice_key, keyword, config.wake_sensitivity)
    if engine == "openwakeword":
        return OpenWakeWord(keyword, config.wake_sensitivity)
    raise WakeWordError(
        f"Bilinmeyen WAKE_ENGINE='{engine}'. Geçerli değerler: openwakeword, porcupine"
    )
