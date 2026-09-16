$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$OutputEncoding=[Console]::OutputEncoding
$recs=@()
try{
  Add-Type -AssemblyName System.Speech
  $recs=@([System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()|ForEach-Object{
    [pscustomobject]@{id=$_.Id;name=$_.Name;language=$_.Culture.Name;description=$_.Description}
  })
}catch{}
$pkg=$null
try{$pkg=Get-AppxPackage -Name 'AudioCodex.WinSpeechIdentity'|Select-Object -First 1}catch{}
$hostExe=Join-Path $PSScriptRoot 'host\win-x64\AudioCodex.WindowsAISpeechHost.exe'
$ai=$null
try{
  $invoke=Join-Path $PSScriptRoot 'WindowsAIInvoke.ps1'
  $lines=@(& powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $invoke -Status 2>$null)
  for($i=$lines.Count-1;$i-ge 0;$i--){
    $line=[string]$lines[$i]
    if($line.Trim().StartsWith('{')){try{$ai=$line|ConvertFrom-Json;break}catch{}}
  }
}catch{}
$aiType=$false
try{$aiType=$null -ne [Type]::GetType('Microsoft.Windows.AI.Speech.SpeechRecognitionModel, Microsoft.Windows.AI.Speech, ContentType=WindowsRuntime',$false)}catch{}
$devMode=$false
try{$devMode=[bool](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock' -Name AllowDevelopmentWithoutDevLicense -ErrorAction SilentlyContinue).AllowDevelopmentWithoutDevLicense}catch{}
[pscustomobject]@{
  windows_build=[Environment]::OSVersion.Version.Build
  system_speech=($recs.Count -gt 0)
  recognizers=$recs
  package_identity_registered=($null -ne $pkg)
  developer_mode=$devMode
  windows_ai_type_visible=$aiType
  windows_ai_host_built=(Test-Path $hostExe)
  windows_ai_available=([bool]($ai -and $ai.ok -and $ai.available))
  windows_ai_ready=([bool]($ai -and $ai.ok -and $ai.ready))
  windows_ai_ready_state=($(if($ai){[string]$ai.ready_state}else{''}))
  windows_ai_engine=($(if($ai){[string]$ai.engine}else{''}))
  windows_ai_error=($(if($ai -and $ai.error){[string]$ai.error}else{''}))
}|ConvertTo-Json -Depth 6 -Compress
