# Jarvis

Windows için sesle kontrol edilen kişisel asistan. "Hey Jarvis" dersin,
konuşursun, o bilgisayarı kullanır.

Üç katman:

- **Ses** — uyandırma kelimesi, konuşma algılama ve konuşma tanıma tamamen
  yerel çalışır. Sesin hiçbir sunucuya gitmez.
- **Beyin** — Claude Code, komutu yorumlar ve araçları çağırır. Kalıcı
  hafızası vardır; tercihlerini her seferinde baştan anlatman gerekmez.
- **Aksiyon** — uygulama ve dosya açar, pencere yönetir, ses/medya kontrol
  eder, ekranı görür ve ekranda tıklayıp yazabilir.

Geri dönüşsüz her işlem önce onayına düşer ve her eylem denetim günlüğüne
yazılır. Onay eşiği ayarlanabilir; bkz. [Onay ayarları](#onay-ayarları--nasıl-gevşetilir).

**Ne yok:** Gmail, takvim ve Playwright tarayıcı otomasyonu henüz bağlı değil.

### Gereksinimler

| Ne | Neden |
|---|---|
| Windows 10/11 | Masaüstü kontrolü Windows API'lerini kullanır |
| Python 3.10+ | 3.12 ile geliştirildi |
| Node.js + `claude` CLI | Beyin, Claude Code'u alt süreç olarak çalıştırır |
| Mikrofon | — |
| NVIDIA GPU (isteğe bağlı) | Konuşma tanımayı hızlandırır; yoksa CPU'ya düşer |

Ayrı bir API anahtarı gerekmez — mevcut Claude Code oturumun kullanılır.

---

## Mimari

```
Mikrofon ──> openWakeWord ──> Silero VAD ──> faster-whisper (STT)
                                                    │
                                                    ▼
                                          Claude Agent SDK (beyin)
                                                    │
                                       PreToolUse kancası ──> PolicyGate
                                                    │              │
                            ┌───────────────────────┤              │
                            ▼                       ▼              ▼
                   masaüstü araçları        yerleşik araçlar   onay iste
                   (uygulama, pencere,      (Read, Glob,           │
                    ses, ekran)              Grep, Bash …)         │
                            │                       │              │
                            └───────────┬───────────┘              │
                                        ▼                          ▼
                          edge-tts (sesli yanıt)          HUD + audit log
                                                        (tıklanabilir onay)
```

| Katman | Kütüphane | Nerede |
|---|---|---|
| Uyandırma kelimesi | `openwakeword` (seçenek: `pvporcupine`) | `jarvis/audio/wake.py` |
| Konuşma algılama | Silero VAD (seçenek: `pvcobra`, RMS) | `jarvis/audio/vad.py` |
| Konuşmadan metne | `faster-whisper` (CUDA) | `jarvis/audio/stt.py` |
| Metinden konuşmaya | `edge-tts` (yedek: SAPI) | `jarvis/audio/tts.py` |
| Mikrofon akışı | `sounddevice` | `jarvis/audio/mic.py` |
| Ses ana döngüsü | — | `jarvis/audio/listener.py` |
| Beyin | `claude-agent-sdk` | `jarvis/brain/agent.py` |
| Sistem istemi | — | `jarvis/brain/prompt.py` |
| Kalıcı hafıza | `sqlite3` + markdown | `jarvis/brain/memory.py` |
| Masaüstü eylemleri | `pygetwindow`, `pycaw`, `mss` | `jarvis/desktop/actions.py` |
| Uygulama bulma | — | `jarvis/desktop/apps.py` |
| Araç tanımları | `claude-agent-sdk` (MCP) | `jarvis/desktop/server.py` |
| HUD sunucusu | `fastapi`, `uvicorn` | `jarvis/hud/server.py` |
| HUD arayüzü | saf HTML/Canvas | `jarvis/hud/static/index.html` |
| Olay veri yolu | — | `jarvis/hud/bus.py` |
| Güvenlik kapısı | — | `jarvis/policy.py` |
| Denetim günlüğü | — | `jarvis/logger.py` |
| Ayarlar | `python-dotenv` | `jarvis/config.py` |

Beyin, kurulu `claude` CLI'sini alt süreç olarak çalıştırır; ayrıca bir API
anahtarı gerekmez, mevcut Claude Code oturumunu kullanır.

Ses buluta gitmez: uyandırma kelimesi, VAD ve konuşma tanıma tamamen yerel
çalışır. Yalnızca `edge-tts` (sesli yanıt) internet kullanır ve ona sadece
Jarvis'in söyleyeceği metin gider, senin sesin değil.

---

## Kurulum

### 1. Bağımlılıklar

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Hesap açman, kayıt olman veya anahtar alman **gerekmiyor**. Uyandırma kelimesi
ve VAD modelleri açık kaynak ve ilk çalıştırmada otomatik iner.

### 2. Ayarlar

```powershell
Copy-Item .env.example .env
```

Varsayılanlar çoğu kurulumda çalışır; `.env`'i açman şart değil.
GPU'n yoksa `WHISPER_DEVICE=cpu` ve `WHISPER_COMPUTE_TYPE=int8` yap.

### 3. ZIP indirdiysen: dosyaların damgasını kaldır

Depoyu `git clone` ile aldıysan bu adımı atla.

GitHub'dan ZIP indirdiysen Windows tüm dosyalara "internetten geldi" damgası
(Mark of the Web) basar ve `run.ps1` çalışmaz:

```
run.ps1 cannot be loaded. The file is not digitally signed.
```

Çözüm çalıştırma politikasını düşürmek değil, damgayı kaldırmak:

```powershell
Get-ChildItem -Recurse -File | Unblock-File
```

Bu yalnızca bu klasördeki dosyaları etkiler, sistem güvenlik ayarına
dokunmaz.

### 4. Doğrula

```powershell
.\.venv\Scripts\python.exe -m jarvis.selftest
```

Tüm satırlar TAMAM olmalı.

---

## Çalıştırma

```powershell
.\run.ps1
```

Sonra **"Hey Jarvis"** de, bip sesini bekle, komutunu söyle. Ekranda ne
anladığını göreceksin.

**Konuşmaya devam edebilirsin.** Jarvis yanıt verdikten sonra 7 saniye
boyunca uyandırma kelimesi beklemeden dinlemeye devam eder; sana bir soru
sorduysa 15 saniye. Yani "peki onu aç" demek için tekrar "Hey Jarvis"
demen gerekmez. HUD'da bu sırada durum **DEVAM EDİYOR** görünür. Süreleri
`.env` içindeki `FOLLOWUP_WINDOW_MS` ve `FOLLOWUP_QUESTION_MS` ile
değiştirebilir, `0` yaparak kapatabilirsin.

**Aynı anda tek Jarvis çalışır.** İkincisini başlatmaya kalkarsan hangi
sürecin çalıştığını söyleyip durur — iki örnek aynı anda dinlerse her şeyi
çift duyarsın.

> Uyandırma kelimesi `hey_jarvis` — tek başına "Jarvis" değil, "Hey Jarvis".
> Sadece "Jarvis" demek istersen `WAKE_ENGINE=porcupine` gerekir; o motor
> Picovoice'tan ücretsiz AccessKey ister (bkz. `.env.example`).

| Komut | Ne yapar |
|---|---|
| `.\run.ps1` | HUD'u açar, dinlemeye başlar, yanıtları seslendirir |
| `.\run.ps1 --text` | Mikrofonsuz mod: komutları klavyeden al (test için) |
| `.\run.ps1 --text --speak` | Klavyeden yaz, yanıtı sesli duy |
| `.\run.ps1 --no-browser` | HUD çalışsın ama tarayıcı otomatik açılmasın |
| `.\run.ps1 --no-hud` | HUD'u hiç başlatma, sadece terminal |
| `.\run.ps1 --once` | Tek komut alıp çıkar |
| `.\run.ps1 --devices` | Mikrofonları listeler |
| `.\run.ps1 --say "merhaba"` | Sadece seslendirme testi |

**Kapatma:** terminalde `Ctrl+C`. Arka planda hiçbir servis kalmaz — Jarvis
yalnızca bu terminal açıkken çalışır, Windows servisi veya başlangıç görevi
kurmaz.

İlk çalıştırmada Whisper modeli (~1.6 GB) `models/` altına iner; sonraki
açılışlarda yalnızca yüklenir (GPU'da birkaç saniye, CPU'da daha uzun).

---

## Gerekli izinler

| İzin | Neden | Nasıl verilir |
|---|---|---|
| Mikrofon | Sesi duymak | Ayarlar > Gizlilik ve güvenlik > Mikrofon > "Masaüstü uygulamalarının mikrofona erişmesine izin ver" açık olmalı |
| İnternet (giden) | Sadece `edge-tts` sesli yanıt. `TTS_ENGINE=sapi` yaparsan hiç gerekmez | — |

Yönetici hakkı **gerekmez**.

---

## Gizlilik — hangi veri makineden çıkıyor

Bunu net yazmak önemli, çünkü "yerel çalışıyor" cümlesi yalnızca yarı doğru.

**Sesin makineden çıkmaz.** Uyandırma kelimesi, konuşma algılama ve konuşma
tanıma tamamen yerel modellerle çalışır. Mikrofon kaydı diske bile yazılmaz.

**Ama karar veren model uzakta.** Komutun yazıya çevrildikten sonra Claude'a
gider, ve modelin görmesi gereken her şey onunla birlikte gider.

| Veri | Ağa çıkar mı | Nereye |
|---|---|---|
| Mikrofon sesi | hayır | — |
| Uyandırma kelimesi, VAD, konuşma tanıma | hayır | — |
| Denetim günlüğü | hayır | — |
| HUD arayüzü | hayır | yalnızca `127.0.0.1` |
| Komutun metni | **evet** | Anthropic |
| **Ekran görüntüleri** | **evet** | Anthropic |
| Okunan dosyaların içeriği | **evet** | Anthropic |
| Hafıza notların (`memory/*.md`) | **evet** | Anthropic — her oturum başında |
| Son 6 konuşma turu | **evet** | Anthropic — her oturum başında |
| Sesli yanıtın metni | **evet** | Microsoft (`TTS_ENGINE=sapi` ile kesilir) |
| Model dosyaları | **evet** | Hugging Face — yalnızca ilk kurulumda |

### Ekran görüntüleri hakkında bilinmesi gerekenler

- **Ne çekilir:** ekranın o anki görüntüsü — gözünle gördüğün her şey.
  Üstü örtülü pencereler, küçültülmüş uygulamalar ve tarayıcının aktif
  olmayan sekmeleri çekilmez.
- **Varsayılan tüm ekranlardır.** Birden fazla monitörün varsa
  `monitor=0` hepsini birleştirir. Bakmadığın ikinci ekran da çekilir.
- **Diskte kalır.** Görüntüler `logs/screenshots/` altına yazılır ve
  otomatik silinmez. Ne çekildiğini sonradan görebilmen için böyle, ama
  bu klasörü düzenli temizlemek sana kalmış.
- **Bu yüzden `MEDIUM`.** Varsayılan `APPROVAL_LEVEL=medium` ile her ekran
  görüntüsü onayına düşer. `critical` veya `none` seçersen bu onay kalkar
  ve Jarvis sormadan ekran çekebilir.

Hassas bir şey açıkken ekran görüntüsü aldırma; parola yöneticisi, bankacılık
sekmesi veya özel yazışma açıkken bu kareler hem diskine yazılır hem modele
gider. Görüntüleri temizlemek için:

```powershell
Remove-Item -Recurse -Force .\logs\screenshots
```

---

## Bilgisayar kontrolü

Jarvis'in masaüstünde yapabildikleri. Hepsi `jarvis/desktop/` altında ve
her biri güvenlik kapısından geçer.

| Ne dersen | Ne olur | Risk |
|---|---|---|
| "Spotify aç", "hesap makinesini aç" | Başlat menüsündeki kurulu uygulamaları adıyla bulur ve açar | MEDIUM |
| "Masaüstündeki rapor.pdf'i aç" | Dosyayı varsayılan uygulamasında açar | MEDIUM |
| "İndirilenler klasörünü aç" | Explorer'da açar | MEDIUM |
| "Şu siteyi aç" | Varsayılan tarayıcıda açar (sadece http/https) | MEDIUM |
| "Bütçe diye bir dosya var mı?" | Masaüstü, Belgeler, İndirilenler, Resimler içinde arar (dosya ve klasör) | LOW |
| "Neler açık?" | Açık pencereleri listeler | LOW |
| "Chrome'u öne getir" | Pencereyi odaklar | LOW |
| "VS Code'u sol yarıya al" | Pencereyi ekranın yarısına yerleştirir | LOW |
| "Her şeyi küçült" | Masaüstünü gösterir | LOW |
| "Discord'u kapat" | Pencereyi kapatır | **HIGH** (kaydedilmemiş iş gidebilir) |
| "Sesi otuza indir", "sessize al" | Ana ses seviyesini ayarlar | LOW |
| "Duraklat", "sonraki şarkı" | Medya tuşu gönderir, hangi uygulama çalıyorsa ona gider | LOW |
| "Ekranımda ne var?" | Ekran görüntüsü alıp okur ve yorumlar | MEDIUM |
| "Bilgisayar ne durumda?" | CPU, RAM, GPU, VRAM | LOW |

Ekran görüntüleri `logs/screenshots/` altına kaydedilir, silinmez — sonradan
neyin çekildiğini görebilirsin.

### Ekran otomasyonu

Jarvis ekranı görüp üzerinde işlem yapabilir; yani ekranda görünen herhangi
bir uygulamayı kullanabilir (Discord, tarayıcı, oyun ayarları, ne olursa).

| Araç | Ne yapar | Risk |
|---|---|---|
| `screenshot` | Ekranı çeker, Jarvis görüntüye bakar | MEDIUM |
| `click` | Bir noktaya tıklar (sol/sağ/orta, çift tık) | MEDIUM |
| `move_mouse` | Fareyi götürür, tıklamaz (menü açmak için) | LOW |
| `drag` | Sürükler | MEDIUM |
| `scroll_screen` | Kaydırır | LOW |
| `type_text` | Metin yazar, Türkçe karakterler dahil | MEDIUM |
| `press_keys` | Tuş gönderir (`enter`, `ctrl+s`, `alt+tab`) | MEDIUM |
| `cursor_position` | Farenin yerini söyler | LOW |

**Koordinatlar nasıl tutuyor.** Ekran görüntüsü 1280 piksel genişliğe
küçültülüp Jarvis'e gösterilir; tıklama koordinatları o görüntüye göre
verilir ve gerçek ekran koordinatına otomatik çevrilir. Çok monitörlü
kurulumda negatif ofsetler de hesaplanır (soldaki ikinci ekranın negatif
koordinatları dahil).

**Emniyetler:**
- `ctrl+alt+delete` ve `win+l` hiç gönderilmez (oturumu kilitler/atar)
- `alt+f4`, `ctrl+w`, `ctrl+q`, `shift+delete` riski HIGH'a yükseltilir
- Fareyi ekranın sol üst köşesine götürmek otomasyonu anında durdurur
  (pyautogui acil freni) — kontrolü kaybedersen refleksin bu olsun
- Parola/kart bilgisi yazması istenirse reddeder, senin yazmanı ister

**Sınırı:** Jarvis her tıklamadan sonra yeni görüntü alıp doğrular, beş altı
adımda ilerleyemezse durup nerede takıldığını söyler. Karmaşık arayüzlerde
ilk denemede tutturamayabilir; nerede olduğunu tarif etmen işi hızlandırır.

---

## HUD arayüzü

`.\run.ps1` çalışınca tarayıcıda `http://127.0.0.1:8765` açılır.

- **Reaktör** — konuşurken ve düşünürken canlanır, mikrofon seviyesiyle
  şekil değiştirir
- **Durum** — DİNLEMEDE / DİNLİYOR / DÜŞÜNÜYOR / KONUŞUYOR / ONAY BEKLİYOR
- **Sistem** — CPU, RAM, GPU, VRAM, mikrofon seviyesi
- **Eylemler** — çağrılan her araç, riskine göre renkli
- **Günlük** — tüm olay akışı
- **Onay ekranı** — geri dönüşsüz bir işlem çıkınca öne gelir; **Onayla** /
  **Reddet** butonları, geri sayım, kritik işlemlerde onay cümlesi kutusu

Onay hem HUD'dan tıklanarak hem terminale yazarak verilebilir; hangisi önce
gelirse o geçerli. Klavye: `Enter` onayla, `Esc` reddet.

Sunucu yalnızca `127.0.0.1`'e bağlanır — ağdaki başka cihazlar erişemez.
İkinci bir ekranda tam ekran açmak için tarayıcıda `F11`.

Arayüz istemiyorsan `.\run.ps1 --no-hud` ya da `.env` içinde
`HUD_ENABLED=false`.

---

## Hafıza

Jarvis iki yerde hatırlar:

| Nerede | Ne | Kim yazar |
|---|---|---|
| `memory/preferences.md` | Nasıl davranmasını istediğin kurallar | Sen ve Jarvis |
| `memory/people.md` | Kişiler, e-postaları, ilişkiler | Sen ve Jarvis |
| `memory/projects.md` | Takip ettiğin işler ve durumları | Sen ve Jarvis |
| `memory/facts.md` | Sabit bilgiler (saat dilimi, klasörler) | Sen ve Jarvis |
| `state.db` | Tüm konuşma geçmişi (SQLite) | Otomatik |

Bu dosyalar her oturumun başında sistem istemine eklenir. "Bundan sonra hep
şöyle yap" dediğinde Jarvis ilgili dosyayı günceller — bu bir yazma işlemi
olduğu için senden onay ister. Dosyaları elle de düzenleyebilirsin.

Son altı konuşma turu da isteme eklenir, böylece Jarvis'i kapatıp açsan bile
kaldığınız yerden devam edebilirsiniz.

`memory/` ve `state.db` `.gitignore`'dadır — kişisel notların ve konuşma
geçmişin depoya girmez. İlk çalıştırmada boş şablonlar otomatik oluşur.

---

## Güvenlik

- **Onay kapısı** (`jarvis/policy.py`): her araç çağrısı dört risk
  seviyesinden birine ayrılır.

  | Seviye | Örnek |
  |---|---|
  | `LOW` | dosya okuma, arama, pencere listeleme, ses seviyesi |
  | `MEDIUM` | uygulama/dosya açma, dosya yazma, ekran görüntüsü |
  | `HIGH` | kabuk komutu, pencere kapatma, alt ajan başlatma |
  | `CRITICAL` | mail gönderme, kalıcı silme, ödeme, `rm -rf`, `DROP TABLE` |

- **Eşiği sen seçersin.** `.env` içindeki `APPROVAL_LEVEL`, hangi seviyeden
  itibaren onay sorulacağını belirler:

  | Değer | Ne sorulur |
  |---|---|
  | `medium` | MEDIUM ve üzeri (en sıkı) |
  | `high` | HIGH ve üzeri |
  | `critical` | yalnızca geri dönüşsüz işlemler |
  | `none` | hiçbir şey |

  `CRITICAL` işlemlerde onay istenirse tam olarak `onaylıyorum` yazman
  beklenir; tek tıkla kazara onaylanmasın diye.

- **İstisna listeleri.** Seviyeden bağımsız ince ayar için `.env`:
  `ALWAYS_ALLOW=araç1,araç2` (hiç sorma) ve `ALWAYS_ASK=Bash,PowerShell`
  (her zaman sor). Araç adları günlükte göründüğü gibi yazılır, örneğin
  `mcp__desktop__open_path`.

- **Seviye ne olursa olsun her eylem günlüğe yazılır.** `APPROVAL_LEVEL=none`
  bile olsa `logs/audit-*.jsonl` eksiksiz kalır.
- **Zaman aşımı reddeder.** `APPROVAL_TIMEOUT_SEC` (varsayılan 60 sn) içinde
  yanıt gelmezse işlem **reddedilir**, kabul edilmez.
- **Onay sesli verilmez.** "Evet" kelimesini yanlış duymak, geri dönüşü
  olmayan bir işlemi yanlışlıkla onaylatabilir. Jarvis yalnızca "onayın
  gerekiyor" diye seslenir; kararı HUD'dan tıklayarak veya terminale yazarak
  verirsin.
- **Aynı araç, farklı risk.** Girdiye göre risk yükselir: `window_action`
  normalde MEDIUM ama `action=close` ise HIGH olur.
- **Ekran görüntüsü MEDIUM.** Teknik olarak salt okuma ama ekranda parola,
  banka ekranı veya özel mesaj olabilir; sessizce çekilmesin diye onaya tabi.
- **İçerik taraması.** Araç adı zararsız görünse bile girdisinde
  `rm -rf`, `Remove-Item -Recurse`, `git push --force`, `DROP TABLE`,
  ödeme kalıpları geçiyorsa risk `CRITICAL`'a yükseltilir.
- **Kapıyı hiçbir araç atlayamaz.** Zorlama, Claude Agent SDK'nın `PreToolUse`
  kancasında yapılır — bu kanca istisnasız her araç çağrısında çalışır.
  (Yalnızca `can_use_tool` kullanılsaydı, Claude Code'un kendi izin katmanının
  zararsız sayıp otomatik onayladığı çağrılar — `Read`, `Glob`, hatta `ls`
  gibi Bash komutları — kapıya hiç uğramazdı.)
- **Denetim günlüğü.** Her uyandırma, her transkript, her araç çağrısı ve her
  onay kararı `logs/audit-YYYY-MM-DD.jsonl` dosyasına yazılır. Örnek:
  ```powershell
  Get-Content .\logs\audit-2026-08-17.jsonl | ConvertFrom-Json | Format-Table ts, kind, message
  ```
- **Kimlik bilgileri.** Hepsi `.env` içinde, `.env` `.gitignore`'da.
  Koda gömülü hiçbir anahtar yok.

---

## Onay ayarları — nasıl gevşetilir

Varsayılan `APPROVAL_LEVEL=medium`. Yani kutudan çıktığı hâliyle Jarvis, bir
klasör açmak için bile sana sorar. Bu bilinçli bir seçim: yeni kullanan biri
asistanın ne yaptığını görmeden ona kontrol vermemeli.

Alıştıktan sonra gevşetmek isteyeceksin. Üç yolu var.

### 1. Eşiği düşür (en yaygın)

`.env` dosyasını aç, `APPROVAL_LEVEL` satırını değiştir:

```ini
APPROVAL_LEVEL=critical
```

| Değer | Sorulur | Sorulmaz |
|---|---|---|
| `medium` (varsayılan) | dosya/uygulama açma, yazma, ekran görüntüsü, kabuk, silme, mail | okuma, arama, pencere listeleme |
| `high` | kabuk komutu, pencere kapatma, silme, mail, ödeme | uygulama/dosya açma, yazma, ekran görüntüsü, tıklama |
| `critical` | yalnızca geri dönüşsüz olanlar: mail gönderme, kalıcı silme, ödeme, `rm -rf`, `DROP TABLE` | diğer her şey |
| `none` | hiçbir şey | her şey |

Günlük kullanımda **`critical` çoğu kişi için doğru denge**: bilgisayarı
akıcı kullanır ama geri alınamayan bir şey yapmadan önce durur.

`none` yalnızca ne yaptığını bilerek ve tek kullanıcılı bir makinede
seçilmeli. Jarvis o modda mail gönderirken, dosya silerken veya ödeme
ekranında ilerlerken sormaz.

### 2. Tek tek istisna tanı

Eşiği değiştirmeden belirli araçları listeye al. Araç adlarını
`logs/audit-*.jsonl` içinden aynen kopyalayabilirsin.

```ini
# Bu araçlar hiç sormasın
ALWAYS_ALLOW=mcp__desktop__open_path,mcp__desktop__open_app,mcp__desktop__screenshot

# Bu araçlar eşik ne olursa olsun her zaman sorsun
ALWAYS_ASK=Bash,PowerShell
```

`ALWAYS_ASK`, `ALWAYS_ALLOW`'dan önce gelir. `APPROVAL_LEVEL=none` seçip
tehlikeli birkaç aracı `ALWAYS_ASK` ile geri sormaya alabilirsin.

### 3. Risk sınıflandırmasını değiştir

Bir aracın riskini kalıcı olarak farklı görüyorsan
[jarvis/policy.py](jarvis/policy.py) içindeki `TOOL_RISK` sözlüğünü düzenle.
Örneğin ekran görüntüsünü zararsız buluyorsan:

```python
"mcp__desktop__screenshot": Risk.LOW,
```

Aynı dosyadaki `ESCALATIONS`, girdiye bakarak riski yükseltir
(`window_action` + `action=close` → HIGH gibi). `CRITICAL_PATTERNS` ise araç
adı ne olursa olsun içerikte `rm -rf`, `DROP TABLE`, ödeme kalıpları
görürse `CRITICAL`'a çıkarır. Bunları gevşetirken dikkatli ol.

### Neyi kapatmıyorsun

Eşik ne olursa olsun **denetim günlüğü çalışmaya devam eder**.
`APPROVAL_LEVEL=none` bile olsa her araç çağrısı, her transkript ve her karar
`logs/audit-YYYY-MM-DD.jsonl` dosyasına yazılır. Onayı kapatmak, ne olduğunu
sonradan görme imkânını kapatmaz.

---

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| `Library cublas64_12.dll is not found` | `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` |
| `cudaErrorMemoryAllocation: out of memory` | GPU belleği başka uygulamalarda. Jarvis bunu yakalayıp CPU'ya düşer ve çalışmaya devam eder; kalıcı çözüm için GPU kullanan uygulamaları kapat veya `WHISPER_MODEL=medium` yap. Boş VRAM'i görmek için `python -m jarvis.selftest` |
| CUDA yok / VRAM dolu | `.env` içinde `WHISPER_DEVICE=cpu` ve `WHISPER_COMPUTE_TYPE=int8`. Kod zaten CUDA başlatılamazsa kendiliğinden CPU'ya düşer |
| CPU'ya düşünce "small modeline inildi" uyarısı | RAM de darmış. Başka uygulama kapat; kalıcı çözüm için `WHISPER_MODEL=medium` |
| Yanlış mikrofon | `.\run.ps1 --devices`, sonra `.env` içinde `AUDIO_INPUT_DEVICE=<indeks>` |
| Uyandırma kelimesi tetiklenmiyor | Önce "Hey Jarvis" dediğinden emin ol. Sonra `.env` içinde `WAKE_SENSITIVITY` değerini 0.75'e çıkar |
| Kendi kendine tetikleniyor | `WAKE_SENSITIVITY` değerini 0.45'e düşür |
| Cümlenin sonu kesiliyor | `VAD_SILENCE_MS` değerini 1400'e çıkar |
| Konuşma bitince uzun bekliyor | `VAD_SILENCE_MS` değerini 600'e düşür |
| Konuşmayı hiç algılamıyor ("Konuşma algılanmadı") | `VAD_THRESHOLD` değerini 0.15'e düşür |
| Türkçe sesli yanıt İngilizce aksanlı | `TTS_ENGINE=edge` olduğundan emin ol (`sapi` Türkçe dil paketi ister) |
| `run.ps1 cannot be loaded ... not digitally signed` | ZIP'ten çıkan dosyalarda "internetten geldi" damgası var. Proje klasöründe `Get-ChildItem -Recurse -File \| Unblock-File`. Alternatif: `run.ps1` yerine doğrudan `.\.venv\Scripts\python.exe -m jarvis` |
| `claude CLI` bulunamadı | `npm install -g @anthropic-ai/claude-code`, sonra bir kez `claude` çalıştırıp oturum aç |
| Çok fazla onay soruyor | Bkz. [Onay ayarları](#onay-ayarları--nasıl-gevşetilir). Kısası: `.env` içinde `APPROVAL_LEVEL=critical` |
| Belirli bir araç için hiç sormasın | `.env` içinde `ALWAYS_ALLOW=<araç adı>`. Araç adını `logs/audit-*.jsonl` içinden kopyala |
| Mail gönderme / silme için de sormasın | `APPROVAL_LEVEL=none`. Bunu yapınca hiçbir işlem sorulmaz; günlük yine tutulur |
| Yanıt çok uzun, sesli okuması bitmiyor | `memory/preferences.md` dosyasına "yanıtlar en fazla iki cümle olsun" gibi bir kural ekle |
| Her şeyi iki kez duyuyorum | İki Jarvis çalışıyordur. Yeni sürüm ikincisini engelliyor; eskiden kalan varsa `Get-CimInstance Win32_Process -Filter "Name='python.exe'"` ile bul ve kapat. `run.ps1` yerine düz `python -m jarvis` çalıştırmak da ikinci örnek açar |
| "Jarvis zaten çalışıyor" diyor ama çalışmıyor | Süreç düzgün kapanmamış. `logs/jarvis.lock` ve `logs/jarvis.pid` dosyalarını sil |
| Devam dinlemesi istemiyorum | `.env` içinde `FOLLOWUP_WINDOW_MS=0` ve `FOLLOWUP_QUESTION_MS=0` |
| Devam dinlemesi ortam sesiyle tetikleniyor | Süreleri kısalt (örn. 4000) veya `VAD_THRESHOLD` değerini 0.35'e çıkar |
| HUD açılmıyor / port dolu | Başka bir Jarvis çalışıyor olabilir. `python -m jarvis.selftest` portu kontrol eder; `.env` içinde `HUD_PORT` değiştir |
| HUD'da bağlantı noktası kırmızı | Sunucu düştü. Sayfa kendi kendine yeniden bağlanmayı dener; olmazsa Jarvis'i tekrar başlat |
| "X uygulaması bulunamadı" | Başlat menüsünde kısayolu yok demektir. Uygulamayı bir kez elle çalıştır ya da tam adını söyle (`selftest` kaç kısayol bulduğunu gösterir) |
| Pencere komutları yanlış pencereyi buluyor | Başlıktan daha uzun bir parça söyle; Jarvis en kısa eşleşen başlığı seçer |

---

## Lisans

[MIT](LICENSE). Kullan, değiştir, dağıt — telif notunu koru, garanti yok.

Kullanılan başlıca kütüphaneler ve lisansları: openWakeWord (Apache-2.0),
Silero VAD (MIT), faster-whisper (MIT), claude-agent-sdk (MIT), pyautogui
(BSD-3), pygetwindow (BSD-3), pyperclip (BSD-3), soundfile (BSD-3), mss (MIT),
pycaw (MIT), FastAPI (MIT), edge-tts (LGPL-3.0).

edge-tts LGPL'dir ama değiştirilmeden, ayrı bir pip paketi olarak import
edilir; bu kullanım MIT lisanslı bu projeyle uyumludur ve lisans bulaşması
yaratmaz. İstemezsen `.env` içinde `TTS_ENGINE=sapi` yapıp Windows'un
yerleşik sesini kullanabilir, paketi hiç kurmayabilirsin.

---

## Sıradaki aşamalar

- **Google Calendar ve Gmail** (salt okuma OAuth): "bugünkü takvimimi oku",
  önemli mail özeti, günlerdir yanıtlanmamış kişileri hatırlatma. Bunlar
  olmadan Jarvis takvim ve yazışmalarına dair hiçbir şey bilmez.
- **Playwright** ile tarayıcı otomasyonu: form doldurma, sekme yönetimi,
  oturum açık sitelerden bilgi çekme.
- **Proje takibi**: `memory/projects.md` içindeki klasörleri tarayıp durum
  özeti çıkarma.
