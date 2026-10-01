"""
PayPal Subscriptions billing.

Flow:
1. Frontend renders PayPal Subscribe buttons (JS SDK) for the Creator/Pro plans.
2. User approves the subscription in the PayPal popup — PayPal returns a
   subscription_id to the frontend.
3. Frontend calls POST /api/billing/confirm-subscription with that ID.
4. This backend verifies the subscription directly with PayPal's API
   (never trusts the frontend blindly) — checks it's ACTIVE and belongs
   to the plan claimed — then updates the user's plan in Supabase.

PayPal webhooks (for renewals/cancellations) can be added later; for the
MVP, confirm-subscription handles the initial upgrade, which is the main
flow that matters first.
"""
import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from .config import settings
from .auth import get_current_user

router = APIRouter(prefix="/api/billing", tags=["billing"])

PAYPAL_API_BASE = "https://api-m.sandbox.paypal.com" if settings.paypal_env == "sandbox" else "https://api-m.paypal.com"


def _plan_id_to_name():
    return {
        settings.paypal_plan_creator: "creator",
        settings.paypal_plan_pro: "pro",
    }


def _paypal_configured() -> bool:
    return bool(settings.paypal_client_id and settings.paypal_client_secret)


class ConfirmSubscriptionRequest(BaseModel):
    subscription_id: str
    plan: str  # "creator" or "pro" — what the frontend claims; we verify against PayPal


async def _get_paypal_access_token() -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{PAYPAL_API_BASE}/v1/oauth2/token",
            auth=(settings.paypal_client_id, settings.paypal_client_secret),
            data={"grant_type": "client_credentials"},
        )
    if resp.status_code != 200:
        # Surface the real PayPal error (e.g. invalid_client) instead of a
        # generic message — this is the only way to diagnose credential or
        # environment (sandbox vs live) mismatches from the deployed app.
        raise HTTPException(
            status_code=502,
            detail=f"Could not authenticate with PayPal ({resp.status_code}, env={settings.paypal_env}): {resp.text[:200]}",
        )
    return resp.json()["access_token"]


async def _update_profile_plan(user_id: str, plan: str, paypal_subscription_id: str):
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"{settings.supabase_url}/rest/v1/profiles",
            params={"id": f"eq.{user_id}"},
            headers={
                "apikey": settings.supabase_service_key,
                "Authorization": f"Bearer {settings.supabase_service_key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json={
                "plan": plan,
                "minutes_used_this_period": 0,
                "paypal_subscription_id": paypal_subscription_id,
            },
        )
    if resp.status_code not in (200, 204):
        # Surface the real Supabase error instead of failing silently —
        # the payment already succeeded, so we need to know exactly why
        # activation failed (e.g. missing column, RLS issue).
        raise HTTPException(
            status_code=502,
            detail=f"Payment succeeded but plan activation failed in our database ({resp.status_code}): {resp.text[:200]}",
        )


@router.post("/confirm-subscription")
async def confirm_subscription(payload: ConfirmSubscriptionRequest, user=Depends(get_current_user)):
    if not _paypal_configured():
        raise HTTPException(status_code=501, detail="Payments are not configured yet.")

    if payload.plan not in ("creator", "pro"):
        raise HTTPException(status_code=400, detail="Invalid plan.")

    # Verify the subscription directly with PayPal — never trust the frontend alone.
    token = await _get_paypal_access_token()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{PAYPAL_API_BASE}/v1/billing/subscriptions/{payload.subscription_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Could not verify this subscription with PayPal.")

    sub = resp.json()
    status = sub.get("status")
    plan_id = sub.get("plan_id")

    if status != "ACTIVE":
        raise HTTPException(status_code=400, detail=f"Subscription is not active yet (status: {status}).")

    real_plan = _plan_id_to_name().get(plan_id)
    if not real_plan:
        raise HTTPException(status_code=400, detail="Unrecognized PayPal plan.")

    await _update_profile_plan(user["id"], real_plan, payload.subscription_id)

    return {"plan": real_plan, "status": "active"}
