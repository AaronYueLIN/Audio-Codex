param(
  [string]$InputPath = '',
  [switch]$Status,
  [switch]$Ensure,
  [switch]$AllowModelDownload
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$hostExe = Join-Path $PSScriptRoot 'host\win-x64\AudioCodex.WindowsAISpeechHost.exe'
$legacy = Join-Path $PSScriptRoot 'WindowsAISpeech.ps1'

if (Test-Path $hostExe) {
  $a = @()
  if ($Status) { $a += '--status' }
  elseif ($Ensure) { $a += '--ensure' }
  else {
    if (-not $InputPath) { throw 'InputPath is required for transcription.' }
    $a += @('--input', [IO.Path]::GetFullPath($InputPath))
    if ($AllowModelDownload) { $a += '--allow-model-download' }
  }
  & $hostExe @a
  exit $LASTEXITCODE
}

# Source-tree / emergency fallback: preserve the original PowerShell WinRT bridge.
$legacyArgs = @()
if ($Status) { $legacyArgs += '-Status' }
elseif ($Ensure) { $legacyArgs += '-Ensure' }
else {
  if (-not $InputPath) { throw 'InputPath is required for transcription.' }
  $legacyArgs += @('-InputPath', [IO.Path]::GetFullPath($InputPath))
  if ($AllowModelDownload) { $legacyArgs += '-AllowModelDownload' }
}
& $legacy @legacyArgs
exit $LASTEXITCODE
