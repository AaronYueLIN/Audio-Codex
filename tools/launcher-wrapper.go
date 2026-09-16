package main

import (
    "os"
    "os/exec"
    "path/filepath"
    "strings"
)

func main() {
    exe, err := os.Executable()
    if err != nil { return }
    root := filepath.Dir(exe)
    venvScripts := filepath.Join(root, "venv", "Scripts")
    venvPython := filepath.Join(venvScripts, "python.exe")
    original := filepath.Join(root, "AudioCodex-R9.exe")

    if _, err := os.Stat(venvPython); err != nil {
        repair := filepath.Join(root, "Audio Codex Maintenance.exe")
        if _, e := os.Stat(repair); e == nil {
            c := exec.Command(repair, "--repair")
            c.Dir = root
            _ = c.Start()
        }
        return
    }
    if _, err := os.Stat(original); err != nil { return }

    env := os.Environ()
    env = append(env,
        "PATH="+venvScripts+";"+os.Getenv("PATH"),
        "PYTHONPATH="+filepath.Join(root, "backend", "src"),
        "PYTHONUTF8=1",
        "AUDIO_CODEX_PYTHON="+venvPython,
        "AUDIO_CODEX_BUILD_ID=1.2.1-R12.1-AUDIO-INTELLIGENCE-WINDOWSAISPEECH1",
    )
    cmd := exec.Command(original, os.Args[1:]...)
    cmd.Dir = root
    cmd.Env = dedupeEnv(env)
    _ = cmd.Start()
}

func dedupeEnv(env []string) []string {
    seen := map[string]bool{}
    out := make([]string, 0, len(env))
    for i := len(env)-1; i >= 0; i-- {
        item := env[i]
        key := item
        if p := strings.IndexByte(item, '='); p >= 0 { key = item[:p] }
        key = strings.ToUpper(key)
        if seen[key] { continue }
        seen[key] = true
        out = append(out, item)
    }
    for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 { out[i], out[j] = out[j], out[i] }
    return out
}
