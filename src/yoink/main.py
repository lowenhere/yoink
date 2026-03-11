import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import webbrowser
import argparse
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

STATIC_DIR = Path(__file__).parent / "static"
DOWNLOAD_DIR = Path("/tmp/yoink")
DEFAULT_VIDEO_FORMAT = "best[height<=1080]/bestvideo[height<=1080]+bestaudio"
HEVC_ENCODER_PREFERENCE = (
    "hevc_videotoolbox",
    "hevc_nvenc",
    "hevc_qsv",
    "hevc_amf",
    "hevc_vaapi",
)

_cached_video_encoder: str | None = None

app = FastAPI()
state: dict = {}


def sanitize(title: str) -> str:
    """Make a title safe to use as a filename."""
    s = re.sub(r"[^\w\s-]", "", title)
    s = re.sub(r"\s+", "_", s.strip())
    return s[:80] or "video"


def sanitize_filename(name: str) -> str:
    safe = re.sub(r"[^\w.\- ]", "_", name.strip())
    safe = re.sub(r"\s+", "_", safe)
    if not safe:
        return "clip.mp4"
    return safe[:140]


def normalize_section(section: str) -> str | None:
    if not section:
        return None

    s = section.strip()
    if not s:
        return None

    if s.startswith("*"):
        return s

    # Common user inputs are raw timestamp ranges like 00:01:00-00:02:00 or 90-120.
    # yt-dlp requires a selector prefix for section matching.
    if "-" in s and (":" in s or all(ch.isdigit() or ch in ".-:" for ch in s)):
        return f"*{s}"

    return s


def _get_available_encoders() -> set[str]:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return set()

    encoders: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and re.match(r"^[VAS][A-Z\.DIBS]{5,}", parts[0]):
            encoders.add(parts[1])
    return encoders


def _select_export_video_encoder() -> str:
    global _cached_video_encoder
    if _cached_video_encoder is not None:
        return _cached_video_encoder

    encoders = _get_available_encoders()

    for name in HEVC_ENCODER_PREFERENCE:
        if name in encoders:
            _cached_video_encoder = name
            return name

    if "libx265" in encoders:
        _cached_video_encoder = "libx265"
        return "libx265"

    _cached_video_encoder = "libx264"
    return "libx264"


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

    if not isinstance(req.start, (int, float)) or not isinstance(req.end, (int, float)):
        raise HTTPException(400, "start and end must be numeric")
    if req.start < 0 or req.end <= req.start:
        raise HTTPException(400, "start must be >= 0 and end must be greater than start")

    filename = sanitize_filename(Path(req.filename).name)
    if not filename.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
        filename = f"{filename}.mp4"

    downloads = Path.home() / "Downloads"
    downloads.mkdir(exist_ok=True)
    out_path = downloads / filename

    codec = _select_export_video_encoder()
    cmd = [
        "ffmpeg", "-y", "-hide_banner",
        "-i", str(input_path),
        "-ss", str(req.start),
        "-to", str(req.end),
        "-c:v", codec,
        "-c:a", "aac",
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


def download_video(url: str, section: str | None = None) -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for f in DOWNLOAD_DIR.glob("video.*"):
        f.unlink(missing_ok=True)

    template = str(DOWNLOAD_DIR / "video.%(ext)s")
    args = [
        "yt-dlp",
        "-o", template,
        "-f", DEFAULT_VIDEO_FORMAT,
    ]
    if section:
        section = normalize_section(section)
        if section:
            args.extend(["--download-sections", section])
    args.append(url)
    subprocess.run(
        args,
        check=True,
    )

    matches = [m for m in DOWNLOAD_DIR.glob("video.*") if m.suffix != ".part"]
    if not matches:
        print("Error: could not find downloaded file", file=sys.stderr)
        sys.exit(1)
    return matches[0]


# ── CLI entry point ───────────────────────────────────────────────────────────

def cli():
    parser = argparse.ArgumentParser(
        description="Download and trim a video with a browser editor."
    )
    parser.add_argument("url", help="Video URL supported by yt-dlp")
    parser.add_argument(
        "--section",
        help="Optional yt-dlp section selector (eg: '*00:01:00-00:02:00')",
    )
    parser.add_argument(
        "-s",
        "--sections",
        help="Alias for --section",
    )
    args = parser.parse_args()

    url = args.url
    section = args.section or args.sections

    if not url.startswith(("http://", "https://")):
        print(f"Error: '{url}' doesn't look like a URL", file=sys.stderr)
        sys.exit(1)
    if section is not None and not section.strip():
        print("Error: --section cannot be empty", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching title: {url}")
    title = fetch_title(url)
    state["title"] = title
    print(f"Title: {title}")

    if section:
        print(f"Downloading section: {section}")
    print(f"Downloading: {url}")
    video_path = download_video(url, section=section.strip() if section else None)
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
