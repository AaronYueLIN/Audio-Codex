# AudioCodex

**AudioCodex** 是一个 Windows-first、本地优先的音频知识系统：把长音频转成可搜索、可引用、可验证的个人知识库，并在其上提供应用级 Intelligence。

当前仓库对应 **AudioCodex 1.2.1 / R12.1 Hotfix 1**：

`1.2.1-R12.1-AUDIO-INTELLIGENCE-WINDOWSAISPEECH1`

> **源码来源：** Python 后端、Web UI、Windows AI Speech C# 源码、PowerShell Bridge、manifest 和 launcher-wrapper 都直接从 R12.1 正式安装包的内嵌 payload 恢复。外层 Go 安装器的 `setup.go` 没有作为源码嵌入 EXE，所以仓库中的版本是按同一安装器谱系重建的，不会冒充“逐字节原源码”。详见 [恢复与来源](docs/RECOVERY.md)。

## 主要能力

- 本地 Episode / Transcript / Notes / Bookmarks / Collections / Entity / Knowledge。
- SQLite/FTS5 搜索，并保留可选向量检索路线。
- Windows AI Speech / faster-whisper / Windows System Speech 多级转录。
- Live Context / Rewind、Listening Recap、Entity Context Lens。
- Dynamic Intelligence Profiles、Knowledge Watch、Vision、SSE 流式回答。
- AI 写入操作必须确认，并记录 Action Receipt。
- Reference Transcript + Audio 来源链和 stale 检测。
- 宽屏 Intelligence Pane / 窄屏 Modal 自适应呈现。

## 从源码运行

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements-core.txt
.\venv\Scripts\python.exe -m pip install -r requirements-transcription.txt

$env:PYTHONPATH = "$PWD\backend\src"
$env:AUDIO_CODEX_BUILD_ID = "1.2.1-R12.1-AUDIO-INTELLIGENCE-WINDOWSAISPEECH1"
.\venv\Scripts\python.exe -m uvicorn podcast_codex.server:app --host 127.0.0.1 --port 8765
```

访问 `http://127.0.0.1:8765/`。

## 隐私提醒

当前版本如果在设置中填写 AI API Key，它会随本地设置保存在 SQLite 中，**不要把它理解成已加密的凭据保险库**。不要把 `library.db`、日志、缓存、模型目录或本机设置上传到 GitHub。本仓库的 `.gitignore` 已排除这些内容。

Windows 安装 EXE 应放在 GitHub Releases，不应直接提交进 Git 历史。

## License

MIT，见 [LICENSE](LICENSE)。
