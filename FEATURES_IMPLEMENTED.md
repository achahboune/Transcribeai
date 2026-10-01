# TranscribeAI feature implementation

## Implemented

- AI summaries for Creator and Pro through Groq chat completions.
- TXT, DOCX, SRT, and VTT downloads.
- Whisper timestamp segments returned by the transcription endpoint.
- Creator/Pro custom dictionary prompts for names, brands, and jargon.
- Capability flags in `/api/me` and `/api/capabilities`.
- Versioned Pro API aliases:
  - `POST /api/v1/transcribe`
  - `POST /api/v1/summary`
  - `POST /api/v1/export/{txt|docx|srt|vtt}`
- Frontend controls for custom terms, summary, and exports.
- Logout/reset clears the URL, language, transcript, summary, usage state, and custom dictionary.

## Environment

The existing variables remain required. Optional:

```env
GROQ_SUMMARY_MODEL=llama-3.1-8b-instant
```

The DOCX export dependency is included in `requirements.txt`.

## Plan gates

- Free: transcription and TXT export.
- Creator: summary, DOCX/SRT/VTT, and custom dictionary.
- Pro: all Creator capabilities plus versioned API routes and advanced plan capability flags.

## Important limitation

The current Groq Whisper integration returns transcript timestamps but not speaker embeddings. The code therefore does **not** claim to provide true speaker diarization. A real speaker-separated result requires an additional diarization provider such as pyannote with a hosted model/token and a storage/processing policy. Team workspaces, shared glossaries, and API-key management likewise require Supabase tables/migrations and an invitation workflow; they are not silently represented as complete features.
