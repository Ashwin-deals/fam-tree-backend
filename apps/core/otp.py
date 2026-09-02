"""Email OTP verification: generation, hashing, rate limiting, and delivery.

Used for two purposes (see the `purpose` field on the `otps` collection):
- "registration": verifying a new account's email before it can sign in.
- "password_change": confirming an authenticated user really owns the account
  before a password change is applied.

Codes are never stored in plaintext — only a salted hash (Django's password
hasher, the same one used for user passwords) is persisted, and a code is
single-use (`consumed_at` is set the moment it's accepted).
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from email.mime.image import MIMEImage
from pathlib import Path

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import EmailMultiAlternatives
from rest_framework.exceptions import APIException

from .database import get_database
from .services import now

logger = logging.getLogger(__name__)

OTP_LENGTH = 6
OTP_EXPIRY_MINUTES = 10
OTP_RESEND_COOLDOWN_SECONDS = 45
OTP_MAX_RESENDS_PER_HOUR = 5
OTP_MAX_VERIFY_ATTEMPTS = 5

_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo-email.png"
_LOGO_CID = "rootkin_logo"


class OtpThrottled(APIException):
    status_code = 429
    default_detail = "Please wait before requesting another code."
    default_code = "otp_throttled"


class EmailDeliveryError(APIException):
    status_code = 503
    default_detail = "We couldn't send the verification email. Please try again shortly."
    default_code = "email_delivery_failed"


def is_test_account(email: str) -> bool:
    """
    Development/testing exception ONLY — do not broaden.

    Exact-suffix match on "@test.com". Accounts matching this skip OTP
    verification entirely (both registration and password-change) so local
    and automated-test flows never depend on real email delivery. This is the
    single place that decision is made; every caller that needs to know
    whether OTP applies to an account must go through this function rather
    than re-implementing the check, so the exception can't quietly drift into
    a broader bypass.
    """
    return email.strip().lower().endswith("@test.com")


def _generate_code() -> str:
    return f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"


def _latest_otp(user_id, purpose: str) -> dict | None:
    return get_database().otps.find_one(
        {"user_id": user_id, "purpose": purpose, "consumed_at": None},
        sort=[("created_at", -1)],
    )


def verify_otp_code(user_id, purpose: str, code: str) -> tuple[bool, str | None]:
    """Check a submitted code against the latest unconsumed OTP for this user/purpose.

    Returns (ok, error_message). On success the OTP is marked consumed (single-use).
    """
    otp = _latest_otp(user_id, purpose)
    if not otp:
        return False, "Request a new code and try again."
    if otp["expires_at"] < now():
        return False, "This code has expired. Request a new one."
    if otp.get("attempts", 0) >= OTP_MAX_VERIFY_ATTEMPTS:
        return False, "Too many incorrect attempts. Request a new code."
    if not check_password(code, otp["code_hash"]):
        get_database().otps.update_one({"_id": otp["_id"]}, {"$inc": {"attempts": 1}})
        return False, "Incorrect code. Please try again."
    get_database().otps.update_one({"_id": otp["_id"]}, {"$set": {"consumed_at": now()}})
    return True, None


def request_otp(user: dict, purpose: str) -> dict:
    """Create a fresh OTP (subject to resend rate limits) and email it.

    Raises OtpThrottled if the caller is resending too fast/too often, or
    EmailDeliveryError if sending fails. Returns timing info for the frontend
    countdown/expiry UI.
    """
    database = get_database()
    user_id = user["_id"]
    timestamp = now()
    window_start = timestamp - timedelta(hours=1)

    most_recent = database.otps.find_one({"user_id": user_id, "purpose": purpose}, sort=[("created_at", -1)])
    if most_recent:
        elapsed = (timestamp - most_recent["created_at"]).total_seconds()
        if elapsed < OTP_RESEND_COOLDOWN_SECONDS:
            raise OtpThrottled(detail=f"Please wait {int(OTP_RESEND_COOLDOWN_SECONDS - elapsed)}s before requesting another code.")

    recent_count = database.otps.count_documents({"user_id": user_id, "purpose": purpose, "created_at": {"$gte": window_start}})
    if recent_count >= OTP_MAX_RESENDS_PER_HOUR:
        raise OtpThrottled(detail="Too many codes requested. Please try again in an hour.")

    code = _generate_code()
    expires_at = timestamp + timedelta(minutes=OTP_EXPIRY_MINUTES)
    database.otps.insert_one({
        "user_id": user_id,
        "purpose": purpose,
        "code_hash": make_password(code),
        "created_at": timestamp,
        "expires_at": expires_at,
        "consumed_at": None,
        "attempts": 0,
    })

    send_otp_email(user, code, purpose)

    return {
        "expires_in_seconds": OTP_EXPIRY_MINUTES * 60,
        "resend_after_seconds": OTP_RESEND_COOLDOWN_SECONDS,
    }


def request_otp_lenient(user: dict, purpose: str) -> dict:
    """Like request_otp, but a delivery failure doesn't block the caller.

    The OTP record is created either way (request_otp creates it before attempting to
    send), so the account can still reach a recoverable "verify your email" screen and
    retry sending via the resend button, instead of a dead end — e.g. account creation
    should never fail outright just because SMTP is temporarily misconfigured. Throttling
    (OtpThrottled) is a real, intentional block and is NOT swallowed here.
    """
    try:
        return request_otp(user, purpose)
    except EmailDeliveryError:
        return {
            "expires_in_seconds": OTP_EXPIRY_MINUTES * 60,
            "resend_after_seconds": OTP_RESEND_COOLDOWN_SECONDS,
        }


_COPY = {
    "registration": {
        "subject": "Verify your email — Root & Kin",
        "title": "Verify your email",
        "intro": "Welcome to Root & Kin! Enter this code to verify your email and activate your account.",
    },
    "password_change": {
        "subject": "Confirm your password change — Root & Kin",
        "title": "Confirm your password change",
        "intro": "We received a request to change your Root & Kin password. Enter this code to confirm it's really you.",
    },
}


def _build_email_html(name: str, code: str, purpose: str) -> str:
    copy = _COPY[purpose]
    spaced_code = " ".join(code)
    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f9f7f2;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f9f7f2;padding:40px 16px;">
<tr><td align="center">
<table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px;width:100%;background:#fffdf9;border-radius:16px;border:1px solid #e7e1d9;overflow:hidden;font-family:Georgia,'Times New Roman',serif;">
<tr><td style="padding:36px 40px 0;text-align:center;">
  <img src="cid:{_LOGO_CID}" width="52" height="52" alt="Root &amp; Kin" style="display:block;margin:0 auto 14px;" />
  <div style="font-size:18px;font-weight:600;color:#282923;letter-spacing:-0.02em;">Root &amp; Kin</div>
</td></tr>
<tr><td style="padding:26px 40px 6px;text-align:center;">
  <h1 style="margin:0 0 10px;font-size:23px;font-weight:600;color:#282923;">{copy['title']}</h1>
  <p style="margin:0;font-size:14px;line-height:1.6;color:#6c6b62;font-family:Helvetica,Arial,sans-serif;">{copy['intro']}</p>
</td></tr>
<tr><td style="padding:24px 40px 4px;text-align:center;">
  <p style="margin:0 0 8px;font-size:11px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:#a76545;font-family:Helvetica,Arial,sans-serif;">Your verification code</p>
  <div style="display:inline-block;padding:16px 28px;background:#f3e4d9;border-radius:12px;letter-spacing:6px;font-size:30px;font-weight:700;color:#804b32;font-family:'Courier New',monospace;">{spaced_code}</div>
</td></tr>
<tr><td style="padding:16px 40px 0;text-align:center;">
  <p style="margin:0;font-size:13px;color:#6c6b62;font-family:Helvetica,Arial,sans-serif;">This code expires in {OTP_EXPIRY_MINUTES} minutes.</p>
</td></tr>
<tr><td style="padding:18px 40px 32px;text-align:center;">
  <p style="margin:0 0 6px;font-size:12px;color:#96958c;font-family:Helvetica,Arial,sans-serif;">If you did not request this, you can safely ignore this email.</p>
  <p style="margin:0;font-size:12px;font-weight:700;color:#96958c;font-family:Helvetica,Arial,sans-serif;">Never share this code with anyone.</p>
</td></tr>
<tr><td style="padding:20px 40px;border-top:1px solid #e5e0d7;text-align:center;">
  <p style="margin:0 0 4px;font-size:12px;color:#96958c;font-family:Helvetica,Arial,sans-serif;">Root &amp; Kin — Your family. Your story. Your space.</p>
  <p style="margin:0;font-size:11px;color:#96958c;font-family:Helvetica,Arial,sans-serif;">This is an automated message. Please do not reply.</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _build_email_text(name: str, code: str, purpose: str) -> str:
    copy = _COPY[purpose]
    return (
        f"Root & Kin\n\n"
        f"{copy['title']}\n\n"
        f"{copy['intro']}\n\n"
        f"Your verification code is: {code}\n\n"
        f"This code expires in {OTP_EXPIRY_MINUTES} minutes.\n"
        f"If you did not request this, you can safely ignore this email.\n"
        f"Never share this code with anyone.\n\n"
        f"— Root & Kin — Your family. Your story. Your space.\n"
        f"This is an automated message. Please do not reply."
    )


def send_otp_email(user: dict, code: str, purpose: str) -> None:
    copy = _COPY[purpose]
    message = EmailMultiAlternatives(
        subject=copy["subject"],
        body=_build_email_text(user.get("name", ""), code, purpose),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user["email"]],
    )
    message.attach_alternative(_build_email_html(user.get("name", ""), code, purpose), "text/html")
    message.mixed_subtype = "related"
    try:
        if _LOGO_PATH.exists():
            with open(_LOGO_PATH, "rb") as handle:
                logo = MIMEImage(handle.read())
            logo.add_header("Content-ID", f"<{_LOGO_CID}>")
            logo.add_header("Content-Disposition", "inline", filename="root-and-kin-logo.png")
            message.attach(logo)
        message.send(fail_silently=False)
    except Exception as error:  # noqa: BLE001 — any SMTP/network failure should surface uniformly
        # Deliberately never log the OTP code itself, only that a send attempt failed.
        logger.exception("Failed to send %s OTP email to user %s", purpose, user.get("_id"))
        raise EmailDeliveryError() from error
