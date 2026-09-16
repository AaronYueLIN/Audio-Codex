from __future__ import annotations

import base64
import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wav", ".opus", ".mp4"}
_MAX_ARTWORK_BYTES = 64 * 1024 * 1024


def have_command(name: str) -> bool:
    return shutil.which(name) is not None


def probe_audio(path: Path) -> dict:
    result = {
        "duration_ms": 0,
        "title": path.stem,
        "artist": "",
        "album": "",
        "date": None,
    }
    if not have_command("ffprobe"):
        return result
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=True)
        data = json.loads(p.stdout)
        fmt = data.get("format", {})
        tags = {str(k).lower(): v for k, v in fmt.get("tags", {}).items()}
        duration = float(fmt.get("duration") or 0)
        result.update({
            "duration_ms": int(duration * 1000),
            "title": tags.get("title") or path.stem,
            "artist": tags.get("artist") or "",
            "album": tags.get("album") or "",
            "date": tags.get("date") or tags.get("year"),
        })
    except Exception:
        pass
    return result


def collect_audio_paths(inputs: list[str]) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for raw in inputs:
        p = Path(raw).expanduser().resolve()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS:
            key = os.path.normcase(str(p))
            if key not in seen:
                found.append(p)
                seen.add(key)
        elif p.is_dir():
            for candidate in p.rglob("*"):
                if candidate.is_file() and candidate.suffix.lower() in AUDIO_EXTENSIONS:
                    key = os.path.normcase(str(candidate.resolve()))
                    if key not in seen:
                        found.append(candidate.resolve())
                        seen.add(key)
    return sorted(found, key=lambda x: str(x).lower())


def normalize_to_wav(source: Path, target: Path, start_s: float | None = None,
                     duration_s: float | None = None) -> None:
    if not have_command("ffmpeg"):
        raise RuntimeError("ffmpeg is required for audio normalization")
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if start_s is not None:
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += ["-i", str(source)]
    if duration_s is not None:
        cmd += ["-t", f"{duration_s:.3f}"]
    cmd += ["-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(target)]
    subprocess.run(cmd, check=True, timeout=300)


def _syncsafe(b: bytes) -> int:
    if len(b) != 4:
        return 0
    return ((b[0] & 0x7F) << 21) | ((b[1] & 0x7F) << 14) | ((b[2] & 0x7F) << 7) | (b[3] & 0x7F)


def _image_blob(data: bytes) -> tuple[bytes, str, str] | None:
    """Locate a common image signature inside a metadata payload."""
    candidates: list[tuple[int, str, str]] = []
    for sig, ext, mime in (
        (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
        (b"GIF87a", ".gif", "image/gif"),
        (b"GIF89a", ".gif", "image/gif"),
    ):
        i = data.find(sig)
        if i >= 0:
            candidates.append((i, ext, mime))
    pos = data.find(b"RIFF")
    while pos >= 0:
        if pos + 12 <= len(data) and data[pos + 8:pos + 12] == b"WEBP":
            candidates.append((pos, ".webp", "image/webp"))
            break
        pos = data.find(b"RIFF", pos + 1)
    if not candidates:
        return None
    i, ext, mime = min(candidates, key=lambda x: x[0])
    blob = data[i:]
    if len(blob) > _MAX_ARTWORK_BYTES:
        blob = blob[:_MAX_ARTWORK_BYTES]
    return blob, ext, mime


def _deunsync(data: bytes) -> bytes:
    return data.replace(b"\xff\x00", b"\xff")


def _parse_id3_blob(blob: bytes) -> tuple[bytes, str, str] | None:
    if len(blob) < 10 or blob[:3] != b"ID3":
        return None
    version = blob[3]
    flags = blob[5]
    tag_size = min(_syncsafe(blob[6:10]), len(blob) - 10, _MAX_ARTWORK_BYTES)
    body = blob[10:10 + tag_size]
    if flags & 0x80:
        body = _deunsync(body)
    pos = 0
    if version == 2:
        while pos + 6 <= len(body):
            frame_id = body[pos:pos + 3]
            if frame_id == b"\x00\x00\x00":
                break
            size = int.from_bytes(body[pos + 3:pos + 6], "big")
            pos += 6
            if size <= 0 or pos + size > len(body):
                break
            payload = body[pos:pos + size]
            pos += size
            if frame_id == b"PIC":
                found = _image_blob(payload)
                if found:
                    return found
        return None
    if version not in (3, 4):
        return None
    while pos + 10 <= len(body):
        frame_id = body[pos:pos + 4]
        if frame_id == b"\x00\x00\x00\x00":
            break
        size_bytes = body[pos + 4:pos + 8]
        size = _syncsafe(size_bytes) if version == 4 else int.from_bytes(size_bytes, "big")
        pos += 10
        if size <= 0 or pos + size > len(body):
            break
        payload = body[pos:pos + size]
        pos += size
        if frame_id == b"APIC":
            found = _image_blob(payload)
            if found:
                return found
    return None


def _extract_id3(source: Path) -> tuple[bytes, str, str] | None:
    try:
        with source.open("rb") as f:
            head = f.read(10)
            if len(head) < 10 or head[:3] != b"ID3":
                return None
            size = min(_syncsafe(head[6:10]), _MAX_ARTWORK_BYTES)
            return _parse_id3_blob(head + f.read(size))
    except OSError:
        return None


def _parse_flac_picture_block(block: bytes) -> tuple[bytes, str, str] | None:
    try:
        p = 0
        if len(block) < 32:
            return None
        p += 4  # picture type
        mime_len = int.from_bytes(block[p:p + 4], "big"); p += 4
        mime_raw = block[p:p + mime_len]; p += mime_len
        desc_len = int.from_bytes(block[p:p + 4], "big"); p += 4 + desc_len
        p += 16  # width, height, depth, indexed colors
        data_len = int.from_bytes(block[p:p + 4], "big"); p += 4
        if data_len <= 0 or data_len > _MAX_ARTWORK_BYTES or p + data_len > len(block):
            return None
        data = block[p:p + data_len]
        found = _image_blob(data)
        if found:
            return found
        mime = mime_raw.decode("ascii", "ignore").lower()
        ext = ".png" if "png" in mime else ".webp" if "webp" in mime else ".gif" if "gif" in mime else ".jpg"
        return data, ext, mime if mime.startswith("image/") else "image/jpeg"
    except Exception:
        return None


def _extract_flac(source: Path) -> tuple[bytes, str, str] | None:
    try:
        with source.open("rb") as f:
            if f.read(4) != b"fLaC":
                return None
            for _ in range(256):
                h = f.read(4)
                if len(h) < 4:
                    break
                last = bool(h[0] & 0x80)
                block_type = h[0] & 0x7F
                length = int.from_bytes(h[1:4], "big")
                if block_type == 6 and 0 < length <= _MAX_ARTWORK_BYTES:
                    found = _parse_flac_picture_block(f.read(length))
                    if found:
                        return found
                else:
                    f.seek(length, 1)
                if last:
                    break
    except OSError:
        pass
    return None


def _iter_ogg_packets(source: Path):
    try:
        with source.open("rb") as f:
            packet = bytearray()
            while True:
                header = f.read(27)
                if not header:
                    return
                if len(header) < 27 or header[:4] != b"OggS":
                    return
                n = header[26]
                lace = f.read(n)
                if len(lace) != n:
                    return
                body = f.read(sum(lace))
                if len(body) != sum(lace):
                    return
                off = 0
                for seg_len in lace:
                    if len(packet) + seg_len > _MAX_ARTWORK_BYTES * 2:
                        packet.clear()
                        off += seg_len
                        continue
                    packet.extend(body[off:off + seg_len])
                    off += seg_len
                    if seg_len < 255:
                        yield bytes(packet)
                        packet.clear()
    except OSError:
        return


def _ogg_comments(packet: bytes) -> list[str]:
    if packet.startswith(b"OpusTags"):
        p = 8
    elif packet.startswith(b"\x03vorbis"):
        p = 7
    else:
        return []
    try:
        vendor_len = int.from_bytes(packet[p:p + 4], "little"); p += 4 + vendor_len
        count = int.from_bytes(packet[p:p + 4], "little"); p += 4
        out = []
        for _ in range(min(count, 10000)):
            if p + 4 > len(packet):
                break
            n = int.from_bytes(packet[p:p + 4], "little"); p += 4
            if n < 0 or p + n > len(packet):
                break
            out.append(packet[p:p + n].decode("utf-8", "replace"))
            p += n
        return out
    except Exception:
        return []


def _extract_ogg(source: Path) -> tuple[bytes, str, str] | None:
    coverart: str | None = None
    for packet in _iter_ogg_packets(source):
        comments = _ogg_comments(packet)
        if not comments:
            continue
        for comment in comments:
            key, sep, value = comment.partition("=")
            if not sep:
                continue
            k = key.upper()
            if k == "METADATA_BLOCK_PICTURE":
                try:
                    raw = base64.b64decode(value, validate=False)
                    found = _parse_flac_picture_block(raw)
                    if found:
                        return found
                except Exception:
                    pass
            elif k == "COVERART":
                coverart = value
        if coverart:
            try:
                raw = base64.b64decode(coverart, validate=False)
                found = _image_blob(raw)
                if found:
                    return found
            except Exception:
                pass
        return None
    return None


def _mp4_atoms(f, start: int, end: int, depth: int = 0) -> tuple[bytes, str, str] | None:
    if depth > 8:
        return None
    pos = start
    containers = {b"moov", b"udta", b"meta", b"ilst"}
    while pos + 8 <= end:
        f.seek(pos)
        h = f.read(8)
        if len(h) < 8:
            return None
        size = int.from_bytes(h[:4], "big")
        typ = h[4:8]
        header_len = 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                return None
            size = int.from_bytes(ext, "big")
            header_len = 16
        elif size == 0:
            size = end - pos
        if size < header_len or pos + size > end:
            return None
        body_start = pos + header_len
        body_end = pos + size
        if typ == b"covr":
            body_len = body_end - body_start
            if 0 < body_len <= _MAX_ARTWORK_BYTES + 4096:
                f.seek(body_start)
                found = _image_blob(f.read(body_len))
                if found:
                    return found
        elif typ in containers:
            child_start = body_start + (4 if typ == b"meta" and body_start + 4 <= body_end else 0)
            found = _mp4_atoms(f, child_start, body_end, depth + 1)
            if found:
                return found
        pos += size
    return None


def _extract_mp4(source: Path) -> tuple[bytes, str, str] | None:
    try:
        size = source.stat().st_size
        with source.open("rb") as f:
            return _mp4_atoms(f, 0, size)
    except OSError:
        return None


def _extract_wav(source: Path) -> tuple[bytes, str, str] | None:
    try:
        with source.open("rb") as f:
            if f.read(4) != b"RIFF":
                return None
            f.seek(8)
            if f.read(4) != b"WAVE":
                return None
            while True:
                h = f.read(8)
                if len(h) < 8:
                    break
                cid = h[:4].lower()
                size = int.from_bytes(h[4:8], "little")
                if cid in {b"id3 ", b"id3\x00"} and 0 < size <= _MAX_ARTWORK_BYTES:
                    found = _parse_id3_blob(f.read(size))
                    if found:
                        return found
                else:
                    f.seek(size, 1)
                if size & 1:
                    f.seek(1, 1)
    except OSError:
        pass
    return None


def _embedded_artwork(source: Path) -> tuple[bytes, str, str] | None:
    suffix = source.suffix.lower()
    # MP3/AAC files can begin with ID3 regardless of extension.
    found = _extract_id3(source)
    if found:
        return found
    if suffix == ".flac":
        return _extract_flac(source)
    if suffix in {".m4a", ".mp4"}:
        return _extract_mp4(source)
    if suffix in {".ogg", ".opus"}:
        return _extract_ogg(source)
    if suffix == ".wav":
        return _extract_wav(source)
    return None


def _write_artwork(target: Path, found: tuple[bytes, str, str]) -> Path | None:
    data, ext, _mime = found
    if not data or len(data) > _MAX_ARTWORK_BYTES:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    base = target.with_suffix("") if target.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"} else target
    out = base.with_suffix(ext)
    try:
        out.write_bytes(data)
        return out if out.stat().st_size > 0 else None
    except OSError:
        return None


def extract_artwork(source: Path, target: Path) -> Path | None:
    """Extract embedded artwork without FFmpeg; adjacent cover files are fallback only."""
    source = Path(source)
    found = _embedded_artwork(source)
    if found:
        written = _write_artwork(target, found)
        if written:
            return written.resolve()
    for name in (
        "cover.jpg", "folder.jpg", "Cover.jpg", "Folder.jpg", "cover.jpeg", "folder.jpeg",
        "cover.png", "folder.png", "cover.webp", "folder.webp",
    ):
        candidate = source.parent / name
        if candidate.is_file():
            return candidate.resolve()
    return None


def image_media_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".gif":
        return "image/gif"
    if ext == ".webp":
        return "image/webp"
    try:
        found = _image_blob(path.read_bytes()[:64])
        return found[2] if found else "application/octet-stream"
    except OSError:
        return "application/octet-stream"
