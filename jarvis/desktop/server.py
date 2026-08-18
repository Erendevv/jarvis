"""Masaüstü araçlarının Claude'a açılan yüzeyi (süreç içi MCP sunucusu).

Araç adları Claude tarafında `mcp__desktop__<ad>` olarak görünür; risk
sınıflandırması policy.py içinde bu adlarla eşleşir.

Her araç, hatasını metin olarak döndürür (istisna fırlatmaz), çünkü modelin
hatayı okuyup kullanıcıya açıklaması, aracın çökmesinden iyidir.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import actions, screen


def _ok(payload: Any) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}]}


def _fail(exc: Exception) -> dict:
    return {"content": [{"type": "text", "text": f"HATA: {exc}"}], "is_error": True}


def _guard(fn: Callable[[], Any]) -> dict:
    try:
        return _ok(fn())
    except Exception as exc:
        return _fail(exc)


def build(screenshot_dir: Path):  # noqa: ANN201
    """Masaüstü MCP sunucusunu üretir."""

    @tool(
        "open_app",
        "Bir uygulamayı adıyla açar. Örnek: 'spotify', 'chrome', 'hesap makinesi'. "
        "Başlat menüsündeki tüm kurulu uygulamaları bulabilir.",
        {"name": str},
    )
    async def open_app(args: dict) -> dict:
        return _guard(lambda: actions.open_app(args["name"]))

    @tool(
        "open_path",
        "Bir dosyayı veya klasörü varsayılan uygulamasında açar. Tam yol ver. "
        "Ortam değişkenleri ve ~ desteklenir.",
        {"path": str},
    )
    async def open_path(args: dict) -> dict:
        return _guard(lambda: actions.open_path(args["path"]))

    @tool(
        "open_url",
        "Bir web adresini varsayılan tarayıcıda açar. Sadece http ve https.",
        {"url": str},
    )
    async def open_url(args: dict) -> dict:
        return _guard(lambda: actions.open_url(args["url"]))

    @tool(
        "search_files",
        "Dosya adına göre arar. root verilmezse Masaüstü, Belgeler, İndirilenler "
        "ve Resimler klasörlerine bakar. Dosyayı açmadan önce yerini bulmak için kullan.",
        {"query": str, "root": str, "limit": int},
    )
    async def search_files(args: dict) -> dict:
        def run() -> list[dict]:
            found = actions.search_files(
                args["query"],
                root=args.get("root") or None,
                limit=int(args.get("limit") or 20),
            )
            return [f.__dict__ for f in found]

        return _guard(run)

    @tool("list_windows", "Açık pencereleri listeler: başlık, boyut, konum, aktif mi.", {})
    async def list_windows(args: dict) -> dict:  # noqa: ARG001
        return _guard(actions.list_windows)

    @tool(
        "focus_window",
        "Başlığı verilen pencereyi öne getirir. Başlığın bir parçası yeterli.",
        {"title": str},
    )
    async def focus_window(args: dict) -> dict:
        return _guard(lambda: actions.focus_window(args["title"]))

    @tool(
        "window_action",
        "Pencereyi küçültür, büyütür, eski haline getirir veya kapatır. "
        "action: minimize | maximize | restore | close",
        {"title": str, "action": str},
    )
    async def window_action(args: dict) -> dict:
        return _guard(lambda: actions.window_action(args["title"], args["action"]))

    @tool(
        "snap_window",
        "Pencereyi ekranın bir bölgesine yerleştirir. "
        "position: left | right | top | bottom | full",
        {"title": str, "position": str},
    )
    async def snap_window(args: dict) -> dict:
        return _guard(lambda: actions.snap_window(args["title"], args["position"]))

    @tool("minimize_all", "Tüm pencereleri küçültüp masaüstünü gösterir.", {})
    async def minimize_all(args: dict) -> dict:  # noqa: ARG001
        return _guard(actions.minimize_all)

    @tool("get_volume", "Şu anki ses seviyesini ve sessize alınmış olup olmadığını verir.", {})
    async def get_volume(args: dict) -> dict:  # noqa: ARG001
        return _guard(actions.get_volume)

    @tool("set_volume", "Ana ses seviyesini yüzde olarak ayarlar (0-100).", {"percent": int})
    async def set_volume(args: dict) -> dict:
        return _guard(lambda: actions.set_volume(int(args["percent"])))

    @tool("set_mute", "Sesi kapatır veya açar.", {"muted": bool})
    async def set_mute(args: dict) -> dict:
        return _guard(lambda: actions.set_mute(bool(args["muted"])))

    @tool(
        "media_key",
        "Medya tuşu gönderir: play_pause | next | previous | stop | mute | "
        "volume_up | volume_down. Hangi uygulama çalıyorsa ona gider.",
        {"key": str},
    )
    async def media_key(args: dict) -> dict:
        return _guard(lambda: actions.media_key(args["key"]))

    @tool(
        "screenshot",
        "Ekran görüntüsü alır ve PNG yolunu döndürür. Görüntüyü GÖRMEK için "
        "dönen yolu Read aracıyla aç. monitor=0 tüm ekranlar, 1 birinci ekran, "
        "2 ikinci ekran. Tıklamadan önce mutlaka bunu çağır: tıklama "
        "koordinatları bu görüntüye göre yorumlanır.",
        {"monitor": int},
    )
    async def screenshot(args: dict) -> dict:
        def run() -> dict:
            shot = screen.capture(screenshot_dir, int(args.get("monitor") or 0))
            return {
                "path": shot.path,
                "goruntu_boyutu": f"{shot.image_size[0]}x{shot.image_size[1]}",
                "gercek_ekran": f"{shot.screen_size[0]}x{shot.screen_size[1]}",
                "not": "Tıklama koordinatlarını bu görüntüdeki piksellere göre ver.",
            }

        return _guard(run)

    @tool(
        "click",
        "Ekranda bir noktaya tıklar. Koordinatlar EN SON alınan ekran "
        "görüntüsündeki piksellerdir. button: left | right | middle. "
        "clicks=2 çift tıklama yapar.",
        {"x": int, "y": int, "button": str, "clicks": int},
    )
    async def click(args: dict) -> dict:
        return _guard(lambda: screen.click(
            int(args["x"]), int(args["y"]),
            button=args.get("button") or "left",
            clicks=int(args.get("clicks") or 1),
        ))

    @tool(
        "move_mouse",
        "Fareyi bir noktaya götürür, tıklamaz. Menü açmak veya ipucu "
        "göstermek için (hover).",
        {"x": int, "y": int},
    )
    async def move_mouse(args: dict) -> dict:
        return _guard(lambda: screen.move(int(args["x"]), int(args["y"])))

    @tool(
        "drag",
        "Bir noktadan diğerine sürükler (sol düğme basılı).",
        {"x1": int, "y1": int, "x2": int, "y2": int},
    )
    async def drag(args: dict) -> dict:
        return _guard(lambda: screen.drag(
            int(args["x1"]), int(args["y1"]), int(args["x2"]), int(args["y2"])
        ))

    @tool(
        "scroll_screen",
        "Kaydırır. amount pozitifse yukarı, negatifse aşağı. x,y verilirse "
        "önce oraya gider.",
        {"amount": int, "x": int, "y": int},
    )
    async def scroll_screen(args: dict) -> dict:
        x, y = args.get("x"), args.get("y")
        return _guard(lambda: screen.scroll(
            int(args["amount"]),
            int(x) if x not in (None, "") else None,
            int(y) if y not in (None, "") else None,
        ))

    @tool(
        "type_text",
        "Odaktaki alana metin yazar. Türkçe karakterler dahil. Önce ilgili "
        "alana tıklayıp odağı vermeyi unutma.",
        {"text": str},
    )
    async def type_text(args: dict) -> dict:
        return _guard(lambda: screen.type_text(args["text"]))

    @tool(
        "press_keys",
        "Tuş veya kombinasyon gönderir. Örnek: 'enter', 'esc', 'ctrl+s', "
        "'alt+tab', 'ctrl+shift+m'.",
        {"keys": str},
    )
    async def press_keys(args: dict) -> dict:
        return _guard(lambda: screen.press_keys(args["keys"]))

    @tool("cursor_position", "Farenin şu anki ekran koordinatını verir.", {})
    async def cursor_position(args: dict) -> dict:  # noqa: ARG001
        return _guard(screen.cursor_position)

    @tool("system_status", "CPU, RAM ve varsa GPU kullanımını verir.", {})
    async def system_status(args: dict) -> dict:  # noqa: ARG001
        return _guard(actions.system_status)

    return create_sdk_mcp_server(
        name="desktop",
        version="1.0.0",
        tools=[
            open_app, open_path, open_url, search_files,
            list_windows, focus_window, window_action, snap_window, minimize_all,
            get_volume, set_volume, set_mute, media_key,
            screenshot, click, move_mouse, drag, scroll_screen,
            type_text, press_keys, cursor_position,
            system_status,
        ],
    )
