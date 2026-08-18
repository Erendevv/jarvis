"""Kalıcı hafıza.

İki parça:

  memory/*.md   Claude'un okuyup güncelleyebildiği serbest metin notlar.
                Her oturumun başında sistem istemine eklenir, böylece
                tercihlerini her seferinde baştan anlatman gerekmez.

  state.db      Konuşma geçmişi (SQLite). Ham kayıt; "geçen hafta ne
                konuşmuştuk" gibi sorular ve ileride ihmal edilen kişileri
                tespit etmek için.

Notlar neden markdown? Çünkü Claude onları doğrudan Edit aracıyla
güncelleyebiliyor ve sen de aynı dosyaları elle açıp düzeltebiliyorsun.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCAFFOLD: dict[str, str] = {
    "preferences.md": """# Tercihler

Jarvis'in benimle çalışırken uyması gereken kurallar.

- Yanıtlar kısa olsun; sesli okunacak, uzun liste yorucu.
- Bilmediğin bir şeyi uydurma, bilmiyorum de.

<!-- Yeni tercihleri buraya ekle. Jarvis de buraya yazabilir. -->
""",
    "people.md": """# Kişiler

Sık iletişim kurduğum insanlar ve haklarında bilinmesi gerekenler.

<!-- Biçim:
## Ad Soyad
- e-posta: ...
- ilişki: ...
- not: ...
-->
""",
    "projects.md": """# Projeler

Takip ettiğim işler ve durumları.

<!-- Biçim:
## Proje adı
- durum: aktif | beklemede | bitti
- klasör: C:\\...
- sonraki adım: ...
-->
""",
    "facts.md": """# Kalıcı bilgiler

Jarvis'in her konuşmada bilmesi gereken sabit bilgiler.

<!-- Örnek: çalışma saatlerim, saat dilimim, sık kullandığım klasörler -->
""",
}


@dataclass
class Turn:
    ts: str
    user: str
    assistant: str


class Memory:
    def __init__(self, memory_dir: Path, db_path: Path) -> None:
        self.dir = memory_dir
        self.db_path = db_path
        self.dir.mkdir(parents=True, exist_ok=True)
        self._ensure_scaffold()
        self._ensure_db()

    def _ensure_scaffold(self) -> None:
        """Eksik not dosyalarını başlangıç içeriğiyle oluşturur.

        Var olan dosyalara dokunmaz; kullanıcının yazdığı hiçbir şey silinmez.
        """
        for name, content in SCAFFOLD.items():
            path = self.dir / name
            if not path.exists():
                path.write_text(content, encoding="utf-8")

    def _ensure_db(self) -> None:
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS turns (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts         TEXT NOT NULL,
                    session_id TEXT,
                    user_text  TEXT NOT NULL,
                    reply_text TEXT
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS turns_ts ON turns(ts)")

    def notes(self) -> str:
        """Tüm markdown notları tek metin olarak döndürür."""
        chunks: list[str] = []
        for path in sorted(self.dir.glob("*.md")):
            text = path.read_text(encoding="utf-8").strip()
            if text:
                chunks.append(f"--- {path.name} ---\n{text}")
        return "\n\n".join(chunks)

    def log_turn(self, user_text: str, reply_text: str, session_id: str | None = None) -> None:
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO turns (ts, session_id, user_text, reply_text) VALUES (?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), session_id, user_text, reply_text),
            )

    def recent_turns(self, limit: int = 10) -> list[Turn]:
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(
                "SELECT ts, user_text, reply_text FROM turns ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Turn(ts=r[0], user=r[1], assistant=r[2] or "") for r in reversed(rows)]
