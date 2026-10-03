"""
Contact form endpoint.

Uses Resend (free tier: 3000 emails/month, no credit card) to:
1. Notify Alaa at his email with the visitor's message.
2. Send the visitor a short thank-you confirmation.

Sends from a verified transcribeai.site address, required for delivering
to arbitrary visitor emails (Resend's shared sandbox address only allows
sending to the account owner's own verified email).
"""
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from .config import settings

router = APIRouter(prefix="/api", tags=["contact"])

RESEND_API_URL = "https://api.resend.com/emails"
FROM_ADDRESS = "TranscribeAI <contact@transcribeai.site>"


def _resend_configured() -> bool:
    return bool(settings.resend_api_key)


class ContactRequest(BaseModel):
    name: str
    email: EmailStr
    message: str


async def _send_email(to: str, subject: str, html: str):
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={"from": FROM_ADDRESS, "to": [to], "subject": subject, "html": html},
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Resend error ({resp.status_code}): {resp.text[:200]}")


@router.post("/contact")
async def contact(payload: ContactRequest):
    if not _resend_configured():
        raise HTTPException(status_code=501, detail="Contact form is not configured yet.")

    name = payload.name.strip()
    email = payload.email
    message = payload.message.strip()

    if not name or not message:
        raise HTTPException(status_code=400, detail="Name and message are required.")

    # 1. Notify Alaa
    notify_html = f"""
        <h2>New message from TranscribeAI</h2>
        <p><b>Name:</b> {name}</p>
        <p><b>Email:</b> {email}</p>
        <p><b>Message:</b></p>
        <p>{message}</p>
    """
    try:
        await _send_email(settings.contact_notify_email, f"New contact from {name}", notify_html)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # 2. Thank the visitor. This is best effort and must not fail the request if this
    # particular send has an issue (Alaa already got the notification).
    thanks_html = f"""
        <div style="max-width:620px;margin:0 auto;padding:36px 28px;background:#ffffff;color:#172033;font-family:Arial,Helvetica,sans-serif;line-height:1.6;border:1px solid #e8edf5;border-radius:18px;">
            <div style="font-size:30px;margin-bottom:14px;">✨</div>
            <h1 style="margin:0 0 16px;font-size:26px;line-height:1.25;color:#101828;">Thanks for contacting TranscribeAI</h1>
            <p style="margin:0 0 18px;font-size:16px;">Hi {name},</p>
            <p style="margin:0 0 22px;font-size:16px;">Your message has been received successfully. Our team will review it and get back to you shortly.</p>
            <div style="margin:24px 0;padding:18px 20px;background:#f5f8ff;border:1px solid #dce7ff;border-radius:12px;font-size:15px;">
                <strong>📩 Message received</strong><br>
                <span style="color:#526070;">Thank you for taking the time to write to us.</span>
            </div>
            <p style="margin:0 0 24px;font-size:16px;">We appreciate your interest in TranscribeAI and look forward to helping you.</p>
            <p style="margin:0;font-size:16px;">Best regards,<br><strong>The TranscribeAI Team</strong> 🤝</p>
        </div>
    """
    try:
        await _send_email(email, "Thanks for contacting TranscribeAI", thanks_html)
    except RuntimeError as e:
        # Best-effort: Alaa already got the notification above, so don't
        # fail the whole request, but this shouldn't happen now that the
        # domain is verified, so print it for visibility in Render logs.
        print(f"[contact] Thank-you email failed: {e}")

    return {"status": "ok"}
