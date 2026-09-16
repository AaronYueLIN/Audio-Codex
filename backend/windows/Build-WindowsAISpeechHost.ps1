param(
  [ValidateSet('Debug','Release')][string]$Configuration = 'Release',
  [switch]$Clean
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$project = Join-Path $root 'AudioCodex.WindowsAISpeechHost\AudioCodex.WindowsAISpeechHost.csproj'
$out = Join-Path $root 'host\win-x64'
if ($Clean -and (Test-Path $out)) { Remove-Item $out -Recurse -Force }
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
  throw '.NET 8 SDK is required to build the Windows AI Speech host.'
}
New-Item -ItemType Directory -Path $out -Force | Out-Null
Write-Host 'Building Audio Codex Windows AI Speech host...' -ForegroundColor Cyan
& dotnet publish $project -c $Configuration -r win-x64 --self-contained true -o $out
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$exe = Join-Path $out 'AudioCodex.WindowsAISpeechHost.exe'
if (-not (Test-Path $exe)) { throw "Host build completed without expected output: $exe" }
Write-Host "Windows AI Speech host ready: $exe" -ForegroundColor Green
