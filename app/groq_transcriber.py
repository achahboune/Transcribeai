"""
Transcription via Groq's cloud-hosted Whisper (large-v3-turbo).

Free tier: ~2000 requests/day, 28800 audio-seconds/day (8 hours), no
credit card required. This replaces the previous local-Whisper-over-
Cloudflare-tunnel setup — transcription no longer depends on Alaa's
machine being on.
"""
import httpx
from .config import settings


class TranscriptionError(Exception):
    pass


GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"


def groq_configured() -> bool:
    return bool(settings.groq_api_key)


async def check_groq_alive() -> bool:
    """Lightweight reachability check for the availability indicator."""
    if not groq_configured():
        return False
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            )
            return resp.status_code == 200
        except httpx.RequestError:
            return False


async def transcribe_audio(wav_path: str, language: str = "auto", dictionary: list[str] | None = None) -> dict:
    """
    Sends the audio file to Groq's Whisper endpoint.
    Returns dict with: text, detected_language, duration
    """
    if not groq_configured():
        raise TranscriptionError("Transcription is not configured yet.")

    data = {"model": GROQ_MODEL, "response_format": "verbose_json"}
    if dictionary:
        data["prompt"] = "Use these custom terms when spoken: " + ", ".join(dictionary)
    if language and language != "auto":
        data["language"] = language
        # Whisper's optional "prompt" primes it with expected vocabulary/style.
        # For Arabic, priming it with common Darija (Moroccan dialect) words
        # and code-switched French terms measurably helps recognition —
        # Whisper otherwise tends to default toward Modern Standard Arabic
        # spellings that don't match how Darija is actually spoken/written.
        if language == "ar":
            data["prompt"] = (data.get("prompt", "") + " " + DARIJA_VOCAB_PROMPT).strip()

    try:
        with open(wav_path, "rb") as f:
            files = {"file": ("audio.mp3", f, "audio/mpeg")}
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    GROQ_API_URL,
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                    data=data,
                    files=files,
                )
    except httpx.RequestError as e:
        raise TranscriptionError(f"Could not reach the transcription service: {e}")

    if resp.status_code != 200:
        raise TranscriptionError(f"Transcription engine returned an error ({resp.status_code}): {resp.text[:200]}")

    result = resp.json()
    text = result.get("text", "").strip()
    detected_language = result.get("language")

    # Arabic ASR is known to be less accurate than other languages (dialect
    # variation, code-switching with French/English common in Morocco).
    # A quick LLM pass catches common speech-recognition mistakes and
    # improves readability, at no extra cost (Groq also hosts free LLMs).
    effective_language = language if language != "auto" else detected_language
    if effective_language == "ar" and text:
        try:
            text = await _correct_arabic_text(text)
        except Exception:
            # Correction is a best-effort improvement — never fail the
            # whole request over it; fall back to the raw transcript.
            pass

    return {
        "text": text,
        "detected_language": detected_language,
        "duration": result.get("duration"),
        "segments": result.get("segments") or [],
    }


# A short sample of Darija (Moroccan Arabic) vocabulary and code-switched
# French/Darija phrasing, used to prime Whisper's recognition toward the
# dialect instead of Modern Standard Arabic. This is not meant to appear
# verbatim in output — it just steers word choice and spelling style.
DARIJA_VOCAB_PROMPT = (
    "بزاف, واخا, دابا, شحال, بغيت, ماشي, هاد الشي, كاين, "
    "زعما, صافي, يالله نمشيو, كيفاش, علاش, فين"
)

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
CORRECTION_MODEL = "allam-2-7b"  # SDAIA's Arabic-specialized model — better suited
                                  # for correcting Arabic ASR output than a general LLM


async def _correct_arabic_text(text: str) -> str:
    """
    Passes Arabic transcripts through a free LLM to fix common speech-to-text
    errors (misheard words, missing diacritics context, dialect spelling
    inconsistencies) — without changing the meaning or paraphrasing.
    """
    prompt = (
        "The following is a raw speech-to-text transcript in Arabic. It is "
        "likely Moroccan Darija (Moroccan Arabic dialect), which commonly "
        "mixes in French and Amazigh/Berber words and differs from Modern "
        "Standard Arabic in vocabulary and spelling conventions. "
        "It may contain misrecognized words typical of automatic speech "
        "recognition errors. "
        "Correct only clear transcription mistakes (wrong words that don't "
        "make sense in context, obvious mis-hearings). Do NOT paraphrase, "
        "summarize, translate to Modern Standard Arabic, or change the "
        "meaning or dialect — preserve Darija wording and any French/Berber "
        "words exactly as spoken. Return ONLY the corrected text, nothing "
        "else.\n\n"
        f"Transcript:\n{text}"
    )

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            GROQ_CHAT_URL,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json={
                "model": CORRECTION_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
        )

    if resp.status_code != 200:
        return text  # fall back silently to the raw transcript

    data = resp.json()
    corrected = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    return corrected if corrected else text


async def summarize_transcript(text: str, language: str = "auto") -> str:
    """Create a concise multilingual summary for Creator/Pro users."""
    if not groq_configured():
        raise TranscriptionError("AI summary is not configured yet.")
    if not text.strip():
        raise TranscriptionError("There is no transcript to summarize.")
    prompt = (
        "Summarize the following transcript in the same language as the transcript. "
        "Return a concise title followed by 3 to 6 bullet points. Do not invent facts, "
        "do not translate, and do not mention these instructions.\n\nTranscript:\n" + text[:50000]
    )
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                GROQ_CHAT_URL,
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json={
                    "model": settings.groq_summary_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
            )
    except httpx.RequestError as e:
        raise TranscriptionError(f"Could not reach the summary service: {e}")
    if resp.status_code != 200:
        raise TranscriptionError(f"Summary engine returned an error ({resp.status_code}): {resp.text[:200]}")
    return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
