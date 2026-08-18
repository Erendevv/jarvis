"""Giriş noktası: `python -m jarvis`

Varsayılan mod: HUD'u aç, uyandırma kelimesini bekle, konuşmayı yazıya
çevir, Claude'a ilet, yanıtı seslendir.

Mikrofonsuz test için `--text` var: aynı beyin, aynı güvenlik kapısı,
girdiyi klavyeden alır.
"""

from __future__ import annotations

import argparse
import webbrowser

from . import config as config_module
from .audio.listener import Listener
from .audio.mic import list_input_devices
from .audio.tts import Speaker
from .brain import Brain
from .hud import EventBus, HudServer, attach_audit, console_only_asker, make_asker
from .logger import AuditLog, console
from .policy import ApprovalRequest, PolicyGate
from .single import AlreadyRunning, SingleInstance


def build_gate(cfg, audit: AuditLog, speaker: Speaker | None, bus: EventBus | None) -> PolicyGate:  # noqa: ANN001
    """Onay kapısı.

    Onayın kendisi bilerek yazılı ya da tıklamalı alınır: "evet" kelimesini
    yanlış duymak, geri dönüşü olmayan bir işlemi yanlışlıkla onaylatabilir.
    Ses yalnızca dikkat çekmek için kullanılır.
    """

    def announce(request: ApprovalRequest) -> None:
        if speaker is not None:
            speaker.say("Onayın gerekiyor.")

    ask = make_asker(bus, on_prompt=announce) if bus else console_only_asker(on_prompt=announce)
    gate = PolicyGate(
        audit,
        approval_level=cfg.security.approval_level,
        approval_timeout_sec=cfg.security.approval_timeout_sec,
        always_allow=cfg.security.always_allow,
        always_ask=cfg.security.always_ask,
        ask=ask,
    )
    audit.info(
        "Güvenlik kapısı etkin",
        approval_level=gate.approval_level,
        always_allow=list(gate.always_allow) or None,
        always_ask=list(gate.always_ask) or None,
    )
    return gate


def start_hud(cfg, audit: AuditLog, open_browser: bool) -> tuple[EventBus, HudServer]:  # noqa: ANN001
    bus = EventBus()
    attach_audit(audit, bus)
    server = HudServer(bus, host=cfg.hud.host, port=cfg.hud.port)
    server.start()
    audit.info(f"HUD açık: {server.url}")
    if open_browser:
        webbrowser.open(server.url)
    return bus, server


def cmd_devices() -> int:
    for device in list_input_devices():
        mark = " (varsayılan)" if device.default else ""
        console().print(f"[{device.index}] {device.name}{mark}")
    console().print("\n[dim].env içinde AUDIO_INPUT_DEVICE=<indeks> ile sabitleyebilirsin.[/dim]")
    return 0


def cmd_say(text: str, cfg) -> int:  # noqa: ANN001
    Speaker(cfg.audio.tts_engine, cfg.audio.tts_voice).say(text)
    return 0


def cmd_text(cfg, audit: AuditLog, speak: bool, bus: EventBus | None) -> int:  # noqa: ANN001
    """Mikrofonsuz mod: komutları klavyeden al."""
    speaker = Speaker(cfg.audio.tts_engine, cfg.audio.tts_voice) if speak else None
    brain = Brain(cfg, audit, build_gate(cfg, audit, speaker, bus))
    brain.start()
    console().print("[dim]Yazılı mod. Çıkmak için boş satır veya Ctrl+C.[/dim]")
    try:
        while True:
            try:
                text = input("\nSen> ").strip()
            except EOFError:
                break
            if not text:
                break
            audit.event("heard", text, source="klavye")
            if bus:
                bus.set_phase("dusunuyor")
            reply = brain.ask(text)
            audit.event("speak", reply.text, cost_usd=reply.cost_usd, turns=reply.turns)
            if speaker is not None:
                speaker.say(reply.text)
            if bus:
                bus.set_phase("dinlemede")
    except KeyboardInterrupt:
        audit.info("Kullanıcı durdurdu")
    finally:
        brain.close()
    return 0


def cmd_listen(cfg, audit: AuditLog, once: bool, bus: EventBus | None) -> int:  # noqa: ANN001
    listener = Listener(cfg, audit, bus=bus)
    brain = Brain(cfg, audit, build_gate(cfg, audit, listener.speaker, bus))
    try:
        brain.start()
        for utterance in listener.listen():
            try:
                reply = brain.ask(utterance.text)
            except Exception as exc:
                audit.error(f"Claude yanıt veremedi: {exc}")
                listener.say("Bir hata oldu, isteği tamamlayamadım.")
                continue

            if reply.text:
                audit.event("speak", reply.text, cost_usd=reply.cost_usd, turns=reply.turns)
                listener.say(reply.text)
            else:
                listener.say("Buna verecek bir yanıtım yok.")

            if once:
                return 0

            # Konuşmanın akışını koru: yanıttan sonra kısa bir süre uyandırma
            # kelimesi beklemeden dinle. Jarvis soru sorduysa daha uzun bekle.
            asked_question = reply.text.rstrip().endswith(("?", "?"))
            listener.arm_followup(
                cfg.audio.followup_question_ms if asked_question else cfg.audio.followup_window_ms
            )
    except KeyboardInterrupt:
        audit.info("Kullanıcı durdurdu")
    finally:
        brain.close()
        listener.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="jarvis", description="Sesle kontrol edilen asistan")
    parser.add_argument("--devices", action="store_true", help="Mikrofonları listele ve çık")
    parser.add_argument("--say", metavar="METİN", help="Metni seslendir ve çık (TTS testi)")
    parser.add_argument("--text", action="store_true", help="Mikrofonsuz mod: komutları klavyeden al")
    parser.add_argument("--speak", action="store_true", help="--text ile birlikte: yanıtları da seslendir")
    parser.add_argument("--once", action="store_true", help="Tek komuttan sonra çık")
    parser.add_argument("--no-hud", action="store_true", help="HUD arayüzünü başlatma")
    parser.add_argument("--no-browser", action="store_true", help="HUD'u başlat ama tarayıcı açma")
    args = parser.parse_args()

    cfg = config_module.load()
    audit = AuditLog(cfg.log_dir)

    if args.devices:
        return cmd_devices()
    if args.say:
        return cmd_say(args.say, cfg)

    # İkinci bir Jarvis çalışırsa ikisi de dinler ve ikisi de konuşur.
    lock = SingleInstance(cfg.log_dir / "jarvis.lock")
    try:
        lock.acquire()
    except AlreadyRunning as exc:
        console().print(f"\n[bold red]{exc}[/bold red]\n")
        return 1

    bus: EventBus | None = None
    server: HudServer | None = None
    use_hud = cfg.hud.enabled and not args.no_hud
    try:
        if use_hud:
            try:
                bus, server = start_hud(cfg, audit, open_browser=not args.no_browser)
            except Exception as exc:
                # HUD olmadan da çalışabiliriz; ama "açık" diye yalan söyleme.
                audit.error(f"HUD başlatılamadı, arayüzsüz devam ediliyor: {exc}")
                bus, server = None, None
        if args.text:
            return cmd_text(cfg, audit, speak=args.speak, bus=bus)
        return cmd_listen(cfg, audit, once=args.once, bus=bus)
    except Exception as exc:
        audit.error(f"Başlatılamadı: {exc}")
        console().print("\n[dim]Tanı için: python -m jarvis.selftest[/dim]")
        return 1
    finally:
        if server is not None:
            server.stop()
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
