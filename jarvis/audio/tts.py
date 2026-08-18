"""Metinden konuşmaya.

İki motor:
  edge : Microsoft Edge ses servisi. Doğal Türkçe, internet gerektirir.
  sapi : Windows yerleşik SAPI (pyttsx3). Çevrimdışı, ses kalitesi düşük ve
         Türkçe ses yalnızca Türkçe dil paketi kuruluysa vardır.

edge başarısız olursa (internet yok, mp3 çözülemedi) otomatik sapi'ye düşer.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path


class Speaker:
    def __init__(self, engine: str = "edge", voice: str = "tr-TR-AhmetNeural") -> None:
        self.engine = engine
        self.voice = voice
        self._sapi = None

    def say(self, text: str) -> None:
        if not text.strip():
            return
        if self.engine == "edge":
            try:
                self._say_edge(text)
                return
            except Exception:
                self.engine = "sapi"
        self._say_sapi(text)

    def _say_edge(self, text: str) -> None:
        import edge_tts
        import sounddevice as sd
        import soundfile as sf

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "speech.mp3"

            async def render() -> None:
                communicate = edge_tts.Communicate(text, self.voice)
                await communicate.save(str(path))

            asyncio.run(render())
            data, rate = sf.read(str(path), dtype="float32")
            sd.play(data, rate)
            sd.wait()

    def _say_sapi(self, text: str) -> None:
        import pyttsx3

        # pyttsx3 motoru yeniden kullanıldığında Windows'ta takılabiliyor;
        # her seferinde taze motor açıp kapatmak en güvenilir yol.
        engine = pyttsx3.init()
        for voice in engine.getProperty("voices"):
            if "turkish" in voice.name.lower() or "tr-tr" in str(voice.id).lower():
                engine.setProperty("voice", voice.id)
                break
        engine.say(text)
        engine.runAndWait()
        engine.stop()
