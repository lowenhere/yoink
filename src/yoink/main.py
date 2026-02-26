import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

STATIC_DIR = Path(__file__).parent / "static"
DOWNLOAD_DIR = Path("/tmp/yoink")

app = FastAPI()
state: dict = {}


def sanitize(title: str) -> str:
    """Make a title safe to use as a filename."""
    s = re.sub(r"[^\w\s-]", "", title)
    s = re.sub(r"\s+", "_", s.strip())
    return s[:80] or "video"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/clip")
async def clip_page():
    return FileResponse(STATIC_DIR / "clip.html")


@app.get("/video")
async def video():
    path = state.get("video_path")
    if not path or not Path(path).exists():
        raise HTTPException(404, "Video not found")
    mime, _ = mimetypes.guess_type(path)
    return FileResponse(path, media_type=mime or "video/mp4")


@app.get("/clip_video")
async def clip_video():
    path = state.get("clip_path")
    if not path or not Path(path).exists():
        raise HTTPException(404, "Clip not found")
    mime, _ = mimetypes.guess_type(path)
    return FileResponse(path, media_type=mime or "video/mp4")


@app.get("/clip_info")
async def clip_info():
    path = state.get("clip_path")
    if not path:
        raise HTTPException(404, "No clip yet")
    return JSONResponse({
        "saved_to": str(path),
        "filename": Path(path).name,
    })


@app.get("/video_info")
async def video_info():
    path = state.get("video_path")
    if not path or not Path(path).exists():
        raise HTTPException(404, "Video not found")
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise HTTPException(500, "ffprobe failed")
    info = json.loads(result.stdout)
    fps = 30.0  # fallback
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            r = stream.get("r_frame_rate", "30/1")
            num, den = r.split("/")
            fps = float(num) / float(den)
            break
    ext = Path(path).suffix.lstrip(".")
    title = state.get("title", "video")
    default_filename = f"{sanitize(title)}_yoinked.{ext or 'mp4'}"
    return JSONResponse({"fps": fps, "ext": ext or "mp4", "default_filename": default_filename})


class ExportRequest(BaseModel):
    start: float
    end: float
    filename: str = "clip.mp4"


@app.post("/export")
async def export(req: ExportRequest):
    input_path = state.get("video_path")
    if not input_path or not Path(input_path).exists():
        raise HTTPException(404, "Video not found")

    filename = Path(req.filename).name or "clip.mp4"

    downloads = Path.home() / "Downloads"
    downloads.mkdir(exist_ok=True)
    out_path = downloads / filename

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-ss", str(req.start),
        "-to", str(req.end),
        "-c", "copy",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(500, f"ffmpeg error: {result.stderr[-500:]}")

    state["clip_path"] = str(out_path)
    return JSONResponse({"saved_to": str(out_path)})


@app.post("/shutdown")
async def shutdown():
    def _exit():
        import time
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=_exit, daemon=True).start()
    return JSONResponse({"ok": True})


# ── Helpers ───────────────────────────────────────────────────────────────────

def find_free_port(start: int = 8765) -> int:
    port = start
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1


def fetch_title(url: str) -> str:
    result = subprocess.run(
        ["yt-dlp", "--no-download", "--print", "%(title)s", url],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return "video"


def download_video(url: str) -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for f in DOWNLOAD_DIR.glob("video.*"):
        f.unlink(missing_ok=True)

    template = str(DOWNLOAD_DIR / "video.%(ext)s")
    subprocess.run(
        ["yt-dlp", "-o", template, url],
        check=True,
    )

    matches = [m for m in DOWNLOAD_DIR.glob("video.*") if m.suffix != ".part"]
    if not matches:
        print("Error: could not find downloaded file", file=sys.stderr)
        sys.exit(1)
    return matches[0]


# ── CLI entry point ───────────────────────────────────────────────────────────

def cli():
    if len(sys.argv) < 2:
        print("Usage: yoink <url>")
        sys.exit(1)

    url = sys.argv[1]
    if not url.startswith(("http://", "https://")):
        print(f"Error: '{url}' doesn't look like a URL", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching title: {url}")
    title = fetch_title(url)
    state["title"] = title
    print(f"Title: {title}")

    print(f"Downloading: {url}")
    video_path = download_video(url)
    print(f"Downloaded: {video_path}")

    state["video_path"] = str(video_path)

    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    print(f"Starting server at {base_url}")

    def open_browser():
        import time
        time.sleep(1.0)
        webbrowser.open(base_url)

    threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
