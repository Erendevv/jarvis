"""Olay veri yolu: arka plandaki iş parçacıkları ile HUD arasındaki köprü.

Jarvis'te üç ayrı iş parçacığı var (ses döngüsü, beyin olay döngüsü, HUD
sunucusu) ve olaylar her üçünden de doğabiliyor. EventBus bunları tek bir
yerde toplayıp bağlı tüm HUD istemcilerine dağıtır.

Onay akışı da buradan geçer: onay isteyen iş parçacığı bloklanır, HUD'dan
ya da konsoldan gelen ilk yanıt onu serbest bırakır.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class PendingApproval:
    id: str
    tool: str
    risk: str
    detail: str
    require_phrase: str | None
    timeout_sec: int
    created: float
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


class EventBus:
    def __init__(self, history: int = 200) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._history: deque[dict] = deque(maxlen=history)
        self.pending: dict[str, PendingApproval] = {}
        # HUD'un göstermesi için son bilinen durum.
        self.state: dict[str, Any] = {"phase": "baslatiliyor", "mic_level": 0.0}

    # --- sunucu tarafı ---

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def register(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._queues.add(queue)
        return queue

    def unregister(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._queues.discard(queue)

    def snapshot(self) -> list[dict]:
        """Yeni bağlanan istemciye verilecek geçmiş."""
        with self._lock:
            return list(self._history)

    # --- yayın ---

    def publish(self, kind: str, message: str = "", **payload: Any) -> None:
        """Her iş parçacığından güvenle çağrılabilir."""
        event = {
            "kind": kind,
            "message": message,
            "ts": datetime.now().strftime("%H:%M:%S"),
            **payload,
        }
        if kind != "mic":  # Ses seviyesi çok sık gelir, geçmişi doldurmasın.
            with self._lock:
                self._history.append(event)

        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._fanout, event)

    def _fanout(self, event: dict) -> None:
        with self._lock:
            queues = list(self._queues)
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def set_phase(self, phase: str, **extra: Any) -> None:
        self.state["phase"] = phase
        self.state.update(extra)
        self.publish("phase", phase, **extra)

    # --- onay ---

    def request_approval(
        self,
        tool: str,
        risk: str,
        detail: str,
        require_phrase: str | None,
        timeout_sec: int,
    ) -> PendingApproval:
        approval = PendingApproval(
            id=uuid.uuid4().hex[:8],
            tool=tool,
            risk=risk,
            detail=detail,
            require_phrase=require_phrase,
            timeout_sec=timeout_sec,
            created=datetime.now().timestamp(),
        )
        self.pending[approval.id] = approval
        self.publish(
            "approval_request",
            f"{tool} için onay bekleniyor",
            id=approval.id,
            tool=tool,
            risk=risk,
            detail=detail,
            require_phrase=require_phrase,
            timeout_sec=timeout_sec,
        )
        return approval

    def resolve_approval(self, approval_id: str, approved: bool, source: str) -> bool:
        """HUD veya konsoldan gelen yanıtı işler. İlk yanıt kazanır."""
        approval = self.pending.get(approval_id)
        if approval is None or approval.event.is_set():
            return False
        approval.approved = approved
        approval.event.set()
        self.publish(
            "approval_result",
            f"{approval.tool}: {'onaylandı' if approved else 'reddedildi'}",
            id=approval_id,
            approved=approved,
            source=source,
        )
        return True

    def finish_approval(self, approval_id: str) -> None:
        self.pending.pop(approval_id, None)
