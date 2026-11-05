"""
Transcription via Groq's cloud-hosted Whisper (large-v3-turbo).

Free tier: ~2000 requests/day, 28800 audio-seconds/day (8 hours), no
credit card required. This replaces the previous local-Whisper-over-
Cloudflare-tunnel setup — transcription no longer depends on Alaa's
machine being on.
"""
import asyncio
import json
import logging
import re

import httpx
from .config import settings

log = logging.getLogger("transcribeai.translate")


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
                    **({"reasoning_effort": "low"} if "gpt-oss" in settings.groq_summary_model else {}),
                },
            )
    except httpx.RequestError as e:
        raise TranscriptionError(f"Could not reach the summary service: {e}")
    if resp.status_code != 200:
        raise TranscriptionError(f"Summary engine returned an error ({resp.status_code}): {resp.text[:200]}")
    return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()


# ---------------------------------------------------------------------------
# Translation (transcript -> language chosen by the user)
# ---------------------------------------------------------------------------
LANGUAGE_NAMES = {
    "en": "English", "fr": "French", "es": "Spanish", "ar": "Arabic",
    "de": "German", "it": "Italian", "pt": "Portuguese", "nl": "Dutch",
    "ru": "Russian", "zh": "Chinese (Simplified)", "ja": "Japanese",
    "ko": "Korean", "tr": "Turkish", "hi": "Hindi", "pl": "Polish",
}
_NAME_TO_CODE = {
    "english": "en", "french": "fr", "spanish": "es", "arabic": "ar",
    "german": "de", "italian": "it", "portuguese": "pt", "dutch": "nl",
    "russian": "ru", "chinese": "zh", "mandarin": "zh", "japanese": "ja",
    "korean": "ko", "turkish": "tr", "hindi": "hi", "polish": "pl",
}
TRANSLATE_BATCH_CHARS = 3500
TRANSLATE_BATCH_ITEMS = 80
TRANSLATE_CONCURRENCY = 3


def normalize_lang(value) -> str | None:
    """Whisper returns names like 'english'; the UI uses codes like 'en'."""
    if not value:
        return None
    v = str(value).strip().lower()
    if v in LANGUAGE_NAMES:
        return v
    return _NAME_TO_CODE.get(v)


def _translate_model() -> str:
    return settings.groq_translate_model or settings.groq_summary_model


async def _chat(prompt: str) -> str:
    """One chat-completion call; returns the text content (raises on failure)."""
    model = _translate_model()
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    if "gpt-oss" in model:
        body["reasoning_effort"] = "low"
    for attempt in (1, 2):
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                GROQ_CHAT_URL,
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json=body,
            )
        if resp.status_code == 429 and attempt == 1:
            await asyncio.sleep(2)
            continue
        if resp.status_code != 200:
            raise TranscriptionError(f"Translation engine error ({resp.status_code})")
        return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "") or ""
    raise TranscriptionError("Translation engine is busy")


def _parse_json_array(content: str, expected: int) -> list[str]:
    t = content.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("no JSON array")
    arr = json.loads(t[start:end + 1])
    if not isinstance(arr, list) or len(arr) != expected or not all(isinstance(x, str) for x in arr):
        raise ValueError("wrong shape")
    return arr


async def _translate_batch(texts: list[str], target_name: str, stats: dict) -> list[str]:
    prompt = (
        f"Translate the following JSON array of transcript lines into {target_name}. "
        "Rules: return exactly the same number of items in the same order; translate each "
        "item on its own (never merge or split items); keep names, numbers and brand names; "
        "no explanations. Output ONLY a JSON array of strings.\n\n"
        + json.dumps(texts, ensure_ascii=False)
    )
    if stats.get("engine_down"):  # engine unreachable: do not hammer it
        stats["failed"] += len(texts)
        return list(texts)
    try:
        return _parse_json_array(await _chat(prompt), len(texts))
    except (TranscriptionError, httpx.RequestError) as e:  # engine/network problem
        log.warning("translation engine problem: %s", e)
        stats["engine_down"] = True
        stats["failed"] += len(texts)
        return list(texts)
    except Exception as e:  # bad JSON or wrong item count
        log.warning("translation batch of %d failed: %s", len(texts), e)
        if len(texts) > 1:  # retry with two smaller batches
            mid = len(texts) // 2
            left = await _translate_batch(texts[:mid], target_name, stats)
            right = await _translate_batch(texts[mid:], target_name, stats)
            return left + right
        stats["failed"] += 1
        return list(texts)  # keep the original line rather than losing it


async def translate_texts(texts: list[str], target_code: str) -> tuple[list[str], int]:
    """Translate lines, preserving order. Returns (translated_lines, failed_count)."""
    target_name = LANGUAGE_NAMES[target_code]
    batches: list[list[int]] = []
    cur: list[int] = []
    size = 0
    for i, t in enumerate(texts):
        if cur and (size + len(t) > TRANSLATE_BATCH_CHARS or len(cur) >= TRANSLATE_BATCH_ITEMS):
            batches.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(t)
    if cur:
        batches.append(cur)

    stats = {"failed": 0}
    sem = asyncio.Semaphore(TRANSLATE_CONCURRENCY)

    async def run(idx: list[int]):
        async with sem:
            return await _translate_batch([texts[i] for i in idx], target_name, stats)

    results = await asyncio.gather(*(run(b) for b in batches))
    out = list(texts)
    for idx, translated in zip(batches, results):
        for i, t in zip(idx, translated):
            out[i] = t
    return out, stats["failed"]


def _split_text(text: str, limit: int = 900) -> list[str]:
    parts = re.split(r"(?<=[.!?\u061f\u3002])\s+", text.strip())
    chunks, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) + 1 > limit:
            chunks.append(cur)
            cur = p
        else:
            cur = (cur + " " + p).strip()
    if cur:
        chunks.append(cur)
    return chunks or [text]


async def translate_result(result: dict, target: str) -> dict:
    """Translate a transcription result into `target` (a UI language code).
    target 'auto' (or the same language as the audio) leaves it untouched."""
    out = dict(result)
    detected = normalize_lang(result.get("detected_language"))
    target = (target or "auto").strip().lower()
    out["output_language"] = detected or result.get("detected_language")
    out["translated"] = False
    if target in ("", "auto") or target not in LANGUAGE_NAMES:
        return out
    out["output_language"] = target
    if detected == target or not (result.get("text") or "").strip():
        return out

    segments = [
        s for s in (result.get("segments") or [])
        if str(s.get("text", "")).strip()
    ]
    try:
        if segments:
            lines = [str(s["text"]).strip() for s in segments]
            translated, failed = await translate_texts(lines, target)
            if failed >= len(lines):
                raise TranscriptionError("all lines failed")
            out["segments"] = [
                {"id": i, "start": s.get("start"), "end": s.get("end"), "text": t}
                for i, (s, t) in enumerate(zip(segments, translated))
            ]
            out["text"] = " ".join(t.strip() for t in translated).strip()
        else:
            pieces = _split_text(result["text"])
            translated, failed = await translate_texts(pieces, target)
            if failed >= len(pieces):
                raise TranscriptionError("all lines failed")
            out["text"] = " ".join(t.strip() for t in translated).strip()
        out["translated"] = True
        if failed:
            out["translation_error"] = "Some lines could not be translated and are shown in the original language."
    except Exception as e:
        log.warning("translation failed: %s", e)
        out["output_language"] = detected or result.get("detected_language")
        out["translation_error"] = "Translation is unavailable right now. Showing the original text."
    return out
