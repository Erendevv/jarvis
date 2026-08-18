"""HUD katmanı: yerel web arayüzü, canlı olay akışı, tıklanabilir onay."""

from __future__ import annotations

import threading

from ..logger import console
from ..policy import ApprovalRequest, ask_console, is_yes, prompt_text
from .bus import EventBus
from .server import HudServer

__all__ = ["EventBus", "HudServer", "make_asker", "attach_audit"]


def attach_audit(audit, bus: EventBus) -> None:  # noqa: ANN001
    """Denetim günlüğüne düşen her olayı HUD'a da yollar."""
    audit.sinks.append(lambda kind, message, payload: bus.publish(kind, message, **payload))


def make_asker(bus: EventBus, on_prompt=None):  # noqa: ANN001, ANN201
    """Hem HUD'dan hem konsoldan onay kabul eden bir sorucu üretir.

    İki kanal paralel açılır ve ilk gelen yanıt kazanır: HUD'da butona
    basmak da, terminale 'evet' yazmak da işe yarar. Süre dolarsa yanıt
    reddedilmiş sayılır.
    """

    def ask(request: ApprovalRequest) -> bool:
        if on_prompt is not None:
            on_prompt(request)

        approval = bus.request_approval(
            tool=request.tool,
            risk=request.risk.name,
            detail=request.detail,
            require_phrase=request.require_phrase,
            timeout_sec=request.timeout_sec,
        )

        def watch_console() -> None:
            try:
                answer = input()
            except (EOFError, RuntimeError):
                return
            bus.resolve_approval(
                approval.id, is_yes(answer, request.require_phrase), source="konsol"
            )

        console().print(prompt_text(request))
        threading.Thread(target=watch_console, daemon=True).start()

        approval.event.wait(request.timeout_sec)
        bus.finish_approval(approval.id)
        return approval.approved

    return ask


def console_only_asker(on_prompt=None):  # noqa: ANN001, ANN201
    """HUD kapalıyken kullanılan basit konsol sorucusu."""

    def ask(request: ApprovalRequest) -> bool:
        if on_prompt is not None:
            on_prompt(request)
        return ask_console(request)

    return ask
