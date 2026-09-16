param(
  [string]$InputPath = '',
  [switch]$Status,
  [switch]$Ensure,
  [switch]$AllowModelDownload
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$bridge = Join-Path $PSScriptRoot 'WindowsAIHostBridge.ps1'

function Quote-Arg([string]$s) {
  return '"' + $s.Replace('"','\"') + '"'
}

$modeArgs = @()
if ($Status) {
  $modeArgs += '-Status'
} elseif ($Ensure) {
  $modeArgs += '-Ensure'
} else {
  if (-not $InputPath) { throw 'InputPath is required unless -Status or -Ensure is used.' }
  $modeArgs += @('-InputPath', [IO.Path]::GetFullPath($InputPath))
  if ($AllowModelDownload) { $modeArgs += '-AllowModelDownload' }
}

try {
  $pkg = Get-AppxPackage -Name 'AudioCodex.WinSpeechIdentity' -ErrorAction SilentlyContinue | Select-Object -First 1
  $invoke = Get-Command Invoke-CommandInDesktopPackage -ErrorAction SilentlyContinue
  if ($pkg -and $invoke) {
    $parts = @('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',(Quote-Arg $bridge))
    for ($i=0; $i -lt $modeArgs.Count; $i++) {
      $v = [string]$modeArgs[$i]
      if ($v.StartsWith('-')) { $parts += $v } else { $parts += (Quote-Arg $v) }
    }
    $argLine = $parts -join ' '
    Invoke-CommandInDesktopPackage -PackageFamilyName $pkg.PackageFamilyName -AppId 'App' `
      -Command 'powershell.exe' -Args $argLine -PreventBreakaway
    exit $LASTEXITCODE
  }
} catch {
  # Fall through to a direct launch. The JSON response will explain CapabilityMissing
  # if the systemAIModels capability is required but package identity is unavailable.
}

& powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $bridge @modeArgs
exit $LASTEXITCODE
