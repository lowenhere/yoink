# yoink

Download a video, clip it in the browser, export it — all in one command.

```text
yoink <url> [--section "*00:01:00-00:02:00"]
```

Optional:
- `--section` or `-s` (eg. `*00:01:00-00:02:00`, `*12-24`, etc.) for partial download.

## How it works

1. **Download** — runs `yt-dlp` to fetch the video to `/tmp/yoink/`
2. **Open** — starts a local web server and opens a browser tab
3. **Clip** — set In/Out points with frame-precise controls and a waveform scrubber
4. **Export** — runs `ffmpeg` to cut the clip, saves it to `~/Downloads/`

## Requirements

- Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/)
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) — in `$PATH`
- [`ffmpeg`](https://ffmpeg.org/) + `ffprobe` — in `$PATH`

## Installation

```bash
uv tool install git+https://github.com/lowenhere/yoink
```

Or clone and run directly:

```bash
git clone https://github.com/lowenhere/yoink
cd yoink
uv run yoink <url>
```

## Usage

```
yoink <url>
```

Any URL supported by yt-dlp works (YouTube, Vimeo, Twitter/X, etc.).

Default download quality is capped at **1080p**. The downloader now prefers a single-file stream (`best[height<=1080]`) first, and falls back to `bestvideo[height<=1080]+bestaudio` only when needed.

Exports are re-encoded to HEVC when possible, using hardware acceleration if a hardware HEVC encoder is available. If hardware HEVC is unavailable, it falls back to `libx265` and then `libx264`.

### Browser controls

| Action | How |
|---|---|
| Frame step | `←` / `→` arrow keys, or Prev/Next buttons |
| Mark In | Click **Mark In** or type seconds |
| Mark Out | Click **Mark Out** or type seconds |
| Seek | Click anywhere on the waveform |
| Export | Click **Export Clip** — saves to `~/Downloads/` |
