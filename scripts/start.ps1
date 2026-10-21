# PowerShell から Docker で文字起こしサーバーを起動する（REQ-5.2）。
# 使い方: scripts\start.ps1          （GPU を使う）
#         scripts\start.ps1 -Cpu     （GPU を使わない）
# スクリプトの実行が許可されていない環境では、README の docker compose コマンドを直接実行する。
param([switch]$Cpu)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force -Path output, models | Out-Null
$files = @("-f", "docker/compose.yaml")
if ($Cpu) { $files += @("-f", "docker/compose.cpu.yaml") }
Write-Host "取得ページ: http://localhost:8765/  （Edge で開く。停止は Ctrl+C）"
docker compose @files up --build
