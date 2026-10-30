import subprocess
import uuid
import json
import shutil
import tempfile
import logging
from pathlib import Path

log = logging.getLogger("transcribeai.downloader")

TMP_DIR = Path(tempfile.gettempdir()) / "transcribeai_jobs"
TMP_DIR.mkdir(exist_ok=True)


UPLOAD_HINT = "You can also save the video on your device and use Upload a file."


def _friendly_download_error(stderr: str) -> str:
    t = (stderr or "").lower()
    if any(k in t for k in ("log in", "login", "sign in", "cookies", "comfortable for some audiences", "age-restricted", "age restricted")):
        return "This video requires a login or is age-restricted, so it cannot be fetched from a link. " + UPLOAD_HINT
    if "private" in t:
        return "This video is private. Use a public link. " + UPLOAD_HINT
    if "unsupported url" in t:
        return "This link is not supported. Use a TikTok, Instagram, Facebook or X video link."
    if any(k in t for k in ("unavailable", "removed", "not available", "404", "does not exist")):
        return "This video is unavailable or was removed. " + UPLOAD_HINT
    if any(k in t for k in ("429", "too many requests", "rate limit", "blocked")):
        return "The platform is limiting requests right now. Try again in a few minutes. " + UPLOAD_HINT
    return "Could not download this link. It may be private, restricted or unavailable. " + UPLOAD_HINT


class DownloadError(Exception):
    pass


def detect_platform(url: str) -> str:
    u = url.lower()
    if "tiktok" in u:
        return "tiktok"
    if "instagram" in u:
        return "instagram"
    if "facebook" in u or "fb.watch" in u:
        return "facebook"
    if "x.com" in u or "twitter" in u:
        return "x"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    return "unknown"


def download_audio(url: str, max_duration_seconds: int = 900) -> dict:
    job_id = str(uuid.uuid4())
    job_dir = TMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(job_dir / "source.%(ext)s")

    # Probe duration first
    probe_cmd = ["yt-dlp", "--no-warnings", "--skip-download", "--print-json", url]
    duration, title = None, "untitled"
    try:
        probe = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=60)
        if probe.returncode == 0 and probe.stdout.strip():
            info = json.loads(probe.stdout.strip().splitlines()[-1])
            duration = info.get("duration") or None
            title = info.get("title", "untitled")
            if duration and duration > max_duration_seconds:
                raise DownloadError(
                    f"Video is {int(duration/60)} min long, exceeds the {int(max_duration_seconds/60)} min limit."
                )
    except subprocess.TimeoutExpired:
        raise DownloadError("Timed out reading video info. The link may be invalid or private.")
    except json.JSONDecodeError:
        pass

    # Download audio as MP3 (much smaller than WAV — keeps files under
    # Groq's 25MB limit: 60 min at 48kbps is about 21.6MB).
    # 16kHz mono at 48kbps is plenty for speech recognition accuracy.
    dl_cmd = [
        "yt-dlp", "--no-warnings",
        "-f", "bestaudio/best",
        "--extract-audio", "--audio-format", "mp3", "--audio-quality", "48K",
        "--postprocessor-args", "ffmpeg:-ar 16000 -ac 1",
        "-o", output_template,
        url,
    ]
    try:
        result = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise DownloadError("Download timed out. Try a shorter video or check the link.")

    if result.returncode != 0:
        # Keep the technical details in the server log only; show a short,
        # friendly message to the visitor (raw yt-dlp text is long and confusing).
        log.warning("yt-dlp failed for %s: %s", url, result.stderr.strip()[-600:])
        raise DownloadError(_friendly_download_error(result.stderr))

    audio_files = list(job_dir.glob("*.mp3"))
    if not audio_files:
        raise DownloadError("Download succeeded but no audio track was produced.")

    return {
        "wav_path": str(audio_files[0]),
        "duration_seconds": duration,
        "title": title,
        "job_dir": str(job_dir),
    }


def cleanup_job_dir(job_dir: str):
    shutil.rmtree(job_dir, ignore_errors=True)


def convert_media_file(src_path: str, max_duration_seconds: int = 900) -> dict:
    """Extract a 16 kHz mono 48 kbps MP3 from an uploaded audio/video file.
    Same output format as download_audio(), so the rest of the pipeline is shared."""
    src = Path(src_path)
    job_dir = src.parent

    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(src)],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise DownloadError("Timed out reading this file. Please try another file.")
    if probe.returncode != 0:
        raise DownloadError("This file could not be read. Please upload a valid audio or video file.")

    duration = None
    try:
        duration = float(probe.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        duration = None
    if duration and duration > max_duration_seconds:
        raise DownloadError(
            f"This file is {int(duration/60)} min long, which exceeds the {int(max_duration_seconds/60)} min limit."
        )

    out = job_dir / "audio.mp3"
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-i", str(src),
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
        "-t", str(int(max_duration_seconds) + 1),
        str(out),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        raise DownloadError("Converting this file took too long. Try a shorter file.")
    if result.returncode != 0 or not out.exists():
        raise DownloadError("Could not extract audio. Does this file contain an audio track?")

    src.unlink(missing_ok=True)  # free disk space right away
    return {
        "wav_path": str(out),
        "duration_seconds": duration,
        "title": "upload",
        "job_dir": str(job_dir),
    }
