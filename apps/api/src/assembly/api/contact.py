"""Phase 10B.7 — public Contact Us form endpoint.

Receives {name, email, message} from the marketing site's contact
form, validates the payload, applies a per-IP rate limit, and
forwards the message to the Assembly team mailbox via Resend.

Failures are graceful: when no email provider is configured, the
endpoint returns 503 with a clear, non-leaking error so the
frontend can show "we couldn't send your message right now —
please try again or email us directly".
"""
from __future__ import annotations

import logging
import re
import time
from collections import deque
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from assembly.config import get_settings


_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$",
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class ContactRequest(BaseModel):
    """Payload for POST /contact. The frontend mirrors this shape."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    message: str = Field(min_length=10, max_length=4000)
    # Honeypot — a hidden form field bots love to auto-fill.
    # Real users never see / touch this; if it's non-empty, treat
    # as spam and silently 200 to avoid telling the bot.
    company: str | None = Field(default=None, max_length=120)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("invalid email address")
        return v


class ContactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    detail: str


# ---------------------------------------------------------------------------
# Rate limiter — per-IP, in-memory, sliding 5-min window.
# ---------------------------------------------------------------------------


_RATE_WINDOW_SECONDS = 300
_RATE_MAX_REQUESTS = 3
_rate_buckets: dict[str, deque[float]] = {}


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Honors X-Forwarded-For (Railway/Vercel
    set it) and falls back to the direct peer."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_ok(ip: str) -> bool:
    now = time.monotonic()
    bucket = _rate_buckets.setdefault(ip, deque(maxlen=_RATE_MAX_REQUESTS + 1))
    # Drop stale entries
    while bucket and (now - bucket[0]) > _RATE_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= _RATE_MAX_REQUESTS:
        return False
    bucket.append(now)
    return True


# ---------------------------------------------------------------------------
# Email delivery — Resend HTTPS API. No SDK dependency; we already
# ship httpx.
# ---------------------------------------------------------------------------


def _build_email_body(payload: ContactRequest, ip: str) -> dict[str, Any]:
    settings = get_settings()
    safe_message = payload.message.strip()
    # Escape outgoing HTML aggressively — the message content
    # rendered in our inbox should never be live HTML.
    import html
    safe_message_html = html.escape(safe_message).replace("\n", "<br>")
    safe_name_html = html.escape(payload.name)
    safe_email_html = html.escape(payload.email)
    return {
        "from": settings.contact_from_email,
        "to": [settings.contact_to_email],
        "reply_to": payload.email,
        "subject": f"[Assembly contact] {payload.name}",
        "text": (
            f"New contact form submission.\n\n"
            f"Name:    {payload.name}\n"
            f"Email:   {payload.email}\n"
            f"Source:  {ip}\n\n"
            f"Message:\n"
            f"--------\n"
            f"{safe_message}\n"
        ),
        "html": (
            f"<p><strong>New contact form submission</strong></p>"
            f"<p><strong>Name:</strong> {safe_name_html}<br>"
            f"<strong>Email:</strong> {safe_email_html}<br>"
            f"<strong>Source IP:</strong> {html.escape(ip)}</p>"
            f"<p><strong>Message:</strong></p>"
            f"<p>{safe_message_html}</p>"
        ),
    }


async def _send_via_resend(payload: ContactRequest, ip: str) -> None:
    """POST the email to Resend. Raises HTTPException on failure
    so the route returns a sane status to the frontend."""
    settings = get_settings()
    if not settings.resend_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Contact email is not configured on this deployment. "
                "Please email team@assemblysimulator.com directly."
            ),
        )
    if not settings.contact_to_email or not settings.contact_from_email:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Contact recipient/sender not configured. Please "
                "email team@assemblysimulator.com directly."
            ),
        )

    body = _build_email_body(payload, ip)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.HTTPError as exc:
        logger.exception("contact.resend_transport_error")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Couldn't reach the email service right now. "
                "Please try again or email us directly."
            ),
        ) from exc

    if resp.status_code >= 400:
        # Don't echo Resend's body — could contain internal hints.
        # Log the status for ops, return a generic 502 to the user.
        logger.warning(
            "contact.resend_rejected status=%d body_len=%d",
            resp.status_code,
            len(resp.content or b""),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "The email service rejected the message. Please try "
                "again or email us directly."
            ),
        )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post(
    "/contact",
    response_model=ContactResponse,
    status_code=status.HTTP_200_OK,
    tags=["contact"],
)
async def contact(
    payload: ContactRequest, request: Request,
) -> ContactResponse:
    """Accept a contact-form submission, validate, and forward via
    email to the Assembly team mailbox."""
    # Honeypot: bots often auto-fill any text field they see. Real
    # users never see / touch `company`. Silently return ok so the
    # bot doesn't learn it was flagged.
    if payload.company and payload.company.strip():
        logger.info("contact.honeypot_tripped")
        return ContactResponse(
            ok=True,
            detail="Thanks — we'll get back to you soon.",
        )

    ip = _client_ip(request)
    if not _rate_limit_ok(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Too many contact requests from this address. "
                "Please try again in a few minutes."
            ),
        )

    await _send_via_resend(payload, ip)

    logger.info(
        "contact.delivered name=%s email_domain=%s",
        payload.name,
        payload.email.split("@", 1)[-1] if "@" in payload.email else "?",
    )
    return ContactResponse(
        ok=True,
        detail="Thanks — we'll get back to you soon.",
    )
