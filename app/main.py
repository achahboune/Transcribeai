"""
TranscribeAI — Render orchestrator.

1. Authenticates the user via their Supabase session token and checks quota.
2. Downloads video audio via yt-dlp.
3. Sends audio to Groq-hosted Whisper for transcription.
4. Returns the transcript and updates the user's quota usage.
"""

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, HttpUrl

from .config import settings, PLAN_LIMITS_MINUTES
from .downloader import (
    download_audio,
    detect_platform,
    cleanup_job_dir,
    DownloadError,
)
from .groq_transcriber import (
    transcribe_audio,
    summarize_transcript,
    check_groq_alive,
    groq_configured,
    TranscriptionError,
)
from .auth import get_current_user, get_profile, update_minutes_used
from .billing import router as billing_router
from .contact import router as contact_router
from .features import (
    clean_dictionary,
    has_plan,
    normalize_segments,
    plan_capabilities,
    transcript_to_docx,
    transcript_to_srt,
    transcript_to_vtt,
)

app = FastAPI(title="TranscribeAI Orchestrator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(billing_router)
app.include_router(contact_router)


# ---------------------------------------------------------------------------
# Frontend and SEO pages
# ---------------------------------------------------------------------------

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Max simultaneous downloads/conversions (the Oracle VM has 1 CPU core).
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)

SEO_PAGES = [
    "tiktok-transcription",
    "instagram-transcription",
    "video-to-text",
    "audio-to-text",
    "ai-subtitle-generator",
    "arabic-transcription",
    "french-transcription",
]


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /auth/\n"
        "Sitemap: https://transcribeai.site/sitemap.xml\n"
    )


@app.get("/sitemap.xml", response_class=Response)
def sitemap_xml():
    urls = [
        ("https://transcribeai.site/", "weekly", "1.0"),
        *[
            (
                f"https://transcribeai.site/seo/{slug}.html",
                "monthly",
                "0.8",
            )
            for slug in SEO_PAGES
        ],
    ]

    body = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]

    for loc, changefreq, priority in urls:
        body.append(
            f"  <url>"
            f"<loc>{loc}</loc>"
            f"<changefreq>{changefreq}</changefreq>"
            f"<priority>{priority}</priority>"
            f"</url>"
        )

    body.append("</urlset>")

    return Response(
        content="\n".join(body) + "\n",
        media_type="application/xml",
    )


@app.get("/")
def serve_index():
    page = FRONTEND_DIR / "index.html"

    if not page.exists():
        raise HTTPException(
            status_code=404,
            detail="Frontend index.html not found.",
        )

    return FileResponse(str(page))


@app.get("/seo.css")
def serve_seo_css():
    page = FRONTEND_DIR / "seo.css"

    if not page.exists():
        raise HTTPException(
            status_code=404,
            detail="SEO stylesheet not found.",
        )

    return FileResponse(
        str(page),
        media_type="text/css",
    )


@app.get("/favicon.svg")
def serve_favicon():
    page = FRONTEND_DIR / "favicon.svg"

    if not page.exists():
        raise HTTPException(
            status_code=404,
            detail="Favicon not found.",
        )

    return FileResponse(
        str(page),
        media_type="image/svg+xml",
    )


def serve_seo_page(slug: str):
    page = FRONTEND_DIR / "seo" / f"{slug}.html"

    if not page.exists():
        raise HTTPException(
            status_code=404,
            detail=f"SEO page not found: {slug}",
        )

    return FileResponse(
        str(page),
        media_type="text/html; charset=utf-8",
    )


@app.get("/seo/youtube-transcription.html")
def youtube_transcription_removed():
    # YouTube is no longer supported: permanently redirect the old SEO URL.
    return RedirectResponse(url="/", status_code=301)


@app.get("/seo/tiktok-transcription.html")
def tiktok_transcription():
    return serve_seo_page("tiktok-transcription")


@app.get("/seo/instagram-transcription.html")
def instagram_transcription():
    return serve_seo_page("instagram-transcription")


@app.get("/seo/video-to-text.html")
def video_to_text():
    return serve_seo_page("video-to-text")


@app.get("/seo/audio-to-text.html")
def audio_to_text():
    return serve_seo_page("audio-to-text")


@app.get("/seo/ai-subtitle-generator.html")
def ai_subtitle_generator():
    return serve_seo_page("ai-subtitle-generator")


@app.get("/seo/arabic-transcription.html")
def arabic_transcription():
    return serve_seo_page("arabic-transcription")


@app.get("/seo/french-transcription.html")
def french_transcription():
    return serve_seo_page("french-transcription")


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------

class TranscribeRequest(BaseModel):
    url: HttpUrl
    language: str = "auto"
    dictionary: str = Field(default="", max_length=4000)


class TextRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=200000)
    language: str = "auto"
    segments: list[dict] = Field(default_factory=list)
    dictionary: str = Field(default="", max_length=4000)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/whisper-status")
async def whisper_status():
    """Returns the live availability of the transcription service."""
    if not groq_configured():
        return {
            "available": False,
            "reason": "Transcription is not configured yet.",
        }

    alive = await check_groq_alive()

    return {
        "available": alive,
        "reason": (
            None
            if alive
            else "Transcription service is temporarily unreachable."
        ),
    }


@app.get("/api/me")
async def me(user=Depends(get_current_user)):
    profile = await get_profile(user["id"])
    limit = PLAN_LIMITS_MINUTES.get(
        profile.get("plan", "free"),
        30,
    )

    return {
        "email": profile.get("email"),
        "plan": profile.get("plan", "free"),
        "minutes_used_this_period": profile.get(
            "minutes_used_this_period",
            0,
        ),
        "minutes_limit": limit,
        "capabilities": plan_capabilities(
            profile.get("plan", "free")
        ),
    }


@app.get("/api/capabilities")
async def capabilities(user=Depends(get_current_user)):
    profile = await get_profile(user["id"])

    return {
        "plan": profile.get("plan", "free"),
        "capabilities": plan_capabilities(
            profile.get("plan", "free")
        ),
    }


@app.post("/api/transcribe")
@app.post("/api/v1/transcribe")
async def transcribe(
    payload: TranscribeRequest,
    user=Depends(get_current_user),
):
    # 1. Load profile and enforce quota.
    profile = await get_profile(user["id"])

    plan = profile.get("plan", "free")
    minutes_used = profile.get(
        "minutes_used_this_period",
        0,
    )
    minutes_limit = PLAN_LIMITS_MINUTES.get(
        plan,
        30,
    )

    if minutes_used >= minutes_limit:
        raise HTTPException(
            status_code=402,
            detail=(
                f"You've used your {minutes_limit} min/month "
                f"on the {plan} plan. Upgrade to continue."
            ),
        )

    if not groq_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Transcription is temporarily unavailable. "
                "Please try again shortly."
            ),
        )

    dictionary = clean_dictionary(payload.dictionary)

    if dictionary and not has_plan(plan, "creator"):
        raise HTTPException(
            status_code=402,
            detail=(
                "Custom dictionary is available on "
                "Creator and Pro plans."
            ),
        )

    # 2. Download audio, limited by the remaining quota.
    url = str(payload.url)
    platform = detect_platform(url)
    job_dir = None

    if platform == "youtube":
        raise HTTPException(
            status_code=400,
            detail=(
                "YouTube links are not supported. Use a public TikTok, "
                "Instagram, Facebook or X video link."
            ),
        )

    remaining_minutes = minutes_limit - minutes_used
    max_seconds = min(
        settings.max_job_seconds,
        int(remaining_minutes * 60),
    )

    try:
        # Run the blocking yt-dlp/ffmpeg download in a worker thread so the
        # server stays responsive, and allow only a few at once (1 CPU core).
        async with DOWNLOAD_SEMAPHORE:
            download = await asyncio.to_thread(
                download_audio,
                url,
                max_duration_seconds=max_seconds,
            )

        job_dir = download["job_dir"]

        # 3. Transcribe via Groq.
        result = await transcribe_audio(
            download["wav_path"],
            language=payload.language,
            dictionary=dictionary,
        )

        # 4. Update quota usage.
        actual_duration = (
            result.get("duration")
            or download.get("duration_seconds")
            or 0
        )

        new_total = minutes_used + (actual_duration / 60.0)

        await update_minutes_used(
            user["id"],
            new_total,
        )

        return {
            "transcript": result["text"],
            "detected_language": result.get("detected_language"),
            "platform": platform,
            "duration_seconds": actual_duration,
            "segments": result.get("segments", []),
            "dictionary_terms": dictionary,
            "minutes_used_this_period": round(new_total, 2),
            "minutes_limit": minutes_limit,
        }

    except DownloadError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    except TranscriptionError as e:
        raise HTTPException(
            status_code=502,
            detail=str(e),
        )

    finally:
        if job_dir:
            cleanup_job_dir(job_dir)


@app.post("/api/summary")
@app.post("/api/v1/summary")
async def summary(
    payload: TextRequest,
    user=Depends(get_current_user),
):
    profile = await get_profile(user["id"])
    plan = profile.get("plan", "free")

    if not has_plan(plan, "creator"):
        raise HTTPException(
            status_code=402,
            detail=(
                "AI summaries are available on "
                "Creator and Pro plans."
            ),
        )

    try:
        result = await summarize_transcript(
            payload.transcript,
            payload.language,
        )

    except TranscriptionError as e:
        raise HTTPException(
            status_code=502,
            detail=str(e),
        )

    return {"summary": result}


@app.post("/api/export/{file_format}")
@app.post("/api/v1/export/{file_format}")
async def export_transcript(
    file_format: str,
    payload: TextRequest,
    user=Depends(get_current_user),
):
    profile = await get_profile(user["id"])
    plan = profile.get("plan", "free")

    file_format = file_format.lower()

    allowed = {
        "txt",
        "docx",
        "srt",
        "vtt",
    }

    if file_format not in allowed:
        raise HTTPException(
            status_code=400,
            detail="Supported formats are TXT, DOCX, SRT, and VTT.",
        )

    if file_format != "txt" and not has_plan(plan, "creator"):
        raise HTTPException(
            status_code=402,
            detail=(
                "This export format is available on "
                "Creator and Pro plans."
            ),
        )

    segments = normalize_segments(
        payload.segments,
        payload.transcript,
    )

    diarized = False

    if file_format == "txt":
        body = payload.transcript.encode("utf-8")
        media_type = "text/plain; charset=utf-8"

    elif file_format == "srt":
        body = transcript_to_srt(
            segments,
            diarized,
        ).encode("utf-8")
        media_type = "application/x-subrip"

    elif file_format == "vtt":
        body = transcript_to_vtt(
            segments,
            diarized,
        ).encode("utf-8")
        media_type = "text/vtt; charset=utf-8"

    else:
        body = transcript_to_docx(payload.transcript)
        media_type = (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        )

    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="transcribeai-transcript.{file_format}"'
            )
        },
    )
