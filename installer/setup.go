// Reconstructed installer source for the public R12.1 repository.
//
// The R12.1 application payload source was recovered directly from the released
// installer. The outer setup.go source itself was not embedded in that EXE.
// This file is reconstructed from the same Audio Codex Go installer lineage
// and updated to the R12.1 release constants.
//
// See docs/RECOVERY.md.

package main

import (
    "archive/zip"
    "bytes"
    "context"
    _ "embed"
    "errors"
    "fmt"
    "io"
    "os"
    "os/exec"
    "path/filepath"
    "runtime"
    "strconv"
    "strings"
    "time"
)

//go:embed payload.zip
var payload []byte

const (
    appName = "Audio Codex"
    displayVersion = "1.2.1"
    buildID = "1.2.1-R12.1-AUDIO-INTELLIGENCE-WINDOWSAISPEECH1"
)

type pyInfo struct {
    Cmd string
    Args []string
    Path string
    Major int
    Minor int
    Bits int
    CoreOK bool
    WhisperOK bool
    RequestsOK bool
    Score int
}

func localAppData() string {
    if v := os.Getenv("LOCALAPPDATA"); v != "" { return v }
    if v := os.Getenv("USERPROFILE"); v != "" { return filepath.Join(v, "AppData", "Local") }
    return "."
}
func installDir() string { return filepath.Join(localAppData(), "Programs", "Audio Codex") }
func logPath() string { return filepath.Join(installDir(), "install.log") }

func logf(format string, args ...any) {
    msg := fmt.Sprintf(format, args...)
    fmt.Println(msg)
    _ = os.MkdirAll(installDir(), 0755)
    f, err := os.OpenFile(logPath(), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
    if err == nil { defer f.Close(); fmt.Fprintln(f, time.Now().Format(time.RFC3339), msg) }
}

func captureTimeout(timeout time.Duration, name string, args ...string) (string, error) {
    ctx, cancel := context.WithTimeout(context.Background(), timeout); defer cancel()
    cmd := exec.CommandContext(ctx, name, args...)
    cmd.Env = append(os.Environ(), "PYTHONUTF8=1")
    out, err := cmd.CombinedOutput()
    if ctx.Err() == context.DeadlineExceeded { return string(out), errors.New("command timed out") }
    return strings.TrimSpace(string(out)), err
}

func parsePython(cmd string, args []string) *pyInfo {
    script := `import sys,struct,importlib.util
core=all(importlib.util.find_spec(x) is not None for x in ('fastapi','uvicorn','pydantic'))
wh=importlib.util.find_spec('faster_whisper') is not None
reqs=importlib.util.find_spec('requests') is not None
print(f"{sys.version_info.major}.{sys.version_info.minor}|{sys.executable}|{struct.calcsize('P')*8}|{int(core)}|{int(wh)}|{int(reqs)}")`
    full := append(append([]string{}, args...), "-c", script)
    out, err := captureTimeout(8*time.Second, cmd, full...)
    if err != nil { return nil }
    parts := strings.Split(strings.TrimSpace(out), "|")
    if len(parts) < 6 { return nil }
    vv := strings.Split(parts[0], "."); if len(vv) < 2 { return nil }
    maj,_ := strconv.Atoi(vv[0]); min,_ := strconv.Atoi(vv[1]); bits,_ := strconv.Atoi(parts[2])
    if maj != 3 || min < 11 || min > 13 || bits != 64 { return nil }
    core := parts[3] == "1"; wh := parts[4] == "1"; reqs := parts[5] == "1"
    score := min
    if core { score += 100 }
    if wh { score += 300 }
    if reqs { score += 150 }
    return &pyInfo{Cmd:cmd, Args:args, Path:parts[1], Major:maj, Minor:min, Bits:bits, CoreOK:core, WhisperOK:wh, RequestsOK:reqs, Score:score}
}

func findPython() (*pyInfo, []pyInfo) {
    candidates := []struct{cmd string; args []string}{
        {"py", []string{"-3.13"}}, {"py", []string{"-3.12"}}, {"py", []string{"-3.11"}},
        {"python", nil}, {"python3", nil},
    }
    byPath := map[string]pyInfo{}
    for _, c := range candidates {
        info := parsePython(c.cmd, c.args)
        if info == nil { continue }
        key := strings.ToLower(info.Path)
        if old, ok := byPath[key]; !ok || info.Score > old.Score { byPath[key] = *info }
    }
    all := make([]pyInfo,0,len(byPath)); var best *pyInfo
    for _, v := range byPath { all = append(all, v); vv:=v; if best==nil || vv.Score>best.Score { best=&vv } }
    return best, all
}

func runVisible(name string, args ...string) error {
    cmd := exec.Command(name, args...)
    cmd.Stdout = os.Stdout; cmd.Stderr = os.Stderr; cmd.Stdin = os.Stdin
    cmd.Env = append(os.Environ(), "PYTHONUTF8=1", "PIP_DISABLE_PIP_VERSION_CHECK=1", "PIP_NO_PYTHON_VERSION_WARNING=1", "PIP_DEFAULT_TIMEOUT=60")
    return cmd.Run()
}

func ensureVenv(base *pyInfo, root string) (string, error) {
    venv := filepath.Join(root, "venv")
    py := filepath.Join(venv, "Scripts", "python.exe")
    if _, err := os.Stat(py); err == nil {
        out, e := captureTimeout(8*time.Second, py, "-c", `import sys,struct; print(f"{sys.version_info.major}.{sys.version_info.minor}|{struct.calcsize('P')*8}")`)
        if e == nil {
            p:=strings.Split(out,"|"); if len(p)==2 && p[1]=="64" { vv:=strings.Split(p[0],"."); if len(vv)==2 { m,_:=strconv.Atoi(vv[1]); if vv[0]=="3" && m>=11 && m<=13 { return py,nil } } }
        }
        _ = os.RemoveAll(venv)
    }
    args := append(append([]string{}, base.Args...), "-m", "venv", "--system-site-packages", venv)
    logf("Creating isolated venv with --system-site-packages (reuses compatible global packages)...")
    if err := runVisible(base.Cmd, args...); err != nil { return "", err }
    if _, err := os.Stat(py); err != nil { return "", errors.New("venv python.exe was not created") }
    return py, nil
}

func pyCheck(py, script string) bool {
    _, err := captureTimeout(15*time.Second, py, "-c", script)
    return err == nil
}

func ensurePackages(py string) error {
    if !pyCheck(py, `import pip`) { if err:=runVisible(py,"-m","ensurepip","--upgrade"); err!=nil{return err} }
    coreCheck := `import importlib.metadata as m
from packaging.version import Version
req={'fastapi':('0.115','1'),'uvicorn':('0.34','1'),'pydantic':('2.10','3')}
for n,(lo,hi) in req.items():
 v=Version(m.version(n)); assert v>=Version(lo) and v<Version(hi),(n,v)`
    // packaging is normally supplied by pip; fall back to import-only if it is absent.
    coreFallback := `import fastapi,uvicorn,pydantic
assert int(pydantic.__version__.split('.')[0])>=2`
    coreOK := pyCheck(py, `import packaging.version;`+coreCheck) || pyCheck(py, coreFallback)
    if coreOK {
        logf("  Core backend packages: reused existing compatible packages")
    } else {
        logf("  Core backend packages: missing/incompatible -> downloading only required packages")
        if err:=runVisible(py,"-m","pip","install","fastapi>=0.115,<1","uvicorn>=0.34,<1","pydantic>=2.10,<3"); err!=nil{return err}
    }
    whisperCheck := `import faster_whisper,importlib.metadata as m
v=m.version('faster-whisper'); print(v)`
    if pyCheck(py, whisperCheck) {
        logf("  faster-whisper: reused existing package")
    } else {
        logf("  faster-whisper: missing -> downloading package")
        if err:=runVisible(py,"-m","pip","install","faster-whisper>=1.1,<2"); err!=nil{return err}
    }
    requestsCheck := `import requests,importlib.metadata as m
v=m.version('requests'); print(v)`
    if pyCheck(py, requestsCheck) {
        logf("  requests: reused existing package")
    } else {
        logf("  requests: missing -> downloading package")
        if err:=runVisible(py,"-m","pip","install","requests>=2.32,<3"); err!=nil{return err}
    }
    return nil
}

func unzipPayload(root string) error {
    zr, err := zip.NewReader(bytes.NewReader(payload), int64(len(payload))); if err!=nil{return err}
    prefix := "AudioCodex/"
    for _, f := range zr.File {
        name := filepath.ToSlash(f.Name)
        if !strings.HasPrefix(name,prefix) { continue }
        rel := strings.TrimPrefix(name,prefix); if rel=="" {continue}
        clean := filepath.Clean(filepath.FromSlash(rel)); if clean=="." || strings.HasPrefix(clean,"..") || filepath.IsAbs(clean) {return fmt.Errorf("unsafe payload path: %s",rel)}
        dst := filepath.Join(root,clean)
        if f.FileInfo().IsDir() { if err:=os.MkdirAll(dst,0755);err!=nil{return err};continue }
        if err:=os.MkdirAll(filepath.Dir(dst),0755);err!=nil{return err}
        rc,err:=f.Open();if err!=nil{return err}; tmp:=dst+".new"
        out,err:=os.Create(tmp); if err!=nil{rc.Close();return err}
        _,copyErr:=io.Copy(out,rc); closeErr:=out.Close();rc.Close()
        if copyErr!=nil{return copyErr};if closeErr!=nil{return closeErr}
        _=os.Remove(dst); if err:=os.Rename(tmp,dst);err!=nil{return err}
    }
    return nil
}

func copyFile(src,dst string) error {
    a,_:=filepath.Abs(src); b,_:=filepath.Abs(dst); if strings.EqualFold(filepath.Clean(a),filepath.Clean(b)){return nil}
    in,err:=os.Open(src);if err!=nil{return err};defer in.Close()
    if err:=os.MkdirAll(filepath.Dir(dst),0755);err!=nil{return err}
    out,err:=os.Create(dst);if err!=nil{return err}
    _,e:=io.Copy(out,in);ce:=out.Close();if e!=nil{return e};return ce
}
func copySelf(root string) error {
    self,err:=os.Executable();if err!=nil{return err}
    if err:=copyFile(self,filepath.Join(root,"Audio Codex Maintenance.exe"));err!=nil{return err}
    return copyFile(self,filepath.Join(root,"Uninstall Audio Codex.exe"))
}

func psQuote(s string) string { return "'"+strings.ReplaceAll(s,"'","''")+"'" }
func runPowerShell(script string) error {
    return runVisible("powershell.exe","-NoLogo","-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-Command",script)
}
func stopRunning(root string) {
    script := fmt.Sprintf(`$root=%s; $v=Join-Path $root 'venv'; Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($v,[System.StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }`, psQuote(root))
    _ = runPowerShell(script)
}
func registerSpeechIdentity(root string) {
    manifest:=filepath.Join(root,"AppxManifest.xml")
    script:=fmt.Sprintf(`$ErrorActionPreference='Stop';$manifest=%s;$root=%s;try{$old=Get-AppxPackage -Name 'AudioCodex.WinSpeechIdentity' -ErrorAction SilentlyContinue;if($old){$old|Remove-AppxPackage -ErrorAction SilentlyContinue};Add-AppxPackage -Register $manifest -ExternalLocation $root -ErrorAction Stop;Write-Output 'REGISTERED'}catch{$dev=[bool](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock' -Name AllowDevelopmentWithoutDevLicense -ErrorAction SilentlyContinue).AllowDevelopmentWithoutDevLicense;if(-not $dev){Write-Output ('WARNING: Windows speech package identity was NOT registered because Windows Developer Mode is off. Enable it in Settings > System > For developers and run Repair. Reason: '+$_.Exception.Message)}else{Write-Output ('WARNING: Windows speech package identity was NOT registered: '+$_.Exception.Message)};Write-Output 'Windows AI Speech stays unavailable until the identity is registered; Windows System Speech and faster-whisper still work.';exit 0}`,psQuote(manifest),psQuote(root))
    _=runPowerShell(script)
}
func unregisterSpeechIdentity(){ _=runPowerShell(`Get-AppxPackage -Name 'AudioCodex.WinSpeechIdentity' -ErrorAction SilentlyContinue | Remove-AppxPackage -ErrorAction SilentlyContinue`) }

func createShortcuts(root string) {
    app:=filepath.Join(root,"AudioCodex.exe"); repair:=filepath.Join(root,"Audio Codex Maintenance.exe"); uninst:=filepath.Join(root,"Uninstall Audio Codex.exe")
    script:=fmt.Sprintf(`$ErrorActionPreference='Stop';$w=New-Object -ComObject WScript.Shell;$desktop=[Environment]::GetFolderPath('DesktopDirectory');$start=Join-Path ([Environment]::GetFolderPath('Programs')) 'Audio Codex';New-Item -ItemType Directory -Path $start -Force|Out-Null;function L([string]$p,[string]$t,[string]$a,[string]$d){$s=$w.CreateShortcut($p);$s.TargetPath=$t;if($a){$s.Arguments=$a};$s.WorkingDirectory=%s;$s.Description=$d;$s.Save()};L (Join-Path $desktop 'Audio Codex.lnk') %s '' 'Audio Codex';L (Join-Path $start 'Audio Codex.lnk') %s '' 'Audio Codex';L (Join-Path $start 'Repair Audio Codex.lnk') %s '--repair' 'Repair Python dependencies';L (Join-Path $start 'Uninstall Audio Codex.lnk') %s '--uninstall' 'Uninstall Audio Codex'`,psQuote(root),psQuote(app),psQuote(app),psQuote(repair),psQuote(uninst))
    _=runPowerShell(script)
}
func removeShortcuts(){
    script:=`$d=[Environment]::GetFolderPath('DesktopDirectory');$s=Join-Path ([Environment]::GetFolderPath('Programs')) 'Audio Codex';Remove-Item (Join-Path $d 'Audio Codex.lnk') -Force -ErrorAction SilentlyContinue;Remove-Item $s -Recurse -Force -ErrorAction SilentlyContinue`
    _=runPowerShell(script)
}

func registerUninstall(root string) {
    key:=`HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\AudioCodex`
    un:=`"`+filepath.Join(root,"Uninstall Audio Codex.exe")+`" --uninstall`
    vals:=[][]string{{"/v","DisplayName","/t","REG_SZ","/d",appName,"/f"},{"/v","DisplayVersion","/t","REG_SZ","/d",displayVersion,"/f"},{"/v","Publisher","/t","REG_SZ","/d","Audio Codex","/f"},{"/v","InstallLocation","/t","REG_SZ","/d",root,"/f"},{"/v","UninstallString","/t","REG_SZ","/d",un,"/f"}}
    for _,v:=range vals { args:=append([]string{"add",key},v...); _=exec.Command("reg.exe",args...).Run() }
}
func unregisterUninstall(){ _=exec.Command("reg.exe","delete",`HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\AudioCodex`,"/f").Run() }

func launch(root string){ cmd:=exec.Command(filepath.Join(root,"AudioCodex.exe"));cmd.Dir=root;_ = cmd.Start() }

func install(repair bool) error {
    if runtime.GOOS!="windows" {return errors.New("this installer is for Windows")}
    root:=installDir();_ = os.MkdirAll(root,0755)
    logf("============================================================")
    logf("Audio Codex %s - Windows 11 x64", displayVersion)
    logf("Build %s", buildID)
    logf("Windows AI Speech + local fallbacks + API-key-only generative AI")
    logf("Install location: %s",root)
    logf("============================================================")

    logf("[1/6] Inspecting existing environment")
    best,all:=findPython()
    for _,p:=range all { logf("  Python %d.%d  %s  core=%v  faster-whisper=%v",p.Major,p.Minor,p.Path,p.CoreOK,p.WhisperOK) }
    if best==nil { return errors.New("No compatible 64-bit Python found. Install Python 3.11, 3.12, or 3.13 and run this installer again.") }
    logf("  Selected Python %d.%d: %s (chosen to maximize reuse)",best.Major,best.Minor,best.Path)
    if ff,err:=exec.LookPath("ffmpeg");err==nil{logf("  FFmpeg: existing %s",ff)}else{logf("  FFmpeg: not found (not required by faster-whisper/PyAV)")}

    logf("[2/6] Installing Audio Codex application files")
    stopRunning(root)
    if err:=unzipPayload(root);err!=nil{return err}
    if err:=copySelf(root);err!=nil{return err}

    logf("[3/6] Preparing Audio Codex Python runtime")
    py,err:=ensureVenv(best,root);if err!=nil{return err}
    logf("  Runtime: %s",py)

    logf("[4/6] Checking FastAPI / Uvicorn / Pydantic")
    if err:=ensurePackages(py);err!=nil{return err}

    logf("[5/6] Checking local Transcript engines")
    if pyCheck(py,`import faster_whisper`) { logf("  faster-whisper: ready") } else { return errors.New("faster-whisper is still unavailable after installation") }
    logf("  Windows AI Speech: optional local engine; package identity will be registered when Windows supports it")
    logf("  Windows System.Speech: optional local fallback; existing Windows language packs are reused")
    logf("  Generative AI: no local LLM packages are installed; API Key is required in Settings")

    logf("[6/6] Registering Windows integration and shortcuts")
    registerSpeechIdentity(root); createShortcuts(root); registerUninstall(root)
    logf("Installation complete. Transcript is local; DeepSeek/OpenAI-compatible AI uses API Key only.")
    launch(root)
    return nil
}

func uninstall() error {
    root:=installDir();fmt.Println("Audio Codex uninstall")
    stopRunning(root);unregisterSpeechIdentity();removeShortcuts();unregisterUninstall()
    // User library/data under %%LOCALAPPDATA%%\AudioCodex is intentionally preserved.
    cmd:=exec.Command("cmd.exe","/c",fmt.Sprintf(`timeout /t 2 /nobreak >nul & rmdir /s /q "%s"`,root));cmd.SysProcAttr=nil;_ = cmd.Start()
    fmt.Println("Application files scheduled for removal. Your Audio Codex library data is preserved.")
    return nil
}

func main(){
    for _,a:=range os.Args[1:] { if a=="--uninstall" { if err:=uninstall();err!=nil{fmt.Println("ERROR:",err);os.Exit(1)};return } }
    repair:=false;for _,a:=range os.Args[1:] {if a=="--repair"{repair=true}}
    if err:=install(repair);err!=nil { fmt.Println();fmt.Println("INSTALL FAILED:",err);fmt.Println("See install.log under:",installDir());fmt.Println("Press Enter to close.");fmt.Scanln();os.Exit(1) }
}
