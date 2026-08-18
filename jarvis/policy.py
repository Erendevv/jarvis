"""Güvenlik kapısı: hangi eylem onay ister, onay nasıl alınır.

Tasarım ilkeleri:
  1. Varsayılan güvenli. Sınıflandıramadığımız her eylem onay ister.
  2. Geri dönüşü olmayan eylemler (mail gönderme, silme, ödeme) AUTO_APPROVE
     açık olsa bile her zaman onay ister.
  3. Onay zaman aşımına uğrarsa sonuç REDDEDİLİR, kabul değil.
  4. Her karar denetim günlüğüne yazılır.

Aşama 1'de onay konsoldan yazıyla alınır. Aşama 2'de Claude Agent SDK'nın
can_use_tool geri çağrısı buraya bağlanır ve `ask` bir sesli onay
fonksiyonuyla değiştirilir.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable

from .logger import AuditLog, console


class Risk(IntEnum):
    LOW = 0       # Salt okuma. Onay gerekmez.
    MEDIUM = 1    # Yerel yazma/değiştirme. AUTO_APPROVE açıksa geçer.
    HIGH = 2      # Dışa dönük veya geri alınması zor. Her zaman onay ister.
    CRITICAL = 3  # Geri dönüşü yok: ödeme, kalıcı silme. Açık onay cümlesi ister.


# Araç adına göre taban risk. Aşama 2'de Claude Agent SDK araç adları buraya
# eklenir. Listede olmayan her araç UNKNOWN_RISK ile değerlendirilir.
TOOL_RISK: dict[str, Risk] = {
    "Read": Risk.LOW,
    "Glob": Risk.LOW,
    "Grep": Risk.LOW,
    "WebFetch": Risk.LOW,
    "WebSearch": Risk.LOW,
    # ToolSearch yalnızca araç şeması getirir, hiçbir yan etkisi yoktur.
    # Claude Code araç sayısı fazlaysa MCP araçlarını bunun üzerinden
    # yüklüyor; tanınmaz bırakılırsa her masaüstü komutu önce buna takılır.
    "ToolSearch": Risk.LOW,
    "TodoWrite": Risk.LOW,
    "BashOutput": Risk.LOW,
    "NotebookEdit": Risk.MEDIUM,
    "KillShell": Risk.MEDIUM,
    # Alt ajan başlatmak, bu kapının göremediği bir bağlamda araç
    # çalıştırabilir. Onaya tabi.
    "Task": Risk.HIGH,
    "Agent": Risk.HIGH,
    "SlashCommand": Risk.HIGH,
    "calendar_list_events": Risk.LOW,
    "gmail_list": Risk.LOW,
    "gmail_read": Risk.LOW,
    "memory_read": Risk.LOW,
    "Write": Risk.MEDIUM,
    "Edit": Risk.MEDIUM,
    "memory_write": Risk.MEDIUM,
    "browser_navigate": Risk.MEDIUM,
    "browser_read": Risk.LOW,
    "Bash": Risk.HIGH,
    "PowerShell": Risk.HIGH,
    "browser_click": Risk.HIGH,
    "browser_fill": Risk.HIGH,
    "browser_submit": Risk.HIGH,
    "calendar_create_event": Risk.HIGH,
    "calendar_delete_event": Risk.CRITICAL,
    "gmail_send": Risk.CRITICAL,
    "gmail_delete": Risk.CRITICAL,
    "file_delete": Risk.CRITICAL,
    "purchase": Risk.CRITICAL,
    "payment": Risk.CRITICAL,
    # --- masaüstü kontrolü (jarvis/desktop) ---
    "mcp__desktop__search_files": Risk.LOW,
    "mcp__desktop__list_windows": Risk.LOW,
    "mcp__desktop__get_volume": Risk.LOW,
    "mcp__desktop__system_status": Risk.LOW,
    "mcp__desktop__focus_window": Risk.LOW,
    "mcp__desktop__snap_window": Risk.LOW,
    "mcp__desktop__minimize_all": Risk.LOW,
    "mcp__desktop__set_volume": Risk.LOW,
    "mcp__desktop__set_mute": Risk.LOW,
    "mcp__desktop__media_key": Risk.LOW,
    "mcp__desktop__open_app": Risk.MEDIUM,
    "mcp__desktop__open_path": Risk.MEDIUM,
    "mcp__desktop__open_url": Risk.MEDIUM,
    # Ekran görüntüsü teknik olarak salt okuma ama ekranda parola, banka
    # ekranı, özel mesaj olabilir. Sessizce çekilmesin diye MEDIUM.
    "mcp__desktop__screenshot": Risk.MEDIUM,
    # Pencere kapatmak kaydedilmemiş işi yok edebilir.
    "mcp__desktop__window_action": Risk.MEDIUM,
    # --- ekran otomasyonu (fare/klavye) ---
    "mcp__desktop__cursor_position": Risk.LOW,
    "mcp__desktop__move_mouse": Risk.LOW,
    "mcp__desktop__scroll_screen": Risk.LOW,
    "mcp__desktop__click": Risk.MEDIUM,
    "mcp__desktop__drag": Risk.MEDIUM,
    "mcp__desktop__type_text": Risk.MEDIUM,
    "mcp__desktop__press_keys": Risk.MEDIUM,
}

UNKNOWN_RISK = Risk.HIGH

# Araç girdisinin içinde geçerse riski CRITICAL'a yükselten kalıplar.
# Araç adı zararsız görünse bile (örn. Bash) içerik tehlikeliyse yakalanır.
CRITICAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("özyinelemeli silme", re.compile(r"\brm\s+-[a-z]*[rf]", re.I)),
    ("özyinelemeli silme", re.compile(r"Remove-Item\b[^\n]*-Recurse", re.I)),
    ("dosya silme", re.compile(r"\b(del|erase)\s+/[sq]", re.I)),
    ("disk biçimlendirme", re.compile(r"\bformat\s+[a-z]:", re.I)),
    ("git geçmişi bozma", re.compile(r"git\s+push\b[^\n]*--force", re.I)),
    ("git sıfırlama", re.compile(r"git\s+reset\s+--hard", re.I)),
    ("veritabanı silme", re.compile(r"\bDROP\s+(TABLE|DATABASE)\b", re.I)),
    ("ödeme/satın alma", re.compile(r"\b(checkout|satın al|ödeme yap|pay now|place order)\b", re.I)),
    ("kimlik bilgisi sızıntısı", re.compile(r"\b(api[_-]?key|password|secret|token)\s*[=:]\s*\S", re.I)),
]


@dataclass
class Decision:
    allowed: bool
    risk: Risk
    reason: str


@dataclass
class ApprovalRequest:
    """Onay isteyen tarafa verilen yapılandırılmış istek.

    Hem konsol hem HUD aynı isteği alır; hangisi önce yanıtlarsa o geçerlidir.
    """

    tool: str
    risk: Risk
    reason: str
    detail: str
    require_phrase: str | None
    timeout_sec: int


# Aynı araç, girdisine göre daha riskli olabilir. Anahtar: araç adı.
# Değer: (koşul, yükseltilecek risk, açıklama).
ESCALATIONS: dict[str, list[tuple[Callable[[dict], bool], Risk, str]]] = {
    "mcp__desktop__window_action": [
        (
            lambda payload: str(payload.get("action", "")).lower() == "close",
            Risk.HIGH,
            "pencere kapatma kaydedilmemiş işi yok edebilir",
        )
    ],
    "mcp__desktop__press_keys": [
        (
            # Silme, kapatma ve yenileme gibi kısayollar tek tuşla iş yok eder.
            lambda payload: any(
                combo in str(payload.get("keys", "")).lower().replace(" ", "")
                for combo in ("shift+delete", "alt+f4", "ctrl+w", "ctrl+q")
            ),
            Risk.HIGH,
            "kısayol pencere kapatabilir veya kalıcı silebilir",
        )
    ],
}


def classify(tool: str, payload: Any = None) -> tuple[Risk, str]:
    """Bir araç çağrısının risk seviyesini ve gerekçesini döndürür."""
    risk = TOOL_RISK.get(tool, UNKNOWN_RISK)
    reason = f"{tool} aracı için taban risk" if tool in TOOL_RISK else f"{tool} tanınmayan araç"

    if isinstance(payload, dict):
        for condition, escalated, note in ESCALATIONS.get(tool, []):
            if condition(payload) and escalated > risk:
                risk, reason = escalated, f"{reason}; {note}"

    text = "" if payload is None else str(payload)
    for label, pattern in CRITICAL_PATTERNS:
        if pattern.search(text):
            return Risk.CRITICAL, f"{reason}; içerikte {label} tespit edildi"

    return risk, reason


def describe(tool: str, payload: Any, risk: Risk) -> str:
    """Onay istemi için kısa, insan tarafından okunabilir özet."""
    summary = "" if payload is None else str(payload)
    if len(summary) > 400:
        summary = summary[:400] + " …"
    return f"[{risk.name}] {tool}\n{summary}" if summary else f"[{risk.name}] {tool}"


def is_yes(answer: str, require_phrase: str | None) -> bool:
    answer = answer.strip().lower()
    if require_phrase:
        return answer == require_phrase.lower()
    return answer in ("e", "evet", "y", "yes", "onayla", "onaylıyorum", "tamam")


def prompt_text(request: ApprovalRequest) -> str:
    if request.require_phrase:
        hint = f"Onaylamak için tam olarak '{request.require_phrase}' yaz"
    else:
        hint = "Onaylamak için 'evet', reddetmek için Enter"
    return (
        f"\n[bold magenta]ONAY GEREKİYOR[/bold magenta]  ({request.reason})\n"
        f"{request.detail}\n"
        f"[dim]{hint} — {request.timeout_sec} sn içinde yanıt yoksa REDDEDİLİR.[/dim]\n"
        "> "
    )


def ask_console(request: ApprovalRequest) -> bool:
    """Konsoldan onay ister. Süre dolarsa False döner."""
    result: list[str] = []

    def read() -> None:
        try:
            result.append(input())
        except EOFError:
            pass

    console().print(prompt_text(request))
    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(request.timeout_sec)

    return bool(result) and is_yes(result[0], request.require_phrase)


class PolicyGate:
    """Araç çağrılarını değerlendirir, gerekiyorsa onay ister, kararı loglar."""

    # Hangi seviyeden itibaren onay sorulacağı.
    LEVELS: dict[str, Risk | None] = {
        "medium": Risk.MEDIUM,
        "high": Risk.HIGH,
        "critical": Risk.CRITICAL,
        "none": None,  # hiç sorma
    }

    def __init__(
        self,
        audit: AuditLog,
        approval_level: str = "medium",
        approval_timeout_sec: int = 60,
        always_allow: tuple[str, ...] = (),
        always_ask: tuple[str, ...] = (),
        ask: Callable[[ApprovalRequest], bool] | None = None,
    ) -> None:
        self.audit = audit
        self.approval_level = approval_level
        self.threshold = self.LEVELS.get(approval_level, Risk.MEDIUM)
        self.always_allow = always_allow
        self.always_ask = always_ask
        self.approval_timeout_sec = approval_timeout_sec
        self._ask = ask or ask_console

    def check(self, tool: str, payload: Any = None) -> Decision:
        risk, reason = classify(tool, payload)
        detail = describe(tool, payload, risk)

        decision = self._decide(tool, risk, reason, detail)

        self.audit.event(
            "approval" if decision.allowed else "denied",
            f"{tool}: {decision.reason}",
            tool=tool,
            risk=risk.name,
            allowed=decision.allowed,
            payload=str(payload)[:2000] if payload is not None else None,
        )
        return decision

    def _decide(self, tool: str, risk: Risk, reason: str, detail: str) -> Decision:
        """Onay gerekiyor mu? Sıra: açık listeler, sonra risk eşiği."""
        if tool in self.always_ask:
            return self._request_approval(tool, risk, f"{reason}; ALWAYS_ASK listesinde", detail)
        if tool in self.always_allow:
            return Decision(True, risk, "ALWAYS_ALLOW listesinde, onay istenmedi")
        if risk == Risk.LOW:
            return Decision(True, risk, "salt okuma, onay gerekmez")
        if self.threshold is None:
            return Decision(True, risk, f"APPROVAL_LEVEL=none, onay istenmedi ({risk.name})")
        if risk < self.threshold:
            return Decision(
                True, risk, f"APPROVAL_LEVEL={self.approval_level} eşiğinin altında ({risk.name})"
            )
        return self._request_approval(tool, risk, reason, detail)

    def _request_approval(self, tool: str, risk: Risk, reason: str, detail: str) -> Decision:
        request = ApprovalRequest(
            tool=tool,
            risk=risk,
            reason=reason,
            detail=detail,
            require_phrase="onaylıyorum" if risk == Risk.CRITICAL else None,
            timeout_sec=self.approval_timeout_sec,
        )
        if self._ask(request):
            return Decision(True, risk, "kullanıcı onayladı")
        return Decision(False, risk, "kullanıcı reddetti veya süre doldu")
