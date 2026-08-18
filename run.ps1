# Jarvis'i başlatır. Sanal ortamı elle etkinleştirmene gerek yok.
# Kullanım:
#   .\run.ps1              -> dinlemeye başla
#   .\run.ps1 --devices    -> mikrofonları listele
#   .\run.ps1 --say "test" -> seslendirme testi
#   .\run.ps1 --echo       -> duyduğunu geri seslendir
#
# Durdurmak için: Ctrl+C

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Sanal ortam yok. Önce kur:" -ForegroundColor Red
    Write-Host "  python -m venv .venv"
    Write-Host "  .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

& $python -m jarvis @args
