"""Ses katmanının ana döngüsü.

Akış:
    uyandırma kelimesi bekle  ->  bip  ->  konuşma başlayana kadar bekle
    ->  sessizlik olana kadar kaydet  ->  Whisper ile yazıya çevir  ->  metni ver

Metin bir geri çağrıya (handler) verilir. Aşama 1'de bu geri çağrı metni
ekrana basar; Aşama 2'de Claude'a iletecek.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..config import Config
from ..logger import AuditLog
from . import vad as vad_module
from . import wake as wake_module
from .mic import MicStream
from .stt import Transcriber
from .tts import Speaker


def spoken_form(keyword: str) -> str:
    """'hey_jarvis' -> 'Hey Jarvis' (kullanıcıya gösterilecek biçim)."""
    return " ".join(part.capitalize() for part in keyword.replace("_", " ").split())


def _beep(frequency: int = 880, duration_ms: int = 120) -> None:
    if sys.platform == "win32":
        import winsound

        winsound.Beep(frequency, duration_ms)


@dataclass
class Utterance:
    text: str
    audio_seconds: float
    stt_seconds: float


class Listener:
    def __init__(self, config: Config, audit: AuditLog, bus: Any = None) -> None:
        self.config = config
        self.audit = audit
        self.audio_config = config.audio
        # HUD veri yolu (isteğe bağlı). Ördek tiplemesi: publish/set_phase.
        self.bus = bus

        self.wake = wake_module.create(self.audio_config)
        self.vad = vad_module.create(self.audio_config)
        self.speaker = Speaker(self.audio_config.tts_engine, self.audio_config.tts_voice)
        audit.info(
            "Uyandırma motoru hazır",
            engine=self.wake.engine,
            keyword=self.wake.keyword,
            frame_length=self.wake.frame_length,
        )

        audit.info(
            "STT modeli yükleniyor (ilk çalıştırmada model indirilir, sabırlı ol)",
            model=self.audio_config.whisper_model,
        )
        self.stt = Transcriber(self.audio_config, config.model_dir)
        if self.stt.fallback_reason:
            audit.error(
                "CUDA başlatılamadı, CPU'ya düşüldü",
                detail=self.stt.fallback_reason[:300],
            )
        audit.info(
            "STT hazır",
            device=self.stt.device,
            compute_type=self.stt.compute_type,
            vad_backend=self.vad.backend,
        )

        self._frames_per_ms = self.audio_config.sample_rate / 1000.0
        # listen() çalışırken açık olan mikrofon akışı. say() konuşurken
        # bunu susturur; yoksa hoparlörden çıkan ses uyandırma kelimesini
        # yeniden tetikleyebilir.
        self.stream: MicStream | None = None
        # Sıfırdan büyükse bir sonraki tur uyandırma kelimesi beklemez.
        self._followup_ms = 0

    def close(self) -> None:
        self.wake.close()
        self.vad.close()

    def _phase(self, name: str) -> None:
        if self.bus is not None:
            self.bus.set_phase(name)

    def _mic_level(self, frame: np.ndarray) -> None:
        if self.bus is None:
            return
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        self.bus.publish("mic", level=min(1.0, rms / 4000.0))

    def say(self, text: str, stream: MicStream | None = None) -> None:
        """Konuşur; kendi sesini duyup tetiklenmemesi için mikrofonu susturur."""
        stream = stream or self.stream
        if stream is not None:
            stream.muted = True
        self._phase("konusuyor")
        try:
            self.speaker.say(text)
        finally:
            if stream is not None:
                time.sleep(0.2)  # Hoparlör yankısının sönmesini bekle.
                stream.flush()
                stream.muted = False
            self._phase("dinlemede")

    def arm_followup(self, window_ms: int) -> None:
        """Bir sonraki turda uyandırma kelimesi beklemeden dinle.

        Jarvis bir soru sorduğunda ya da konuşma sürdüğünde, kullanıcının
        yanıt vermek için tekrar 'Hey Jarvis' demesi gerekmesin diye.
        """
        self._followup_ms = max(0, window_ms)

    def _capture(self, stream: MicStream, start_timeout_ms: int | None = None) -> np.ndarray | None:
        """Konuşmayı kaydeder. Konuşma başlamazsa None."""
        frame_ms = self.wake.frame_length / self._frames_per_ms
        silence_frames_needed = int(self.audio_config.vad_silence_ms / frame_ms)
        timeout_ms = (
            self.audio_config.vad_start_timeout_ms if start_timeout_ms is None else start_timeout_ms
        )
        start_timeout_frames = int(timeout_ms / frame_ms)
        max_frames = int(self.audio_config.vad_max_utterance_ms / frame_ms)

        collected: list[np.ndarray] = []
        silence_run = 0
        waited = 0
        speaking = False

        while True:
            frame = stream.read()
            if frame is None:
                continue

            self._mic_level(frame)
            is_voice = self.vad.probability(frame) >= self.audio_config.vad_threshold

            if not speaking:
                waited += 1
                if is_voice:
                    speaking = True
                    collected.append(frame)
                elif waited >= start_timeout_frames:
                    return None
                continue

            collected.append(frame)
            silence_run = 0 if is_voice else silence_run + 1

            if silence_run >= silence_frames_needed:
                break
            if len(collected) >= max_frames:
                self.audit.info("Maksimum konuşma süresine ulaşıldı, kayıt kesildi")
                break

        # Sondaki sessizliği kırp ama VAD'in kaçırdığı hece sonlarını korumak
        # için yarım pencere bırak.
        keep = max(0, len(collected) - silence_frames_needed // 2)
        return np.concatenate(collected[:keep]) if keep else None

    def listen(self) -> Iterator[Utterance]:
        """Sonsuz döngü: her tamamlanan konuşmayı Utterance olarak verir."""
        with MicStream(
            frame_length=self.wake.frame_length,
            sample_rate=self.audio_config.sample_rate,
            device=self.audio_config.input_device,
        ) as stream:
            self.stream = stream
            phrase = spoken_form(self.wake.keyword)
            self.audit.info(f"Dinlemede. Uyandırmak için '{phrase}' de. Çıkış: Ctrl+C")
            self._phase("dinlemede")
            while True:
                followup_ms = self._followup_ms
                self._followup_ms = 0

                if followup_ms:
                    # Takip turu: uyandırma kelimesi aranmaz, doğrudan dinlenir.
                    self.audit.info(
                        f"Devam dinlemesi ({followup_ms // 1000} sn) — "
                        f"'{phrase}' demene gerek yok"
                    )
                    self._phase("takip")
                    self.wake.reset()
                    self.vad.reset()
                    started = time.perf_counter()
                    audio = self._capture(stream, start_timeout_ms=followup_ms)
                    if audio is None or audio.size == 0:
                        self.audit.info("Devam gelmedi, uykuya dönüldü")
                        self._phase("dinlemede")
                        continue
                else:
                    frame = stream.read()
                    if frame is None or not self.wake.process(frame):
                        continue

                    self.audit.event("wake", f"'{phrase}' algılandı")
                    self._phase("duyuyor")
                    _beep()
                    # Uyandırma kelimesinin kendi sesi hâlâ tamponlarda; temizle
                    # ki aynı sözcük ikinci kez tetiklemesin ve VAD'a sızmasın.
                    self.wake.reset()
                    self.vad.reset()

                    started = time.perf_counter()
                    audio = self._capture(stream)
                    if audio is None or audio.size == 0:
                        self.audit.info("Konuşma algılanmadı, uykuya dönüldü")
                        self._phase("dinlemede")
                        continue

                self._phase("dusunuyor")

                audio_seconds = audio.size / self.audio_config.sample_rate
                stt_started = time.perf_counter()
                was_on_gpu = self.stt.device != "cpu"
                try:
                    text = self.stt.transcribe(audio)
                except Exception as exc:
                    # Tek bir başarısız transkripsiyon asistanı öldürmemeli.
                    self.audit.error(f"Transkripsiyon başarısız: {exc}")
                    self.say("Seni anlayamadım, tekrar dener misin?")
                    continue
                stt_seconds = time.perf_counter() - stt_started

                if was_on_gpu and self.stt.device == "cpu":
                    self.audit.error(
                        "GPU belleği tükendi, kalıcı olarak CPU'ya geçildi (daha yavaş)",
                        detail=(self.stt.fallback_reason or "")[:300],
                        downgraded_model=self.stt.downgraded_model,
                    )
                    if self.stt.downgraded_model:
                        self.audit.error(
                            f"RAM de dar olduğu için '{self.stt.downgraded_model}' modeline "
                            "inildi; tanıma doğruluğu düşer"
                        )

                if not text:
                    self.audit.info("Yazıya çevrilecek konuşma çıkmadı")
                    continue

                self.audit.event(
                    "heard",
                    text,
                    audio_seconds=round(audio_seconds, 2),
                    stt_seconds=round(stt_seconds, 2),
                    total_seconds=round(time.perf_counter() - started, 2),
                )
                yield Utterance(text=text, audio_seconds=audio_seconds, stt_seconds=stt_seconds)
