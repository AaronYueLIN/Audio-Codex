from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse


def range_response(path: Path, request: Request):
    if not path.exists():
        raise HTTPException(404, "Audio file not found")
    size = path.stat().st_size
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    range_header = request.headers.get("range")

    if not range_header:
        def full():
            with path.open("rb") as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk
        return StreamingResponse(
            full(),
            media_type=content_type,
            headers={"Content-Length": str(size), "Accept-Ranges": "bytes"},
        )

    try:
        units, spec = range_header.split("=", 1)
        if units != "bytes":
            raise ValueError
        start_s, end_s = spec.split("-", 1)
        start = int(start_s or 0)
        end = int(end_s) if end_s else min(size - 1, start + 4 * 1024 * 1024)
        end = min(end, size - 1)
        if start > end or start >= size:
            raise ValueError
    except ValueError:
        raise HTTPException(416, "Invalid Range header")

    length = end - start + 1

    def part():
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        part(),
        status_code=206,
        media_type=content_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        },
    )
