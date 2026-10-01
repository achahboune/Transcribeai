from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Supabase — used for verifying user sessions and reading/writing their
    # profile (plan, quota).
    supabase_url: str
    supabase_service_key: str

    # Groq — hosts Whisper in the cloud (free tier), so transcription no
    # longer depends on Alaa's local machine being on.
    groq_api_key: str = ""
    groq_summary_model: str = "llama-3.1-8b-instant"
    pyannote_auth_token: str = ""

    # PayPal Subscriptions — sandbox by default. Empty client_secret means
    # billing endpoints return a clear 501 instead of crashing.
    paypal_env: str = "sandbox"  # "sandbox" or "live"
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_plan_creator: str = ""  # P-XXXXXXXXXXXXXXXXXXXX
    paypal_plan_pro: str = ""      # P-XXXXXXXXXXXXXXXXXXXX
    frontend_url: str = "https://transcribe-u5sf.onrender.com"

    max_job_seconds: int = 900  # 15 min hard cap per video for the MVP

    # Resend — sends the contact form notification (to Alaa) and the
    # thank-you email (to the visitor). Free tier: 3000 emails/month.
    resend_api_key: str = ""
    contact_notify_email: str = "achahboune@gmail.com"

    class Config:
        env_file = ".env"


settings = Settings()

PLAN_LIMITS_MINUTES = {
    "free": 30,
    "creator": 600,
    "pro": 1800,
}
