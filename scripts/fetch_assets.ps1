# Кладёт PPTX-шаблоны и контент-пакет в data/templates/ и пересобирает JSON-профили.
#
# Использование:
#   powershell -ExecutionPolicy Bypass -File scripts/fetch_assets.ps1 -Source "C:\путь\к\датасету"
#   powershell -ExecutionPolicy Bypass -File scripts/fetch_assets.ps1   # пересобрать профили
#
# Бинарные шаблоны не хранятся в git (docs/DECISIONS.md ADR-002).
param(
    [string]$Source = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dest = Join-Path $root "data\templates"
New-Item -ItemType Directory -Force -Path $dest | Out-Null

if ($Source) {
    if (-not (Test-Path -LiteralPath $Source)) {
        Write-Error "Нет такой папки: $Source"
        exit 1
    }
    $files = Get-ChildItem -LiteralPath $Source -Recurse -Depth 2 -File |
        Where-Object { $_.Extension -in ".pptx", ".docx" }
    foreach ($f in $files) {
        Copy-Item -LiteralPath $f.FullName -Destination $dest -Force
        Write-Output "  + $($f.Name)"
    }
    Write-Output "Скопировано файлов: $($files.Count)"
}

Write-Output ""
Write-Output "Содержимое $dest :"
Get-ChildItem -LiteralPath $dest -File | Where-Object { $_.Name -ne ".gitkeep" } |
    ForEach-Object { Write-Output "  $($_.Name)" }

Write-Output ""
Write-Output "Пересборка профилей..."
python (Join-Path $root "tools\make_profiles.py")
