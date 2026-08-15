"""Password hashing, JWT, and OTP helpers."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import bcrypt
import jwt

from app.config import settings

logger = logging.getLogger(__name__)

ALGO = "HS256"


def _secret() -> str:
    secret = (settings.jwt_secret or "").strip()
    if len(secret) < 16:
        raise RuntimeError("JWT_SECRET is missing or too short. Set it in backend/.env")
    return secret


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def new_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(otp: str) -> str:
    return hmac.new(_secret().encode("utf-8"), otp.encode("utf-8"), hashlib.sha256).hexdigest()


def otp_matches(otp: str, otp_hash: str) -> bool:
    if not otp or not otp_hash:
        return False
    return hmac.compare_digest(hash_otp(otp), otp_hash)


def issue_token(*, user_id: str, email: str, role: str, name: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "name": name,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.jwt_expire_hours)).timestamp()),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGO)


def decode_token(token: str) -> dict:
    return jwt.decode(token, _secret(), algorithms=[ALGO])


def send_otp_email(to_email: str, otp: str, name: str) -> None:
    """Send the signup OTP. If SMTP is not configured, log it for local use."""
    body = (
        f"Hello {name},\n\n"
        f"Your Narco-Graph Intel verification code is {otp}.\n"
        f"It expires in {settings.otp_expire_minutes} minutes.\n\n"
        "If you did not create this account, ignore this message.\n"
    )
    if not settings.smtp_configured:
        logger.info("OTP for %s (SMTP not configured; local only): %s", to_email, otp)
        return

    sender = (settings.smtp_from or "").strip() or settings.smtp_user
    message = EmailMessage()
    message["Subject"] = "Narco-Graph Intel verification code"
    message["From"] = sender
    message["To"] = to_email
    message.set_content(body)
    message.add_alternative(
        (
            f"<p>Hello {name},</p>"
            f"<p>Your Narco-Graph Intel verification code is "
            f"<strong style='font-size:20px;letter-spacing:4px'>{otp}</strong>.</p>"
            f"<p>It expires in {settings.otp_expire_minutes} minutes.</p>"
            "<p>If you did not create this account, ignore this message.</p>"
        ),
        subtype="html",
    )

    host = settings.smtp_host.strip()
    port = settings.smtp_port
    user = settings.smtp_user.strip()
    password = settings.smtp_password.replace(" ", "")
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
                smtp.login(user, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.starttls()
                smtp.login(user, password)
                smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        logger.exception("SMTP login failed")
        raise RuntimeError(
            "SMTP login failed. For Gmail use an App Password, not your normal password."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        logger.exception("SMTP send failed")
        raise RuntimeError(f"Could not send OTP email: {exc}") from exc
    logger.info("OTP emailed to %s", to_email)
