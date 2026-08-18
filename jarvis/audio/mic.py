"""Mikrofon akışı ve cihaz listeleme.

Tek bir 16 kHz mono int16 akış açılır; hem uyandırma kelimesi hem VAD hem de
kayıt aynı akıştan beslenir. Ses geri çağrısı (callback) gerçek zamanlıdır,
bu yüzden içinde hiçbir ağır iş yapılmaz: kareler sadece kuyruğa atılır.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass

import numpy as np
import sounddevice as sd


@dataclass
class DeviceInfo:
    index: int
    name: str
    channels: int
    default: bool


def list_input_devices() -> list[DeviceInfo]:
    default_index = sd.default.device[0]
    devices: list[DeviceInfo] = []
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] < 1:
            continue
        devices.append(
            DeviceInfo(
                index=index,
                name=device["name"],
                channels=device["max_input_channels"],
                default=index == default_index,
            )
        )
    return devices


class MicStream:
    """Sabit uzunlukta int16 kareler üreten mikrofon akışı."""

    def __init__(self, frame_length: int, sample_rate: int = 16000, device: int | None = None) -> None:
        self.frame_length = frame_length
        self.sample_rate = sample_rate
        self.device = device
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._stream: sd.InputStream | None = None
        self.muted = False

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            # Aşırı yüklenme gibi durumlar; kareyi yine de geçiriyoruz.
            pass
        if self.muted:
            return
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            # Tüketici geride kaldı; en eskiyi düşür ki gecikme birikmesin.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(indata[:, 0].copy())
            except queue.Empty:
                pass

    def __enter__(self) -> "MicStream":
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=self.frame_length,
            device=self.device,
            channels=1,
            dtype="int16",
            callback=self._callback,
        )
        self._stream.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        """Bir kare döndürür; süre dolarsa None."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def flush(self) -> None:
        """Biriken kareleri at. Konuşma çaldıktan sonra kullanılır."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
