param(
  [Parameter(Mandatory=$true)][string]$InputPath,
  [string]$Language = "",
  [string]$RecognizerId = ""
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

function Emit-Json($obj) {
  $obj | ConvertTo-Json -Depth 8 -Compress
}

function Await-WinRT($Operation, [Type]$ResultType) {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction SilentlyContinue
  $methods = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
  }
  $m = $methods | Select-Object -First 1
  if (-not $m) { throw 'Cannot locate WinRT AsTask helper.' }
  $task = $m.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
  $task.Wait()
  if ($task.IsFaulted) { throw $task.Exception.GetBaseException() }
  return $task.Result
}

function Await-WinRTAction($Operation) {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction SilentlyContinue
  $m = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
  } | Select-Object -First 1
  if (-not $m) {
    # IAsyncActionWithProgress is generic; poll it as a fallback.
    while ([int]$Operation.Status -eq 0) { Start-Sleep -Milliseconds 120 }
    if ([int]$Operation.Status -ne 1) { throw "Windows media operation failed: status=$($Operation.Status)" }
    return
  }
  $task = $m.Invoke($null, @($Operation))
  $task.Wait()
  if ($task.IsFaulted) { throw $task.Exception.GetBaseException() }
}

function Convert-ToSpeechWav([string]$Source) {
  if ([IO.Path]::GetExtension($Source).ToLowerInvariant() -eq '.wav') { return $Source }

  $storageFile = [Type]::GetType('Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime')
  $storageFolder = [Type]::GetType('Windows.Storage.StorageFolder, Windows.Storage, ContentType=WindowsRuntime')
  $collision = [Type]::GetType('Windows.Storage.CreationCollisionOption, Windows.Storage, ContentType=WindowsRuntime')
  $transcoderType = [Type]::GetType('Windows.Media.Transcoding.MediaTranscoder, Windows.Media, ContentType=WindowsRuntime')
  $prepareType = [Type]::GetType('Windows.Media.Transcoding.PrepareTranscodeResult, Windows.Media, ContentType=WindowsRuntime')
  $profileType = [Type]::GetType('Windows.Media.MediaProperties.MediaEncodingProfile, Windows.Media, ContentType=WindowsRuntime')
  $qualityType = [Type]::GetType('Windows.Media.MediaProperties.AudioEncodingQuality, Windows.Media, ContentType=WindowsRuntime')
  if (-not $storageFile -or -not $transcoderType -or -not $profileType) {
    throw 'Windows Media Foundation/WinRT transcoding API is unavailable.'
  }

  $src = Await-WinRT ($storageFile.GetMethod('GetFileFromPathAsync').Invoke($null,@($Source))) $storageFile
  $temp = [IO.Path]::GetTempPath()
  $folder = Await-WinRT ($storageFolder.GetMethod('GetFolderFromPathAsync').Invoke($null,@($temp))) $storageFolder
  $name = 'AudioCodexSpeech-' + [Guid]::NewGuid().ToString('N') + '.wav'
  $replace = [Enum]::Parse($collision, 'ReplaceExisting')
  $dst = Await-WinRT ($folder.GetMethod('CreateFileAsync',[Type[]]@([string],$collision)).Invoke($folder,@($name,$replace))) $storageFile

  $medium = [Enum]::Parse($qualityType, 'Medium')
  $profile = $profileType.GetMethod('CreateWav').Invoke($null,@($medium))
  try { $profile.Audio.SampleRate = 16000 } catch {}
  try { $profile.Audio.ChannelCount = 1 } catch {}
  try { $profile.Audio.BitsPerSample = 16 } catch {}

  $transcoder = [Activator]::CreateInstance($transcoderType)
  $prepOp = $transcoder.PrepareFileTranscodeAsync($src,$dst,$profile)
  $prep = Await-WinRT $prepOp $prepareType
  if (-not $prep.CanTranscode) {
    throw "Windows Media Foundation cannot decode this file (failure=$($prep.FailureReason))."
  }
  $action = $prep.TranscodeAsync()
  # IAsyncActionWithProgress does not always bind to the non-generic AsTask overload in Windows PowerShell.
  while ([int]$action.Status -eq 0) { Start-Sleep -Milliseconds 150 }
  if ([int]$action.Status -ne 1) { throw "Windows Media Foundation transcode failed (status=$($action.Status))." }
  return (Join-Path $temp $name)
}

try {
  $full = [IO.Path]::GetFullPath($InputPath)
  if (-not [IO.File]::Exists($full)) { throw "Audio file not found: $full" }
  Add-Type -AssemblyName System.Speech

  $recognizers = @([System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers())
  if ($recognizers.Count -eq 0) { throw 'Windows has no installed desktop speech recognizer for System.Speech.' }

  $want = $Language.Trim()
  $wantId = $RecognizerId.Trim()
  $selected = $null
  if ($wantId) {
    $selected = $recognizers | Where-Object { $_.Id -ieq $wantId } | Select-Object -First 1
  }
  if (-not $selected -and $want) {
    $norm = $want.Replace('_','-')
    $selected = $recognizers | Where-Object {
      $_.Culture.Name -ieq $norm -or $_.Culture.TwoLetterISOLanguageName -ieq $norm
    } | Select-Object -First 1
  }
  if (-not $selected) {
    $ui = [Globalization.CultureInfo]::CurrentUICulture
    $selected = $recognizers | Where-Object {
      $_.Culture.Name -ieq $ui.Name -or $_.Culture.TwoLetterISOLanguageName -ieq $ui.TwoLetterISOLanguageName
    } | Select-Object -First 1
  }
  if (-not $selected) { $selected = $recognizers | Select-Object -First 1 }

  $wav = $null
  try {
    $wav = Convert-ToSpeechWav $full
    $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($selected)
    $grammar = New-Object System.Speech.Recognition.DictationGrammar
    $engine.LoadGrammar($grammar)
    $engine.SetInputToWaveFile($wav)
    $segments = @()
    while ($true) {
      # Some recognizers throw instead of returning $null once the wave file is exhausted;
      # treat that as end-of-audio so the segments collected so far are not discarded.
      try { $r = $engine.Recognize() } catch { break }
      if ($null -eq $r) { break }
      $text = [string]$r.Text
      if ([string]::IsNullOrWhiteSpace($text)) { continue }
      $start = [int64][Math]::Round($r.Audio.AudioPosition.TotalMilliseconds)
      $dur = [int64][Math]::Round($r.Audio.Duration.TotalMilliseconds)
      if ($dur -lt 1) { $dur = 1000 }
      $segments += [pscustomobject]@{
        start_ms = $start
        end_ms = $start + $dur
        text = $text.Trim()
        speaker_label = $null
        confidence = [double]$r.Confidence
      }
    }
    $engine.Dispose()
    Emit-Json ([pscustomobject]@{
      ok = $true
      engine = 'windows-system-speech'
      recognizer = $selected.Name
      recognizer_id = $selected.Id
      language = $selected.Culture.Name
      segments = $segments
    })
  }
  finally {
    if ($wav -and $wav -ne $full -and [IO.File]::Exists($wav)) { Remove-Item -LiteralPath $wav -Force -ErrorAction SilentlyContinue }
  }
}
catch {
  Emit-Json ([pscustomobject]@{ ok=$false; engine='windows-system-speech'; error=$_.Exception.Message })
  exit 2
}
