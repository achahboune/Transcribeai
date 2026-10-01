"""Plan-gated post-processing helpers for TranscribeAI.

These helpers deliberately keep the transcript as the source of truth. The
subtitle exporters use Whisper segment timestamps, while the speaker labels
are explicitly marked as estimated turns unless a dedicated diarization
provider is configured later.
"""
from __future__ import annotations

import io
import re
from typing import Any

from .config import PLAN_LIMITS_MINUTES


PLAN_RANK = {"free": 0, "creator": 1, "pro": 2}


def has_plan(plan: str, minimum: str) -> bool:
    return PLAN_RANK.get(plan, 0) >= PLAN_RANK.get(minimum, 99)


def clean_dictionary(value: str | None) -> list[str]:
    if not value:
        return []
    terms = []
    for raw in re.split(r"[,\n]", value):
        term = " ".join(raw.strip().split())
        if term and term not in terms:
            terms.append(term[:80])
    return terms[:50]


def build_transcription_prompt(dictionary: list[str]) -> str | None:
    if not dictionary:
        return None
    return (
        "Use these custom names, brands, and technical terms when they are "
        "spoken. Preserve the original language and do not add terms that "
        "were not spoken: " + ", ".join(dictionary)
    )


def _timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        whole += 1
        millis = 0
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{millis:03d}"


def _vtt_timestamp(seconds: float) -> str:
    return _timestamp(seconds).replace(",", ".")


def normalize_segments(segments: list[dict[str, Any]] | None, transcript: str) -> list[dict[str, Any]]:
    if segments:
        return [
            {
                "start": float(item.get("start") or 0),
                "end": float(item.get("end") or item.get("start") or 0),
                "text": str(item.get("text") or "").strip(),
            }
            for item in segments
            if str(item.get("text") or "").strip()
        ]
    return [{"start": 0.0, "end": 0.0, "text": transcript.strip()}] if transcript.strip() else []


def transcript_to_srt(segments: list[dict[str, Any]], diarized: bool = False) -> str:
    lines = []
    for idx, segment in enumerate(segments, 1):
        text = segment["text"]
        if diarized:
            text = f"[Speaker {((idx - 1) % 2) + 1} — estimated] {text}"
        end = segment["end"] if segment["end"] > segment["start"] else segment["start"] + 2
        lines += [str(idx), f"{_timestamp(segment['start'])} --> {_timestamp(end)}", text, ""]
    return "\n".join(lines)


def transcript_to_vtt(segments: list[dict[str, Any]], diarized: bool = False) -> str:
    lines = ["WEBVTT", ""]
    for segment in segments:
        text = segment["text"]
        if diarized:
            text = f"[Speaker {((len(lines) - 2) % 2) + 1} — estimated] {text}"
        end = segment["end"] if segment["end"] > segment["start"] else segment["start"] + 2
        lines += [f"{_vtt_timestamp(segment['start'])} --> {_vtt_timestamp(end)}", text, ""]
    return "\n".join(lines)


def transcript_to_docx(transcript: str, summary: str | None = None, title: str = "TranscribeAI transcript") -> bytes:
    from docx import Document

    document = Document()
    document.add_heading(title, level=1)
    if summary:
        document.add_heading("Summary", level=2)
        document.add_paragraph(summary)
    document.add_heading("Transcript", level=2)
    for paragraph in transcript.splitlines() or [transcript]:
        document.add_paragraph(paragraph)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def plan_capabilities(plan: str) -> dict[str, bool]:
    return {
        "summary": has_plan(plan, "creator"),
        "docx_export": has_plan(plan, "creator"),
        "srt_export": has_plan(plan, "creator"),
        "vtt_export": has_plan(plan, "creator"),
        "custom_dictionary": has_plan(plan, "creator"),
        "estimated_speaker_turns": has_plan(plan, "creator"),
        "advanced_subtitles": has_plan(plan, "pro"),
        "team": has_plan(plan, "pro"),
        "api_access": has_plan(plan, "pro"),
    }
