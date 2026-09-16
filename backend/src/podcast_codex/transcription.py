from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from .config import PATHS
from .db import Database


class TranscriptionProvider(ABC):
    @abstractmethod
    def transcribe(self, audio_path: Path, model: str, language: str | None,
                   recognizer_id: str | None = None, progress=None) -> list[dict]:
        raise NotImplementedError


class FasterWhisperProvider(TranscriptionProvider):
    def transcribe(self, audio_path: Path, model: str = "base",
                   language: str | None = None, recognizer_id: str | None = None,
                   progress=None) -> list[dict]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                'faster-whisper is not installed in the Audio Codex runtime. '
                'Run Setup-AudioCodex.bat or install with: pip install -e "backend[ai]"'
            ) from exc

        device = "cuda" if os.getenv("AUDIO_CODEX_WHISPER_DEVICE", "auto") == "cuda" else "auto"
        compute_type = os.getenv("AUDIO_CODEX_WHISPER_COMPUTE", "auto")
        whisper = WhisperModel(model or "base", device=device, compute_type=compute_type)
        iterator, info = whisper.transcribe(
            str(audio_path),
            language=language or None,
            beam_size=5,
            vad_filter=True,
            word_timestamps=True,
        )
        duration_s = float(getattr(info, "duration", 0.0) or 0.0)
        segments: list[dict] = []
        for i, seg in enumerate(iterator):
            text = seg.text.strip()
            if not text:
                continue
            confidence = None
            if getattr(seg, "avg_logprob", None) is not None:
                confidence = float(seg.avg_logprob)
            segments.append({
                "start_ms": int(float(seg.start) * 1000),
                "end_ms": int(float(seg.end) * 1000),
                "text": text,
                "speaker_label": None,
                "confidence": confidence,
            })
            if progress:
                if duration_s > 0:
                    ratio = max(0.0, min(1.0, float(seg.end) / duration_s))
                    pct = min(0.94, 0.04 + ratio * 0.90)
                    progress(
                        pct,
                        f"Recognized {len(segments)} caption cues · "
                        f"{float(seg.end):.0f}s / {duration_s:.0f}s",
                    )
                else:
                    progress(min(0.90, 0.04 + i * 0.002),
                             f"Recognized {len(segments)} caption cues")
        if not segments:
            raise RuntimeError("faster-whisper returned no readable text")
        return segments


def windows_ai_bridge_path() -> Path | None:
    """Packaged-identity launcher for Windows AI Speech."""
    helper = Path(__file__).resolve().parents[2] / "windows" / "WindowsAIInvoke.ps1"
    return helper if helper.is_file() else None


def windows_ai_host_path() -> Path | None:
    """Compiled self-contained Windows AI Speech host, when built into the payload."""
    host = (Path(__file__).resolve().parents[2] / "windows" / "host" / "win-x64" /
            "AudioCodex.WindowsAISpeechHost.exe")
    return host if host.is_file() else None


_WINDOWS_AI_FAILURE: str | None = None


class WindowsAISpeechProvider(TranscriptionProvider):
    @staticmethod
    def _script() -> Path:
        p = Path(__file__).resolve().parents[2] / "windows" / "WindowsAIInvoke.ps1"
        if not p.exists():
            raise RuntimeError("Windows AI Speech helper is missing")
        return p

    def transcribe(self, audio_path: Path, model: str = "", language: str | None = None,
                   recognizer_id: str | None = None, progress=None) -> list[dict]:
        global _WINDOWS_AI_FAILURE
        if os.name != "nt":
            raise RuntimeError("Windows AI Speech is only available on Windows.")
        if _WINDOWS_AI_FAILURE:
            raise RuntimeError(_WINDOWS_AI_FAILURE)
        if progress:
            progress(.04, "Checking Windows AI Speech")
        try:
            proc = subprocess.run([
                "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(self._script()),
                "-InputPath", str(audio_path),
            ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
        except subprocess.TimeoutExpired as exc:
            _WINDOWS_AI_FAILURE = ("Windows AI Speech did not respond in time; "
                                   "using another local engine for the rest of this session.")
            raise RuntimeError(_WINDOWS_AI_FAILURE) from exc
        payload = None
        for line in reversed((proc.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass
        if not payload or not payload.get("ok"):
            detail = str((payload or {}).get("error") or proc.stderr or "Windows AI Speech failed")
            if payload and payload.get("needs_download"):
                detail += (" Open Audio Codex Settings → Windows AI Speech and choose "
                           "Prepare Windows AI model, then retry.")
            else:
                # The Windows AI runtime cannot create the model on this PC (for example
                # 0x8007007E). Remember it so later episodes skip straight to the fallbacks.
                _WINDOWS_AI_FAILURE = detail
            raise RuntimeError(detail)
        text = str(payload.get("text") or "").strip()
        if not text:
            raise RuntimeError("Windows AI Speech returned no text")
        if progress:
            progress(.92, "Windows AI Speech completed")
        return [{
            "start_ms": 0,
            "end_ms": 1,
            "text": text,
            "speaker_label": None,
            "confidence": None,
        }]



class WindowsSystemSpeechProvider(TranscriptionProvider):
    @staticmethod
    def _script() -> Path:
        p = Path(__file__).resolve().parents[2] / "windows" / "WindowsSystemSpeech.ps1"
        if not p.exists():
            raise RuntimeError("Windows System.Speech helper is missing")
        return p

    def transcribe(self, audio_path: Path, model: str = "", language: str | None = None,
                   recognizer_id: str | None = None, progress=None) -> list[dict]:
        if os.name != "nt":
            raise RuntimeError("Windows System.Speech is only available on Windows.")
        args = ["-InputPath", str(audio_path)]
        if language:
            args += ["-Language", language]
        if recognizer_id:
            args += ["-RecognizerId", recognizer_id]
        proc = subprocess.run([
            "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(self._script()), *args,
        ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=7200)
        payload = None
        for line in reversed((proc.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass
        if not payload or not payload.get("ok"):
            raise RuntimeError(str((payload or {}).get("error") or proc.stderr or
                                   "Windows System.Speech failed"))
        segments = []
        for item in payload.get("segments") or []:
            text = str(item.get("text") or "").strip()
            if text:
                segments.append({
                    "start_ms": int(item.get("start_ms") or 0),
                    "end_ms": int(item.get("end_ms") or 0),
                    "text": text,
                    "speaker_label": None,
                    "confidence": item.get("confidence"),
                })
        if not segments:
            raise RuntimeError("Windows System.Speech returned no text")
        return segments


class CascadeTranscriptionProvider(TranscriptionProvider):
    """Windows Speech mode with stable fallbacks.

    The explicit Windows Speech option still tries Windows AI first. If the optional bridge
    is unavailable or fails, faster-whisper is preferred before legacy System.Speech so a
    working local Whisper runtime is not bypassed by a more fragile Windows fallback.
    """

    def transcribe(self, audio_path: Path, model: str = "base", language: str | None = None,
                   recognizer_id: str | None = None, progress=None) -> list[dict]:
        chain = [
            ("Windows AI Speech", WindowsAISpeechProvider()),
            ("faster-whisper", FasterWhisperProvider()),
            ("Windows System.Speech", WindowsSystemSpeechProvider()),
        ]
        errors: list[str] = []
        for idx, (name, provider) in enumerate(chain):
            try:
                if progress:
                    progress(.03 + idx * .02, f"Trying {name}")
                result = provider.transcribe(
                    audio_path, model=model, language=language,
                    recognizer_id=recognizer_id, progress=progress,
                )
                result = [x for x in result if str(x.get("text") or "").strip()]
                if result:
                    return result
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        raise RuntimeError("All transcription providers failed. " + " | ".join(errors))


_TERMINAL_RE = re.compile(r"[.!?。！？…][\"'”’）)】》]*$")
_CJK_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?，。；：！？])")
_MULTI_SPACE_RE = re.compile(r"[ \t]+")


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _smart_concat(left: str, right: str) -> str:
    left = left.rstrip()
    right = right.lstrip()
    if not left:
        return right
    if not right:
        return left
    if right[0] in ",.;:!?，。；：！？、)]}”’」』】》":
        return left + right
    if left[-1] in "([{“‘「『【《":
        return left + right
    if _contains_cjk(left[-1:]) and _contains_cjk(right[:1]):
        return left + right
    return left + " " + right


def _ensure_terminal(text: str) -> str:
    text = text.rstrip()
    if not text or _TERMINAL_RE.search(text):
        return text
    return text + ("。" if _contains_cjk(text) else ".")


def continuous_text_from_segments(segments: list[dict]) -> str:
    """Turn ASR decoder cues into one readable, punctuated continuous transcript."""
    pieces = []
    for seg in segments:
        t = str(seg.get("text") or "").strip()
        if t:
            t = _MULTI_SPACE_RE.sub(" ", t)
            t = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", t)
            pieces.append(t)
    if not pieces:
        return ""

    punctuated = sum(1 for p in pieces if _TERMINAL_RE.search(p))
    if punctuated >= max(2, len(pieces) // 10):
        text = ""
        for p in pieces:
            text = _smart_concat(text, p)
        return _ensure_terminal(text)

    sentences: list[str] = []
    buf = ""
    for p in pieces:
        buf = _smart_concat(buf, p)
        target = 55 if _contains_cjk(buf) else 170
        if _TERMINAL_RE.search(p) or len(buf) >= target:
            sentences.append(_ensure_terminal(buf))
            buf = ""
    if buf:
        sentences.append(_ensure_terminal(buf))
    joiner = "" if sentences and all(_contains_cjk(x) for x in sentences) else " "
    return joiner.join(sentences).strip()


def _best_speaker(start_ms: int, end_ms: int, turns: list[dict]) -> str | None:
    best = None
    best_overlap = 0
    for turn in turns:
        overlap = max(0, min(end_ms, turn["end_ms"]) - max(start_ms, turn["start_ms"]))
        if overlap > best_overlap:
            best_overlap = overlap
            best = turn["speaker_label"]
    return best


def diarize(audio_path: Path, token: str | None = None) -> list[dict]:
    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise RuntimeError(
            'pyannote.audio is not installed. Run Install-Optional-AI.bat to enable diarization.'
        ) from exc

    model_name = os.getenv(
        "AUDIO_CODEX_DIARIZATION_MODEL",
        "pyannote/speaker-diarization-community-1",
    )
    kwargs = {}
    if token:
        kwargs["token"] = token
    pipeline = Pipeline.from_pretrained(model_name, **kwargs)
    output = pipeline(str(audio_path))
    annotation = getattr(output, "speaker_diarization", output)
    turns = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        turns.append({
            "start_ms": int(turn.start * 1000),
            "end_ms": int(turn.end * 1000),
            "speaker_label": str(speaker),
        })
    return turns


def _provider(provider_name: str) -> TranscriptionProvider:
    name = str(provider_name or "faster-whisper").strip().lower()
    if name == "faster-whisper":
        return FasterWhisperProvider()
    if name in {"windows-system", "system-speech"}:
        return WindowsSystemSpeechProvider()
    if name == "windows-ai":
        return CascadeTranscriptionProvider()
    raise RuntimeError(f"Unsupported transcription provider: {provider_name}")


def _clean_asr_segments(segments: list[dict]) -> list[dict]:
    out = []
    for seg in segments:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        start = max(0, int(seg.get("start_ms") or 0))
        end = max(start + 1, int(seg.get("end_ms") or start + 1000))
        out.append({
            "start_ms": start,
            "end_ms": end,
            "text": text,
            "speaker_label": seg.get("speaker_label"),
            "confidence": seg.get("confidence"),
        })
    return out


def _save_asr_outputs(database: Database, episode_id: int, segments: list[dict], progress=None) -> None:
    cleaned = _clean_asr_segments(segments)
    if not cleaned:
        raise RuntimeError("Speech engine returned no readable ASR segments")
    database.replace_transcript(episode_id, cleaned)
    database.replace_captions(episode_id, cleaned)
    if progress:
        progress(.99, "Transcript and Live Captions ready")


def transcribe_episode(database: Database, episode_id: int, provider_name: str,
                       model: str, language: str | None, diarize_enabled: bool,
                       diarization_token: str | None, recognizer_id: str | None = None,
                       progress=None) -> None:
    episode = database.one("SELECT * FROM episodes WHERE id=?", (episode_id,))
    if not episode:
        raise RuntimeError("Episode not found")
    audio_path = Path(episode["audio_path"])
    if not audio_path.exists():
        raise RuntimeError(f"Audio file is missing: {audio_path}")

    database.execute("UPDATE episodes SET transcript_status='running' WHERE id=?", (episode_id,))
    try:
        segments = _provider(provider_name).transcribe(
            audio_path, model=model, language=language,
            recognizer_id=recognizer_id, progress=progress,
        )
        if diarize_enabled:
            if progress:
                progress(0.95, "Running speaker diarization")
            turns = diarize(audio_path, token=diarization_token)
            for seg in segments:
                seg["speaker_label"] = _best_speaker(seg["start_ms"], seg["end_ms"], turns)
        _save_asr_outputs(database, episode_id, segments, progress)
    except Exception:
        database.execute("UPDATE episodes SET transcript_status='error' WHERE id=?", (episode_id,))
        raise


def generate_captions_episode(database: Database, episode_id: int, provider_name: str,
                              model: str, language: str | None,
                              recognizer_id: str | None = None, progress=None) -> None:
    existing = database.all(
        """SELECT start_ms,end_ms,COALESCE(user_text,text) AS text,confidence
           FROM transcript_segments WHERE episode_id=? ORDER BY segment_index""",
        (episode_id,),
    )
    existing = _clean_asr_segments(existing)
    if existing:
        database.replace_captions(episode_id, existing)
        if progress:
            progress(.99, "Live Captions ready")
        return

    episode = database.one("SELECT * FROM episodes WHERE id=?", (episode_id,))
    if not episode:
        raise RuntimeError("Episode not found")
    audio_path = Path(episode["audio_path"])
    if not audio_path.exists():
        raise RuntimeError(f"Audio file is missing: {audio_path}")
    if str(provider_name or "").strip().lower() == "windows-ai":
        errors = []
        segments = []
        for name, provider in (("faster-whisper", FasterWhisperProvider()),
                               ("Windows System.Speech", WindowsSystemSpeechProvider())):
            try:
                if progress:
                    progress(.04, f"Live Captions · trying {name}")
                segments = provider.transcribe(
                    audio_path, model=model, language=language,
                    recognizer_id=recognizer_id, progress=progress,
                )
                if segments:
                    break
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        if not segments:
            raise RuntimeError("No local time-coded caption engine succeeded. " + " | ".join(errors))
    else:
        segments = _provider(provider_name).transcribe(
            audio_path, model=model, language=language,
            recognizer_id=recognizer_id, progress=progress,
        )
    _save_asr_outputs(database, episode_id, segments, progress)
