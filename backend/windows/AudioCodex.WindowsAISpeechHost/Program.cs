using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.Windows.AI;
using Microsoft.Windows.AI.MachineLearning;
using Microsoft.Windows.AI.Speech;

namespace AudioCodex.WindowsAISpeechHost;

internal static class Program
{
    private static readonly object LogLock = new();
    private static readonly string LogPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "AudioCodex", "windows-ai-speech.log");

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetDefaultDllDirectories(uint directoryFlags);

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr AddDllDirectory(string newDirectory);

    private const uint LOAD_LIBRARY_SEARCH_DEFAULT_DIRS = 0x00001000;
    private const uint LOAD_LIBRARY_SEARCH_USER_DIRS = 0x00000400;

    [DllImport("kernel32.dll")]
    private static extern IntPtr AddVectoredExceptionHandler(uint first, VectoredHandler handler);

    private delegate int VectoredHandler(IntPtr exceptionInfo);

    private static VectoredHandler? _handlerRef;

    private static int IllegalInstructionHandler(IntPtr exceptionInfo)
    {
        try
        {
            IntPtr record = Marshal.ReadIntPtr(exceptionInfo);        // EXCEPTION_POINTERS->ExceptionRecord
            int code = Marshal.ReadInt32(record, 0);                  // EXCEPTION_RECORD->ExceptionCode
            if (code == unchecked((int)0xC000001D))                   // STATUS_ILLEGAL_INSTRUCTION
            {
                IntPtr addr = Marshal.ReadIntPtr(record, 16);         // ExceptionAddress
                string where = "unknown module";
                foreach (ProcessModule m in Process.GetCurrentProcess().Modules)
                {
                    long b = m.BaseAddress.ToInt64();
                    if (addr.ToInt64() >= b && addr.ToInt64() < b + m.ModuleMemorySize)
                    {
                        where = m.FileName + $"+0x{addr.ToInt64() - b:X}";
                        break;
                    }
                }
                Log($"ILLEGAL INSTRUCTION at 0x{addr.ToInt64():X} in {where}");
            }
        }
        catch { }
        return 0; // EXCEPTION_CONTINUE_SEARCH
    }

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            _handlerRef = IllegalInstructionHandler;
            AddVectoredExceptionHandler(1, _handlerRef);
            RegisterSpeechModelDllDirectory();
            return MainAsync(args).GetAwaiter().GetResult();
        }
        catch (Exception ex)
        {
            Log("FATAL " + ex);
            EmitError(ex, "Windows AI Speech host failed before the request completed.");
            return 2;
        }
    }

    // Microsoft.Windows.AI.Speech loads asrapi.dll / asrmodelapi.dll / cacheapi.dll / vadapi.dll
    // by bare file name, but the speech model package keeps them in its winml\ subfolder, which is
    // not part of the default DLL search path. Without this the load fails with 0x8007007E
    // (ERROR_MOD_NOT_FOUND) before the model can be created.
    private static void RegisterSpeechModelDllDirectory()
    {
        try
        {
            string? winml = FindSpeechModelWinmlDir();
            if (winml == null)
            {
                Log("no Windows AI Speech model package with a winml folder was found");
                return;
            }
            SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS | LOAD_LIBRARY_SEARCH_USER_DIRS);
            IntPtr cookie = AddDllDirectory(winml);
            Log($"model DLL directory registered: {winml} (cookie={(cookie == IntPtr.Zero ? "failed" : "ok")})");
        }
        catch (Exception ex)
        {
            Log("model DLL directory registration failed: " + ex.Message);
        }
    }

    private static string? FindSpeechModelWinmlDir()
    {
        const string repo = @"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages";
        using var packages = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(repo);
        if (packages == null)
            return null;
        foreach (string sub in packages.GetSubKeyNames())
        {
            if (!sub.StartsWith("Microsoft.Windows.AI.Speech", StringComparison.OrdinalIgnoreCase))
                continue;
            using var pkg = packages.OpenSubKey(sub);
            if (pkg?.GetValue("PackageRootFolder") is not string root || string.IsNullOrEmpty(root))
                continue;
            string winml = Path.Combine(root, "winml");
            if (Directory.Exists(winml))
                return winml;
        }
        return null;
    }

    private static async Task<int> MainAsync(string[] args)
    {
        Log("launch " + string.Join(" ", args.Select(QuoteForLog)));
        var parsed = ParseArgs(args);
        if (parsed.Status)
            return EmitStatus();
        if (args.Any(a => a == "--cpuid"))
        {
            Emit(new
            {
                avx_vnni = System.Runtime.Intrinsics.X86.AvxVnni.IsSupported,
                avx512f = System.Runtime.Intrinsics.X86.Avx512F.IsSupported,
                avx2 = System.Runtime.Intrinsics.X86.Avx2.IsSupported,
                cpu = System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture.ToString(),
            });
            return 0;
        }
        if (parsed.Ensure)
            return await EnsureOnlyAsync();
        if (string.IsNullOrWhiteSpace(parsed.InputPath))
        {
            Emit(new
            {
                ok = false,
                engine = "windows-ai-host",
                error = "Usage: AudioCodex.WindowsAISpeechHost.exe --status | --ensure | --input <audio-file> [--allow-model-download]"
            });
            return 64;
        }
        return await TranscribeAsync(parsed.InputPath!, parsed.AllowModelDownload);
    }

    private static int EmitStatus()
    {
        try
        {
            var ready = RunWithTimeout(() => SpeechRecognitionModel.GetReadyState(), 20, "GetReadyState");
            string state = ready.ToString();
            bool supported = !IsUnsupportedState(state);
            string winml = WinMlProviderSummary();
            Emit(new
            {
                ok = true,
                @event = "status",
                engine = "windows-ai-host",
                host_version = typeof(Program).Assembly.GetName().Version?.ToString() ?? "1.1.0.0",
                windows_build = Environment.OSVersion.Version.Build,
                available = supported,
                ready = ready == AIFeatureReadyState.Ready,
                ready_state = state,
                needs_download = supported && ready != AIFeatureReadyState.Ready,
                winml_providers = winml,
            });
            return 0;
        }
        catch (Exception ex)
        {
            Log("status failed " + ex);
            EmitError(ex, "Unable to query Windows AI Speech readiness.");
            return 2;
        }
    }

    private static void LogLoadedModules()
    {
        try
        {
            var names = new List<string>();
            foreach (System.Diagnostics.ProcessModule m in System.Diagnostics.Process.GetCurrentProcess().Modules)
            {
                string n = m.ModuleName ?? "";
                if (n.Contains("asr", StringComparison.OrdinalIgnoreCase) ||
                    n.Contains("onnx", StringComparison.OrdinalIgnoreCase) ||
                    n.Contains("Windows.AI", StringComparison.OrdinalIgnoreCase) ||
                    n.Contains("winml", StringComparison.OrdinalIgnoreCase) ||
                    n.Contains("provider", StringComparison.OrdinalIgnoreCase) ||
                    n.Contains("DirectML", StringComparison.OrdinalIgnoreCase))
                {
                    names.Add(n + " @" + m.FileName);
                }
            }
            Log("loaded modules: " + string.Join(", ", names));
        }
        catch (Exception ex) { Log("module dump failed: " + ex.Message); }
    }

    private static string WinMlProviderSummary()
    {
        try
        {
            var catalog = ExecutionProviderCatalog.GetDefault();
            var parts = new List<string>();
            foreach (var provider in catalog.FindAllProviders())
                parts.Add($"{provider.Name}:{provider.ReadyState}");
            return string.Join("; ", parts);
        }
        catch (Exception ex)
        {
            return $"error:{ex.Message}";
        }
    }

    private static async Task<int> EnsureOnlyAsync()
    {
        var outcome = await EnsureModelAsync(force: true);
        if (!outcome.Ok)
        {
            Emit(new
            {
                ok = false,
                @event = "result",
                engine = "windows-ai-host",
                ready = false,
                ready_state = outcome.ReadyState,
                error = outcome.Error,
                package_installation_failed = outcome.PackageInstallationFailed,
            });
            return 4;
        }
        Emit(new
        {
            ok = true,
            @event = "result",
            engine = "windows-ai-host",
            ready = true,
            ready_state = outcome.ReadyState,
            message = "Windows AI speech recognition model is ready."
        });
        return 0;
    }

    private static async Task<int> TranscribeAsync(string inputPath, bool allowModelDownload)
    {
        string fullPath;
        try
        {
            fullPath = Path.GetFullPath(inputPath);
        }
        catch (Exception ex)
        {
            EmitError(ex, "The audio path is invalid.");
            return 64;
        }
        if (!File.Exists(fullPath))
        {
            Emit(new { ok = false, engine = "windows-ai-host", error = $"Audio file not found: {fullPath}" });
            return 66;
        }

        try
        {
            var ready = RunWithTimeout(() => SpeechRecognitionModel.GetReadyState(), 20, "GetReadyState");
            string state = ready.ToString();
            if (ready != AIFeatureReadyState.Ready)
            {
                if (IsUnsupportedState(state))
                {
                    Emit(new
                    {
                        ok = false,
                        engine = "windows-ai-host",
                        unsupported = true,
                        ready_state = state,
                        error = FriendlyReadyStateError(state),
                    });
                    return 3;
                }
                if (!allowModelDownload)
                {
                    Emit(new
                    {
                        ok = false,
                        engine = "windows-ai-host",
                        needs_download = true,
                        ready_state = state,
                        error = "Windows AI speech recognition model is not ready. Prepare it from Audio Codex Settings before transcription."
                    });
                    return 4;
                }
                var outcome = await EnsureModelAsync();
                if (!outcome.Ok)
                {
                    Emit(new
                    {
                        ok = false,
                        engine = "windows-ai-host",
                        needs_download = true,
                        ready_state = outcome.ReadyState,
                        error = outcome.Error,
                        package_installation_failed = outcome.PackageInstallationFailed,
                    });
                    return 4;
                }
            }

            Emit(new { @event = "progress", engine = "windows-ai-host", phase = "model", progress = 0.10, message = "Loading Windows AI speech model" });
            string winml = "(init skipped for A/B test)";
            Log("WinML init: " + winml);
            Log("TryCreateAsync start");
            var sw = Stopwatch.StartNew();
            var createOp = SpeechRecognitionModel.TryCreateAsync();
            try
            {
                createOp.Progress = (info, p) =>
                {
                    try { Log($"TryCreate progress status={p.Status} value={p.Progress}"); } catch { }
                };
            }
            catch (Exception ex) { Log("progress hook failed: " + ex.Message); }
            var created = RunWithTimeoutAsync(async () => await createOp, 180, "SpeechRecognitionModel.TryCreateAsync");
            sw.Stop();
            if (created.SpeechModel is null)
            {
                string detail = created.ExtendedError?.ToString() ?? "Unknown model creation error";
                Log("TryCreateAsync null after " + sw.Elapsed + " detail=" + detail);
                LogLoadedModules();
                Emit(new
                {
                    ok = false,
                    engine = "windows-ai-host",
                    error = "Windows AI speech model could not be created: " + detail,
                    extended_error = detail,
                    hint = detail.Contains("8007007E", StringComparison.OrdinalIgnoreCase)
                        ? "The Windows AI runtime could not load a required module. Rebuild this host self-contained, repair Windows AI components, or use the local fallback engine."
                        : null,
                });
                return 5;
            }

            using var model = created.SpeechModel;
            using var batch = new BatchRecognition(model);
            Emit(new { @event = "progress", engine = "windows-ai-host", phase = "recognition", progress = 0.20, message = "Transcribing on this PC" });
            Log("RecognizeFromFile start " + fullPath);
            sw.Restart();
            string text = await batch.RecognizeFromFile(fullPath);
            sw.Stop();
            text = (text ?? string.Empty).Trim();
            Log($"RecognizeFromFile done {sw.Elapsed.TotalSeconds:F1}s chars={text.Length}");
            if (string.IsNullOrWhiteSpace(text))
            {
                Emit(new { ok = false, engine = "windows-ai-host", error = "Windows AI Speech returned no readable text." });
                return 6;
            }

            Emit(new
            {
                ok = true,
                @event = "result",
                engine = "windows-ai-host",
                text,
                elapsed_seconds = Math.Round(sw.Elapsed.TotalSeconds, 3),
            });
            return 0;
        }
        catch (Exception ex)
        {
            Log("transcription failed " + ex);
            EmitError(ex, "Windows AI Speech transcription failed.");
            return 2;
        }
    }

    private static async Task<string> InitializeWindowsMlAsync()
    {
        try
        {
            var catalog = ExecutionProviderCatalog.GetDefault();
            var parts = new List<string>();
            try
            {
                int rc = 0;
                var reg = await catalog.RegisterCertifiedAsync();
                foreach (var p in reg) { rc++; parts.Add($"registerCertified {p.Name}:{p.ReadyState}"); }
                parts.Add("registerCertified count=" + rc);
            }
            catch (Exception ex) { parts.Add("registerCertified error=" + ex.Message); }
            var providers = await catalog.EnsureAndRegisterCertifiedAsync();
            foreach (var provider in providers)
            {
                string info = $"{provider.Name}:{provider.ReadyState}";
                try { info += $":register={provider.TryRegister()}"; }
                catch (Exception ex) { info += $":registerError={ex.Message}"; }
                parts.Add(info);
            }
            return string.Join("; ", parts);
        }
        catch (Exception ex)
        {
            return $"error:{ex.Message} ({FormatHResult(ex.HResult)})";
        }
    }

    private static async Task<EnsureOutcome> EnsureModelAsync(bool force = false)
    {
        try
        {
            var before = RunWithTimeout(() => SpeechRecognitionModel.GetReadyState(), 20, "GetReadyState");
            if (before == AIFeatureReadyState.Ready && !force)
                return new(true, before.ToString(), null, false);

            string beforeName = before.ToString();
            if (IsUnsupportedState(beforeName))
                return new(false, beforeName, FriendlyReadyStateError(beforeName), false);

            Emit(new { @event = "progress", engine = "windows-ai-host", phase = "ensure", progress = 0.15, message = "Preparing Windows AI speech recognition model through Windows Update" });
            Log("EnsureReadyAsync start state=" + beforeName);
            var sw = Stopwatch.StartNew();
            var result = RunWithTimeoutAsync(async () => await SpeechRecognitionModel.EnsureReadyAsync(), 900, "EnsureReadyAsync");
            sw.Stop();
            var after = RunWithTimeout(() => SpeechRecognitionModel.GetReadyState(), 20, "GetReadyState");
            string afterName = after.ToString();
            Log($"EnsureReadyAsync done status={result.Status} after={afterName} packageFailed={result.PackageInstallationFailed} elapsed={sw.Elapsed}");
            if (result.Status != AIFeatureReadyResultState.Success || after != AIFeatureReadyState.Ready)
            {
                string error = result.ErrorDisplayText;
                if (string.IsNullOrWhiteSpace(error))
                    error = $"Model preparation did not complete successfully (status={result.Status}, readyState={afterName}, extendedError={result.ExtendedError}).";
                return new(false, afterName, error, result.PackageInstallationFailed);
            }
            Emit(new { @event = "progress", engine = "windows-ai-host", phase = "ensure", progress = 0.95, message = "Windows AI speech recognition model is ready" });
            return new(true, afterName, null, result.PackageInstallationFailed);
        }
        catch (Exception ex)
        {
            Log("ensure failed " + ex);
            return new(false, SafeReadyState(), $"{ex.Message} ({FormatHResult(ex.HResult)})", false);
        }
    }

    private static string SafeReadyState()
    {
        try { return RunWithTimeout(() => SpeechRecognitionModel.GetReadyState(), 20, "GetReadyState").ToString(); }
        catch { return "Unknown"; }
    }

    private static bool IsUnsupportedState(string state) => state is
        "NotSupportedOnCurrentSystem" or
        "NotCompatibleWithSystemHardware" or
        "CapabilityMissing" or
        "OSUpdateNeeded";

    private static string FriendlyReadyStateError(string state) => state switch
    {
        "NotSupportedOnCurrentSystem" => "Windows AI Speech is not supported on this Windows installation or hardware.",
        "NotCompatibleWithSystemHardware" => "This PC is not compatible with the Windows AI speech recognition model.",
        "CapabilityMissing" => "The Audio Codex package identity is missing the systemAIModels capability.",
        "OSUpdateNeeded" => "Windows must be updated before Windows AI Speech can be used.",
        "DisabledByUser" => "Windows AI speech recognition is disabled or its model was removed by the user.",
        _ => $"Windows AI Speech is not ready (state={state}).",
    };

    private static ParsedArgs ParseArgs(string[] args)
    {
        string? input = null;
        bool status = false, ensure = false, allow = false;
        for (int i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--status": status = true; break;
                case "--ensure": ensure = true; break;
                case "--allow-model-download": allow = true; break;
                case "--input" when i + 1 < args.Length: input = args[++i]; break;
                default:
                    if (!args[i].StartsWith("--", StringComparison.Ordinal) && input is null)
                        input = args[i];
                    break;
            }
        }
        return new(input, status, ensure, allow);
    }

    private static void EmitError(Exception ex, string prefix)
    {
        Emit(new
        {
            ok = false,
            @event = "result",
            engine = "windows-ai-host",
            error = $"{prefix} {ex.Message}",
            exception_type = ex.GetType().FullName,
            hresult = FormatHResult(ex.HResult),
            hint = ex.HResult == unchecked((int)0x8007007E)
                ? "A required Windows AI module was not found. The bundled host uses app-local Windows App SDK files to reduce runtime version conflicts; repair Windows AI components if this persists."
                : null,
        });
    }

    private static string FormatHResult(int hresult) => $"0x{unchecked((uint)hresult):X8}";

    // The Windows AI Speech runtime can block forever inside a WinRT call when the process does
    // not carry a package identity that grants systemAIModels. Bound every such call so the host
    // always answers and the caller can fall back to another local engine.
    private static T RunWithTimeout<T>(Func<T> work, int seconds, string label)
    {
        var task = Task.Run(work);
        if (!task.Wait(TimeSpan.FromSeconds(seconds)))
        {
            EmitTimeout(label, seconds);
            Environment.Exit(7);
        }
        return task.GetAwaiter().GetResult();
    }

    private static T RunWithTimeoutAsync<T>(Func<Task<T>> work, int seconds, string label)
    {
        var task = Task.Run(work);
        if (!task.Wait(TimeSpan.FromSeconds(seconds)))
        {
            EmitTimeout(label, seconds);
            Environment.Exit(7);
        }
        return task.GetAwaiter().GetResult();
    }

    private static void EmitTimeout(string label, int seconds)
    {
        Log($"{label} timed out after {seconds}s");
        Emit(new
        {
            ok = false,
            engine = "windows-ai-host",
            timeout = true,
            error = $"{label} did not respond within {seconds} seconds. The Windows AI Speech runtime is unresponsive on this PC; use another local transcription engine.",
        });
        Console.Out.Flush();
    }

    private static void Emit(object value)
    {
        Console.WriteLine(JsonSerializer.Serialize(value));
        Console.Out.Flush();
    }

    private static void Log(string message)
    {
        try
        {
            lock (LogLock)
            {
                Directory.CreateDirectory(Path.GetDirectoryName(LogPath)!);
                File.AppendAllText(LogPath, $"{DateTimeOffset.Now:O} [{Environment.CurrentManagedThreadId}] {message}\r\n");
            }
        }
        catch { }
    }

    private static string QuoteForLog(string value) => value.Contains(' ') ? '"' + value + '"' : value;

    private sealed record ParsedArgs(string? InputPath, bool Status, bool Ensure, bool AllowModelDownload);
    private sealed record EnsureOutcome(bool Ok, string ReadyState, string? Error, bool PackageInstallationFailed);
}
