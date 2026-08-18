"""Claude Agent SDK köprüsü.

Ses katmanı senkron (mikrofon geri çağrıları bloklayıcı), SDK ise asenkron.
Bu yüzden ayrı bir iş parçacığında bir olay döngüsü çalıştırıp senkron bir
`ask()` arayüzü sunuyoruz.

Oturum SDK istemcisinde açık kalır: aynı konuşma boyunca Claude önceki
turları hatırlar. Kalıcı hafıza (oturumlar arası) `memory.py` tarafında.

Güvenlik: `allowed_tools` bilerek boş bırakıldı. Böylece HER araç çağrısı
`can_use_tool` geri çağrısına, oradan da PolicyGate'e düşer. Hiçbir araç
kapıyı atlayamaz.
"""

from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookContext,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
)

from ..config import Config
from ..desktop import build as desktop_server
from ..logger import AuditLog
from ..policy import PolicyGate, classify
from .memory import Memory
from .prompt import build as build_prompt

# Sesli okunduğunda anlamsız olan markdown işaretleri.
_MARKDOWN = re.compile(r"[*_`#>]|^\s*[-•]\s+", re.MULTILINE)


def speakable(text: str) -> str:
    """Markdown süslemelerini atıp seslendirmeye uygun düz metin üretir."""
    text = re.sub(r"```.*?```", " kod bloğu ", text, flags=re.DOTALL)
    text = _MARKDOWN.sub("", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


@dataclass
class Reply:
    text: str
    session_id: str | None = None
    cost_usd: float | None = None
    turns: int | None = None


class Brain:
    """Claude Code'u sesli asistanın beyni olarak çalıştırır."""

    def __init__(self, config: Config, audit: AuditLog, gate: PolicyGate) -> None:
        self.config = config
        self.audit = audit
        self.gate = gate
        self.memory = Memory(config.memory_dir, config.root / "state.db")
        self.session_id: str | None = None

        self._client: ClaudeSDKClient | None = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="jarvis-brain")
        self._thread.start()

    # --- olay döngüsü altyapısı ---

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _call(self, coro, timeout: float | None = None):  # noqa: ANN001
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    # --- güvenlik kapısı ---

    async def _can_use_tool(
        self,
        tool_name: str,
        input_data: dict,
        context: ToolPermissionContext,  # noqa: ARG002
    ) -> PermissionResultAllow | PermissionResultDeny:
        # Emniyet ağı. Normalde _pre_tool_use kararı verdiği için buraya
        # hiç düşülmez; yine de bir yol kalırsa aynı kapıdan geçirilir.
        # PolicyGate bloklayıcı olduğundan ayrı iş parçacığında çalıştırılır.
        decision = await asyncio.to_thread(self.gate.check, tool_name, input_data)
        if decision.allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(
            message=(
                "Kullanıcı bu işlemi onaylamadı. Aynı işi başka bir araçla "
                "denemeyi bırak ve kullanıcıya reddedildiğini bildir."
            )
        )

    # --- asıl zorlama noktası ---

    async def _pre_tool_use(
        self,
        input_data: dict,
        tool_use_id: str | None,
        context: HookContext,  # noqa: ARG002
    ) -> dict:
        """Her araç çağrısını günlüğe yazar ve PolicyGate kararını uygular.

        Neden can_use_tool değil de burası: Claude Code bazı araçları (Read,
        Glob, Grep ve "ls" gibi zararsız saydığı Bash komutları) kendi izin
        katmanında onaylıyor ve can_use_tool'a hiç uğratmıyor. PreToolUse
        kancası ise istisnasız her çağrıda çalışır. Kararı burada verip
        "allow"/"deny" döndürerek hem eksiksiz günlük tutuyor hem de tek bir
        onay istemi çıkmasını sağlıyoruz.
        """
        tool = input_data.get("tool_name", "?")
        payload = input_data.get("tool_input", {})

        risk, _ = classify(tool, payload)
        self.audit.event(
            "action",
            f"{tool} çağrıldı",
            tool=tool,
            risk=risk.name,
            tool_use_id=tool_use_id,
            payload=str(payload)[:2000],
        )

        decision = await asyncio.to_thread(self.gate.check, tool, payload)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow" if decision.allowed else "deny",
                "permissionDecisionReason": decision.reason,
            }
        }

    # --- yaşam döngüsü ---

    def start(self) -> None:
        self._call(self._start(), timeout=120)
        self.audit.info("Beyin hazır", cwd=str(self.config.root))

    async def _start(self) -> None:
        options = ClaudeAgentOptions(
            system_prompt=build_prompt(self.memory, self.config.root),
            cwd=str(self.config.root),
            # Kullanıcının global Claude Code ayarlarını ve CLAUDE.md
            # dosyalarını yükleme; asistanın davranışı yalnızca buradaki
            # sistem isteminden gelsin.
            setting_sources=[],
            mcp_servers={"desktop": desktop_server(self.config.log_dir / "screenshots")},
            # Boş: her araç çağrısı can_use_tool'a düşsün.
            allowed_tools=[],
            can_use_tool=self._can_use_tool,
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[self._pre_tool_use])]},
            permission_mode="default",
            model=self.config.brain.model or None,
            max_turns=self.config.brain.max_turns,
            # Ekran görüntüleri base64 olarak mesaj akışına giriyor; varsayılan
            # 1 MB tampon tek bir görüntüde bile taşıyor ve oturum çöküyor.
            max_buffer_size=self.config.brain.max_buffer_bytes,
        )
        self._client = ClaudeSDKClient(options=options)
        await self._client.connect()

    def close(self) -> None:
        if self._client is not None:
            try:
                self._call(self._client.disconnect(), timeout=30)
            except Exception:
                pass
            self._client = None
        self._loop.call_soon_threadsafe(self._loop.stop)

    # --- ana giriş ---

    def ask(self, text: str) -> Reply:
        """Kullanıcının komutunu Claude'a iletir, yanıtı döndürür."""
        if self._client is None:
            raise RuntimeError("Brain.start() çağrılmadı")
        reply = self._call(self._ask(text), timeout=self.config.brain.timeout_sec)
        self.memory.log_turn(text, reply.text, reply.session_id)
        return reply

    async def _ask(self, text: str) -> Reply:
        assert self._client is not None
        await self._client.query(text)

        chunks: list[str] = []
        session_id: str | None = None
        cost: float | None = None
        turns: int | None = None

        async for message in self._client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
            elif isinstance(message, ResultMessage):
                session_id = getattr(message, "session_id", None)
                cost = getattr(message, "total_cost_usd", None)
                turns = getattr(message, "num_turns", None)

        if session_id:
            self.session_id = session_id

        return Reply(
            text=speakable("\n".join(chunks)),
            session_id=session_id,
            cost_usd=cost,
            turns=turns,
        )
