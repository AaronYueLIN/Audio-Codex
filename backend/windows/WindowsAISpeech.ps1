param(
  [string]$InputPath = '',
  [switch]$AllowModelDownload,
  [switch]$Status,
  [switch]$Ensure
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

function Emit($o) { $o | ConvertTo-Json -Depth 10 -Compress }
function Await-WinRT($Operation, [Type]$ResultType) {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction SilentlyContinue
  $m = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
  } | Select-Object -First 1
  if (-not $m) { throw 'WinRT AsTask helper unavailable.' }
  $t = $m.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
  $t.Wait()
  if ($t.IsFaulted) { throw $t.Exception.GetBaseException() }
  return $t.Result
}
function Result-Type($Operation, [string]$FallbackName) {
  try {
    $t = $Operation.GetType().GenericTypeArguments | Select-Object -First 1
    if ($t) { return $t }
  } catch {}
  return [Type]::GetType($FallbackName, $false)
}
function Is-Unsupported([string]$state) {
  return $state -in @('NotSupportedOnCurrentSystem','NotCompatibleWithSystemHardware','CapabilityMissing','OSUpdateNeeded')
}

try {
  $modelType = [Type]::GetType('Microsoft.Windows.AI.Speech.SpeechRecognitionModel, Microsoft.Windows.AI.Speech, ContentType=WindowsRuntime', $false)
  $batchType = [Type]::GetType('Microsoft.Windows.AI.Speech.BatchRecognition, Microsoft.Windows.AI.Speech, ContentType=WindowsRuntime', $false)
  if (-not $modelType -or -not $batchType) {
    Emit ([pscustomobject]@{ok=$false;unsupported=$true;available=$false;engine='windows-ai-powershell';error='Microsoft.Windows.AI.Speech runtime is not visible in this process.'})
    exit 3
  }

  $ready = $modelType.GetMethod('GetReadyState').Invoke($null, @())
  $readyName = [string]$ready
  if ($Status) {
    Emit ([pscustomobject]@{
      ok=$true;event='status';engine='windows-ai-powershell';available=(-not (Is-Unsupported $readyName));
      ready=($readyName -eq 'Ready');ready_state=$readyName;needs_download=($readyName -ne 'Ready' -and -not (Is-Unsupported $readyName))
    })
    exit 0
  }

  function Ensure-Model {
    $before = [string]$modelType.GetMethod('GetReadyState').Invoke($null, @())
    if ($before -eq 'Ready') { return [pscustomobject]@{ok=$true;ready_state='Ready'} }
    if (Is-Unsupported $before) { return [pscustomobject]@{ok=$false;ready_state=$before;error="Windows AI Speech is unavailable (state=$before)."} }
    Emit ([pscustomobject]@{event='progress';engine='windows-ai-powershell';phase='ensure';progress=.15;message='Preparing Windows AI speech recognition model through Windows Update'})
    $op = $modelType.GetMethod('EnsureReadyAsync').Invoke($null, @())
    $resultType = Result-Type $op 'Microsoft.Windows.AI.AIFeatureReadyResult, Microsoft.Windows.AI, ContentType=WindowsRuntime'
    if (-not $resultType) { throw 'Cannot resolve AIFeatureReadyResult type.' }
    $res = Await-WinRT $op $resultType
    $after = [string]$modelType.GetMethod('GetReadyState').Invoke($null, @())
    $statusName = [string]$res.Status
    if ($statusName -ne 'Success' -or $after -ne 'Ready') {
      return [pscustomobject]@{ok=$false;ready_state=$after;error=([string]$res.ErrorDisplayText);package_installation_failed=[bool]$res.PackageInstallationFailed}
    }
    Emit ([pscustomobject]@{event='progress';engine='windows-ai-powershell';phase='ensure';progress=.95;message='Windows AI speech recognition model is ready'})
    return [pscustomobject]@{ok=$true;ready_state=$after}
  }

  if ($Ensure) {
    $ens = Ensure-Model
    if (-not $ens.ok) { Emit ([pscustomobject]@{ok=$false;event='result';engine='windows-ai-powershell';ready=$false;ready_state=$ens.ready_state;error=$ens.error;package_installation_failed=$ens.package_installation_failed}); exit 4 }
    Emit ([pscustomobject]@{ok=$true;event='result';engine='windows-ai-powershell';ready=$true;ready_state=$ens.ready_state;message='Windows AI speech recognition model is ready.'})
    exit 0
  }

  if (-not $InputPath) { throw 'InputPath is required for transcription.' }
  $full = [IO.Path]::GetFullPath($InputPath)
  if (-not [IO.File]::Exists($full)) { throw "Audio file not found: $full" }
  if ($readyName -ne 'Ready') {
    if (Is-Unsupported $readyName) {
      Emit ([pscustomobject]@{ok=$false;unsupported=$true;engine='windows-ai-powershell';ready_state=$readyName;error="Windows AI Speech is unavailable (state=$readyName)."})
      exit 3
    }
    if (-not $AllowModelDownload) {
      Emit ([pscustomobject]@{ok=$false;needs_download=$true;engine='windows-ai-powershell';ready_state=$readyName;error='Windows AI speech recognition model is not ready. Prepare it from Audio Codex Settings before transcription.'})
      exit 4
    }
    $ens = Ensure-Model
    if (-not $ens.ok) { Emit ([pscustomobject]@{ok=$false;needs_download=$true;engine='windows-ai-powershell';ready_state=$ens.ready_state;error=$ens.error}); exit 4 }
  }

  Emit ([pscustomobject]@{event='progress';engine='windows-ai-powershell';phase='model';progress=.10;message='Loading Windows AI speech model'})
  $createOp = $modelType.GetMethod('TryCreateAsync').Invoke($null, @())
  $resultType = Result-Type $createOp 'Microsoft.Windows.AI.Speech.SpeechRecognitionModelResult, Microsoft.Windows.AI.Speech, ContentType=WindowsRuntime'
  if (-not $resultType) { throw 'Cannot resolve SpeechRecognitionModelResult type.' }
  $result = Await-WinRT $createOp $resultType
  $speechModel = $result.SpeechModel
  if ($null -eq $speechModel) { throw "SpeechRecognitionModel creation failed: $($result.ExtendedError)" }
  Emit ([pscustomobject]@{event='progress';engine='windows-ai-powershell';phase='recognition';progress=.20;message='Transcribing on this PC'})
  $batch = [Activator]::CreateInstance($batchType, @($speechModel))
  $op = $batch.RecognizeFromFile($full)
  $text = Await-WinRT $op ([string])
  Emit ([pscustomobject]@{ok=$true;event='result';engine='windows-ai-powershell';text=[string]$text})
}
catch {
  $hr = ('0x{0:X8}' -f ($_.Exception.HResult -band 0xffffffff))
  Emit ([pscustomobject]@{ok=$false;event='result';engine='windows-ai-powershell';error=$_.Exception.Message;hresult=$hr})
  exit 2
}
