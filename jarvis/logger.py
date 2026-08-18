"""Denetim (audit) günlüğü ve konsol çıktısı.

Her olay iki yere yazılır:
  - logs/audit-YYYY-MM-DD.jsonl : makine tarafından okunabilir, değişmez kayıt
  - konsol : insan tarafından okunabilir renkli çıktı

Amaç, asistanın ne duyduğunu, ne anladığını ve ne yaptığını sonradan
eksiksiz inceleyebilmek.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rich.console import Console

_console = Console()
_lock = threading.Lock()

_STYLES = {
    "wake": "bold cyan",
    "heard": "bold white",
    "speak": "green",
    "action": "yellow",
    "approval": "bold magenta",
    "denied": "bold red",
    "error": "bold red",
    "info": "dim",
}


class AuditLog:
    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # Ek dinleyiciler (ör. HUD). Diske yazma her zaman önce yapılır;
        # bir dinleyicinin patlaması günlüğü bozmaz.
        self.sinks: list[Callable[[str, str, dict[str, Any]], None]] = []

    def _path(self) -> Path:
        day = datetime.now().strftime("%Y-%m-%d")
        return self.log_dir / f"audit-{day}.jsonl"

    def event(self, kind: str, message: str = "", **payload: Any) -> None:
        """Bir olayı diske yaz ve konsola bas."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "message": message,
            **payload,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _lock:
            with self._path().open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        self._print(kind, message, payload)
        for sink in self.sinks:
            try:
                sink(kind, message, payload)
            except Exception:
                continue

    def _print(self, kind: str, message: str, payload: dict[str, Any]) -> None:
        style = _STYLES.get(kind, "")
        stamp = datetime.now().strftime("%H:%M:%S")
        text = message
        if not text and payload:
            text = json.dumps(payload, ensure_ascii=False, default=str)
        _console.print(f"[dim]{stamp}[/dim] [{style}]{kind:<9}[/{style}] {text}")

    # Kısayollar
    def info(self, message: str, **payload: Any) -> None:
        self.event("info", message, **payload)

    def error(self, message: str, **payload: Any) -> None:
        self.event("error", message, **payload)


def console() -> Console:
    return _console
