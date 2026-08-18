"""HUD web sunucusu.

Ayrı bir iş parçacığında uvicorn çalıştırır; ana ses döngüsünü bloklamaz.
Tarayıcı WebSocket ile bağlanır, olayları canlı alır, onayları geri gönderir.

Sunucu yalnızca 127.0.0.1'e bağlanır — ağdaki başka makineler erişemez.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from ..desktop import actions
from .bus import EventBus

STATIC = Path(__file__).parent / "static"


def create_app(bus: EventBus) -> FastAPI:
    app = FastAPI(title="Jarvis HUD")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.websocket("/ws")
    async def websocket(socket: WebSocket) -> None:
        await socket.accept()
        queue = bus.register()

        # Yeni istemciye önce mevcut durumu ve geçmişi ver.
        await socket.send_json({"kind": "snapshot", "state": bus.state, "events": bus.snapshot()})
        for approval in bus.pending.values():
            if not approval.event.is_set():
                await socket.send_json({
                    "kind": "approval_request",
                    "id": approval.id,
                    "tool": approval.tool,
                    "risk": approval.risk,
                    "detail": approval.detail,
                    "require_phrase": approval.require_phrase,
                    "timeout_sec": approval.timeout_sec,
                    "message": f"{approval.tool} için onay bekleniyor",
                })

        async def pump() -> None:
            while True:
                event = await queue.get()
                await socket.send_json(event)

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                data = await socket.receive_json()
                if data.get("type") == "approval":
                    bus.resolve_approval(data["id"], bool(data.get("approved")), source="hud")
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            pump_task.cancel()
            bus.unregister(queue)

    @app.on_event("startup")
    async def on_startup() -> None:
        bus.attach_loop(asyncio.get_running_loop())
        asyncio.create_task(_status_loop(bus))

    return app


async def _status_loop(bus: EventBus, interval: float = 3.0) -> None:
    """Sistem göstergelerini düzenli aralıkla yayınlar."""
    while True:
        try:
            status = await asyncio.to_thread(actions.system_status)
            bus.publish("system", **status)
        except Exception:
            pass
        await asyncio.sleep(interval)


class HudServer:
    def __init__(self, bus: EventBus, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.bus = bus
        self.host = host
        self.port = port
        self.url = f"http://{host}:{port}"
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self, timeout: float = 8.0) -> None:
        """Sunucuyu ayrı iş parçacığında başlatır ve gerçekten açıldığını doğrular.

        uvicorn bağlanamazsa (port dolu gibi) iş parçacığı sessizce ölüyor;
        doğrulamazsak Jarvis "HUD açık" der ama arayüz hiç gelmez.
        """
        config = uvicorn.Config(
            create_app(self.bus),
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True, name="jarvis-hud")
        self._thread.start()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server.started:
                return
            if not self._thread.is_alive():
                raise RuntimeError(
                    f"HUD sunucusu {self.host}:{self.port} adresinde başlatılamadı. "
                    "Port başka bir uygulamada olabilir; .env içinde HUD_PORT değiştir "
                    "ya da --no-hud ile çalıştır."
                )
            time.sleep(0.1)

        raise RuntimeError(f"HUD sunucusu {timeout:.0f} saniyede açılmadı.")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
